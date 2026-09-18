#!/usr/bin/env python3
"""DiaRUGA v0.29.0 `pipeline/refilter.py` 에서 왔다. 판정 문턱만 바꿔 다시 거른다 — 검출기를 다시 돌리지 않는다.

    python refilter.py --dry-run                        # 지금 문턱 그대로 (변화 확인)
    python refilter.py --conf-min 0.4 --dry-run
    python refilter.py --conf-min 0.4                   # 적용
    python refilter.py --slide <slug> --min-um 100

`segment_forams.py` 가 형태 지표·확신도를 통과분·탈락분 **양쪽에** 기록해 두므로,
문턱만 바꿔 다시 판정하면 된다. 장당 20초짜리 추론 대신 밀리초로 끝난다.

DB 로 옮기면서 달라진 것:

- 통과분·탈락분을 합치는 일이 없어졌다. 한 테이블에 `passed` 칼럼이라 **UPDATE 한 번**이다
- **문턱이 `ThresholdSet` 행으로 남는다.** 예전에는 결과 JSON 마다 복사됐고 다시
  거르면 덮어써져서, "원형 669 → 514 가 언제 무슨 문턱으로 바뀌었나" 에 답할 수 없었다
- 실행이 `Run(kind=refilter)` 에 남는다 — 무슨 문턱으로 개수가 어떻게 변했는지

**주지 않은 문턱은 그 검출이 지금 쓰는 값을 그대로 쓴다.** 전부 기본값으로 되돌리면
`--conf-min` 하나 바꾸려다 나머지가 조용히 초기화된다.

사람의 교정은 건드리지 않는다 — `mask_key` 로 붙어 있고 이 스크립트는 판정만 바꾼다.
지운 개체가 탈락분으로 옮겨가도 뷰어에서는 여전히 지운 것으로 보인다.
"""
import argparse
import os
import socket
import subprocess
import sys
from collections import Counter
from pathlib import Path

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

from django.db import transaction                                   # noqa: E402
from django.utils import timezone                                   # noqa: E402

import judge                                                        # noqa: E402
from viewer.models import (Candidate, Detection, Run, RunBatch,     # noqa: E402
                           Slide, ThresholdSet)


def git_version():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5,
                             cwd=Path(__file__).resolve().parent)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def as_record(c: Candidate) -> dict:
    """판정에 필요한 값만 뽑는다. judge 는 dict 를 받는다."""
    return {
        "_pk": c.pk,
        "bbox_xywh": [c.bbox_x, c.bbox_y, c.bbox_w, c.bbox_h],
        "area_px": c.area_px,
        "shape_ok": c.shape_ok,
        "major_um": c.major_um,
        "long_side_um": c.long_side_um,
        "predicted_iou": c.predicted_iou,
    }


def threshold_set_for(values: dict) -> ThresholdSet:
    """같은 문턱 조합이면 한 행을 공유한다. 없으면 만든다."""
    found = ThresholdSet.objects.filter(**values).first()
    if found:
        return found
    name = (f"conf {values['conf_min']:g} · "
            f"{values['min_um']:g}~{values['max_um']:g} µm")
    return ThresholdSet.objects.create(name=name, note="refilter 가 만들었다",
                                       **values)


