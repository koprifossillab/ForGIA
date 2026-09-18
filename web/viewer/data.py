"""DB → 뷰가 쓰는 dict. **읽기 전용이라는 약속이 있다** — 쓰는 문은 `manage_data`.

DiaRUGA v0.29.0 `web/viewer/data.py`(5,632줄)에서 1단계 화면이 쓰는 것만 왔다:
목록(`datasets`·`area_tabs`·`datasets_total`·`datasets_by_locality`) · 지도
(`map_points`) · 시야 목록(`dataset_detail`) · 시야 하나(`group_photos`) · 지점
(`locality_detail`) · 파이프라인 상태(`pipeline_status`) · 이미지 경로
(`safe_image_path`·`stamp`).

2단계에서 검출 층이 왔다 — 분류표(`class_list`·`counted_classes`) · 검토 대상
묶음(`review_batch_id`) · 집계(`_summary_by_sql`, 원시 SQL · DiaRUGA 058) · 표지
마스크(`_kept_masks`) · 개체 목록(`candidate_rows`) · 크롭 기하. **교정에 걸린
값(`reviewed_groups` · 지운 것·되살린 것)은 3단계까지 0 이다** — 그때 SQL 의
`LEFT JOIN viewer_objectreview` 갈래가 붙는다.
"""
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.models import Case, Count, Prefetch, When
from django.utils import timezone

from . import antarctica
from .models import (Candidate, ClassDef, Detection, Frame, Run, RunBatch,
                     Site, Slide, Stack, Viewpoint)


# --- 분류표 --------------------------------------------------------------------
# `ClassDef` 테이블이 정한다 (DiaRUGA 037~040). 요청마다 읽는다 — 프로세스가 여럿이라
# 캐시하면 판이 갈린다. 행이 넷뿐이라 비용도 없다.

def class_list() -> list[dict]:
    return list(ClassDef.objects.filter(active=True)
                .values("key", "label", "short", "badge", "color", "hotkey",
                        "counted", "is_taxon", "sort_order"))


def _labels() -> dict:
    return {c["key"]: c["label"] for c in class_list()}


def counted_classes() -> list[dict]:
    """개체 수로 세는 분류만 — 파편·비유공충은 개체가 아니다 (`ClassDef.counted`).
    목록의 "검출" 칸은 이것만 더한 값이다."""
    return [{"key": c["key"], "label": c["label"], "short": c["short"] or c["label"]}
            for c in class_list() if c["counted"]]


# --- 검토 대상 묶음 ---------------------------------------------------------------

def review_batch_id():
    """검토 대상 묶음의 pk. 없으면 `None` (DiaRUGA P10). 서버 설정이라 요청마다 한 번."""
    return (RunBatch.objects.filter(for_review=True)
            .values_list("id", flat=True).first())


def review_batch_label() -> str:
    return (RunBatch.objects.filter(for_review=True)
            .values_list("label", flat=True).first() or "")


def batches_to_run() -> list[dict]:
    """새 자료가 들어왔을 때 **어떤 순서로 어느 묶음을 채우는가** (DiaRUGA 079).

    검토 중인 묶음이 먼저, 나머지는 최근 것부터. **조리법이 없으면 안 돈다.**
    가중치 파일이 없으면 목록에 남기되 `ready=False` 와 이유를 함께 준다.
    """
    root = Path(settings.DATA_ROOT)
    rows = []
    for b in RunBatch.objects.filter(kind="detect").exclude(recipe={}):
        r = dict(b.recipe)
        ready, why = True, ""
        w = r.get("weights")
        if r.get("backend", "yolo") == "yolo":
            if not w:
                ready, why = False, "조리법에 가중치가 없다"
            else:
                path = Path(w) if Path(w).is_absolute() else root / w
                if not path.exists():
                    ready, why = False, f"가중치 파일이 없다: {path}"
        rows.append({"batch": b, "recipe": r, "ready": ready, "why": why})
    rows.sort(key=lambda x: (not x["batch"].for_review,
                             -x["batch"].started_at.timestamp()))
    return rows


def batches_elsewhere(slide=None, vp=None) -> dict:
    """시야 pk → **검토 대상이 아닌 묶음 중 검출이 있는 것**들의 이름 (DiaRUGA P10).
    검출이 아예 없는 것과 다른 묶음에는 있는 것은 다른 말이다."""
    rb = review_batch_id()
    qs = Detection.objects.filter(is_current=True)
    if slide is not None:
        qs = qs.filter(viewpoint__slide=slide)
    if vp is not None:
        qs = qs.filter(viewpoint=vp)
    if rb is not None:
        qs = qs.exclude(run__batch_id=rb)
    out = defaultdict(list)
    for vp_id, label in (qs.values_list("viewpoint_id", "run__batch__label")
                         .distinct()):
        if label and label not in out[vp_id]:
            out[vp_id].append(label)
    return out


# --- 검출 → dict -----------------------------------------------------------------
# **교정을 얹는 자리는 3단계에서 온다** (`_apply_review`). 지금은 판정 그대로다.

NUM_FIELDS = ("area_um2", "major_um", "minor_um", "long_side_um", "short_side_um",
              "aspect_ratio", "fill_ratio", "circularity", "convexity", "solidity",
              "elongation", "ellipse_iou", "texture", "predicted_iou",
              "stability_score")


def mask_points(c: dict) -> str:
    """SVG `points` 문자열. 점이 셋 미만이면 빈 문자열 — 그릴 수 없다."""
    p = c.get("polygon") or []
    if len(p) < 6:
        return ""
    return " ".join(f"{p[i]},{p[i + 1]}" for i in range(0, len(p) - 1, 2))


