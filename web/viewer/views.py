"""뷰. DiaRUGA v0.29.0 `web/viewer/views.py`(3,120줄)에서 왔다 — 1단계: 목록 ·
시야 목록 · 시야 사진 · 지점 · 정보 편집 · 시스템 설정(자료·파이프라인) · `/img` ·
`/healthz`. 2단계: 검출 갤러리(`crops`) · 계측 표(`detections`) · `/crop` · 문턱
조정 · 시스템 설정(운영). 나머지 화면은 단계마다 온다 (P01 4절).
"""
import hashlib
import json
import math
import os
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import (FileResponse, Http404, HttpResponseBadRequest,
                         JsonResponse)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import antarctica, data, manage_data, ross, thresholds as th
from .models import (Detection, Frame, Locality, Run, Sample, Site, Slide,
                     Stack, ThresholdSet, Viewpoint)

# **뷰어가 저장소의 스크립트 둘을 함께 쓴다** (DiaRUGA 100) — `pipeline/judge.py`
# (판정 규칙: 뷰어와 파이프라인이 **같은 것**을 봐야 한다) · `ops/db_sentinel.py`
# (무결성 깃발: `/healthz` 가 읽는다). 저장소 뿌리는 `web/viewer` 의 두 단계 위다.
# **컨테이너 안에서도 같다** — 이미지가 저장소를 통째로 `/app` 에 담는다.
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "pipeline"))
sys.path.insert(0, str(_ROOT / "ops"))
import judge  # noqa: E402
import db_sentinel  # noqa: E402

# 문턱 조정 화면에 보여줄 순서와 이름 — (칸, 이름, 설명, 최소, 최대, 눈금)
THRESHOLD_FIELDS = [
    ("conf_min", "확신도 하한", "검출기 conf — 낮으면 티끌까지 들어온다", 0, 1, 0.01),
    ("min_um", "크기 하한", "타원 장축 µm — 픽킹 분획과 맞춘다", 0, 500, 1),
    ("max_um", "크기 상한", "타원 장축 µm", 100, 5000, 10),
]

# `/healthz` 가 세는 테이블. 단계마다 늘어난다.
HEALTH_TABLES = [("site", Site), ("locality", Locality),
                 ("sample", Sample), ("slide", Slide),
                 ("viewpoint", Viewpoint), ("frame", Frame), ("stack", Stack),
                 ("detection", Detection)]


def _with_hidden(request) -> bool:
    """숨긴 슬라이드를 보이는가. `?hidden=1`. **화면이 아니라 서버가 거른다** —
    표·카드·지도가 같은 목록을 봐야 한다. 합계는 이 값을 안 본다."""
    return request.GET.get("hidden") == "1"


def _num(raw, cast=float):
    """빈 칸은 None 으로. 잘못된 값은 예외를 올려 보낸다 — 조용히 0 이 되면 안 된다."""
    raw = (raw or "").strip()
    if not raw:
        return None
    return cast(raw)


# --- 목록 --------------------------------------------------------------------
def index(request):
    area = data.area_tabs(request.GET.get("area"))
    with_hidden = _with_hidden(request)
    rows = data.datasets(area["selected"])
    shown = [r for r in rows if with_hidden or not r["hidden"]]
    # **"전체" 에는 지도가 없다** — 권역이 늘면 투영이 섞이므로 DiaRUGA 와 같은
    # 규칙을 처음부터 둔다. 지금은 남극 하나라 `전체` 만 지도가 없다.
    show_map = not area["is_all"]
    return render(request, "viewer/index.html", {
        "datasets": shown,
        "groups": data.datasets_by_locality(rows, with_hidden),
        "area": area,
        "show_map": show_map,
        "totals": data.datasets_total(rows),
        "with_hidden": with_hidden,
        "n_hidden": sum(1 for r in rows if r["hidden"]),
        # 표의 분류 열. 줄이 아니라 여기서 정한다 — 열 수가 줄마다 같아야 한다
        "count_classes": data.counted_classes(),
        # **표의 "검출" 칸이 어느 묶음의 것인가** (DiaRUGA 088)
        "review_batch": data.review_batch_label(),
        "antmap": (_map_ctx(data.map_points(area["selected"], with_hidden))
                   if show_map else None),
    })


def _map_ctx(pts: list) -> dict:
    """남극 지도 상수. 로스해 확대 상태에서 오른쪽 나무가 틀 안의 것만 내도록
    (DiaRUGA 203) 지점·지역에 틀 밖 표시를 적는다 — 판정은 `ross.in_frame` 하나다."""
    for q in pts:
        for c in q["cores"]:
            c["ross_out"] = not ross.in_frame(c["x"], c["y"])
        q["ross_out"] = all(c["ross_out"] for c in q["cores"]) if q["cores"] else True
    return {
        "points": pts, "any_approx": any(not q["exact"] for q in pts),
        "kind": "ant",
        "land": antarctica.LAND,
        "boundary": antarctica.BOUNDARY_KM,
        "lat_circles": antarctica.LAT_CIRCLES,
        "boundary_label": antarctica.BOUNDARY_LABEL,
        "lon_labels": antarctica.LON_LABELS,
        "lon_spokes": antarctica.LON_SPOKES,
        "sea_labels": antarctica.SEA_LABELS,
        "ross": ross.context(pts),
    }


