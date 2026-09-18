#!/usr/bin/env python3
"""DiaRUGA v0.29.0 `pipeline/group_focus_series.py` 에서 왔다. 같은 위치를 초점만 바꿔 찍은 사진들을 하나의 그룹으로 묶는다.

메타에 스테이지 XY/Z 좌표가 없으므로 이미지 내용으로 판단한다 — 유공충도
사람이 초점을 바꿔 여러 장 찍는다(P01 5절). **묶은 순서가 곧 격자 칸 번호다**
(`Viewpoint.cell`, 1 부터). 건너뛴 칸은 사람이 화면에서 고친다.
초점이 달라지면 고주파(선명도)는 크게 변하지만 입자들의 배치는 그대로이므로,
축소 + 가우시안 블러로 고주파를 죽인 뒤 정규화 상관계수를 비교하면
초점 변화에 둔감하고 시야 이동에는 민감한 지문이 된다.

촬영 시각 간격을 보조 신호로 함께 쓴다 (그룹 내부는 촘촘, 시야 이동 시 벌어짐).

사용 예:
    python group_focus_series.py "/data3/ForGIA/photos/261001/RS23-GC03 71cm >125um" --dry-run
    python group_focus_series.py "/data3/ForGIA/photos/261001/RS23-GC03 71cm >125um"

DB 로 옮기면서 달라진 것 (P02 6단계 마지막):

- `Slide`·`Viewpoint`·`Frame` 행을 직접 만든다. `groups_*.json` 은 내보내기로만 남는다
- **이미 검출하거나 교정한 슬라이드는 `--force` 없이 다시 묶지 않는다.** 재그룹핑은
  `Viewpoint` 를 재편하므로 그 아래 검출·교정이 통째로 어긋난다. 이 스크립트가
  6단계의 **마지막**인 이유가 그것이다
- **`--force` 는 교정을 지운다. 고아로 만드는 것이 아니다.** 오래도록 안내문이
  "교정은 mask_key 로 남지만 고아가 된다" 고 적혀 있었는데 **거짓이었다** —
  `ObjectReview.viewpoint` 가 `CASCADE` 라 시야를 지우면 행이 함께 사라진다.
  `mask_key` 는 칼럼 값일 뿐이고, 행이 없으면 아무것도 아니다. `ViewpointReview`·
  `Detection`·`Stack` 도 같다. 그래서 지금은 **교정이 붙은 슬라이드에 `--force` 만
  주면 거부하고**, `--discard-reviews` 를 함께 줘야 넘어간다. 그때도 지우기 전에
  교정을 파일로 먼저 뽑는다(`dump_reviews`) — **재생성 불가한 자료다**
- **분포가 양분되지 않으면 사람을 부른다** (P01 §1). 그룹 안의 최소 상관과 경계의
  최대 상관 사이가 벌어져 있어야 임계값이 의미가 있다. 좁으면 `state="failed"` 로
  남기고 넘어가지 않는다 — 잘못 묶인 시야 위에 쌓은 검출과 교정은 되돌리기가 훨씬 비싸다
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
    schema_guard.check_or_exit("group_focus_series")

from django.conf import settings                                    # noqa: E402
from django.db import transaction                                   # noqa: E402
from django.utils import timezone                                   # noqa: E402

import scale                                                     # noqa: E402
import runlog                                                       # noqa: E402
from viewer.images import ensure_frame_image
from viewer.models import (Frame, Locality, Sample, Site,           # noqa: E402
                           Slide, Viewpoint)
# 메타를 찾는 규칙은 scale 한 곳에만 둔다
from scale import read_timestamp                                 # noqa: E402

THUMB_W = 256
# 사진으로 보는 확장자. `scan_nas.IMAGE_EXT` 와 같아야 한다 — 훑을 때 센 것과
# 묶을 때 읽는 것이 다르면 시야가 조용히 빠진다
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff")

import re                                                           # noqa: E402


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


def separability(corrs, groups, thresh):
    """임계값이 이 슬라이드에서 의미가 있는가.

    "0.55 이상이면 같은 시야" 라는 규칙은 **그룹 안 상관과 경계 상관이 겹치지
    않을 때만** 성립한다. 겹치면 임계값을 어디에 두든 잘못 묶이는 쌍이 생긴다.

    처음에는 `min(그룹안) - max(경계)` 를 재고 그 값이 작으면 미심쩍다고 봤다.
    **틀린 기준이었다.** 최솟값·최댓값은 꼬리라서 표본이 많을수록 나빠진다 —
    실측에서 그룹 쌍이 1개뿐인 슬라이드가 만점(0.9957)을 받고, 34개인 슬라이드가
    최악 하나 때문에 걸렸다. 그런데 그 슬라이드는 사람이 확인하니 **정상이었다.**

        AM22 25cm 그룹 안 34쌍: 0.594 0.623 0.629 0.723 | 0.986 … 0.998
                   경계 25쌍:  … 0.475 0.494
                               └ 0.494~0.594 사이는 완전히 비어 있다

    겹치는 쌍이 하나도 없다. 분포는 깨끗하게 양분돼 있었고 내가 양 끝만 빼서
    "여유 0.1" 이라 부른 것이다.

    그래서 판정과 정보를 가른다.

    - **겹침**(`overlap`) — 경계 상관이 그룹 안 상관보다 높은 쌍이 실제로 있는가.
      있으면 임계값 문제가 맞다. 이것만 사람을 부른다
    - **여유**(`margin`) — 임계값에서 양쪽으로 얼마나 떨어져 있나. 좁아도 겹치지
      않으면 정상이다. 기록만 한다

    표본이 너무 적으면(`within_n` 이 한 자리) 어느 쪽도 말할 수 없다 — 그것도
    그대로 알린다. 모르는 것을 통과로 처리하지 않는다.
    """
    inner, edge = [], []
    for g in groups:
        for i in g[:-1]:
            if i < len(corrs):
                inner.append(corrs[i])
        last = g[-1]
        if last < len(corrs):          # 마지막 그룹의 끝은 경계가 아니다
            edge.append(corrs[last])

    out = {"within_n": len(inner), "between_n": len(edge)}
    if not inner or not edge:
        return {**out, "within_min": None, "between_max": None,
                "gap": None, "overlap": None, "margin": None}

    lo, hi = min(inner), max(edge)
    # 겹치는 쌍: 그룹 안인데 어떤 경계보다 낮은 것 + 경계인데 어떤 그룹 안보다 높은 것.
    # 0 이면 임계값을 두 무리 사이 어디에 둬도 결과가 같다.
    overlap = sum(1 for v in inner if v < hi) + sum(1 for v in edge if v > lo)
    return {
        **out,
        "within_min": round(lo, 4),
        "between_max": round(hi, 4),
        "gap": round(lo - hi, 4),          # 이력 비교용으로 계속 남긴다
        "overlap": overlap,
        # 임계값을 이만큼 움직여도 그룹이 안 바뀐다. 음수면 임계값이 무리 안을
        # 자르고 있다는 뜻이라 겹침이 없어도 위태롭다.
        "margin": round(min(thresh - hi, lo - thresh), 4),
    }


def fingerprint(jpg: Path, blur_sigma: float):
    """초점 변화에 둔감한 저주파 지문."""
    img = cv2.imread(str(jpg), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise IOError(f"cannot read {jpg}")
    h = int(THUMB_W * img.shape[0] / img.shape[1])
    small = cv2.resize(img, (THUMB_W, h), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small.astype(np.float32), (0, 0), blur_sigma)
    small -= small.mean()
    s = small.std()
    return small / s if s > 1e-6 else small


def sharpness(jpg: Path):
    """Laplacian 분산 — 값이 클수록 초점이 잘 맞은 장."""
    img = cv2.imread(str(jpg), cv2.IMREAD_GRAYSCALE)
    small = cv2.resize(img, (1024, int(1024 * img.shape[0] / img.shape[1])),
                       interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(small, cv2.CV_32F).var())


def ncc(a, b):
    return float((a * b).mean())


def slide_slug(slide_dir: Path) -> str:
    """`photos/<촬영일>/<슬라이드>/` 에서 슬러그를 만든다.

    촬영일을 함께 넣는다 — 같은 슬라이드를 다른 날 다시 촬영하면 이름이 부딪힌다.
    `ingest_nas.py` 도 이것을 쓴다. 규칙이 갈라지면 반입한 슬라이드를 그룹핑이
    새것으로 다시 만든다.

    **`urls.py` 의 `<slug:slug>` 를 통과해야 한다.** Django 의 slug 변환기는
    `[-a-zA-Z0-9_]+` 만 받아서, 관찰 접미사 `(1)` 이 붙은 폴더는 그룹핑·합성·
    검출이 멀쩡히 끝나고 **뷰어만 통째로 404** 가 된다. 예외도 경고도 없다.
    그래서 허용 문자가 아닌 것은 여기서 전부 `_` 로 눕힌다 — `RS23-GC03 71cm (1)`
    은 `..._71cm_1` 이 되어 접미사 없는 것과 갈린다.

    **기존 슬러그 10개는 이 규칙으로도 한 글자도 안 바뀐다**(2026-08-05 실측).
    바뀌면 재반입이 같은 슬라이드를 새 행으로 다시 만든다.
    """
    raw = f"{slide_dir.parent.name}_{slide_dir.name}".lower().replace(" ", "_")
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9_-]+", "_", raw)).strip("_")


# 폴더 이름 규칙은 `viewer/naming.py` 하나뿐이다 — 뷰어·파이프라인·마이그레이션이
# 같은 것을 본다. 예전에는 이 파일과 `import_json.py` 에 두 벌이 있었고, 규칙이
# 갈라지면 같은 폴더가 두 자리에 다르게 앉는다.
from viewer.naming import (base_name, parse_folder,                 # noqa: E402
                           parse_obs_no)                            # noqa: F401


def sample_fields(folder: str) -> dict:
    """`Slide.sample` 을 정한다. `update_or_create` 의 `defaults` 에 얹는다.

    **비어 있으면 아무것도 안 쓴다 — 이것이 이 함수의 요점이다.** 예전에는
    `core=None` 을 그대로 실어서, 이름이 규칙에 안 맞는 슬라이드를 다시
    그룹핑하거나 다시 반입하면 **사람이 속성 편집에서 넣은 소속이 지워졌다.**
    `obs_label` 을 defaults 에서 빼 둔 것과 같은 이유다 — 자동값이 사람이 채운
    것을 덮으면 안 된다.

    순서가 있다.

    1. 폴더 이름이 규칙에 맞으면 지역·지점·시료를 만들거나 찾는다
       (`naming.parse_folder`). 남극은 `<지역>-<지점> <깊이>cm`, 육상은
       `<지점>-<시료>` 이고 가르는 표시는 `cm` 이 있느냐다
    2. 안 맞으면 **같은 시료의 다른 관찰에서 물려받는다.** 관찰은 정의상 같은
       시료이므로 같은 행을 가리켜야 한다
    3. 둘 다 아니면 빈 dict — 있던 값을 그대로 둔다

    **접미사가 없는 폴더는 2번으로 안 간다**(`base == folder`). 기준이 되는 쪽이
    관찰들 사이에서 값을 주워 오면 사람이 고친 것이 돌고 돌아 되살아난다.

    **분획도 같은 규칙이다.** 폴더에 `>125um` 이 있으면 `fraction_um` 을 싣고,
    없으면 **키 자체를 안 싣는다** — 사람이 화면에서 적은 값을 지우지 않는다.
    """
    f = parse_folder(folder)
    out = {}
    if f["fraction_um"] is not None:
        out["fraction_um"] = f["fraction_um"]
    if f["site_code"] and f["loc_code"] and f["sample_code"]:
        site, _ = Site.objects.get_or_create(code=f["site_code"])
        loc, _ = Locality.objects.get_or_create(site=site, code=f["loc_code"])
        sample, _ = Sample.objects.get_or_create(
            locality=loc, code=f["sample_code"],
            defaults={"depth_cm": f["depth_cm"]})
        return {**out, "sample": sample}

    base = base_name(folder)
    if not base or base == folder:
        return {}
    # `name__startswith` 로 좁히고 접미사를 떼어 정확히 맞는 것만 남긴다 —
    # `BP09-0901` 이 `BP09-09010` 을 줍지 않게. 관찰이 이미 여럿이면 번호가
    # 작은 쪽(보통 접미사 없는 원본)을 따른다.
    sibs = [s for s in Slide.objects.filter(name__startswith=base)
            .exclude(sample=None).order_by("obs_no", "id")
            if base_name(s.name) == base]
    return {**out, "sample": sibs[0].sample} if sibs else out


def save_grouping(slide_dir: Path, files, groups, sharps, times, args, sep, run):
    """Slide·Viewpoint·Frame 을 만든다. 통째로 한 트랜잭션이다.

    중간에 끊기면 시야 절반만 있는 슬라이드가 남고, 그 위에 검출을 돌리면
    나머지 절반이 조용히 빠진다.
    """
    folder = slide_dir.name
    slug = slide_slug(slide_dir)

    with transaction.atomic():
        slide, _ = Slide.objects.update_or_create(
            slug=slug,
            defaults=dict(name=folder, image_dir=rel(slide_dir),
                          # 시료 소속(코어·깊이·종류)은 여기서 정한다. **빈
                          # dict 면 아무것도 안 쓴다** — 사람이 채운 것을
                          # 자동값이 지우지 않는다 (`sample_fields` 머리말)
                          **sample_fields(folder),
                          corr_thresh=args.corr_thresh,
                          # 자동값만 쓴다. `obs_label` 은 사람 것이라 defaults 에
                          # 넣지 않는다 — 넣으면 다시 그룹핑할 때 지워진다
                          obs_no=parse_obs_no(folder),
                          # 자동 처리가 다 끝나기 전에는 사람이 검토하면 안 된다.
                          # 뷰어가 이 값을 보고 막는다 (P01 §1)
                          state="processing",
                          state_note=f"그룹핑 완료 · 합성·검출 대기 "
                                     f"(경계 여유 {sep['gap']})",
                          discovered_at=timezone.now()))

        # 다시 묶으면 옛 시야는 지운다 — --force 로만 여기까지 온다
        slide.viewpoints.all().delete()

        seq = 0
        for gi, g in enumerate(groups):
            names = [files[i].stem for i in g]
            tag = f"g{gi:03d}_{names[0]}-{names[-1].split('-')[-1]}"
            span = ((times[g[-1]] - times[g[0]]).total_seconds()
                    if times[g[0]] and times[g[-1]] else None)
            vp = Viewpoint.objects.create(
                slide=slide, idx=gi, tag=tag, n_frames=len(g),
                # 격자 칸 번호 — 촬영 순서가 곧 칸 순서다 (P01 5절). 1 부터
                cell=gi + 1,
                span_sec=span, grouping_run=run)

            best_name = max((files[i].stem for i in g), key=lambda n: sharps[n])
            best_frame = None
            for i in g:
                jpg = files[i]
                name = jpg.stem
                sc = scale.scaling_for(jpg)
                fr, _ = Frame.objects.update_or_create(
                    slide=slide, name=name,
                    defaults=dict(viewpoint=vp, path=rel(jpg), seq=seq,
                                  sharpness=sharps[name],
                                  is_sharpest=(name == best_name),
                                  um_per_pixel=sc["um_per_pixel"],
                                  um_per_pixel_source=sc["source"],
                                  acquired_at=times[i]))
                # 이미지 테이블에도 올린다 (P06). `path` 가 열쇠라 다시 묶어도 같다
                ensure_frame_image(fr)
                if name == best_name:
                    best_frame = fr
                seq += 1
            if best_frame:
                vp.sharpest_frame = best_frame
                vp.save(update_fields=["sharpest_frame"])
    return slide


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="이미지 디렉토리")
    ap.add_argument("-o", "--out", default=None,
                    help="groups.json 내보내기 경로 (기본: 안 쓴다)")
    ap.add_argument("--corr-thresh", type=float, default=0.55,
                    help="이 값 이상이면 같은 시야로 판단")
    ap.add_argument("--blur", type=float, default=2.0)
    ap.add_argument("--max-gap-sec", type=float, default=0.0,
                    help=">0 이면 이 시간 이상 벌어진 경우 상관계수와 무관하게 분리")
    # 여유(margin)로는 판정하지 않는다 — 좁아도 겹치지 않으면 정상이다.
    # 실측: 여유 0.05 짜리 슬라이드를 사람이 확인하니 그룹핑이 맞았다.
    ap.add_argument("--min-pairs", type=int, default=5,
                    help="그룹 안 쌍이 이보다 적으면 묶임을 검증할 수 없다고 본다")
    ap.add_argument("--force", action="store_true",
                    help="이미 검출·교정이 있는 슬라이드도 다시 묶는다 (아래를 읽을 것)")
    ap.add_argument("--dry-run", action="store_true",
                    help="묶어 보기만 한다. DB 도 파일도 건드리지 않는다")
    args = ap.parse_args()

    slide_dir = Path(args.input).resolve()
    files = sorted(p for p in slide_dir.iterdir()
                   if p.suffix.lower() in IMAGE_EXT)
    if not files:
        raise SystemExit(f"사진이 없다: {slide_dir}")
    print(f"{len(files)} images")

    # 재그룹핑은 Viewpoint 를 재편하므로 그 아래 검출·교정이 통째로 어긋난다.
    # 이 스크립트가 6단계의 마지막인 이유다.
    existing = Slide.objects.filter(image_dir=rel(slide_dir)).first()
    if existing and not args.dry_run and not args.force:
        # 검출·교정 테이블은 2·3단계에서 온다 — 없으면 0 이다
        n_rev = sum(getattr(vp, "object_reviews").count() if hasattr(vp, "object_reviews") else 0
                    for vp in existing.viewpoints.all())
        n_det = sum(getattr(vp, "detections").count() if hasattr(vp, "detections") else 0
                    for vp in existing.viewpoints.all())
        if n_rev or n_det:
            raise SystemExit(
                f"{existing.slug} 은 이미 검출 {n_det}건 · 교정 {n_rev}건이 있다.\n"
                f"  다시 묶으면 시야가 재편되어 그 아래가 통째로 어긋난다.\n"
                f"  --force 는 이 슬라이드의 시야를 **전부 지운다.** 교정 {n_rev}건도\n"
                f"  함께 사라진다 — ObjectReview 가 Viewpoint 를 CASCADE 로 문다.\n"
                f"  (mask_key 는 칼럼 값일 뿐이라 행이 없으면 아무것도 아니다.)\n"
                f"  시야 몇 개만 잘못 묶였다면 그것만 가르는 쪽이 맞다:\n"
                f"    resplit.py --slide {existing.slug} --after <프레임>")

    # Run 을 계산 **전에** 연다. 끝난 뒤에 만들면 DB 쓰기 시간(2초)만 잡히고
    # 실제 소요(412장에 31초)가 기록되지 않는다.
    run = None
    if not args.dry_run:
        run = runlog.start(
            "group",
            params={"corr_thresh": args.corr_thresh, "blur": args.blur,
                    "max_gap_sec": args.max_gap_sec, "dir": rel(slide_dir),
                    "n_images": len(files)},
            host=socket.gethostname(), code_version=git_version())

    fps = [fingerprint(f, args.blur) for f in files]
    times = [read_timestamp(f) for f in files]

    corrs = [ncc(fps[i], fps[i + 1]) for i in range(len(files) - 1)]
    gaps = [
        (times[i + 1] - times[i]).total_seconds()
        if times[i] and times[i + 1] else float("nan")
        for i in range(len(files) - 1)
    ]

    groups, cur = [], [0]
    for i, c in enumerate(corrs):
        split = c < args.corr_thresh
        if args.max_gap_sec > 0 and gaps[i] == gaps[i] and gaps[i] > args.max_gap_sec:
            split = True
        if split:
            groups.append(cur)
            cur = []
        cur.append(i + 1)
    groups.append(cur)

    sharps = {f.stem: round(sharpness(f), 1) for f in files}
    out = {"dir": rel(slide_dir), "corr_thresh": args.corr_thresh, "groups": []}
    print(f"\n{len(groups)} groups")
    for gi, g in enumerate(groups):
        names = [files[i].stem for i in g]
        sharp = {n: sharps[n] for n in names}
        best = max(sharp, key=sharp.get)
        span = ((times[g[-1]] - times[g[0]]).total_seconds()
                if times[g[0]] and times[g[-1]] else None)
        out["groups"].append({
            "id": gi, "images": names, "n": len(g),
            "sharpest": best, "sharpness": sharp, "span_sec": span,
        })
        print(f"  [{gi:3d}] n={len(g):2d} {names[0]}..{names[-1]}  "
              f"best={best} span={span}s")

    # 임계값이 이 슬라이드에서 의미가 있는가 (P01 §1)
    sep = separability(corrs, groups, args.corr_thresh)
    singles = sum(1 for g in groups if len(g) == 1)
    print(f"\n상관계수 — 그룹 안 {sep['within_min']} 이상({sep['within_n']}쌍) · "
          f"경계 {sep['between_max']} 이하({sep['between_n']}쌍)")
    print(f"  겹치는 쌍 {sep['overlap']}개 · 임계값 여유 {sep['margin']} · "
          f"단독 그룹 {singles}/{len(groups)}개")

    why = []
    if sep["overlap"]:
        why.append(f"두 무리가 겹친다 (쌍 {sep['overlap']}개) — 임계값을 어디에 둬도 "
                   f"잘못 묶이는 쌍이 생긴다")
    elif sep["margin"] is not None and sep["margin"] < 0:
        why.append(f"임계값 {args.corr_thresh} 이 무리 안을 자르고 있다 "
                   f"(그룹 안 최소 {sep['within_min']} · 경계 최대 {sep['between_max']})")
    elif sep["within_n"] < args.min_pairs and sep["within_n"] > 0:
        why.append(f"판단할 표본이 모자란다 (그룹 안 쌍 {sep['within_n']}개) — "
                   f"대부분 단독 촬영이라 묶임을 검증할 수 없다")
    suspect = bool(why)
    if suspect:
        print("\n** 그룹핑을 사람이 확인해야 한다.", file=sys.stderr)
        for w in why:
            print(f"   - {w}", file=sys.stderr)
    elif sep["margin"] is not None and sep["margin"] < 0.05:
        # 겹치지 않으면 정상이다. 여유가 적은 것은 정보로만 알린다.
        print(f"\n(참고) 임계값 여유가 {sep['margin']} 로 좁다. "
              f"겹치는 쌍은 없어 결과는 정상이다.", file=sys.stderr)

    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")

    if args.dry_run:
        print("\ndry-run — DB 에 쓰지 않았다.")
        return

    try:
        slide = save_grouping(slide_dir, files, groups, sharps, times,
                              args, sep, run)
        run.slide = slide
        if suspect:
            # 자동으로 넘기지 않는다. 잘못 묶인 시야 위에 쌓은 검출과 교정은
            # 나중에 되돌리기가 훨씬 비싸다 (P01 §1).
            slide.state = "failed"
            slide.state_note = ("그룹핑 확인 필요 — " + " · ".join(why) +
                                f" (단독 그룹 {singles}/{len(groups)})")
            slide.save(update_fields=["state", "state_note"])
    except Exception as e:
        run.status = "failed"
        run.error = f"{type(e).__name__}: {e}"
        run.finished_at = timezone.now()
        run.save()
        raise

    run.status = "done"
    run.finished_at = timezone.now()
    run.counts = {"viewpoints": len(groups), "frames": len(files),
                  "singletons": singles, **sep}
    run.save()
    print(f"\n{slide.slug} · 시야 {len(groups)}개 · 프레임 {len(files)}개 · "
          f"상태 {slide.state} · Run #{run.pk}")
    if slide.state != "done":
        print(f"  {slide.state_note}")


if __name__ == "__main__":
    main()