def _cand_dict(c: Candidate) -> dict:
    return {
        "id": c.raw_id, "key": c.mask_key, "bbox_xywh": c.bbox_xywh,
        "center_xy": [c.center_x, c.center_y], "area_px": c.area_px,
        "shape_ok": c.shape_ok, "polygon": c.polygon or [],
        "cls": c.cls or None, "passed": c.passed, "reject": c.reject or "",
        # 확신도는 YOLO conf (DiaRUGA 는 SAM2 자리 이름을 그대로 뒀다)
        "conf": c.predicted_iou,
        **{f: getattr(c, f) for f in NUM_FIELDS},
    }


def current_detections(vp: Viewpoint, batch_id=None) -> list[Detection]:
    """이 시야의 현재 검출 — **검토 대상 묶음 안에서** 이미지마다 하나. 합성본이 먼저."""
    if batch_id is None:
        batch_id = review_batch_id()
    if batch_id is None:
        return []
    return list(Detection.objects.filter(viewpoint=vp, is_current=True,
                                         run__batch_id=batch_id)
                .select_related("image")
                .prefetch_related("candidates")
                .order_by(Case(When(image__kind="stack", then=0), default=1), "id"))


def detection_dict(d: Detection) -> dict:
    cands = [_cand_dict(c) for c in d.candidates.all()]
    kept = [c for c in cands if c["passed"]]
    kept.sort(key=lambda c: -(c["area_px"] or 0))
    return {
        "detection_id": d.pk, "image_id": d.image_id,
        "image_rel": d.image.path if d.image_id else d.image_path,
        "image_kind": d.image.kind if d.image_id else "",
        "size": [d.width, d.height], "um_per_pixel": d.um_per_pixel,
        "batch_id": d.run.batch_id if d.run_id else None,
        "thresholds": d.thresholds.as_dict() if d.thresholds_id else None,
        "n_raw_masks": d.n_raw_masks, "n_sized": d.n_sized,
        "candidates": kept, "rejected": [c for c in cands if not c["passed"]],
        "n_candidates": len(kept),
    }


def detection_for_viewpoint(vp: Viewpoint, batch_id=None) -> dict | None:
    """시야의 대표 검출(합성본이 있으면 합성본) dict. 없으면 None."""
    dets = current_detections(vp, batch_id)
    return detection_dict(dets[0]) if dets else None


def candidate_rows(slug: str, batch_id=None, gone: bool = False) -> list[dict]:
    """슬라이드 전체의 검출 개체를 한 목록으로 — 크롭 화면·계측 표가 쓴다.

    대표 이미지(합성본) 하나의 개체만 낸다 (밀도의 정의: 시야 하나에 판 하나).
    `gone=True` 면 탈락분을 낸다 — 문턱이 무엇을 떨어뜨렸는지 보는 자리.
    """
    slide = Slide.objects.filter(slug=slug).first()
    if slide is None:
        return []
    if batch_id is None:
        batch_id = review_batch_id()
    if batch_id is None:
        return []
    rows = []
    for vp in (Viewpoint.objects.filter(slide=slide)
               .prefetch_related(Prefetch(
                   "detections",
                   queryset=Detection.objects.filter(is_current=True, run__batch_id=batch_id)
                   .select_related("image", "run").prefetch_related("candidates")))):
        dets = sorted(vp.detections.all(),
                      key=lambda d: (0 if d.image_id and d.image.kind == "stack" else 1, d.pk))
        if not dets:
            continue
        d = detection_dict(dets[0])
        for c in d["rejected" if gone else "candidates"]:
            rows.append({"group_id": vp.idx, "cell": vp.cell,
                         "stem": Path(d["image_rel"]).stem,
                         "image_rel": d["image_rel"], "image_id": d["image_id"],
                         "batch_id": d["batch_id"], "um_per_pixel": d["um_per_pixel"],
                         "reviewed": False, **c})
    return rows


# --- 집계 (원시 SQL · DiaRUGA 058) ------------------------------------------------
# **왜 원시 SQL 인가.** 목록은 개수 열 몇 개만 쓰는데 ORM 으로 개체를 올리면 폴리곤
# (용량의 대부분)까지 파싱한다. 3단계에서 교정(`viewer_objectreview`)을 `LEFT JOIN`
# 으로 얹는 자리이기도 하다 — ORM 은 `(image_id, mask_key)` 짝으로 못 조인한다.
#
# **시야마다 대표 이미지 하나만 센다** (`rep`). 프레임 검출이 함께 쌓이면 같은
# 개체가 판 수만큼 세어진다 — 학습 자료로는 맞고 계측 통계로는 틀리다.

_REP_CTE = """
WITH rep AS (
    SELECT d.id AS det_id, d.viewpoint_id, d.image_id, run.batch_id,
           ROW_NUMBER() OVER (
               PARTITION BY d.viewpoint_id
               ORDER BY CASE i.kind WHEN 'stack' THEN 0 ELSE 1 END, d.id) AS rn
      FROM viewer_detection d
      JOIN viewer_image i ON i.id = d.image_id
      LEFT JOIN viewer_run run ON run.id = d.run_id
      LEFT JOIN viewer_runbatch rb ON rb.id = run.batch_id
     WHERE d.is_current AND rb.for_review
)
"""