# --- 시야 목록 · 시야 --------------------------------------------------------
def dataset(request, slug):
    ctx = data.dataset_detail(slug)
    if ctx is None:
        raise Http404(f"unknown dataset: {slug}")
    return render(request, "viewer/dataset.html", ctx)


def group(request, slug, gid):
    """시야 하나의 사진. **1단계 화면이다** — 검토 화면(DiaRUGA `group.html`)은
    3단계에서 이 주소를 그대로 물려받는다."""
    ctx = data.group_photos(slug, gid)
    if ctx is None:
        raise Http404(f"unknown viewpoint: {slug}/g{gid}")
    return render(request, "viewer/group.html", ctx)


# --- 지점 --------------------------------------------------------------------
def core_page(request, site_code, core_code):
    """지점 하나 — 깊이 방향으로 본 화면. 속성은 여기서 안 고친다 (`/d/<slug>/edit/`)."""
    with_hidden = _with_hidden(request)
    ctx = data.locality_detail(site_code, core_code, with_hidden)
    if ctx is None:
        raise Http404(f"unknown locality: {site_code}/{core_code}")
    return render(request, "viewer/core.html", {**ctx, "with_hidden": with_hidden})


def core_redirect(request, site_code, core_code):
    """옛 `/core/…` 주소를 `/loc/…` 로 (DiaRUGA 와 같은 주소 모양). 302 다."""
    return redirect("core", site_code=site_code, core_code=core_code)


