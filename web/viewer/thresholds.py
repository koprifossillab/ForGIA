"""DiaRUGA v0.29.0 `web/viewer/thresholds.py` 에서 왔다. 문턱 미리보기와 적용. 판정 규칙 자체는 `judge.py` 하나에 있다.

문턱을 만지는 일의 어려움은 **한 시야를 보며 정한 값이 124개 시야 전부에 걸린다**는
것이다. 그 영향을 눈으로 보지 못하면 적용하고 나서 시야를 하나씩 다시 확인해야 한다.

그래서 미리보기가 두 가지를 함께 돌려준다.

- **전체 영향** — 통과 개수가 어떻게 변하나, 몇 개 시야가 영향받나
- **시야별 뒤집힘** — 어느 시야에서 무엇이 들어오고 빠지나 (영향 큰 순으로)

실측으로 전체 재판정이 50 ms 대이고, 문턱을 바꿔도 판정이 뒤집히는 개체는 전체의
0.4~3.3% 뿐이다(영향 없는 시야가 절반이 넘는다). 그러니 **볼 것은 그 차이뿐이다.**
"""
import sys
import time
from pathlib import Path

# **판정 규칙은 `pipeline/judge.py` 하나뿐이다** (DiaRUGA 100).
# 뷰어와 파이프라인이 같은 것을 봐야 한다 — 규칙이 둘이면 화면과 검출이
# 다른 말을 한다. 저장소 뿌리는 `web/viewer` 의 두 단계 위다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "pipeline"))

import judge                                                        # noqa: E402

from .models import Candidate, Detection, ThresholdSet              # noqa: E402

# 판정에 필요한 값만 뽑는다. 폴리곤은 여기서 쓰지 않는다 — 이미 화면에 있다.
FETCH = ("id", "mask_key", "passed", "cls", "bbox_x", "bbox_y", "bbox_w",
         "bbox_h", "area_px", "shape_ok", "major_um", "long_side_um",
         "predicted_iou", "detection_id")


def _records(rows):
    return [{
        "_pk": r["id"], "_key": r["mask_key"], "_was": r["passed"],
        "_was_cls": r["cls"] or None,
        "bbox_xywh": [r["bbox_x"], r["bbox_y"], r["bbox_w"], r["bbox_h"]],
        "area_px": r["area_px"], "shape_ok": r["shape_ok"],
        "major_um": r["major_um"], "long_side_um": r["long_side_um"],
        "predicted_iou": r["predicted_iou"],
    } for r in rows]


def load_pool(slug=None):
    """현재 검출의 개체를 시야별로 모은다. {detection_id: [record]}"""
    # **검토 대상 묶음의 검출만** (DiaRUGA P10 1단계). 나란히 쌓아 둔 다른 엔진까지
    # 세면 문턱 미리보기의 숫자가 화면의 숫자와 안 맞는다.
    qs = Candidate.objects.filter(
        detection__in=Detection.objects.reviewing())
    if slug:
        qs = qs.filter(detection__viewpoint__slide__slug=slug)
    pool = {}
    for r in qs.values(*FETCH).iterator(chunk_size=5000):
        pool.setdefault(r["detection_id"], []).append(r)
    return {k: _records(v) for k, v in pool.items()}


def detection_index(slug=None):
    """detection_id -> 화면에 쓸 시야 정보."""
    qs = Detection.objects.reviewing().select_related(
        "viewpoint", "viewpoint__slide", "viewpoint__stack", "thresholds")
    if slug:
        qs = qs.filter(viewpoint__slide__slug=slug)
    out = {}
    for d in qs:
        vp = d.viewpoint
        stack = getattr(vp, "stack", None)
        out[d.id] = {
            "detection_id": d.id,
            "slug": vp.slide.slug,
            "label": vp.slide.name,
            "gid": vp.idx,
            "tag": vp.tag,
            "image_rel": (stack.focused_path
                          if stack and d.image and d.image.kind == "stack"
                          else d.image_path),
            "size": [d.width, d.height],
            "um_per_pixel": d.um_per_pixel,
            "thresholds": d.thresholds.as_dict() if d.thresholds else None,
        }
    return out


def current_values(slug=None) -> dict:
    """지금 쓰이는 문턱. 여러 개가 섞여 있으면 가장 많이 쓰이는 것을 준다."""
    qs = Detection.objects.reviewing().select_related("thresholds")
    if slug:
        qs = qs.filter(viewpoint__slide__slug=slug)
    counts = {}
    for d in qs:
        key = d.thresholds_id
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return dict(judge.DEFAULTS)
    best = max(counts, key=counts.get)
    ts = ThresholdSet.objects.filter(pk=best).first() if best else None
    return ts.as_dict() if ts else dict(judge.DEFAULTS)


def threshold_spread(slug=None):
    """문턱이 갈라져 있는가. 갈라지면 시야 간 개수를 비교할 수 없다."""
    qs = Detection.objects.reviewing()
    if slug:
        qs = qs.filter(viewpoint__slide__slug=slug)
    ids = set(qs.values_list("thresholds_id", flat=True))
    return len(ids)