_SUMMARY_SQL = _REP_CTE + """
SELECT v.slide_id AS slide_id, c.cls AS eff_cls,
       COUNT(*) FILTER (WHERE c.passed) AS n_kept,
       COUNT(*) FILTER (WHERE c.passed) AS n_auto,
       0 AS n_labeled
FROM viewer_candidate c
JOIN rep ON rep.det_id = c.detection_id AND rep.rn = 1
JOIN viewer_viewpoint v ON v.id = rep.viewpoint_id
WHERE {where}
GROUP BY v.slide_id, c.cls
"""

_COVER_SQL = _REP_CTE + """
SELECT rep.viewpoint_id, c.polygon, COALESCE(NULLIF(c.cls, ''), 'none') AS mask_cls
FROM viewer_candidate c
JOIN rep ON rep.det_id = c.detection_id AND rep.rn = 1
JOIN viewer_viewpoint v ON v.id = rep.viewpoint_id
WHERE v.slide_id = %s AND c.passed
ORDER BY rep.viewpoint_id, c.area_px DESC
"""


def _kept_masks(slide: Slide) -> tuple[dict[int, list[dict]], dict[int, int]]:
    """시야 pk → (표지에 그릴 마스크, 남는 개체 수). 질의 하나.
    **개수는 마스크 수가 아니다** — 점 셋 미만인 폴리곤도 세어진다."""
    masks, n_kept = {}, {}
    with connection.cursor() as cur:
        cur.execute(_COVER_SQL, [slide.id])
        for vp_id, poly, cls in cur.fetchall():
            n_kept[vp_id] = n_kept.get(vp_id, 0) + 1
            p = json.loads(poly) if isinstance(poly, str) else (poly or [])
            if len(p) < 6:
                continue
            masks.setdefault(vp_id, []).append(
                {"points": " ".join(f"{p[i]},{p[i + 1]}" for i in range(0, len(p) - 1, 2)),
                 "cls": cls})
    return masks, n_kept


def _summary_rows(where: str, params: list) -> dict[int, dict]:
    out = {}
    with connection.cursor() as cur:
        cur.execute(_SUMMARY_SQL.format(where=where), list(params))
        for slide_id, eff_cls, n_kept, n_auto, n_labeled in cur.fetchall():
            r = out.setdefault(slide_id, {"per_cls": {}, "n_detected": 0,
                                          "n_auto": 0, "n_labeled": 0})
            if n_kept and eff_cls:
                r["per_cls"][eff_cls] = r["per_cls"].get(eff_cls, 0) + n_kept
            r["n_detected"] += n_kept
            r["n_auto"] += n_auto
            r["n_labeled"] += n_labeled
    return out


def _summary_by_sql(slide: Slide) -> dict:
    per_cls = {c["key"]: 0 for c in class_list()}
    row = _summary_rows("v.slide_id = %s", [slide.id]).get(slide.id) or {
        "per_cls": {}, "n_detected": 0, "n_auto": 0, "n_labeled": 0}
    per_cls.update({k: v for k, v in row["per_cls"].items() if k in per_cls})
    detected_groups = (Detection.objects.filter(viewpoint__slide=slide).reviewing()
                       .values("viewpoint_id").distinct().count())
    return {"per_cls": per_cls, "n_detected": row["n_detected"],
            "n_auto": row["n_auto"], "n_labeled": row["n_labeled"],
            "detected_groups": detected_groups}


# --- 크롭 기하 (DiaRUGA 그대로) --------------------------------------------------

def polygon_axis(poly) -> tuple[float, float] | None:
    """마스크의 주축 각도(도)와 축 비율 — 채워진 영역의 2차 모멘트로."""
    if not poly or len(poly) < 6:
        return None
    xs = [float(v) for v in poly[0::2]]
    ys = [float(v) for v in poly[1::2]]
    n = len(xs)
    a2 = sxx = syy = sxy = 0.0
    cx = cy = 0.0
    for i in range(n):
        j = (i + 1) % n
        x0, y0, x1, y1 = xs[i], ys[i], xs[j], ys[j]
        cross = x0 * y1 - x1 * y0
        a2 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
        sxx += cross * (x0 * x0 + x0 * x1 + x1 * x1)
        syy += cross * (y0 * y0 + y0 * y1 + y1 * y1)
        sxy += cross * (2 * x0 * y0 + x0 * y1 + x1 * y0 + 2 * x1 * y1)
    area = a2 / 2.0
    if abs(area) < 1e-9:
        return None
    cx /= 6.0 * area
    cy /= 6.0 * area
    m20 = sxx / (12.0 * area) - cx * cx
    m02 = syy / (12.0 * area) - cy * cy
    m11 = sxy / (24.0 * area) - cx * cy
    ang = 0.5 * math.atan2(2.0 * m11, m20 - m02)
    diff = math.hypot(m20 - m02, 2.0 * m11)
    l1 = (m20 + m02 + diff) / 2.0
    l2 = (m20 + m02 - diff) / 2.0
    ratio = math.sqrt(l1 / l2) if l2 > 1e-9 else 999.0
    return math.degrees(ang), ratio


def rotated_extent(poly, deg: float) -> tuple[int, int]:
    r = math.radians(deg)
    cos, sin = math.cos(r), math.sin(r)
    xs = [float(v) for v in poly[0::2]]
    ys = [float(v) for v in poly[1::2]]
    rx = [x * cos - y * sin for x, y in zip(xs, ys)]
    ry = [x * sin + y * cos for x, y in zip(xs, ys)]
    return (max(1, int(round(max(rx) - min(rx)))),
            max(1, int(round(max(ry) - min(ry)))))


UPRIGHT_MIN_RATIO = 1.15