# --- 정보 편집 -----------------------------------------------------------------
def dataset_edit(request, slug):
    """관찰·시료·지점·지역의 속성을 사람이 채우는 화면. 층 넷을 한 폼으로 낸다.
    DiaRUGA `dataset_edit` 에서 노두 갈래를 빼고 분획·분할·칸 수·건시료 무게를 더했다.

    **위 세 층은 여러 관찰이 공유한다.** 몇 개가 함께 바뀌는지 미리 알린다.
    **소속이 없으면 붙이거나 만든다** — 지역이 없는 관찰은 어느 권역 탭에도 안 나온다.
    """
    slide = (Slide.objects.filter(slug=slug)
             .select_related("sample__locality__site").first())
    if slide is None:
        raise Http404(f"unknown dataset: {slug}")
    sample = slide.sample
    loc = sample.locality if sample else None
    site = loc.site if loc else None

    # 이 관찰이 처음부터 들고 있던 소속인가. **폼의 시료·지점·지역 칸을 저장에
    # 쓸지 말지가 여기서 갈린다** — 소속이 없던 관찰의 탭은 빈 칸이고, 그 빈
    # 칸을 남의 행에 그대로 쓰면 이미 채워 둔 좌표·수심이 지워진다 (DiaRUGA 063).
    had = {"sample": sample is not None, "loc": loc is not None,
           "site": site is not None}

    errors, saved, messages_made = [], False, ""
    if request.method == "POST":
        p = request.POST
        made = {"sample": False, "loc": False, "site": False}

        # 1) 이미 있는 시료에 그대로 붙이는 길
        attach = (p.get("attach_sample") or "").strip()
        if sample is None and attach:
            sample = (Sample.objects.select_related("locality__site")
                      .filter(pk=attach).first())
            if sample is None:
                errors.append("고른 시료를 찾지 못했습니다.")
            else:
                loc, site = sample.locality, sample.locality.site

        # 2) 코드를 적어 새로 만드는 길. 같은 코드가 이미 있으면 그것에 붙인다
        site_code = (p.get("site_code") or "").strip()
        loc_code = (p.get("core_code") or "").strip()
        sample_code = (p.get("sample_code") or "").strip()
        if sample is None and site is None and site_code:
            site = Site.objects.filter(code=site_code).first()
            if site is None:
                site, made["site"] = Site(code=site_code), True
        if sample is None and loc is None and loc_code and site is not None:
            loc = (Locality.objects.filter(site=site, code=loc_code).first()
                   if site.pk else None)
            if loc is None:
                loc, made["loc"] = Locality(site=site, code=loc_code), True
        if sample is None and sample_code and loc is not None:
            sample = (Sample.objects.filter(locality=loc, code=sample_code)
                      .first() if loc.pk else None)
            if sample is None:
                sample = Sample(locality=loc, code=sample_code)
                made["sample"] = True

        # 아무것도 안 한 저장이 성공으로 보이면 사람이 같은 일을 다시 한다
        if sample is None and (sample_code or loc_code) and not attach:
            errors.append(
                "시료를 붙이려면 지역·지점·시료 코드를 모두 채워야 합니다 — "
                "위의 기존 시료에 붙이기 에서 고르는 쪽이 안전합니다.")

        own = {k: had[k] or made[k] for k in had}
        try:
            slide.name = (p.get("slide_name") or slide.name).strip()
            slide.description = (p.get("description") or "").strip()
            # 관찰 이름표. **`obs_no` 는 여기서 못 고친다** — 폴더 접미사가
            # 정하는 자동값이라 다음 반입에 덮인다
            slide.obs_label = (p.get("obs_label") or "").strip()[:10]
            # 분획·분할·격자 칸 수 — 관찰의 것 (models.Slide 머리말)
            slide.fraction_um = _num(p.get("fraction_um"))
            slide.split_denom = _num(p.get("split_denom"), int)
            slide.cells = _num(p.get("cells"), int)
            # 체크박스는 안 켜면 아무것도 안 보낸다 — 없는 것이 곧 꺼짐이다
            slide.hide_in_list = bool(p.get("hide_in_list"))
            slide.exclude_from_totals = bool(p.get("exclude_from_totals"))

            # **붙이기만 할 때는 위 세 층의 칸을 안 쓴다** (`own`)
            if sample and own["sample"]:
                sample.code = (p.get("sample_code") or sample.code).strip()
                sample.note = (p.get("sample_note") or "").strip()
                sample.depth_cm = _num(p.get("depth_cm"))
                sample.dry_weight_g = _num(p.get("dry_weight_g"))
            if loc and own["loc"]:
                loc.code = (p.get("core_code") or loc.code).strip()
                loc.collect_kind = (p.get("core_kind") or "").strip()
                loc.lat = _num(p.get("core_lat"))
                loc.lon = _num(p.get("core_lon"))
                loc.water_depth_m = _num(p.get("core_water_depth"))
                d = (p.get("core_collected_at") or "").strip()
                loc.collected_at = date.fromisoformat(d) if d else None
                loc.note = (p.get("core_note") or "").strip()
            if site and own["site"]:
                site.code = (p.get("site_code") or site.code).strip()
                site.name = (p.get("site_name") or "").strip()
                site.region = (p.get("site_region") or "").strip()
                a = (p.get("site_area") or "").strip()
                if a in dict(Site.AREA):
                    site.area = a
                site.lat = _num(p.get("site_lat"))
                site.lon = _num(p.get("site_lon"))
                site.note = (p.get("site_note") or "").strip()
        except ValueError as e:
            errors.append(f"값을 읽지 못했습니다: {e}")

        if not errors:
            try:
                attached = False
                with transaction.atomic():
                    if site and own["site"]:
                        site.save()
                    if loc and own["loc"]:
                        loc.site = site
                        loc.save()
                    if sample and own["sample"]:
                        sample.locality = loc
                        sample.save()
                    # **`pk` 는 저장한 뒤에야 있다.** 새로 만든 시료를 저장 전에
                    # `slide.sample_id != sample.pk` 로 견주면 `None != None` 이라
                    # 안 붙는다 — "새로 만들어 붙였습니다" 만 뜨고 관찰은 그대로
                    # 소속 없이 남았다 (ForGIA 1단계 시험이 잡았다)
                    if sample is not None and slide.sample_id != sample.pk:
                        slide.sample = sample
                        attached = True
                    slide.save()
                saved = True
                new = " · ".join(x for x in (
                    f"지역 {site.code}" if made["site"] else "",
                    f"지점 {loc.code}" if made["loc"] else "",
                    f"시료 {sample.code}" if made["sample"] else "") if x)
                if new:
                    messages_made = f"{new} 을(를) 새로 만들어 붙였습니다."
                elif attached:
                    messages_made = (f"{site.code} · {loc.code} · {sample.code} "
                                     f"시료에 붙였습니다.")
            except IntegrityError as e:
                errors.append(f"같은 코드가 이미 있습니다: {e}")

    sample_choices, sibling = [], None
    if sample is None:
        for sm in (Sample.objects.select_related("locality__site")
                   .order_by("locality__site__code", "locality__code",
                             "depth_cm", "code")):
            sample_choices.append({
                "pk": sm.pk,
                "site_code": sm.locality.site.code,
                "loc_code": sm.locality.code,
                "code": sm.code,
                "label": sm.locality.site.region or sm.locality.site.name or "",
                "n_slides": sm.slides.count(),
            })
        sibling = next((s for s in slide.sibling_observations() if s.sample_id),
                       None)

    scales = data.scales_by_slide()
    return render(request, "viewer/dataset_edit.html", {
        "slug": slug,
        "label": slide.name,
        "slide": slide,
        "sample": sample,
        "core": loc,
        "site": site,
        "sample_choices": sample_choices,
        "sibling": sibling,
        "sibling_sample_pk": sibling.sample_id if sibling else None,
        "core_code": loc.code if loc else "-",
        "site_code": site.code if site else "-",
        "sample_code": sample.code if sample else "-",
        "n_slides_sample": sample.slides.count() if sample and sample.pk else 0,
        "n_samples_loc": loc.samples.count() if loc and loc.pk else 0,
        "n_slides_site": (Slide.objects.filter(
            sample__locality__site=site).count() if site and site.pk else 0),
        "n_viewpoints": slide.viewpoints.count(),
        "n_frames": slide.frames.count(),
        "site_areas": Site.AREA,
        "um_per_pixel": scales.get(slug),
        "errors": errors,
        "saved": saved,
        "made": messages_made,
        "no_site": site is None,
        "no_core": loc is None,
        "no_sample": sample is None,
    })




