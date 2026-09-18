#!/usr/bin/env python3
"""DiaRUGA v0.29.0 `ops/export_yolo.py` 에서 왔다 — 클래스 `foram` 하나. SAM 갈래 없음.

사람의 검토를 YOLO 학습 자료로 내보낸다 (DiaRUGA P04 1단계).

    python export_yolo.py --out datasets/yolo/v1 --dry-run
    python export_yolo.py --out datasets/yolo/v1
    python export_yolo.py --out datasets/yolo/v1-seg --seg

**검토완료 시야만 쓴다.** 검토하지 않은 시야를 넣으면 "SAM2 가 통과시킨 것 = 정답"
이 되어 SAM2 의 오검출까지 그대로 배운다. 사람이 본 것만이 자료다.

판정은 뷰어와 **같은 함수**를 쓴다 — `data.detection_for_viewpoint()`. 규칙을
여기 다시 쓰면(`passed and not removed …`) 언젠가 갈라지고, 갈라진 것은 눈에
띄지 않는다. 화면에 보이는 것이 곧 라벨이어야 한다.

## 타일링을 하지 않는다

원본이 2752×2208 이고 개체 중앙값이 169 px 다. `imgsz=1280` 이면 개체가 79 px,
작은 쪽 10% 도 40 px 라 충분하다. 타일로 자르면 경계에 걸친 개체(최대 1331 px)를
어떻게 셀지 새로 정해야 하고, 그 규칙이 새로 틀릴 수 있다. RTX 8000 은 48 GB 라
1280 을 감당하므로 치를 이유가 없는 대가다. 근거는 P04 §자료 실측.

## 음성은 따로 내보내지 않는다

사람이 지운 4,039건은 **라벨을 안 쓰는 것으로 충분하다.** YOLO 는 라벨이 없는
자리를 배경으로 배운다. SAM2 가 실제로 헷갈린 자리에 사람이 "아니다" 를 붙인
것이라 무작위 배경보다 값어치가 크고, 그것이 이미지 안에 그대로 들어간다.

개체가 하나도 없는 시야(29개)도 버리지 않는다 — 빈 라벨 파일로 낸다. YOLO 는
배경 이미지를 10% 안팎 섞으라고 권한다.

## 검증을 둘로 나눈다

- `val`       — 시야 20%, 슬라이드별 층화. 학습이 되고 있나
- `val_slide` — 슬라이드 한 장 통째로. 슬라이드를 외웠나

`val` 은 좋은데 `val_slide` 가 나쁘면 염색·조명·쇄설물 같은 슬라이드 고유의
특징을 외운 것이고, 새 슬라이드에는 안 듣는다는 뜻이다. 한 슬라이드가 자료의
56% 라 이 구분 없이는 알 수 없다.

시야는 슬라이드 위의 서로 다른 자리다 — 같은 개체가 양쪽에 들어가지는 않는다.

**빼는 슬라이드로 `wap13-gc47_450cm` 을 기본으로 둔다.** 가장 큰
`260731_rs23-gc03_369cm`(847개, 56%)을 빼면 학습이 513개로 줄어 검증이 학습보다
커지는 뒤집힌 모양이 된다. WAP450 을 빼면 1,046 / 256 / 201 로 셋 다 쓸 만하고,
학습은 RS 에 쏠려 있는데 검증은 다른 해역이라 **"새 슬라이드에 듣는가" 라는
실제 질문**에 가장 가깝다. 슬라이드가 더 모이면 다시 고른다.
"""
import argparse
import hashlib
import json
import os
import random
import shutil
import socket
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import django