def crop_geometry(c: dict, rotate: bool = True) -> dict | None:
    """갤러리 크롭의 회전량과 결과 크기(px). 주축을 세로로 세운다."""
    poly = c.get("polygon")
    if not poly or len(poly) < 6:
        return None
    deg = 0.0
    if rotate:
        axis = polygon_axis(poly)
        if axis and axis[1] >= UPRIGHT_MIN_RATIO:
            deg = 90.0 - axis[0]
            while deg > 90:
                deg -= 180
            while deg < -90:
                deg += 180
    w, h = rotated_extent(poly, deg)
    m = max(3, round(0.08 * max(w, h)))
    ow, oh = w + 2 * m, h + 2 * m
    return {"rot": round(deg, 2), "out": f"{ow},{oh}", "out_w": ow, "out_h": oh}


def scalebar_for(out_w: int, um_per_px: float, frac: float = 0.4) -> dict | None:
    """크롭 썸네일에 얹을 스케일바. 이미지 폭에 대한 백분율로 준다."""
    if not out_w or not um_per_px:
        return None
    um_w = out_w * um_per_px
    target = um_w * frac
    if target <= 0:
        return None
    e = 10.0 ** math.floor(math.log10(target))
    m = target / e
    bar = (5 if m >= 5 else 2 if m >= 2 else 1) * e
    if bar <= 0:
        return None
    label = f"{bar:g} µm" if bar >= 1 else f"{bar:.1f} µm"
    return {"pct": round(100.0 * bar / um_w, 2), "um": bar, "label": label}


def stamp(rel: str) -> int:
    """이미지의 mtime. URL 에 넣어 "내용이 바뀌면 주소도 바뀌게" 만든다."""
    try:
        return int((Path(settings.DATA_ROOT) / rel).stat().st_mtime)
    except (OSError, TypeError, ValueError):
        return 0


def safe_image_path(rel: str) -> Path | None:
    """p= 로 들어온 상대경로를 실제 파일로 바꾼다.

    IMAGE_DIRS 안에 실제로 들어 있는 파일만 허용한다. symlink 까지 풀어서
    비교하므로 ../ 나 링크로 바깥을 가리키는 경로는 통과하지 못한다.
    """
    if not rel:
        return None
    root = Path(settings.DATA_ROOT).resolve()
    try:
        target = (root / rel).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not target.is_file():
        return None
    for allowed in settings.IMAGE_DIRS:
        base = (root / allowed).resolve()
        if target.is_relative_to(base):
            return target
    return None


def slide_label(slug: str) -> str | None:
    """머리글에 쓸 이름만. 없으면 None."""
    return Slide.objects.filter(slug=slug).values_list("name", flat=True).first()


# --- 배율 --------------------------------------------------------------------
def scales_by_slide() -> dict:
    """슬라이드마다의 µm/px. 배율을 바꿔 찍으면 슬라이드마다 다르다.

    **검출 행에서 세고, 검출이 없는 슬라이드는 프레임에서 센다** — 검출 전에도
    목록에 배율이 보여야 한다. 한 슬라이드 안에 값이 갈리면 가장 많은 쪽을 쓴다
    — 갈린 것 자체는 `check_db` 가 따로 잡는다.
    """
    per: dict[str, dict[float, int]] = {}
    seen = set()
    for slug, um in (Detection.objects.reviewing().filter(um_per_pixel__isnull=False)
                     .values_list("viewpoint__slide__slug", "um_per_pixel")):
        per.setdefault(slug, {})
        k = round(um, 9)
        per[slug][k] = per[slug].get(k, 0) + 1
        seen.add(slug)
    for slug, um in (Frame.objects.filter(um_per_pixel__isnull=False)
                     .exclude(slide__slug__in=seen)
                     .values_list("slide__slug", "um_per_pixel")):
        per.setdefault(slug, {})
        k = round(um, 9)
        per[slug][k] = per[slug].get(k, 0) + 1
    return {slug: max(c, key=c.get) for slug, c in per.items()}


# --- 집계 --------------------------------------------------------------------
def _slide_summary(slide: Slide) -> dict:
    """목록 화면의 집계. 세는 일은 `_summary_by_sql` 하나로 모았다 (DiaRUGA 060).
    **교정 값(`reviewed_groups`)은 3단계까지 0 이다.**"""
    vps = Viewpoint.objects.filter(slide=slide)
    n_groups = vps.count()
    sizes = list(vps.values_list("n_frames", flat=True))
    n_img = sum(sizes)

    r = _summary_by_sql(slide)
    per_cls = r["per_cls"]
    n_detected, n_auto, n_counts = r["n_detected"], r["n_auto"], r["detected_groups"]
    labels = _labels()
    class_counts = [{"key": k, "label": labels.get(k, k), "n": v}
                    for k, v in per_cls.items() if v]
    # 세는 분류만 더한 값. 0 인 분류도 자리를 남긴다 — 표의 열이 줄마다 같아야 한다
    counted = [{**c, "n": per_cls.get(c["key"], 0)} for c in counted_classes()]
    n_counted = sum(c["n"] for c in counted)
    return {
        "n_groups": n_groups,
        "n_images": n_img,
        "mean_size": round(n_img / n_groups, 1) if n_groups else 0,
        "singletons": sum(1 for s in sizes if s == 1),
        "max_size": max(sizes) if sizes else 0,
        "n_stacks": Stack.objects.filter(viewpoint__slide=slide).count(),
        "detected_groups": n_counts,
        "n_auto": n_auto,
        "n_detected": n_detected,
        "mean_detected": round(n_detected / n_counts, 1) if n_counts else None,
        "n_counted": n_counted,
        "mean_counted": round(n_counted / n_counts, 1) if n_counts else None,
        "counted": counted,
        "class_counts": class_counts,
        "reviewed_groups": 0,           # 3단계
    }