def preview(values: dict, pool: dict) -> dict:
    """새 문턱으로 전체를 재판정한다. 저장하지 않는다.

    돌려주는 것:
      total    — 전체 개수와 영향받는 시야 수
      per_det  — 시야별 {before, after, added[], removed[], verdict{}}
                 verdict 는 mask_key -> cls|null (화면이 색만 바꿔 그린다)
    """
    th = judge.Thresholds(**values)
    t0 = time.time()

    per_det = {}
    before = after = n_added = n_removed = 0
    for did, recs in pool.items():
        kept, _rej = judge.apply([dict(r) for r in recs], th)
        now = {r["_pk"]: r for r in kept}

        added, removed, verdict = [], [], {}
        b = a = 0
        for r in recs:
            hit = now.get(r["_pk"])
            verdict[r["_key"]] = hit["cls"] if hit else None
            if r["_was"]:
                b += 1
            if hit:
                a += 1
            if hit and not r["_was"]:
                added.append(r["_key"])
            elif not hit and r["_was"]:
                removed.append(r["_key"])
        before += b
        after += a
        n_added += len(added)
        n_removed += len(removed)
        per_det[did] = {"before": b, "after": a, "added": added,
                        "removed": removed, "verdict": verdict}

    touched = sum(1 for v in per_det.values() if v["added"] or v["removed"])
    return {
        "total": {
            "before": before, "after": after, "delta": after - before,
            "added": n_added, "removed": n_removed,
            "viewpoints": len(per_det), "touched": touched,
            "ms": round((time.time() - t0) * 1000),
        },
        "per_det": per_det,
    }


def class_counts_from(per_det: dict) -> dict:
    """`preview()` 결과에서 분류별 개수를 센다. **판정을 다시 걸지 않는다.**

    예전에는 `class_counts(values, pool)` 이 전체를 다시 판정했다 — `preview()` 가
    방금 한 일을 똑같이 한 번 더 한 것이다. 프로파일에 `judge.apply` 가 시야
    수의 **두 배**로 찍혀서 드러났다(검출 74개에 148번).

    `preview` 가 이미 `verdict`(개체 키 → 분류)를 들고 있으므로 그것을 센다.
    통과분만 분류가 붙고 탈락분은 None 이라 그대로 세면 된다.
    """
    out = {}
    for r in per_det.values():
        for cls in r["verdict"].values():
            if cls is None:
                continue
            out[cls] = out.get(cls, 0) + 1
    return out


def class_counts(values: dict, pool: dict) -> dict:
    """새 문턱에서의 분류별 개수. 판정 결과가 없을 때만 쓴다 — 전체를 다시 센다."""
    th = judge.Thresholds(**values)
    out = {}
    for recs in pool.values():
        kept, _ = judge.apply([dict(r) for r in recs], th)
        for r in kept:
            k = r.get("cls") or "?"
            out[k] = out.get(k, 0) + 1
    return out


def apply_values(values: dict, slug=None, run=None) -> dict:
    """미리보기와 **같은 판정**을 실제로 저장한다.

    미리보기와 저장이 다른 코드로 갈리면 "보고 정한 것과 다른 결과" 가 나온다.
    그래서 여기서도 `judge.apply` 하나만 쓴다.
    """
    from django.db import transaction

    th = judge.Thresholds(**values)
    # **검토 대상 묶음에만 적용한다** (DiaRUGA P10 1단계). 다른 묶음은 그때의 문턱으로
    # 판정된 기록이고, 그것을 지금 값으로 다시 쓰면 회차 비교가 무의미해진다.
    qs = Detection.objects.reviewing().prefetch_related("candidates")
    if slug:
        qs = qs.filter(viewpoint__slide__slug=slug)

    ts, _ = _threshold_set(values)
    before = after = changed = 0
    with transaction.atomic():
        for det in qs:
            cands = list(det.candidates.all())
            before += sum(1 for c in cands if c.passed)
            recs = [{
                "_pk": c.pk, "bbox_xywh": [c.bbox_x, c.bbox_y, c.bbox_w, c.bbox_h],
                "area_px": c.area_px, "shape_ok": c.shape_ok,
                "major_um": c.major_um, "long_side_um": c.long_side_um,
                "predicted_iou": c.predicted_iou,
            } for c in cands]
            kept, rejected = judge.apply(recs, th)
            after += len(kept)

            by_pk = {c.pk: c for c in cands}
            dirty = []
            for r in kept:
                c = by_pk[r["_pk"]]
                if not c.passed or c.cls != r["cls"] or c.reject:
                    c.passed, c.cls, c.reject = True, r["cls"], ""
                    dirty.append(c)
            for r in rejected:
                c = by_pk[r["_pk"]]
                cls = r.get("cls") or ""
                if c.passed or c.cls != cls or c.reject != r["reject"]:
                    c.passed, c.cls, c.reject = False, cls, r["reject"]
                    dirty.append(c)
            if dirty:
                Candidate.objects.bulk_update(
                    dirty, ["passed", "cls", "reject"], batch_size=1000)
                changed += len(dirty)
            if det.thresholds_id != ts.pk:
                det.thresholds = ts
                det.save(update_fields=["thresholds"])
        if run:
            run.counts = {"before": before, "after": after, "changed": changed}
            run.save(update_fields=["counts"])
    return {"before": before, "after": after, "changed": changed,
            "threshold_set": ts.pk}


def _threshold_set(values: dict):
    """같은 문턱 조합이면 한 행을 공유한다."""
    found = ThresholdSet.objects.filter(**values).first()
    if found:
        return found, False
    name = (f"conf {values['conf_min']:g} · "
            f"{values['min_um']:g}~{values['max_um']:g} µm")
    return ThresholdSet.objects.create(name=name, note="뷰어에서 만들었다",
                                       **values), True


def clean_values(raw: dict, base: dict) -> dict:
    """받은 값을 검사한다. 주지 않은 것은 base 그대로 — 하나를 바꾸려다
    나머지가 조용히 초기화되면 안 된다."""
    out = dict(base)
    for f in judge.FIELDS:
        if f not in raw:
            continue
        try:
            v = float(raw[f])
        except (TypeError, ValueError):
            raise ValueError(f)
        if not (0 <= v <= 1e6):
            raise ValueError(f)
        out[f] = v
    if out["min_um"] > out["max_um"] or not (0 <= out["conf_min"] <= 1):
        raise ValueError("range")
    return out