# --- 검출 갤러리 · 계측 표 · 크롭 ---------------------------------------------------
DETECT_PER_PAGE = 300


def detections(request, slug):
    """슬라이드 전체의 검출 후보를 한 표로 모아 크기 분포를 본다."""
    label = data.slide_label(slug)
    if label is None:
        raise Http404(f"unknown dataset: {slug}")
    rows = data.candidate_rows(slug)
    rows.sort(key=lambda r: -(r["long_side_um"] or 0))
    # **요약은 전부를 기준으로 낸다** — 보고 있는 쪽만 세면 페이지마다 중앙값이 달라진다
    sizes = sorted(r["major_um"] or r["long_side_um"] or 0 for r in rows)
    summary = None
    if sizes:
        summary = {"n": len(sizes), "min": round(sizes[0], 1),
                   "median": round(sizes[len(sizes) // 2], 1),
                   "max": round(sizes[-1], 1),
                   "mean": round(sum(sizes) / len(sizes), 1)}
    total = len(rows)
    try:
        offset = max(0, int(request.GET.get("offset", 0)))
    except ValueError:
        offset = 0
    page = rows[offset:offset + DETECT_PER_PAGE]
    return render(request, "viewer/detections.html", {
        "slug": slug, "label": label, "rows": page, "summary": summary,
        "total": total, "review_batch": data.review_batch_label(),
        "shown_from": offset + 1 if page else 0, "shown_to": offset + len(page),
        "prev_url": (f"?offset={max(0, offset - DETECT_PER_PAGE)}" if offset else None),
        "next_url": (f"?offset={offset + DETECT_PER_PAGE}"
                     if offset + DETECT_PER_PAGE < total else None)})


CROPS_PER_PAGE = 500


def crops(request, slug):
    """검출된 개체만 잘라 썸네일로 늘어놓는다 — "이것들이 정말 유공충인가" 를 한 화면에서."""
    label = data.slide_label(slug)
    if label is None:
        raise Http404(f"unknown dataset: {slug}")
    cls = request.GET.get("cls") or ""
    gone = cls == "gone"
    rows = data.candidate_rows(slug, gone=gone)
    keys = {c["key"] for c in data.class_list()}
    if cls in keys:
        rows = [r for r in rows if r.get("cls") == cls]
    rows.sort(key=lambda r: -(r["long_side_um"] or 0))

    upright = request.GET.get("upright", "1") != "0"
    n_upright = 0
    for r in rows:
        geo = data.crop_geometry(r, rotate=upright)
        if not geo:
            continue
        r["rot"], r["out"] = geo["rot"], geo["out"]
        if geo["rot"]:
            n_upright += 1
        r["sb"] = data.scalebar_for(geo["out_w"], r.get("um_per_pixel") or 0)

    total = len(rows)
    try:
        offset = max(0, int(request.GET.get("offset", 0)))
    except ValueError:
        offset = 0
    page = rows[offset:offset + CROPS_PER_PAGE]

    def page_url(off):
        q = {"offset": off}
        if cls:
            q["cls"] = cls
        if not upright:
            q["upright"] = 0
        return f"?{urlencode(q)}"

    return render(request, "viewer/crops.html", {
        "slug": slug, "label": label, "rows": page,
        "review_batch": data.review_batch_label(),
        "classes": data.class_list(), "cls": cls, "gone": gone,
        "upright": upright, "n_upright": n_upright,
        "upright_url": f"?{urlencode({'cls': cls} if cls else {})}",
        "flat_url": f"?{urlencode(dict({'cls': cls} if cls else {}, upright=0))}",
        "total": total, "shown_from": offset + 1 if page else 0,
        "shown_to": offset + len(page), "per_page": CROPS_PER_PAGE,
        "prev_url": page_url(max(0, offset - CROPS_PER_PAGE)) if offset else None,
        "next_url": (page_url(offset + CROPS_PER_PAGE)
                     if offset + CROPS_PER_PAGE < total else None)})


def crop(request):
    """`?p=<상대경로>&b=<x,y,w,h>&w=<출력 폭>[&rot=&out=]` — 개체 하나만 잘라 낸다.
    개체가 수백이라 매번 자르면 갤러리가 못 쓸 정도로 느려지므로 축소본처럼 캐시한다."""
    path = data.safe_image_path(request.GET.get("p", ""))
    if path is None:
        raise Http404("image not found or outside allowed dirs")
    try:
        box = [int(round(float(v))) for v in request.GET.get("b", "").split(",")]
    except ValueError:
        return HttpResponseBadRequest("bad bbox")
    if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
        return HttpResponseBadRequest("bad bbox")
    try:
        width = max(32, min(int(request.GET.get("w", 200)), 512))
    except ValueError:
        return HttpResponseBadRequest("bad width")
    raw_pad = request.GET.get("pad")
    try:
        pad = None if raw_pad is None else max(0, min(int(raw_pad), 512))
    except ValueError:
        return HttpResponseBadRequest("bad pad")
    rot = request.GET.get("rot")
    size = request.GET.get("out")
    try:
        rot = None if rot is None else max(-180.0, min(float(rot), 180.0))
        if size is not None:
            ow, oh = (int(v) for v in size.split(","))
            if not (0 < ow <= 4096 and 0 < oh <= 4096):
                raise ValueError("out")
            size = (ow, oh)
    except ValueError:
        return HttpResponseBadRequest("bad rot/out")
    if rot is not None and size is not None:
        out = _upright_thumb(path, box, width, rot, size)
    else:
        out = _crop_thumb(path, box, width, pad)
    if out is None:
        raise Http404("cannot crop")
    return _jpeg(request, out)


def _upright_thumb(path, box, width, rot, size):
    """개체를 세워서 잘라 낸 축소본 — 한 번의 affine 으로 회전과 자르기를 함께.
    회전 규약은 `data.rotated_extent()` 와 같아야 한다."""
    from PIL import Image

    x, y, w, h = box
    ow, oh = size
    stat = path.stat()
    key = f"up|{path}|{stat.st_mtime_ns}|{x},{y},{w},{h}|{rot:.2f}|{ow}x{oh}|{width}"
    name = hashlib.sha1(key.encode()).hexdigest()[:20] + ".jpg"
    out = settings.THUMB_CACHE / name
    if out.exists():
        return out
    rad = math.radians(rot)
    cos, sin = math.cos(rad), math.sin(rad)
    scx, scy = x + w / 2.0, y + h / 2.0
    ocx, ocy = ow / 2.0, oh / 2.0
    a1, b1, d1, e1 = cos, sin, -sin, cos
    c1 = scx - (a1 * ocx + b1 * ocy)
    f1 = scy - (d1 * ocx + e1 * ocy)
    try:
        settings.THUMB_CACHE.mkdir(parents=True, exist_ok=True)
        with Image.open(path) as img:
            img = img.convert("RGB")
            piece = img.transform((ow, oh), Image.AFFINE, (a1, b1, c1, d1, e1, f1),
                                  resample=Image.BICUBIC)
            piece.thumbnail((width, width), Image.LANCZOS)
            tmp = out.with_suffix(".tmp")
            piece.save(tmp, "JPEG", quality=84)
            tmp.replace(out)
    except OSError:
        return None
    return out


def _crop_thumb(path, box, width, pad=None):
    from PIL import Image

    x, y, w, h = box
    stat = path.stat()
    key = f"crop|{path}|{stat.st_mtime_ns}|{x},{y},{w},{h}|{width}|{pad}"
    name = hashlib.sha1(key.encode()).hexdigest()[:20] + ".jpg"
    out = settings.THUMB_CACHE / name
    if out.exists():
        return out
    try:
        settings.THUMB_CACHE.mkdir(parents=True, exist_ok=True)
        with Image.open(path) as img:
            img = img.convert("RGB")
            if pad is None:
                pad = max(4, round(0.08 * max(w, h)))
            left, top = max(0, x - pad), max(0, y - pad)
            right, bottom = min(img.width, x + w + pad), min(img.height, y + h + pad)
            if right <= left or bottom <= top:
                return None
            piece = img.crop((left, top, right, bottom))
            piece.thumbnail((width, width), Image.LANCZOS)
            tmp = out.with_suffix(".tmp")
            piece.save(tmp, "JPEG", quality=84)
            tmp.replace(out)
    except OSError:
        return None
    return out


# --- 문턱 조정 ---------------------------------------------------------------------
def threshold_page(request, slug=None):
    """한 시야를 보며 정한 값이 시야 전부에 걸린다 — 영향받는 시야를 영향 큰 순으로."""
    label = data.slide_label(slug) if slug else None
    if slug and label is None:
        raise Http404(f"unknown dataset: {slug}")
    scales = data.scales_by_slide()
    return render(request, "viewer/thresholds.html", {
        "slug": slug or "", "label": label or "전체",
        "fields": THRESHOLD_FIELDS,
        "current": th.current_values(slug),
        "defaults": dict(judge.DEFAULTS),
        "presets": list(ThresholdSet.objects.values("id", "name", *judge.FIELDS)),
        "spread": th.threshold_spread(slug),
        "scale_mixed": len(set(scales.values())) > 1,
        "scale_list": " · ".join(f"{s} {v:g}" for s, v in sorted(scales.items())),
    })


@require_POST
def threshold_preview(request):
    """문턱을 받아 전체 영향과 시야별 뒤집힘을 돌려준다. 저장하지 않는다."""
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest("bad json")
    slug = payload.get("slug") or None
    try:
        values = th.clean_values(payload.get("values") or {}, th.current_values(slug))
    except ValueError as e:
        return HttpResponseBadRequest(f"bad threshold: {e}")
    pool = th.load_pool(slug)
    result = th.preview(values, pool)
    index = th.detection_index(slug)
    rows = []
    for did, r in result["per_det"].items():
        meta = index.get(did)
        if not meta:
            continue
        flips = len(r["added"]) + len(r["removed"])
        rows.append({**meta, "before": r["before"], "after": r["after"],
                     "added": r["added"], "removed": r["removed"], "flips": flips})
    rows.sort(key=lambda x: (-x["flips"], x["slug"], x["gid"]))
    touched = [r for r in rows if r["flips"]]
    only_touched = bool(payload.get("only_touched", True))
    limit = max(1, min(int(payload.get("limit") or 24), 200))
    if only_touched:
        shown = touched[:limit]
    else:
        head = touched[:max(1, limit // 2)]
        seen = {r["detection_id"] for r in head}
        rest = sorted((r for r in rows if r["detection_id"] not in seen),
                      key=lambda x: (x["slug"], x["gid"]))
        shown = head + rest[:limit - len(head)]
    verdicts = {r["detection_id"]: result["per_det"][r["detection_id"]]["verdict"]
                for r in shown}
    return JsonResponse({"ok": True, "values": values, "total": result["total"],
                         "classes": th.class_counts_from(result["per_det"]),
                         "rows": shown, "n_rows": len(rows), "n_touched": len(touched),
                         "only_touched": only_touched, "verdicts": verdicts})


@require_POST
def threshold_apply(request):
    """미리보기와 같은 판정을 실제로 저장한다."""
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest("bad json")
    slug = payload.get("slug") or None
    try:
        values = th.clean_values(payload.get("values") or {}, th.current_values(slug))
    except ValueError as e:
        return HttpResponseBadRequest(f"bad threshold: {e}")
    run = Run.objects.create(kind="refilter", status="running",
                             params={"values": values, "slide": slug or "*", "via": "viewer"})
    try:
        out = th.apply_values(values, slug, run)
    except Exception as e:                       # noqa: BLE001
        run.status = "failed"
        run.error = str(e)
        run.finished_at = timezone.now()
        run.save()
        raise
    run.status = "done"
    run.finished_at = timezone.now()
    run.save()
    return JsonResponse({"ok": True, "run": run.pk, **out})


def threshold_masks(request):
    """시야 하나의 폴리곤 — 문턱과 무관하니 한 번만 받고 판정만 갈아 끼운다."""
    try:
        det_id = int(request.GET.get("det", ""))
    except ValueError:
        return HttpResponseBadRequest("bad det")
    det = Detection.objects.reviewing().filter(pk=det_id).first()
    if det is None:
        raise Http404("unknown detection")
    masks = []
    for c in det.candidates.all():
        pts = data.mask_points({"polygon": c.polygon})
        if pts:
            masks.append({"k": c.mask_key, "p": pts})
    resp = JsonResponse({"ok": True, "masks": masks})
    resp["Cache-Control"] = "private, max-age=300"
    return resp


def threshold_history(request, slug=None):
    """문턱을 언제 무엇으로 바꿨나. 되돌리기의 근거."""
    runs = Run.objects.filter(kind="refilter", status="done").order_by("-started_at")[:50]
    rows = []
    for r in runs:
        v = (r.params or {}).get("values") or (r.params or {}).get("overrides") or {}
        changed = {k: val for k, val in v.items()
                   if abs(float(val) - judge.DEFAULTS.get(k, val)) > 1e-9}
        rows.append({"id": r.pk, "at": r.started_at.strftime("%m-%d %H:%M"),
                     "slide": (r.params or {}).get("slide", "*"),
                     "via": (r.params or {}).get("via", "cli"),
                     "values": v, "changed": changed, "counts": r.counts or {}})
    return JsonResponse({"ok": True, "rows": rows})


# --- 시스템 설정 · 운영 --------------------------------------------------------------
def system_settings_ops(request):
    """검토할 묶음과 조리법 (DiaRUGA 083). 자료 화면과 갈라 둔다 — 묻는 것이 다르다."""
    if request.method == "POST":
        p = request.POST
        act = (p.get("act") or "").strip()
        if act == "review_batch":
            ok, m = manage_data.set_review_batch(_num(p.get("batch"), int) or 0)
        elif act == "recipe":
            ok, m = manage_data.set_recipe(_num(p.get("batch"), int) or 0, p)
        elif act == "new_batch":
            ok, m = manage_data.create_batch(p)
        else:
            ok, m = False, "모르는 동작입니다."
        return redirect(f"{reverse('system_settings_ops')}?{'msg' if ok else 'err'}={m}")
    plan = [{**r, "args": " ".join(_recipe_args(r["recipe"]))}
            for r in data.batches_to_run()]
    return render(request, "viewer/system_settings_ops.html", {
        "msg": request.GET.get("msg", ""), "err": request.GET.get("err", ""),
        "batches": manage_data.batch_choices(),
        "batches_all": manage_data.batches_with_recipe(),
        "backends": manage_data.BACKENDS, "plan": plan,
    })


def _recipe_args(recipe: dict) -> list:
    """조리법을 명령줄 모양으로 — 화면이 보여줄 뿐 여기서 돌리지 않는다.
    `batch_plan.py` 와 같은 것을 내야 한다."""
    out = []
    for k, flag in (("backend", "--backend"), ("scale", "--scale"),
                    ("min_um", "--min-um"), ("max_um", "--max-um"),
                    ("conf_min", "--conf-min"),
                    ("weights", "--weights"), ("yolo_conf", "--yolo-conf"),
                    ("yolo_imgsz", "--yolo-imgsz")):
        if recipe.get(k) is not None:
            out += [flag, str(recipe[k])]
    if recipe.get("all_images"):
        out.append("--all-images")
    return out


# --- 시스템 설정 ---------------------------------------------------------------
def system_settings(request):
    """관리 화면 — 지역·지점·시료를 만들고 고치고 지운다. 소속도 여기서 옮긴다.
    **관찰은 여기서 안 만들고 안 지운다.** **쓰기는 전부 POST 다.**"""
    if request.method == "POST":
        p = request.POST
        act = (p.get("act") or "").strip()
        if act == "create":
            ok, m = manage_data.create((p.get("kind") or "").strip(), p)
        elif act == "delete":
            ok, m = manage_data.delete((p.get("kind") or "").strip(),
                                       _num(p.get("pk"), int) or 0)
        elif act == "move_slide":
            ok, m = manage_data.move_slide(_num(p.get("slide"), int) or 0,
                                           _num(p.get("sample"), int))
        elif act == "move_sample":
            ok, m = manage_data.move_sample(_num(p.get("sample"), int) or 0,
                                            _num(p.get("locality"), int) or 0)
        else:
            ok, m = False, "모르는 동작입니다."
        # POST 뒤에 redirect 한다 — 새로 고침이 같은 일을 다시 하면 안 된다
        return redirect(f"{reverse('system_settings')}?{'msg' if ok else 'err'}={m}")

    msg, err = request.GET.get("msg", ""), request.GET.get("err", "")
    ctx = manage_data.overview()
    # 지우기 문턱을 **미리** 계산해 행에 달아 둔다 — 눌러 보고 "지울 수 없습니다"
    # 를 만나면 무엇을 먼저 치워야 하는지 알 수 없다
    for kind, rows in (("site", ctx["sites"]), ("locality", ctx["localities"]),
                       ("sample", ctx["samples"])):
        for row in rows:
            row.block_why = " · ".join(manage_data.deletable(kind, row.pk)[1])
    return render(request, "viewer/system_settings.html", {
        **ctx, "site_areas": Site.AREA, "msg": msg, "err": err,
    })


def system_settings_pipeline(request):
    """시스템 설정 · 파이프라인 — 폴러가 살아 있는가, 무엇이 밀려 있는가 (DiaRUGA 098)."""
    return render(request, "viewer/system_settings_pipeline.html",
                  {"p": data.pipeline_status()})


def settings_redirect(request, tab=""):
    """옛 `/manage/…` 주소를 `/system-settings/…` 로 (DiaRUGA 와 같은 주소 모양). 302."""
    name = {"ops": "system_settings_ops",
            "pipeline": "system_settings_pipeline"}.get(tab, "system_settings")
    url = reverse(name)
    if request.META.get("QUERY_STRING"):
        url = f"{url}?{request.META['QUERY_STRING']}"
    return redirect(url)


# --- 이미지 --------------------------------------------------------------------
def image(request):
    """`?p=<DATA_ROOT 기준 상대경로>&w=<가로 픽셀>`. 폴더명에 공백이 있어서 경로를
    쿼리로 받는다. `w` 가 있으면 축소본을 만들어 캐시한다."""
    rel = request.GET.get("p", "")
    path = data.safe_image_path(rel)
    if path is None:
        raise Http404("image not found or outside allowed dirs")

    raw = request.GET.get("w")
    if not raw:
        return _jpeg(request, path)
    try:
        width = max(32, min(int(raw), 2048))
    except ValueError:
        raise Http404("bad width")
    thumb = _thumbnail(path, width)
    return _jpeg(request, thumb or path)


_CTYPE = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def _jpeg(request, path):
    """이미지 응답. `v=`(원본 mtime)가 붙은 주소면 영구 캐시를 허용한다 — 그림이
    바뀌면 주소가 바뀐다. 축소본은 늘 JPEG 이지만 원본은 PNG 일 수 있다."""
    ctype = _CTYPE.get(path.suffix.lower(), "image/jpeg")
    resp = FileResponse(path.open("rb"), content_type=ctype)
    if request.GET.get("v"):
        resp["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        resp["Cache-Control"] = "no-cache"
    return resp


def _thumbnail(path, width):
    """축소본 경로를 돌려준다. 원본 mtime 이 바뀌면 자동으로 다시 만든다."""
    from PIL import Image

    stat = path.stat()
    key = f"{path}|{stat.st_mtime_ns}|{width}"
    name = hashlib.sha1(key.encode()).hexdigest()[:20] + ".jpg"
    cache_dir = settings.THUMB_CACHE
    out = cache_dir / name
    if out.exists():
        return out
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(path) as img:
            img = img.convert("RGB")
            if img.width > width:
                height = round(img.height * width / img.width)
                img = img.resize((width, height), Image.LANCZOS)
            tmp = out.with_suffix(".tmp")
            img.save(tmp, "JPEG", quality=82)
            tmp.replace(out)
    except OSError:
        return None
    return out


# --- healthz -------------------------------------------------------------------
def healthz(request):
    """판·DB·안전망 상태를 한 번에 낸다 (DiaRUGA `/healthz` 와 같은 모양).

    | 상태 | 코드 | 뜻 |
    |---|---|---|
    | `ok` | 200 | 정상 |
    | `degraded` | **200** | 서비스는 되는데 손상이 감지됐다 (무결성 깃발 · 백업이 낡았다) |
    | `unhealthy` | 503 | DB 를 못 열거나 자료가 통째로 없다 |

    **`degraded` 를 503 으로 두면 안 된다.** `deploy.sh` 의 기동 게이트가 200 을
    기다린다. 배포를 막는 일은 `smoke.sh` 가 `status != ok` 로 한다.
    **가볍게 유지한다** — `count(*)` 몇 개와 `stat` 하나, 깃발 파일 읽기뿐이다.
    **`slide` 가 0 이면 unhealthy 다** — 마운트가 어긋나 빈 DB 가 새로 만들어져도
    "파일이 있는가" 검사는 통과하기 때문이다.
    """
    info = {"status": "ok", "version": os.environ.get("IMAGE_TAG", "")}
    notes = []

    try:
        info["db"] = {name: model.objects.count() for name, model in HEALTH_TABLES}
    except Exception as e:                       # noqa: BLE001 — 무엇이 나오든 죽지 않는다
        info["status"] = "unhealthy"
        info["db"] = None
        notes.append(f"DB 를 읽지 못했다: {e}")
    else:
        if info["db"]["slide"] == 0:
            info["status"] = "unhealthy"
            notes.append("슬라이드가 0 이다 — DB 마운트가 어긋났을 수 있다")

    # 백업·폴러가 세운 무결성 깃발. 파일을 읽기만 한다 (DiaRUGA 034·061).
    # **"백업 실패" 라고 적지 않는다** — 깃발을 세우는 주인이 백업만이 아니다
    flags = db_sentinel.read(settings.FORGIA_DB)
    info["integrity_flags"] = flags
    if flags and info["status"] == "ok":
        info["status"] = "degraded"
    for f in flags:
        notes.append(f"무결성 깃발 [{f['source']} {f['time']}] {f['reason']}")

    age_h = None
    try:
        snaps = list(settings.BACKUP_DIR.glob("ForGIA_*.db"))
        if snaps:
            newest = max(snaps, key=lambda p: p.stat().st_mtime)
            age_h = round((time.time() - newest.stat().st_mtime) / 3600, 1)
    except OSError:
        pass
    info["backup"] = {"age_h": age_h, "max_age_h": settings.BACKUP_MAX_AGE_H}
    if settings.BACKUP_MAX_AGE_H and (age_h is None
                                      or age_h > settings.BACKUP_MAX_AGE_H):
        if info["status"] == "ok":
            info["status"] = "degraded"
        notes.append("백업 사본이 없다" if age_h is None else
                     f"백업이 낡았다 — 가장 새 사본이 {age_h} 시간 전 "
                     f"(문턱 {settings.BACKUP_MAX_AGE_H})")

    info["notes"] = notes
    code = 503 if info["status"] == "unhealthy" else 200
    resp = JsonResponse(info, status=code, json_dumps_params={"ensure_ascii": False})
    resp["Cache-Control"] = "no-store"
    return resp