def _slide_order(sl):
    """관찰을 세우는 순서 — 시료의 깊이, 분획, 관찰 번호. `Slide.Meta.ordering` 과 같아야 한다."""
    depth = sl.sample.depth_cm if sl.sample_id else None
    return (depth is None, depth or 0, sl.fraction_um or 0, sl.obs_no, sl.name)


def _obs(slide) -> dict:
    """행에 싣는 관찰 정보. 목록·지점 페이지·지도가 같은 열쇠를 쓴다."""
    return {
        "obs_no": slide.obs_no,
        "obs_label": slide.obs_label,
        "obs_badge": slide.obs_badge,
        "fraction_um": slide.fraction_um,
        "fraction_badge": slide.fraction_badge,
        "split_denom": slide.split_denom,
        "hidden": slide.hide_in_list,
        "excluded": slide.exclude_from_totals,
    }


def _slides_in_order(qs):
    """목록과 같은 차례 — 지역 → 지점 → 시료 깊이 → 분획 → 관찰 번호."""
    return (qs.select_related("sample__locality__site")
              .order_by("sample__locality__site__code",
                        "sample__locality__code", "sample__depth_cm",
                        "fraction_um", "obs_no", "name"))


def datasets(area: str | None = None) -> list[dict]:
    """**숨긴 슬라이드도 담아 돌려준다.** 거르는 자리는 `datasets_by_locality()` 다."""
    scales = scales_by_slide()
    out = []
    slides = _slides_in_order(Slide.objects.all())
    if area and area != AREA_ALL:
        slides = slides.filter(sample__locality__site__area=area)
    for slide in slides:
        sample = slide.sample
        loc = sample.locality if sample else None
        site = loc.site if loc else None
        out.append({
            "slug": slide.slug,
            "label": slide.name,
            "image_dir": slide.image_dir,
            "corr_thresh": slide.corr_thresh,
            "site": (site.region or site.name or site.code) if site else "",
            "site_code": site.code if site else "",
            "core": loc.code if loc else "",
            "sample_code": sample.code if sample else "",
            "depth_cm": sample.depth_cm if sample else None,
            "description": slide.description,
            "um_per_pixel": scales.get(slide.slug),
            "state": slide.state,
            "state_note": slide.state_note,
            "missing_dir": not (Path(settings.DATA_ROOT)
                                / slide.image_dir).is_dir(),
            **_obs(slide),
            **_slide_summary(slide),
        })
    return out


# "전체" 는 권역이 아니라 **거르지 않는다**는 뜻이다.
AREA_ALL = "all"


def area_tabs(selected: str | None = None) -> dict:
    """목록 위의 [남극|전체] 갈래. 지금은 권역이 하나라 탭이 둘이다 — 늘면 따라온다.

    **아무것도 주지 않으면 `전체` 다.** `전체` 는 지역(Site)이 안 붙은 슬라이드
    까지 담는다 — 새로 반입된 슬라이드는 지역이 정해지기 전까지 어느 권역에도
    없어서, 이 탭이 없으면 있다는 것만 알리고 열어 볼 길이 없다 (DiaRUGA).
    """
    counts = dict(Slide.objects.filter(sample__locality__site__isnull=False)
                  .values_list("sample__locality__site__area")
                  .annotate(n=Count("id")))
    tabs = [{"key": k, "label": v, "n": counts.get(k, 0)} for k, v in Site.AREA]
    tabs.append({"key": AREA_ALL, "label": "전체", "n": Slide.objects.count()})
    keys = [t["key"] for t in tabs]
    if selected not in keys:
        selected = AREA_ALL
    for t in tabs:
        t["on"] = t["key"] == selected
    return {
        "tabs": tabs,
        "selected": selected,
        "is_all": selected == AREA_ALL,
        "orphans": Slide.objects.filter(sample__isnull=True).count(),
    }


def datasets_total(rows: list[dict]) -> dict:
    """목록 표의 합계 줄. 합칠 수 있는 것만 합친다.

    **합계는 `집계 제외`(`excluded`)만 읽고 `숨김` 은 안 읽는다** — 읽으면 보기
    토글 한 번에 같은 자료가 다른 숫자를 낸다. 숨긴 행이 합계에 들어 있으면
    세어서 알린다(`n_hidden_in`).
    """
    counted_rows = [r for r in rows if not r.get("excluded")]
    keys = ("n_images", "n_groups", "n_stacks", "n_detected", "n_counted",
            "reviewed_groups")
    total = {k: sum(r.get(k) or 0 for r in counted_rows) for k in keys}
    per = {c["key"]: 0 for c in counted_classes()}
    for r in counted_rows:
        for c in r.get("counted") or []:
            if c["key"] in per:
                per[c["key"]] += c["n"]
    total["counted"] = [{**c, "n": per[c["key"]]} for c in counted_classes()]
    total["n_excluded"] = len(rows) - len(counted_rows)
    total["n_hidden_in"] = sum(1 for r in counted_rows if r.get("hidden"))
    return total