# 이 스크립트는 저장소 밖(/srv/DiaRUGA/scripts)에 복사해 두고 컨테이너 안에서
# 돌릴 수도 있다. 그때 Django 코드가 어디 있는지는 FORGIA_APP 이 알려 준다 —
# 이미지 안의 /app 이고, 뷰어 컨테이너가 쓰는 바로 그 코드다. 저장소에서 그냥
# 돌리면 예전처럼 자기 옆의 web/ 을 본다.
# **저장소에서는 한 단계 위가 뿌리다** (스크립트가 pipeline/·ops/·migrate/
# 안에 있다). `/srv/DiaRUGA/scripts` 처럼 저장소 밖에서 돌 때는 그 짐작이
# 안 맞으므로 `FORGIA_APP` 이 알려 준다 — 컨테이너에서는 이미지 안의 /app 이다.
APP = Path(os.environ.get("FORGIA_APP")
          or Path(__file__).resolve().parent.parent)
# **`APP` 은 Django 코드를 찾는 자리일 뿐이다** (DiaRUGA 100). `sys.path` 앞에 통째로
# 밀어 넣으면 **이미지 안의 옛 `judge.py`·`zen_meta.py` 가 자기 옆의 것을 가린다**
# — `/srv/DiaRUGA/scripts` 로 밀어 넣은 새 규칙이 안 먹는 채로 돌았다(실측).
# 그래서 **뒤에 붙인다**: 스크립트 자신의 디렉토리(파이썬이 `sys.path[0]` 에
# 놓는다)가 먼저이고, Django 는 그 뒤에서 찾힌다.
sys.path.insert(0, str(APP / "web"))
sys.path.append(str(APP))
# **`pipeline/` 의 모듈을 함께 쓴다.** 판정 규칙(`judge`)·촬영 XML(`zen_meta`)·
# 실행 기록(`runlog`)은 파이프라인과 이쪽이 같은 것을 봐야 한다 — 규칙이 둘이면
# 검출과 검사가 다른 말을 한다. `/srv/DiaRUGA/scripts` 는 평평해서 이 줄이 없어도
# 되지만, 저장소에서는 디렉토리가 갈려 있어 알려 줘야 한다.
sys.path.insert(0, str(APP / "pipeline"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "forgiaweb.settings")
django.setup()

from django.conf import settings                                    # noqa: E402
from django.utils import timezone                                   # noqa: E402

import runlog                                                       # noqa: E402
from viewer import data as vdata                                    # noqa: E402
from viewer.models import Viewpoint                                 # noqa: E402

SPLITS = ("train", "val", "val_slide")


def git_version():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5,
                             cwd=Path(__file__).resolve().parent)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


# --- 라벨 만들기 ------------------------------------------------------------
def bbox_line(c: dict, w: int, h: int, cls: int) -> str | None:
    """YOLO 상자 한 줄: `cls cx cy bw bh` (전부 0~1 로 정규화).

    상자가 이미지 밖으로 조금 나가 있는 경우가 있어 잘라 준다. 자른 뒤 넓이가
    0 이면 라벨이 될 수 없다.
    """
    bx, by, bw0, bh0 = c["bbox_xywh"]
    x0 = max(0.0, float(bx))
    y0 = max(0.0, float(by))
    x1 = min(float(w), float(bx) + float(bw0))
    y1 = min(float(h), float(by) + float(bh0))
    bw, bh = x1 - x0, y1 - y0
    if bw <= 1 or bh <= 1:
        return None
    return (f"{cls} {(x0 + bw / 2) / w:.6f} {(y0 + bh / 2) / h:.6f} "
            f"{bw / w:.6f} {bh / h:.6f}")


def seg_line(c: dict, w: int, h: int, cls: int) -> str | None:
    """YOLO 분할 한 줄: `cls x1 y1 x2 y2 …` (정규화).

    폴리곤은 `[x,y,x,y,…]` 평면 목록이다. 점이 3개(값 6개) 미만이면 면이 아니다.
    """
    p = c.get("polygon") or []
    if len(p) < 6:
        return None
    out = [str(cls)]
    for i in range(0, len(p) - 1, 2):
        out.append(f"{min(max(float(p[i]), 0.0), w) / w:.6f}")
        out.append(f"{min(max(float(p[i + 1]), 0.0), h) / h:.6f}")
    return " ".join(out)