def refilter_detection(det: Detection, values: dict, dry_run: bool):
    """검출 하나를 다시 거른다. (전, 후, 사유별 개수, 바뀐 행 수)."""
    cands = list(det.candidates.all())
    before = sum(1 for c in cands if c.passed)

    th = judge.Thresholds(**values)
    kept, rejected = judge.apply([as_record(c) for c in cands], th)

    by_pk = {c.pk: c for c in cands}
    changed = []
    for r in kept:
        c = by_pk[r["_pk"]]
        if not c.passed or c.cls != r["cls"] or c.reject:
            c.passed, c.cls, c.reject = True, r["cls"], ""
            changed.append(c)

    reasons = Counter()
    for r in rejected:
        c = by_pk[r["_pk"]]
        # 중첩정리로 떨어진 것은 판정을 통과한 뒤 정리된 것이라 cls 를 남긴다
        cls = r.get("cls") or ""
        reasons[r["reject"]] += 1
        if c.passed or c.cls != cls or c.reject != r["reject"]:
            c.passed, c.cls, c.reject = False, cls, r["reject"]
            changed.append(c)

    if changed and not dry_run:
        Candidate.objects.bulk_update(changed, ["passed", "cls", "reject"],
                                      batch_size=1000)
    return before, len(kept), reasons, len(changed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slide", help="이 슬러그만 (기본: 전부)")
    # **묶음을 고를 수 있어야 한다** (DiaRUGA 078). 검토 중인 것만 만질 수 있으면
    # 나머지 회차는 손댈 수 없는 기록이 된다 — 새 자료가 들어왔을 때 옛 묶음도
    # 같이 따라가야 하고, 판정이 어긋난 묶음을 고칠 길도 있어야 한다.
    ap.add_argument("--batch", help="이 묶음만 (기본: 검토 중인 묶음)")
    ap.add_argument("--all-batches", action="store_true",
                    help="모든 묶음. 문턱을 바꾸면서 쓰지 말 것 (아래 --force)")
    ap.add_argument("--force", action="store_true",
                    help="검토 중이 아닌 묶음의 문턱까지 바꾼다 (회차 비교가 깨진다)")
    ap.add_argument("--dry-run", action="store_true", help="확인만, 저장 안 함")
    ap.add_argument("-q", "--quiet", action="store_true", help="시야별 줄을 줄인다")
    ap.add_argument("--defaults", action="store_true",
                    help="주지 않은 문턱을 지금 값이 아니라 기본값으로 둔다")
    g = ap.add_argument_group("판정 문턱 (주지 않으면 지금 쓰는 값 그대로)")
    for f in judge.FIELDS:
        g.add_argument(f"--{f.replace('_', '-')}", type=float, default=None,
                       help=f"기본 {judge.DEFAULTS[f]:g}")
    args = ap.parse_args()

    overrides = {f: getattr(args, f) for f in judge.FIELDS
                 if getattr(args, f) is not None}

    if args.slide and not Slide.objects.filter(slug=args.slide).exists():
        raise SystemExit(f"슬라이드를 찾지 못했다: {args.slide}")
    # **기본은 검토 중인 묶음이다** (DiaRUGA P10 1단계). 정규화 뒤에는 모든 묶음의
    # 검출이 자기 안에서 `is_current` 라, 그것만 걸면 쌓아 둔 것 전부가 딸려 온다.
    #
    # **다른 묶음을 고를 수 있다** (DiaRUGA 078). 두 가지가 다른 일이라 갈라 둔다:
    #
    # - **문턱을 안 바꾸고 다시 판정** — 저장된 지표로 캐시를 다시 쓰는 것뿐이라
    #   어느 묶음에든 안전하다. DiaRUGA 077 이전에 생긴 어긋남을 여기서 고친다
    # - **문턱을 바꿔 다시 거름** — 그 묶음이 "그때 그 문턱으로 낸 결과" 라는
    #   사실이 깨진다. **회차끼리 견줄 수 없게 되므로** 검토 중인 묶음이
    #   아니면 `--force` 를 받아야 한다
    if args.batch and args.all_batches:
        raise SystemExit("--batch 와 --all-batches 는 함께 못 쓴다")
    if args.all_batches:
        dets = Detection.objects.filter(is_current=True)
        where = "모든 묶음"
    elif args.batch:
        if not RunBatch.objects.filter(label=args.batch).exists():
            names = ", ".join(RunBatch.objects.order_by("label")
                              .values_list("label", flat=True)[:20])
            raise SystemExit(f"묶음을 찾지 못했다: {args.batch}\n  있는 것: {names}")
        dets = Detection.objects.filter(is_current=True,
                                        run__batch__label=args.batch)
        where = f"묶음 {args.batch}"
    else:
        dets = Detection.objects.reviewing()
        where = "검토 중인 묶음"

    if overrides and (args.batch or args.all_batches) and not args.force:
        cur = (RunBatch.objects.filter(for_review=True)
               .values_list("label", flat=True).first())
        if args.all_batches or args.batch != cur:
            raise SystemExit(
                f"검토 중이 아닌 묶음({where})의 문턱을 바꾸려 한다 — 그 묶음이 "
                f"'그때 그 문턱으로 낸 결과' 라는 사실이 깨지고 회차끼리 견줄 수 "
                f"없게 된다.\n  정말 그러려면 --force 를 준다. 문턱을 안 바꾸고 "
                f"다시 판정만 하려면 문턱 인자를 빼고 부른다.")

    dets = (dets
            .select_related("thresholds", "viewpoint", "viewpoint__slide")
            .prefetch_related("candidates"))
    if args.slide:
        dets = dets.filter(viewpoint__slide__slug=args.slide)
    dets = list(dets)
    if not dets:
        raise SystemExit(f"{where}에 현재 검출이 없다.")

    run = None
    if not args.dry_run:
        run = Run.objects.create(kind="refilter", status="running",
                                 params={"overrides": overrides,
                                         "batch": where,
                                         "slide": args.slide or "*",
                                         "from_defaults": args.defaults},
                                 host=socket.gethostname(),
                                 code_version=git_version())

    tot_before = tot_after = tot_changed = 0
    per_cls = Counter()
    reasons = Counter()
    ts_cache = {}

    with transaction.atomic():
        for det in dets:
            # 주지 않은 문턱은 이 검출이 지금 쓰는 값 그대로 — 하나를 바꾸려다
            # 나머지가 조용히 초기화되면 안 된다.
            base = (judge.DEFAULTS if args.defaults or not det.thresholds
                    else det.thresholds.as_dict())
            values = {**judge.DEFAULTS, **base, **overrides}

            before, after, why, changed = refilter_detection(det, values,
                                                             args.dry_run)
            tot_before += before
            tot_after += after
            tot_changed += changed
            reasons += why
            for c in det.candidates.all():
                if c.passed and c.cls:
                    per_cls[c.cls] += 1

            if not args.dry_run:
                key = tuple(sorted(values.items()))
                if key not in ts_cache:
                    ts_cache[key] = threshold_set_for(values)
                det.thresholds = ts_cache[key]
                det.save(update_fields=["thresholds"])

            if not args.quiet and before != after:
                vp = det.viewpoint
                print(f"  {vp.slide.slug} g{vp.idx:<3d} {before:4d} -> {after:4d}")

        if args.dry_run:
            # dry-run 은 아무것도 남기지 않는다 — 메모리에서만 판정했지만
            # 실수로 저장되는 경로가 생기지 않게 통째로 되돌린다.
            transaction.set_rollback(True)

    print(f"\n[{where}] 검출 {len(dets)}개 · 통과 {tot_before} -> {tot_after} "
          f"({tot_after - tot_before:+d})")
    if per_cls:
        print("  " + " · ".join(f"{k} {v}" for k, v in sorted(per_cls.items())))
    if reasons:
        top = " · ".join(f"{k} {v}" for k, v in reasons.most_common(6))
        print(f"  탈락 사유: {top}")

    if args.dry_run:
        print(f"\ndry-run — 바뀔 행 {tot_changed}개. 저장하지 않았다.")
        if overrides:
            print("  적용하려면 --dry-run 을 빼고 같은 명령을 다시 돌린다.")
        return

    run.status = "done"
    run.finished_at = timezone.now()
    run.counts = {"detections": len(dets), "before": tot_before,
                  "after": tot_after, "changed": tot_changed,
                  **{f"cls_{k}": v for k, v in per_cls.items()}}
    run.save()
    print(f"\n저장했다. 행 {tot_changed}개 갱신 · 문턱 조합 {len(ts_cache)}개 · "
          f"Run #{run.pk}")


if __name__ == "__main__":
    main()