def datasets_by_locality(rows: list[dict], with_hidden: bool = False) -> list[dict]:
    """목록을 지점으로 묶는다. 표·카드 둘 다 이것을 쓴다.

    **줄 순서는 이미 지역→지점→깊이다**(`datasets()`) — 나온 순서대로 묶기만
    한다. 지점이 안 붙은 슬라이드는 "지점 미지정" 묶음이 된다 — 어디에도 안
    들어가면 목록에서 사라진다. **빈 묶음이 되어도 안 지운다**(머리줄 숫자는
    숨긴 것까지 센 값이라 그것을 남긴다).
    """
    out, at = [], {}
    for r in rows:
        key = (r.get("site_code") or "", r.get("core") or "")
        g = at.get(key)
        if g is None:
            g = at[key] = {
                "key": f"{key[0]}/{key[1]}",
                "site_code": key[0],
                "site": r.get("site") or "",
                "core": key[1],
                "no_core": not key[1],
                "all_rows": [],
            }
            out.append(g)
        g["all_rows"].append(r)
    for g in out:
        g["totals"] = datasets_total(g["all_rows"])
        g["rows"] = [r for r in g["all_rows"]
                     if with_hidden or not r.get("hidden")]
        g["n"] = len(g["rows"])
        g["n_hidden"] = len(g["all_rows"]) - g["n"]
    return out


# --- 지도 --------------------------------------------------------------------
def _polar_xy(lat: float, lon: float) -> tuple[float, float]:
    """EPSG:3031 로 투영해 SVG 좌표(km)로. antarctica.py 와 같은 식이어야 한다.

    상수를 여기 한 번 더 적지 않고 antarctica 에서 가져온다 — 식이 갈라지면
    지도와 마커가 어긋난다.
    """
    import math

    a, e = antarctica.WGS84_A, antarctica.WGS84_E

    def t(phi):
        s = e * math.sin(phi)
        return math.tan(math.pi / 4 + phi / 2) / (((1 + s) / (1 - s)) ** (e / 2))

    phi_c = math.radians(-71.0)
    mc = math.cos(phi_c) / math.sqrt(1 - e * e * math.sin(phi_c) ** 2)
    rho = a * mc * t(math.radians(lat)) / t(phi_c)
    lam = math.radians(lon)
    return rho * math.sin(lam) / 1000, -rho * math.cos(lam) / 1000


def map_points(area: str | None = None,
               with_hidden: bool = False) -> list[dict]:
    """지도에 찍을 지역별 묶음. 슬라이드가 아니라 **지역 단위**다.

    좌표는 `Site.lat/lon` 이 원칙이고, 비어 있으면 관찰이 있는 지점 좌표의
    평균(DiaRUGA 199), 그것도 없으면 해역 대략값으로 물러난다. **어느 쪽인지
    반드시 함께 낸다** — 대략값을 실측처럼 보이게 두면 안 된다.

    **숨긴 슬라이드는 여기서도 뺀다** — 세 보기가 같은 것을 봐야 한다.
    """
    area = area or "ant"
    approx_sites, project = antarctica.APPROX_SITES, _polar_xy

    sites = (Site.objects.filter(area=area)
             .prefetch_related("localities__samples__slides"))

    def visible(qs):
        return [sl for sl in qs if with_hidden or not sl.hide_in_list]

    def slides_of(loc):
        return [sl for sm in loc.samples.all() for sl in visible(sm.slides.all())]

    out = []
    for site in sites:
        slides = [sl for loc in site.localities.all() for sl in slides_of(loc)]
        if not slides:
            continue
        base = re.sub(r"\d+$", "", site.code).upper()
        approx = approx_sites.get(base)
        loc_xy = [(loc.lat, loc.lon) for loc in site.localities.all()
                  if loc.lat is not None and loc.lon is not None
                  and slides_of(loc)]
        if site.lat is not None and site.lon is not None:
            lat, lon, exact, note = site.lat, site.lon, True, ""
        elif loc_xy:
            lat = sum(la for la, _ in loc_xy) / len(loc_xy)
            lon = sum(lo for _, lo in loc_xy) / len(loc_xy)
            exact, note = True, ""
        elif approx:
            lat, lon, exact, note = approx[0], approx[1], False, approx[2]
        else:
            continue                    # 어디인지 짐작할 수도 없으면 안 찍는다
        x, y = project(lat, lon)

        cores = []
        for loc in sorted(site.localities.all(), key=lambda c: c.code):
            rows = sorted(slides_of(loc), key=_slide_order)
            if not rows:
                continue
            if loc.lat is not None and loc.lon is not None:
                cx, cy = project(loc.lat, loc.lon)
                c_exact = True
            else:
                cx, cy, c_exact = x, y, exact
            slide_rows = [{
                "slug": sl.slug,
                "label": sl.name,
                "depth_cm": sl.depth_cm,
                "state": sl.state,
                **_obs(sl),
                "n_viewpoints": sl.viewpoints.count(),
                "reviewed": 0,          # 3단계
            } for sl in rows]
            cores.append({
                "code": loc.code,
                "n_slides": len(rows),
                "x": round(cx, 1), "y": round(cy, 1), "exact": c_exact,
                "water_depth_m": loc.water_depth_m,
                "n_viewpoints": sum(r["n_viewpoints"] for r in slide_rows),
                "slides": slide_rows,
            })

        out.append({
            "code": site.code,
            "label": site.region or site.name or site.code,
            "x": round(x, 1), "y": round(y, 1),
            "exact": exact, "approx_note": note,
            "n_slides": len(slides),
            "n_viewpoints": sum(c["n_viewpoints"] for c in cores),
            "cores": cores,
            "core_codes": [c["code"] for c in cores],
            "slugs": [s.slug for s in slides],
        })
    return out


# --- 시야 목록 · 시야 하나 ------------------------------------------------------
def _cover_of(vp: Viewpoint) -> str | None:
    """목록의 대표 그림. 합성본이 원칙이고, 없으면 가장 선명한 프레임."""
    st = getattr(vp, "stack", None)
    if st:
        return st.focused_path
    fr = vp.sharpest_frame or next(iter(vp.frames.all()), None)
    if fr and (Path(settings.DATA_ROOT) / fr.path).exists():
        return fr.path
    return None