def viewpoint_rows(vp: Viewpoint, seg: bool, classes: list[str]) -> tuple:
    """시야 하나 → (이미지 절대경로, 라벨 줄 목록, 분류별 개수, 버린 개수).

    라벨을 못 만든 개체는 조용히 넘기지 않고 세어서 돌려준다 — 자료가 조용히
    줄어드는 것이 가장 알아채기 어렵다.
    """
    det = vdata.detection_for_viewpoint(vp)
    if not det:
        return None, [], Counter(), 0

    img = Path(settings.DATA_ROOT) / det["image"]
    w, h = det["size"]
    lines, counts, dropped = [], Counter(), 0
    for c in det["candidates"]:
        cls_name = c.get("cls") or "?"
        idx = 0 if len(classes) == 1 else (
            classes.index(cls_name) if cls_name in classes else None)
        if idx is None:
            dropped += 1
            continue
        line = seg_line(c, w, h, idx) if seg else bbox_line(c, w, h, idx)
        if line is None:
            dropped += 1
            continue
        lines.append(line)
        counts[cls_name] += 1
    return img, lines, counts, dropped


# --- 나누기 -----------------------------------------------------------------
def split_viewpoints(vps: list[Viewpoint], val_frac: float,
                     holdout: str | None, seed: int) -> dict:
    """시야를 train/val/val_slide 로 가른다.

    `val` 은 **슬라이드별로** 뽑는다. 통째로 무작위면 작은 슬라이드(개체 3개짜리
    한 장이 있다)가 val 에서 통째로 빠지거나 통째로 몰릴 수 있다.
    """
    rng = random.Random(seed)
    by_slide = defaultdict(list)
    out = {s: [] for s in SPLITS}
    for vp in vps:
        if holdout and vp.slide.slug == holdout:
            out["val_slide"].append(vp)
        else:
            by_slide[vp.slide.slug].append(vp)

    for slug in sorted(by_slide):
        group = sorted(by_slide[slug], key=lambda v: v.id)
        rng.shuffle(group)
        n_val = round(len(group) * val_frac)
        out["val"].extend(group[:n_val])
        out["train"].extend(group[n_val:])
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description="검토를 YOLO 자료로 내보낸다")
    ap.add_argument("--out", required=True, help="자료 꾸러미를 만들 곳")
    ap.add_argument("--val-frac", type=float, default=0.2,
                    help="슬라이드마다 val 로 뺄 시야 비율 (기본 0.2)")
    ap.add_argument("--holdout-slide", default="wap13-gc47_450cm",
                    help="val_slide 로 통째로 뺄 슬라이드 slug (기본 "
                         "wap13-gc47_450cm — 왜 이것인지는 머리말). "
                         "'' 를 주면 val_slide 를 안 만든다")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--seg", action="store_true",
                    help="상자 대신 폴리곤을 낸다 (-seg 모델용)")
    ap.add_argument("--classes", default="foram",
                    help="쉼표로 나눈 분류 이름. 기본은 단일 분류. "
                         "여럿을 주면 그 이름에 해당하는 개체만 나간다")
    ap.add_argument("--link", action="store_true",
                    help="이미지를 복사하지 않고 심볼릭 링크로 (다른 기계로 "
                         "옮길 것이면 쓰지 말 것 — 링크는 따라가지 않는다)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    classes = [c.strip() for c in args.classes.split(",") if c.strip()]
    holdout = args.holdout_slide or None
    out_dir = Path(args.out).resolve()

    # **검토 완료는 묶음마다다** (DiaRUGA 073). 예전에는 시야마다 한 줄이라
    # `review__done` 하나로 끝났는데, 지금은 `(viewpoint, batch)` 가 열쇠다.
    # 고치지 않고 두었더니 이 스크립트가 `FieldError` 로 죽고 있었다 — 화면이
    # 아니라 명령줄이라 아무도 안 밟았을 뿐이다.
    #
    # **검토 중인 묶음에서 끝낸 시야**를 쓴다. 다른 묶음에서 끝낸 것을 섞으면
    # "이 묶음이 낸 검출을 사람이 봤다" 가 아니게 되고, 그러면 정답이 아니다.
    rb = vdata.review_batch_id()
    if rb is None:
        sys.exit("검토 대상 묶음이 없다 — 관리 화면에서 먼저 고를 것")
    vps = list(Viewpoint.objects
               .filter(reviews__done=True, reviews__batch_id=rb)
               .select_related("slide", "slide__sample__locality__site")
               .order_by("slide__slug", "idx").distinct())
    print(f"검토완료 시야 {len(vps)}개 (묶음: {vdata.review_batch_label()})")
    if not vps:
        sys.exit("검토완료 시야가 없다 — 내보낼 것이 없다")

    if holdout and not any(v.slide.slug == holdout for v in vps):
        sys.exit(f"--holdout-slide '{holdout}' 에 해당하는 검토완료 시야가 없다. "
                 f"있는 것: {sorted({v.slide.slug for v in vps})}")

    splits = split_viewpoints(vps, args.val_frac, holdout, args.seed)

    # 먼저 전부 모아 본다. 파일을 쓰기 전에 무엇이 나갈지 보여야 --dry-run 이
    # 뜻이 있다.
    plan = {s: [] for s in SPLITS}
    cls_count = {s: Counter() for s in SPLITS}
    dropped_total, missing = 0, []
    for split, group in splits.items():
        for vp in group:
            img, lines, counts, dropped = viewpoint_rows(vp, args.seg, classes)
            dropped_total += dropped
            if img is None:
                continue
            if not img.exists():
                missing.append(str(img))
                continue
            stem = f"{vp.slide.slug}_vp{vp.idx:03d}"
            plan[split].append((stem, img, lines))
            cls_count[split].update(counts)

    print()
    print(f"{'분할':<11}{'시야':>6}{'개체':>8}{'빈 시야':>9}")
    for s in SPLITS:
        n_obj = sum(len(l) for _, _, l in plan[s])
        n_empty = sum(1 for _, _, l in plan[s] if not l)
        print(f"{s:<11}{len(plan[s]):>6}{n_obj:>8}{n_empty:>9}")
    if len(classes) > 1:
        print()
        for s in SPLITS:
            if cls_count[s]:
                print(f"  {s:<10} {dict(cls_count[s].most_common())}")
    if dropped_total:
        print(f"\n라벨을 못 만들어 버린 개체 {dropped_total}개 "
              f"(분류가 목록 밖이거나 폴리곤/상자가 비었다)")
    if missing:
        print(f"\n이미지가 없는 시야 {len(missing)}개 — 건너뛴다")
        for m in missing[:5]:
            print(f"    {m}")

    if args.dry_run:
        print("\n--dry-run 이라 아무것도 쓰지 않았다")
        return

    if out_dir.exists():
        sys.exit(f"{out_dir} 가 이미 있다. 지우거나 다른 이름을 줄 것 — "
                 f"덮어쓰면 어느 판으로 학습했는지 알 수 없게 된다")

    run = runlog.start("export", params={
        "out": str(out_dir), "seg": args.seg, "classes": classes,
        "val_frac": args.val_frac, "holdout_slide": holdout, "seed": args.seed,
    }, host=socket.gethostname(), code_version=git_version())

    try:
        manifest_vps = {s: [] for s in SPLITS}
        for split in SPLITS:
            if not plan[split]:
                continue
            (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
            (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)
            for stem, img, lines in plan[split]:
                dst = out_dir / "images" / split / f"{stem}{img.suffix}"
                if args.link:
                    dst.symlink_to(img)
                else:
                    shutil.copy2(img, dst)
                (out_dir / "labels" / split /
                 f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
                manifest_vps[split].append({"stem": stem,
                                            "source": str(img),
                                            "n_objects": len(lines)})

        # data.yaml 은 상대경로로 쓴다. 절대경로면 RTX 8000 으로 옮긴 뒤 안 맞는다.
        # 단 ultralytics 는 path 를 yaml 위치가 아니라 실행 디렉토리 기준으로
        # 푼다 — 학습은 꾸러미 디렉토리 안에서 실행해야 한다.
        val_line = "val: images/val\n"
        if plan["val_slide"]:
            # ultralytics 는 val 에 목록을 받지만, 두 검증을 따로 보고 싶으므로
            # val_slide 는 학습에 걸지 않고 나중에 `model.val(data=…)` 로 따로 잰다.
            val_line += "# val_slide 는 따로 잰다: images/val_slide\n"
        yaml = (f"# {timezone.now():%Y-%m-%d} · export_yolo.py · "
                f"{git_version() or '?'}\n"
                f"path: .\n"
                f"train: images/train\n"
                f"{val_line}"
                f"names:\n" +
                "".join(f"  {i}: {c}\n" for i, c in enumerate(classes)))
        (out_dir / "data.yaml").write_text(yaml)

        # val_slide 만 겨냥한 두 번째 yaml — 같은 자료를 val 로 걸어 둔 것이다.
        if plan["val_slide"]:
            (out_dir / "data_val_slide.yaml").write_text(
                yaml.replace("val: images/val\n", "val: images/val_slide\n"))

        manifest = {
            "created_at": timezone.now().isoformat(),
            "code_version": git_version(),
            "host": socket.gethostname(),
            "run_id": run.id,
            "args": vars(args),
            "classes": classes,
            "label_kind": "polygon" if args.seg else "bbox",
            "note": ("검토완료 시야만. 판정은 viewer.data.detection_for_viewpoint() "
                     "와 같다. 사람이 지운 개체는 라벨 없이 배경으로 남는다."),
            "recall_caveat": ("정답은 사람이 SAM2 후보 위에서 만든 것이다. "
                              "여기서 재는 재현율은 'SAM2 가 마스크를 만든 범위 "
                              "안에서' 이고, 밖에 낼 때는 이 한정을 함께 적는다."),
            "counts": {s: {"viewpoints": len(plan[s]),
                           "objects": sum(len(l) for _, _, l in plan[s]),
                           "empty": sum(1 for _, _, l in plan[s] if not l),
                           "classes": dict(cls_count[s])} for s in SPLITS},
            "dropped_objects": dropped_total,
            "missing_images": missing,
            "viewpoints": manifest_vps,
        }
        (out_dir / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2))

        # 옮기다 깨진 것을 학습이 끝난 뒤에 알면 늦다.
        if not args.link:
            sums = {}
            for split in SPLITS:
                for p in sorted((out_dir / "images" / split).glob("*")) if (
                        out_dir / "images" / split).exists() else []:
                    sums[f"images/{split}/{p.name}"] = sha256(p)
            (out_dir / "SHA256SUMS").write_text(
                "".join(f"{v}  {k}\n" for k, v in sorted(sums.items())))

        run.status = "done"
        run.counts = manifest["counts"]
    except Exception as e:
        run.status, run.error = "failed", f"{type(e).__name__}: {e}"
        raise
    finally:
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error", "finished_at", "counts"])

    print(f"\n{out_dir} 에 썼다")
    print(f"  data.yaml · MANIFEST.json"
          f"{' · SHA256SUMS' if not args.link else ' (심볼릭 링크 — 옮기지 말 것)'}")
    if plan["val_slide"]:
        print(f"  val_slide 는 data_val_slide.yaml 로 따로 잰다 "
              f"(슬라이드 {holdout})")


if __name__ == "__main__":
    main()
