#!/usr/bin/env python3
"""DiaRUGA v0.29.0 `pipeline/focus_stack.py` 에서 왔다. 초점 시리즈를 합쳐 all-in-focus 이미지와 깊이(초점) 맵을 만든다.

group_focus_series.py 가 만든 groups.json 을 입력으로 받는다.

원리:
  1. 정렬 — 같은 위치라도 초점을 돌리면 배율이 미세하게 변하고(focus breathing)
     스테이지가 흔들리므로, ECC로 평행이동+스케일을 맞춘다.
  2. 선명도 맵 — 각 장에서 픽셀별 국소 선명도를 구한다. 라플라시안 절댓값을
     가우시안으로 부드럽게 한 값을 쓴다 (노이즈에 덜 민감).
  3. 합성 — 픽셀마다 가장 선명한 장을 고른다. 하드 선택은 경계에 이음매를
     만들므로, 선명도를 가중치로 한 soft blending 을 기본으로 한다.
  4. 깊이 맵 — 픽셀별로 '몇 번째 장이 가장 선명했나'가 곧 상대적인 높이다.
     유공충 껍질의 입체 구조가 여기에 드러난다.

사용 예:
    python focus_stack.py groups_RS23.json --dry-run
    python focus_stack.py groups_RS23.json
    python focus_stack.py groups_RS23.json --only 0 1 2

DB 로 옮기면서 달라진 것 (P02 6단계):

- **`stack_report.json` 이 없어졌다.** 슬라이드마다 덮어써져서, 마지막으로 돌린
  것만 남고 나머지는 사라졌다. 이제 시야마다 `Stack` 행이다
- 실행이 `Run(kind=stack)` 에 남는다 — 무슨 설정으로 몇 개를 합성했는지
- **이미지 경로를 DB 에서 얻는다.** groups.json 의 `dir` 은 사진을 옮기면 낡는다
  (실제로 `260729/…` 를 가리킨 채 남아 있었다). 어느 시야의 어느 프레임인지는
  DB 가 안다 — JSON 은 아직 "무엇이 한 그룹인가" 만 알려 준다
  (`group_focus_series.py` 가 6단계 마지막이라 그렇다)

이미지 파일 자체는 그대로 파일로 둔다. `*_scale.json` 사이드카도 계속 쓴다 —
검출 단계도 같은 값을 `Stack` 행에서 읽는다.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import django

# 이 스크립트는 저장소 밖(/srv/ForGIA/scripts)에 복사해 두고 컨테이너 안에서
# 돌릴 수도 있다. 그때 Django 코드가 어디 있는지는 FORGIA_APP 이 알려 준다 —
# 이미지 안의 /app 이고, 뷰어 컨테이너가 쓰는 바로 그 코드다. 저장소에서 그냥
# 돌리면 예전처럼 자기 옆의 web/ 을 본다.
# **저장소에서는 한 단계 위가 뿌리다** (스크립트가 pipeline/·ops/·migrate/
# 안에 있다). `/srv/ForGIA/scripts` 처럼 저장소 밖에서 돌 때는 그 짐작이
# 안 맞으므로 `FORGIA_APP` 이 알려 준다 — 컨테이너에서는 이미지 안의 /app 이다.
APP = Path(os.environ.get("FORGIA_APP")
          or Path(__file__).resolve().parent.parent)
# **`APP` 은 Django 코드를 찾는 자리일 뿐이다** (100). `sys.path` 앞에 통째로
# 밀어 넣으면 **이미지 안의 옛 `judge.py`·`scale.py` 가 자기 옆의 것을 가린다**
# — `/srv/ForGIA/scripts` 로 밀어 넣은 새 규칙이 안 먹는 채로 돌았다(실측).
# 그래서 **뒤에 붙인다**: 스크립트 자신의 디렉토리(파이썬이 `sys.path[0]` 에
# 놓는다)가 먼저이고, Django 는 그 뒤에서 찾힌다.
sys.path.insert(0, str(APP / "web"))
sys.path.append(str(APP))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "forgiaweb.settings")
django.setup()
# **모델 코드와 DB 의 판이 같은가** — DB 를 만지기 전에 본다 (198). 이미지의
# 모델이 걷힌 칼럼을 알고 있으면 SELECT 한 번에 죽는데, 저장 도중이면 앞
# 트랜잭션이 이미 커밋된 뒤라 실패마다 행이 남는다. 어긋나면 3 으로 끝낸다.
# 스크립트로 돌 때만이다 — 시험이 임포트할 때는 시험 DB 가 아직 없다.
import schema_guard                                                 # noqa: E402
if __name__ == "__main__":
    schema_guard.check_or_exit("focus_stack")

from django.conf import settings                                    # noqa: E402
from django.db import transaction                                   # noqa: E402
from django.utils import timezone                                   # noqa: E402

import runlog                                                       # noqa: E402
from viewer.images import ensure_stack_images
from viewer.models import Frame, Run, Slide, Stack, Viewpoint       # noqa: E402
from scale import ScaleLog, scaling_for, write_scale_sidecar     # noqa: E402


def git_version():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5,
                             cwd=Path(__file__).resolve().parent)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def rel(p) -> str:
    """DATA_ROOT 기준 상대경로. DB 는 이 형태로만 경로를 담는다."""
    p = Path(p)
    if not p.is_absolute():
        return str(p)
    try:
        return str(p.relative_to(Path(settings.DATA_ROOT)))
    except ValueError:
        return str(p)


def resolve_slide(groups_dir: str, want: str | None) -> Slide:
    """groups.json 의 dir 로 슬라이드를 찾는다.

    경로를 그대로 믿지 않는다 — 사진을 옮기면 JSON 은 낡은 채 남는다. 폴더
    이름으로 맞추되, 촬영일이 다른 같은 이름이 둘이면 사람에게 묻는다
    (photos/<촬영일>/<슬라이드>/ 구조라 그 상황이 실제로 가능하다).
    """
    if want:
        try:
            return Slide.objects.get(slug=want)
        except Slide.DoesNotExist:
            raise SystemExit(f"그런 슬라이드가 없다: {want}")

    name = Path(groups_dir).name
    hits = [s for s in Slide.objects.all() if Path(s.image_dir).name == name]
    if not hits:
        raise SystemExit(
            f"'{name}' 에 맞는 슬라이드가 DB 에 없다.\n"
            f"  groups.json 의 dir: {groups_dir}\n"
            f"  아직 임포트하지 않았는가?")
    if len(hits) > 1:
        opts = " · ".join(f"{s.slug}({s.image_dir})" for s in hits)
        raise SystemExit(f"'{name}' 이 여럿이다 — --slide 로 고를 것: {opts}")
    return hits[0]


def align_to_reference(ref_gray, img, img_gray, use_ecc=True):
    """img 를 ref 에 맞춘다. 실패하면 원본 그대로 반환."""
    if not use_ecc:
        return img, True
    warp = np.eye(2, 3, dtype=np.float32)
    # 초점 차이에 강건하도록 블러 후 정렬
    a = cv2.GaussianBlur(ref_gray, (0, 0), 3)
    b = cv2.GaussianBlur(img_gray, (0, 0), 3)
    try:
        cv2.findTransformECC(
            a, b, warp, cv2.MOTION_EUCLIDEAN,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5),
            None, 5,
        )
    except cv2.error:
        return img, False
    h, w = img.shape[:2]
    out = cv2.warpAffine(img, warp, (w, h),
                         flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
                         borderMode=cv2.BORDER_REPLICATE)
    return out, True


def sharpness_map(gray, sigma=3.0):
    """픽셀별 국소 선명도."""
    lap = cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F, ksize=3)
    return cv2.GaussianBlur(np.abs(lap), (0, 0), sigma)


def build_depth(smaps, best, conf_pct):
    """초점 슬라이스 인덱스에서 연속 깊이 맵과 신뢰 영역 마스크를 만든다.

    슬라이스가 5~6장뿐이라 argmax를 그대로 쓰면 깊이가 5~6단계로 뭉툭해진다.
    최대점 주변 3점에 포물선을 맞춰 소수점 단위로 보간한다 (subslice refinement).

    배경은 어느 장에서도 초점이 맞지 않아 argmax가 사실상 난수다. 최대 선명도가
    배경 수준을 뚜렷이 넘는 픽셀만 신뢰 영역으로 남기고 나머지는 마스킹한다.
    """
    n = smaps.shape[0]
    conf = smaps.max(axis=0)

    # 배경 수준 추정: 하위 절반의 중앙값을 잡음 바닥으로 본다
    floor = np.median(conf[conf <= np.percentile(conf, 50)])
    mask = (conf > np.percentile(conf, conf_pct)) & (conf > floor * 3.0)
    # 자잘한 스페클 제거
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN,
                            np.ones((5, 5), np.uint8)).astype(bool)

    # 포물선 보간: d = i + 0.5*(s[i-1]-s[i+1]) / (s[i-1]-2*s[i]+s[i+1])
    idx = np.clip(best, 1, n - 2)
    take = lambda o: np.take_along_axis(smaps, (idx + o)[None], axis=0)[0]  # noqa: E731
    s0, s1, s2 = take(-1), take(0), take(1)
    denom = s0 - 2 * s1 + s2
    shift = np.where(np.abs(denom) > 1e-6, 0.5 * (s0 - s2) / (denom + 1e-12), 0.0)
    shift = np.clip(shift, -1.0, 1.0)
    depth = idx.astype(np.float32) + shift
    # 가장자리 슬라이스가 최대인 경우는 보간 불가 — argmax 그대로
    edge = (best == 0) | (best == n - 1)
    depth[edge] = best[edge]

    # 공간 정규화: 신뢰 영역 안에서만 median 필터
    depth_f = cv2.medianBlur(depth.astype(np.float32), 5)
    depth = np.where(mask, depth_f, np.nan).astype(np.float32)
    return depth, mask


def carry_scaling(paths, scale, out_img, tag, scale_log=None):
    """합성본에 픽셀 크기를 물려준다.

    합성본에는 촬영 메타가 따라오지 않으므로, 원본에서 읽은 값을 사이드카로
    남기지 않으면 검출 단계가 계측 기준을 잃는다. 사람이 못 박은 배율은
    DiaRUGA 의 `um_per_pixel_override` 대신 폴더의 `scale.toml`(`override = true`)
    이 맡는다 — `scale.scaling_for` 가 그것을 먼저 본다.
    """
    scalings = [scaling_for(p) for p in paths]
    native = scalings[0]["um_per_pixel"]
    if any(abs(s["um_per_pixel"] - native) > native * 1e-3 for s in scalings):
        print(f"{tag}: 경고 — 그룹 안에서 픽셀 크기가 다르다. "
              f"첫 장({paths[0].name}) 기준으로 남긴다", file=sys.stderr)

    um = native / scale        # 리사이즈했으면 픽셀이 그만큼 커진다
    write_scale_sidecar(out_img, um,
                        source=scalings[0]["source"],
                        native_um_per_pixel=native,
                        resize_scale=scale,
                        stacked_from=[p.name for p in paths])
    if scale_log is not None:
        scale_log.add(tag, um)
    # 사이드카에 적는 것과 같은 값을 Stack 행에도 넣는다. 사이드카는
    # segment_forams.py 가 아직 읽으므로 둘 다 남긴다 (6단계 다음 차례).
    return {"um_per_pixel": um,
            "native_um_per_pixel": native,
            "resize_scale": scale,
            "um_per_pixel_source": scalings[0]["source"]}


def stack_group(paths, scale, use_ecc, soft, conf_pct, out_dir, tag, scale_log=None):
    imgs, grays = [], []
    for p in paths:
        im = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if im is None:
            raise IOError(f"cannot read {p}")
        if scale != 1.0:
            im = cv2.resize(im, None, fx=scale, fy=scale,
                            interpolation=cv2.INTER_AREA)
        imgs.append(im)
        grays.append(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY))

    # 가장 선명한 장을 정렬 기준으로 삼는다
    ref_i = int(np.argmax([cv2.Laplacian(g, cv2.CV_32F).var() for g in grays]))
    ref_gray = grays[ref_i]

    aligned, ok_flags = [], []
    for i, (im, g) in enumerate(zip(imgs, grays)):
        if i == ref_i:
            aligned.append(im); ok_flags.append(True); continue
        a, ok = align_to_reference(ref_gray, im, g, use_ecc)
        aligned.append(a); ok_flags.append(ok)
    n_failed = ok_flags.count(False)

    grays_a = [cv2.cvtColor(a, cv2.COLOR_BGR2GRAY) for a in aligned]
    smaps = np.stack([sharpness_map(g) for g in grays_a])       # (N,H,W)
    stack = np.stack(aligned).astype(np.float32)                # (N,H,W,3)

    best = np.argmax(smaps, axis=0).astype(np.int32)            # 깊이 인덱스

    if soft:  # noqa: SIM108  (아래에서 hard 분기와 함께 읽히도록 유지)
        # 선명도를 강조해 가중 평균 — 이음매 없이 합성
        w = smaps - smaps.min(axis=0, keepdims=True)
        w = (w / (w.max(axis=0, keepdims=True) + 1e-6)) ** 4
        w /= w.sum(axis=0, keepdims=True) + 1e-6
        fused = (stack * w[..., None]).sum(axis=0)
    else:
        fused = np.take_along_axis(stack, best[None, ..., None], axis=0)[0]
    fused = np.clip(fused, 0, 255).astype(np.uint8)

    depth, conf_mask = build_depth(smaps, best, conf_pct)

    depth_vis = np.full(depth.shape + (3,), 60, np.uint8)
    if conf_mask.any():
        lo, hi = depth[conf_mask].min(), depth[conf_mask].max()
        norm = (depth - lo) / (hi - lo) if hi > lo else np.zeros_like(depth)
        norm = np.nan_to_num(norm, nan=0.0)   # 마스크 밖은 NaN
        colored = cv2.applyColorMap((np.clip(norm, 0, 1) * 255).astype(np.uint8),
                                    cv2.COLORMAP_TURBO)
        depth_vis[conf_mask] = colored[conf_mask]

    out_img = out_dir / f"{tag}_focused.jpg"
    cv2.imwrite(str(out_img), fused, [cv2.IMWRITE_JPEG_QUALITY, 92])
    sc = carry_scaling(paths, scale, out_img, tag, scale_log)
    cv2.imwrite(str(out_dir / f"{tag}_depth.jpg"), depth_vis,
                [cv2.IMWRITE_JPEG_QUALITY, 92])
    np.savez_compressed(str(out_dir / f"{tag}_depth.npz"),
                        depth=depth.astype(np.float32),
                        mask=conf_mask)

    # 선명도 비교는 전역 Laplacian 분산으로 하면 안 된다. 초점이 크게 어긋난
    # 덩어리는 짙은 그림자가 되어 오히려 분산을 키우므로, 합성이 성공해도
    # 숫자가 내려간다. 물체가 있는 영역(conf_mask)에서의 국소 선명도로 비교한다.
    fused_sharp = sharpness_map(cv2.cvtColor(fused, cv2.COLOR_BGR2GRAY))
    m = conf_mask
    per_slice = [float(s[m].mean()) for s in smaps] if m.any() else [0.0]
    fused_mean = float(fused_sharp[m].mean()) if m.any() else 0.0
    best_single = max(per_slice)
    print(f"{tag}: n={len(paths)} ref={paths[ref_i].stem} align_failed={n_failed} "
          f"local sharpness (object px) best-single {best_single:.2f} -> "
          f"fused {fused_mean:.2f} ({fused_mean / max(best_single, 1e-6):.2f}x)")
    return {"tag": tag, "n": len(paths), "ref": paths[ref_i].stem,
            "align_failed": n_failed,
            # 배율은 반올림하지 않는다. 계측의 기준이고, 사이드카·Frame 과 값이
            # 어긋나면 check_db 의 "배율이 하나다" 검사가 둘로 갈라진다.
            "um_per_pixel": sc["um_per_pixel"],
            "native_um_per_pixel": sc["native_um_per_pixel"],
            "resize_scale": sc["resize_scale"],
            "um_per_pixel_source": sc["um_per_pixel_source"],
            "object_px_frac": round(float(m.mean()), 4),
            "sharpness_best_single": round(best_single, 3),
            "sharpness_fused": round(fused_mean, 3),
            "gain": round(fused_mean / max(best_single, 1e-6), 3)}


def save_stack(vp: Viewpoint, out_dir: Path, r: dict, run: Run) -> None:
    """합성 결과를 Stack 행으로 남긴다.

    Viewpoint 당 하나(OneToOne)라 다시 합성하면 덮어쓴다. 검출과 달리 쌓지 않는
    이유는 합성본이 재생성 가능한 산출물이고 사람의 교정이 붙지 않기 때문이다 —
    교정은 mask_key 로 검출에 붙는다.
    """
    focused = out_dir / f"{r['tag']}_focused.jpg"
    depth = out_dir / f"{r['tag']}_depth.jpg"
    npz = out_dir / f"{r['tag']}_depth.npz"

    ref = Frame.objects.filter(slide=vp.slide, name=r["ref"]).first()
    if ref is None:
        # 프레임을 못 찾아도 합성 자체는 유효하다. 조용히 넘기지 않고 알린다.
        print(f"  {r['tag']}: 경고 — 기준 프레임 {r['ref']} 을 DB 에서 못 찾았다",
              file=sys.stderr)

    st, _ = Stack.objects.update_or_create(
        viewpoint=vp,
        defaults=dict(
            focused_path=rel(focused),
            depth_path=rel(depth) if depth.exists() else "",
            depth_npz_path=rel(npz) if npz.exists() else "",
            um_per_pixel=r["um_per_pixel"],
            native_um_per_pixel=r["native_um_per_pixel"],
            resize_scale=r["resize_scale"],
            um_per_pixel_source=r["um_per_pixel_source"],
            ref_frame=ref,
            align_failed=r["align_failed"],
            object_px_frac=r["object_px_frac"],
            sharpness_best_single=r["sharpness_best_single"],
            sharpness_fused=r["sharpness_fused"],
            gain=r["gain"],
            run=run))
    # 합성본과 깊이맵을 이미지 테이블에 올린다 (P06). 다시 합성해도 `path` 가
    # 같으면 같은 행이고, 링크만 새 `Stack` 으로 맞춰진다.
    ensure_stack_images(st)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("groups_json", nargs="?",
                    help="옛 방식. 지금은 --slide 로 DB 에서 읽는 것이 기본이다")
    ap.add_argument("-o", "--out", default=None,
                    help="기본값은 DATA_ROOT/stacked")
    ap.add_argument("--slide", help="슬라이드 slug. 시야를 DB 에서 읽는다")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--min-n", type=int, default=2,
                    help="이 장수 미만인 그룹은 건너뜀 (단발 촬영)")
    ap.add_argument("--only", type=int, nargs="*", help="특정 그룹 id 만 처리")
    ap.add_argument("--no-ecc", action="store_true", help="정렬 생략 (빠름)")
    ap.add_argument("--hard", action="store_true",
                    help="soft blending 대신 픽셀별 최선명 장을 그대로 선택")
    ap.add_argument("--conf-pct", type=float, default=90.0,
                    help="깊이 맵 신뢰 영역 퍼센타일 (높을수록 물체만 남음)")
    ap.add_argument("--force", action="store_true",
                    help="이미 합성된 시야도 다시 합성한다")
    ap.add_argument("--dry-run", action="store_true",
                    help="무엇을 할지만 보여 준다. 파일도 DB 도 건드리지 않는다")
    args = ap.parse_args()

    if not args.slide and not args.groups_json:
        raise SystemExit("--slide <slug> 또는 groups.json 중 하나가 필요하다")

    data_root = Path(settings.DATA_ROOT)
    if args.slide:
        slide = resolve_slide("", args.slide)
        # 시야도 프레임도 DB 에 있다 (group_focus_series.py 가 넣었다).
        # JSON 을 거치지 않으므로 경로가 낡을 자리가 없다.
        plan = [(vp, [data_root / f.path
                      for f in vp.frames.order_by("seq")])
                for vp in slide.viewpoints.order_by("idx")]
    else:
        # 옛 방식 — 6단계 전에 만든 groups_*.json 으로도 돌 수 있게 남겨 둔다
        meta = json.loads(Path(args.groups_json).read_text(encoding="utf-8"))
        slide = resolve_slide(meta["dir"], args.slide)
        root = data_root / slide.image_dir
        by_tag = {vp.tag: vp for vp in slide.viewpoints.all()}
        plan = []
        for g in meta["groups"]:
            tag = f"g{g['id']:03d}_{g['images'][0]}-{g['images'][-1].split('-')[-1]}"
            vp = by_tag.get(tag)
            plan.append((vp if vp else tag,
                         [root / f"{n}.jpg" for n in g["images"]]))

    out_dir = Path(args.out) if args.out else (data_root / settings.STACK_DIR)
    done = {vp.tag for vp in slide.viewpoints.filter(stack__isnull=False)}

    todo, skipped, missing = [], [], []
    for vp, paths in plan:
        if not hasattr(vp, "tag"):          # 시야를 못 찾은 옛 JSON 항목
            missing.append(str(vp))
            continue
        if args.only is not None and vp.idx not in args.only:
            continue
        if len(paths) < args.min_n:         # 싱글턴은 합성본이 없다
            continue
        if vp.tag in done and not args.force:
            skipped.append(vp.tag)
            continue
        todo.append((vp, vp.tag, paths))
    by_tag = {vp.tag: vp for vp in slide.viewpoints.all()}

    print(f"슬라이드 {slide.slug} · 시야 {len(by_tag)}개 · "
          f"합성 대상 {len(todo)}개 (완료 {len(skipped)} 건너뜀)")
    if missing:
        # 조용히 넘기지 않는다 — 그룹핑과 DB 가 어긋났다는 뜻이다
        print(f"  경고: DB 에 없는 시야 {len(missing)}개 — "
              f"{', '.join(missing[:3])}{' …' if len(missing) > 3 else ''}",
              file=sys.stderr)
    if not todo:
        return

    if args.dry_run:
        for _, tag, _ in todo:
            print(f"  합성할 것: {tag}")
        print(f"\ndry-run — {len(todo)}개. 아무것도 쓰지 않았다.")
        return
    del by_tag

    out_dir.mkdir(parents=True, exist_ok=True)
    run = runlog.start(
        "stack", slide=slide,
        params={"scale": args.scale, "ecc": not args.no_ecc,
                "soft": not args.hard, "conf_pct": args.conf_pct,
                "min_n": args.min_n, "groups_json": args.groups_json},
        host=socket.gethostname(), code_version=git_version())

    scale_log = ScaleLog()
    n_ok = 0
    try:
        for vp, tag, paths in todo:
            r = stack_group(paths, args.scale, not args.no_ecc, not args.hard,
                            args.conf_pct, out_dir, tag, scale_log)
            # 그룹 하나마다 커밋한다. 합성이 그룹당 17초라 전체를 한 트랜잭션으로
            # 묶으면 그동안 뷰어의 쓰기가 막히고, 중간에 끊기면 전부 잃는다.
            with transaction.atomic():
                save_stack(vp, out_dir, r, run)
            n_ok += 1
    except Exception as e:
        run.status = "failed"
        run.error = f"{type(e).__name__}: {e}"
        run.finished_at = timezone.now()
        run.counts = {"stacked": n_ok, "planned": len(todo)}
        run.save()
        raise

    run.status = "done"
    run.finished_at = timezone.now()
    run.counts = {"stacked": n_ok, "skipped": len(skipped),
                  "missing_viewpoint": len(missing)}
    run.save()
    print(f"\n합성 {n_ok}개 -> {out_dir} · Run #{run.pk}")


if __name__ == "__main__":
    main()