def dataset_detail(slug: str) -> dict | None:
    """시야 목록. 완료 표시는 3단계에서 붙는다.

    **개체를 dict 로 만들지 않는다** (DiaRUGA 060). 표지에 얹을 마스크와 수만
    SQL 로 받는다(`_kept_masks`). 검출을 돌린 이미지와 표지가 같을 때만 마스크를
    얹는다 — 다른 이미지의 좌표를 얹으면 조용히 어긋난 그림이 된다.
    """
    slide = Slide.objects.filter(slug=slug).select_related(
        "sample__locality__site").first()
    if slide is None:
        return None

    cover_masks, n_kept = _kept_masks(slide)
    dets = {}
    for d in (Detection.objects.filter(viewpoint__slide=slide).reviewing()
              .select_related("image")
              .order_by("viewpoint_id",
                        Case(When(image__kind="stack", then=0), default=1), "id")
              .values("viewpoint_id", "image_path", "width", "height")):
        dets.setdefault(d["viewpoint_id"], d)
    elsewhere = batches_elsewhere(slide=slide)

    groups = []
    for vp in (Viewpoint.objects.filter(slide=slide)
               .select_related("sharpest_frame", "stack")
               .prefetch_related("frames")):
        st = getattr(vp, "stack", None)
        det = dets.get(vp.id)
        cover_rel = _cover_of(vp)
        masks, size = [], None
        if det and cover_rel and Path(det["image_path"]).stem == Path(cover_rel).stem:
            size = [det["width"], det["height"]]
            masks = cover_masks.get(vp.id, [])
        groups.append({
            "id": vp.idx,
            "cell": vp.cell,
            "n": vp.n_frames,
            "tag": vp.tag,
            "span_sec": round(vp.span_sec or 0, 1),
            "sharpest": vp.sharpest_frame.name if vp.sharpest_frame else None,
            "cover_rel": cover_rel,
            "cover_size": size,
            "masks": masks,
            "has_stack": st is not None,
            "missing": det is None,
            "elsewhere": elsewhere.get(vp.id, []),
            "n_detected": n_kept.get(vp.id, 0) if det else None,
            "reviewed": False,      # 3단계
        })

    return {
        "missing_groups": sum(1 for g in groups if g["missing"]),
        "missing_elsewhere": sum(1 for g in groups if g["missing"] and g["elsewhere"]),
        "review_batch": review_batch_label(),
        "slug": slug,
        "label": slide.name,
        "corr_thresh": slide.corr_thresh,
        "site": (slide.site.region or slide.site.name
                 or slide.site.code) if slide.site else "",
        "site_code": slide.site.code if slide.site else "",
        "core": slide.locality.code if slide.locality else "",
        "sample_code": slide.sample.code if slide.sample_id else "",
        "depth_cm": slide.depth_cm,
        "um_per_pixel": scales_by_slide().get(slide.slug),
        "groups": groups,
        **_obs(slide),
        **_slide_summary(slide),
    }


def _frames(vp: Viewpoint) -> list[dict]:
    """프레임 목록 — 이름·선명도·촬영/반입 시각·경로."""
    frames = list(vp.frames.all())
    values = [f.sharpness for f in frames if f.sharpness is not None]
    top = max(values) if values else 0
    return [{
        "name": f.name,
        "acquired_at": f.acquired_at,
        "created_at": f.created_at,
        "sharpness": f.sharpness,
        "sharp_pct": (round(100 * f.sharpness / top) if f.sharpness and top else 0),
        "is_sharpest": f.is_sharpest,
        "seq": f.seq,
        "rel": f.path,
        "um_per_pixel": f.um_per_pixel,
        "um_per_pixel_source": f.um_per_pixel_source,
        "exists": (Path(settings.DATA_ROOT) / f.path).exists(),
    } for f in frames]


def group_photos(slug: str, gid: int) -> dict | None:
    """시야 하나의 사진 — 합성본·깊이맵·프레임. **1단계의 시야 화면이다.**

    DiaRUGA 의 `group_detail`(검출·교정까지 실은 검토 화면)은 3단계에서 온다.
    그때 이 함수와 `group.html` 은 그쪽으로 갈아 끼운다.
    """
    slide = Slide.objects.filter(slug=slug).first()
    if slide is None:
        return None
    vp = (Viewpoint.objects.filter(slide=slide, idx=gid)
          .select_related("sharpest_frame", "stack").prefetch_related("frames")
          .first())
    if vp is None:
        return None
    st = getattr(vp, "stack", None)
    idxs = list(Viewpoint.objects.filter(slide=slide)
                .order_by("idx").values_list("idx", flat=True))
    i = idxs.index(vp.idx)
    return {
        "slug": slug, "label": slide.name, "gid": vp.idx, "cell": vp.cell,
        "tag": vp.tag, "n": vp.n_frames, "span_sec": vp.span_sec,
        "prev": idxs[i - 1] if i > 0 else None,
        "next": idxs[i + 1] if i + 1 < len(idxs) else None,
        "pos": i + 1, "total": len(idxs),
        "stack": ({
            "focused_rel": st.focused_path,
            "depth_rel": st.depth_path or None,
            "um_per_pixel": st.um_per_pixel,
            "um_per_pixel_source": st.um_per_pixel_source,
            "align_failed": st.align_failed,
            "gain": st.gain,
            "ref": st.ref_frame.name if st.ref_frame else None,
        } if st else None),
        "frames": _frames(vp),
        # 검출 — 대표 이미지(합성본)의 것. 화면이 폴리곤을 SVG 로 얹는다.
        "detection": detection_for_viewpoint(vp),
        "review_batch": review_batch_label(),
        "elsewhere": batches_elsewhere(vp=vp).get(vp.id, []),
    }


# --- 지점 --------------------------------------------------------------------
def locality_detail(site_code: str, loc_code: str,
                    with_hidden: bool = False) -> dict | None:
    """지점 하나 — 깊이 방향으로 본 시료·관찰. DiaRUGA `locality_detail` 의 골자만.

    그쪽의 코어 자료 곡선(P17 · `CoreSeries`)·노두 사진은 5단계 / 안 온다.
    """
    from .models import Locality
    loc = (Locality.objects.select_related("site")
           .filter(site__code=site_code, code=loc_code).first())
    if loc is None:
        return None
    scales = scales_by_slide()
    rows, n_hidden = [], 0
    for sm in loc.samples.order_by("depth_cm", "code").prefetch_related("slides"):
        slides = sorted(sm.slides.all(), key=_slide_order)
        for sl in slides:
            if sl.hide_in_list and not with_hidden:
                n_hidden += 1
                continue
            rows.append({
                "sample_code": sm.code, "depth_cm": sm.depth_cm,
                "dry_weight_g": sm.dry_weight_g,
                "slug": sl.slug, "label": sl.name, "state": sl.state,
                "um_per_pixel": scales.get(sl.slug),
                **_obs(sl), **_slide_summary(sl),
            })
        if not slides:
            rows.append({"sample_code": sm.code, "depth_cm": sm.depth_cm,
                         "dry_weight_g": sm.dry_weight_g, "slug": None})
    return {
        "site": loc.site, "loc": loc,
        "title": f"{loc.site.code}-{loc.code}",
        "rows": rows, "n_hidden": n_hidden,
        "n_samples": loc.samples.count(),
        "n_slides": Slide.objects.filter(sample__locality=loc).count(),
        "totals": datasets_total([r for r in rows if r.get("slug")]),
    }


# --- 파이프라인 상태 (시스템 설정 · 파이프라인) ------------------------------
def pipeline_status() -> dict:
    """파이프라인이 지금 어떤 상태인가 (DiaRUGA 098).

    셋을 모은다 — 정찰(`logs/last_scan.json` 의 나이) · 실행(`Run` 최근 것) ·
    밀린 슬라이드(`done` 이 아닌 것 전부와 각각의 진행). 해석은 화면이 먼저
    한다(경고 줄).
    """
    now = timezone.now()

    scan = {"exists": False, "age_min": None, "slides": [], "error": ""}
    scan_path = Path(settings.DATA_ROOT) / "logs" / "last_scan.json"
    try:
        st = scan_path.stat()
        scan["exists"] = True
        scan["age_min"] = round((now.timestamp() - st.st_mtime) / 60, 1)
        d = json.loads(scan_path.read_text(encoding="utf-8"))
        scan["slides"] = [{"rel": r.get("rel", ""), "state": r.get("state", ""),
                          "jpgs": r.get("jpgs", 0),
                          "stable_min": round(r.get("stable_min") or 0, 1)}
                         for r in d.get("slides", [])]
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        scan["error"] = f"{type(e).__name__}: {e}"

    runs = []
    for r in (Run.objects.select_related("slide", "batch")
              .order_by("-started_at")[:12]):
        dur = None
        if r.finished_at:
            dur = round((r.finished_at - r.started_at).total_seconds() / 60, 1)
        runs.append({
            "kind": r.kind, "status": r.status,
            "slide": r.slide.slug if r.slide else "",
            "batch": r.batch.label if r.batch else "",
            "started_at": r.started_at, "finished_at": r.finished_at,
            "minutes": dur,
            "error": (r.error or "")[:200],
        })

    busy = []
    for sl in Slide.objects.exclude(state="done").order_by("pk"):
        vps = Viewpoint.objects.filter(slide=sl).count()
        busy.append({
            "slug": sl.slug, "name": sl.name, "state": sl.state,
            "note": sl.state_note or "",
            "n_frames": Frame.objects.filter(slide=sl).count(),
            "n_vps": vps,
            "n_stacks": Stack.objects.filter(viewpoint__slide=sl).count(),
            "n_det_vps": (Detection.objects.filter(viewpoint__slide=sl, is_current=True)
                          .values("viewpoint_id").distinct().count()),
            "discovered_at": sl.discovered_at, "copied_at": sl.copied_at,
        })

    last_done = (Run.objects.exclude(finished_at=None)
                 .order_by("-finished_at").first())

    warnings = []
    running = [r for r in runs if r["status"] == "running"]
    if (scan["exists"] and scan["age_min"] is not None
            and scan["age_min"] > 5 and not running):
        warnings.append(f"정찰이 {scan['age_min']:.0f}분째 없다 — 폴러가 멈춰 "
                        "있을 수 있다 (cron 은 1분마다 돈다)")
    if busy and not running:
        newest = last_done.finished_at if last_done else None
        idle_min = ((now - newest).total_seconds() / 60) if newest else None
        if idle_min is None or idle_min > 15:
            warnings.append(
                f"끝나지 않은 슬라이드가 {len(busy)}개 있는데 도는 실행이 없다 — "
                "폴러가 데리러 오지 않는 상태일 수 있다 (DiaRUGA 097 이 그 모양이었다)")

    return {"scan": scan, "runs": runs, "busy": busy,
            "last_done": last_done, "warnings": warnings, "now": now}
