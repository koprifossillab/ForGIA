"""뷰. DiaRUGA v0.29.0 `web/viewer/views.py`(3,120줄)에서 왔다 — 1단계: 목록 ·
시야 목록 · 시야 사진 · 지점 · 정보 편집 · 시스템 설정(자료·파이프라인) · `/img` ·
`/healthz`. 2단계: 검출 갤러리(`crops`) · 계측 표(`detections`) · `/crop` · 문턱
조정 · 시스템 설정(운영). 나머지 화면은 단계마다 온다 (P01 4절).
"""
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import (FileResponse, Http404, HttpResponse, HttpResponseBadRequest,
                         JsonResponse)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import antarctica, data, manage_data, regroup, ross, thresholds as th
from django.db.models import Case, Count, When

from .models import (Candidate, Detection, ForamObject, Frame, Taxon,
                     Image as ImageModel, Locality, ObjectReview, Run, Sample,
                     Site, Slide, Stack, ThresholdSet, Viewpoint, ViewpointReview)

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
                 ("detection", Detection), ("objectreview", ObjectReview),
                 ("taxon", Taxon)]


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


def dataset(request, slug):
    ctx = data.dataset_detail(slug)
    if ctx is None:
        raise Http404(f"unknown dataset: {slug}")
    # 방금 무엇을 했는지. 바뀐 수를 주소에 실어 새로고침해도 남게 한다 —
    # POST 뒤에 redirect 하므로(뒤로 가기가 다시 쓰지 않게) 이 길밖에 없다.
    ctx["marked"] = request.GET.get("marked") or ""
    ctx["marked_n"] = request.GET.get("n") or ""
    return render(request, "viewer/dataset.html", ctx)


def group(request, slug, gid):
    """검토 화면. `?batch=<실행 번호>` 로 **엔진을 갈아 끼운다** (051).

    검출 엔진 고르기는 `/engine/` 이라는 다른 화면에만 있던 기능이다. 비교하려면
    화면을 나갔다 들어와야 했고, 그때마다 어느 시야를 보고 있었는지 잃었다. 이제
    검토 화면 안에서 고른다 — 시야는 그대로 두고 그림 위의 개체만 바뀐다.
    **그 화면은 075 에서 지웠다. 비교는 이 길 하나로만 한다.**

    **현재 검출(SAM2)일 때만 교정이 저장된다.** 다른 묶음을 고르면 읽기 전용이고
    화면 가운데 위에 그렇게 적힌다. 근거는 `data.group_detail` 머리말.
    """
    # 이 시야에 쌓인 묶음들. 검토 화면이 그리는 현재 검출도 그중 하나로 나온다.
    raw = (request.GET.get("batch") or "").strip()
    try:
        run_id = int(raw) if raw else None
    except ValueError:
        run_id = None

    bs = data.batches_for_viewpoint(slug, gid, run_id)
    picked = next((b for b in bs if b["on"]), None) if run_id else None
    # 모르는 묶음이거나, 지금 검토 화면이 이미 그리고 있는 그것이면 제 주소로
    # 돌려보낸다. **읽기 전용 화면으로 현재 검출을 보게 두면 안 된다** — 같은
    # 것을 교정만 뗀 채 보게 되고, 거기서 고친 것은 저장되지 않는다.
    if run_id is not None and (picked is None or picked["current"]):
        return redirect("group", slug=slug, gid=gid)

    ctx = data.group_detail(slug, gid, run_id)
    if ctx is None:
        raise Http404(f"unknown group: {slug}/{gid}")

    # **묶음이 아니라 엔진으로 고른다.** `yolo-1차`·`yolo-3차` 가 따로 서면
    # 무엇을 눌러야 할지 알 수 없다 — 재는 것은 엔진이다 (`engines_from_batches`).
    here = reverse("group", args=[slug, gid])
    engines = data.engines_from_batches(bs)
    for e in engines:
        # 현재 검출을 낸 엔진이 곧 검토 가능한 화면이다 — 맨 주소로 간다.
        e["url"] = here if e["current"] else f"{here}?batch={e['run_id']}"
        e["editable"] = e["current"]
    ctx["engines"] = engines
    # 아무 것도 안 켜져 있으면(=?batch= 가 없으면) 현재 검출을 보고 있는 것이다
    ctx["engine_now"] = (next((e for e in engines if e["on"]), None) if run_id
                         else next((e for e in engines if e["current"]), None))
    # 링크가 짚어 온 개체 (118). 읽기 전용 갈래(`?batch=`)에도 그대로 얹는다 —
    # 표시는 보는 일이지 고치는 일이 아니다.
    ctx["hl"] = _highlight_arg(request)
    # **오프라인 검토기를 여기서 꺼낸다** (사용자 2026-09-07). 지금 보고 있는
    # 시야가 기본 범위다 — 어디를 꺼낼지는 대개 지금 보는 자리에서 정한다.
    #
    # **자동 처리 중에는 안 놓는다** — 그때 꺼낸 파일로 검토해 봐야 반입이
    # 막힌다(`save_review` 가 409 로 물린다). 헛수고를 만들지 않는다.
    #
    # **다른 엔진을 보는 화면(`?batch=`)에도 안 놓는다.** 파일은 늘 **검토 대상
    # 묶음**으로 구워지므로(`review_bundle`), 지금 보고 있는 것과 다른 것이
    # 나온다 — 화면에서 고른 것과 손에 쥔 것이 다른 자리를 만들지 않는다(051).
    # 오프라인 검토기(`offline_pick_ctx`)는 5단계 — 그때까지 자리는 비워 둔다
    ctx["off"] = None
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
    # `gone` 은 사람이 지운 것(DiaRUGA), `rejected` 는 문턱이 떨어뜨린 것(ForGIA)
    gone = cls == "gone"
    rows = data.candidate_rows(slug, gone=gone, rejected=(cls == "rejected"))
    keys = {c["key"] for c in data.class_list()}
    if cls in keys:
        rows = [r for r in rows if r.get("cls") == cls]
    elif cls == "manual":
        rows = [r for r in rows if r.get("manual")]
    elif cls == "labeled":
        rows = [r for r in rows if r.get("cls_user")]
    elif cls == "noted":
        rows = [r for r in rows if r.get("note")]
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
        elif act == "batch_code":
            ok, m = manage_data.set_batch_code(_num(p.get("batch"), int) or 0, p.get("code"))
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
            "pipeline": "system_settings_pipeline",
            "taxa": "system_settings_taxa"}.get(tab, "system_settings")
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


# ─────────────────────────────────────────── 교정·동정 (3단계 · DiaRUGA 그대로)

# 파일명으로 그대로 쓰이므로 경로 성분이 될 수 있는 문자를 막는다.
SAFE_STEM = re.compile(r"^[A-Za-z0-9._-]+$")

# 메모 길이 상한. 사람이 손으로 적는 것이라 넉넉하면 충분하다.
NOTE_MAX = 500

def _note(v) -> str:
    """시야 코멘트를 정리한다. **문이 둘이라 여기 한 벌만 둔다** (180 B2).

    `{"only": "note"}` 와 옛 탭의 판 payload 가 같은 값을 보내는데, 규칙이
    갈라지면 어느 문으로 들어왔느냐에 따라 저장되는 글이 달라진다.
    """
    if not isinstance(v, str):
        return ""
    # 줄바꿈은 남기고 앞뒤 공백만 정리한다. 빈 메모는 저장하지 않는다.
    return v.replace("\r\n", "\n").strip()[:NOTE_MAX]

@require_POST
def mark_all(request, slug):
    """시야 전체를 검토 완료로 / 미검토로. **POST 로만 온다.**

    GET 으로 열어 두면 주소를 누르는 것만으로 508 시야의 판단이 뒤집힌다 —
    브라우저의 미리 가져오기나 링크 검사기도 그것을 누른다.

    `done` 깃발만 뒤집고 시야 코멘트·개체 교정은 안 건드린다
    (`data.mark_all_reviewed` 머리말).
    """
    want = (request.POST.get("act") or "").strip()
    if want not in ("done", "undone"):
        raise Http404("unknown act")

    # 자동 처리가 안 끝났으면 검토를 막는 규칙이 여기에도 걸린다 (P01 §1) —
    # 한 시야씩은 막아 놓고 전체 표시로는 뚫리면 막은 뜻이 없다.
    slide = Slide.objects.filter(slug=slug).first()
    if slide is None:
        raise Http404(f"unknown dataset: {slug}")
    if data.review_blocked(slide):
        return redirect(f"{reverse('dataset', args=[slug])}?marked=blocked")

    out = data.mark_all_reviewed(slug, done=(want == "done"))
    if out is None:
        raise Http404(f"unknown dataset: {slug}")
    return redirect(f"{reverse('dataset', args=[slug])}"
                    f"?marked={want}&n={out['changed']}")

def _highlight_arg(request):
    """링크가 짚어 온 개체 — `?obj=<mask_key>&img=<이미지>` (118).

    카탈로그·크롭·계측 표에서 사진을 누르면 그 시야로 오는데, **거기 개체가
    수십 개라 어느 것을 보고 눌렀는지 다시 찾아야 했다** (사용자 보고
    2026-08-13). 링크가 개체와 그 개체가 있는 판을 함께 나르고 화면이 그 자리를
    표시한다.

    **`img` 가 함께 가야 한다.** 시야 하나에 판이 여럿이고(합성본 + 프레임마다
    하나) 교정도 판마다 따로다 — 키만 보내면 화면은 지금 열린 판에서만 찾고,
    프레임에서 잡힌 개체는 "못 찾았다" 가 된다.

    **서버는 그 값이 실제로 있는지 안 본다.** 어느 판에 무엇이 있는지는 화면이
    이미 들고 있고(`shot_dets`), 못 찾으면 화면이 그렇게 적는다. 여기서 404 를
    내면 **적어 둔 링크 하나가 낡았다는 이유로 시야 전체를 못 열게 된다** —
    표시는 곁들이는 것이지 이 화면이 서는 조건이 아니다.
    """
    key = (request.GET.get("obj") or "").strip()
    # 64 는 `ReviewMark.mask_key` 의 폭이다. 그보다 긴 것은 이 DB 에 있을 수
    # 없는 값이라 찾을 것도 없다.
    if not key or len(key) > 64:
        return None
    img = (request.GET.get("img") or "").strip()
    return {"key": key, "image": img if img.isdigit() else ""}

@require_POST
def split_group(request, slug, gid):
    """시야를 프레임 경계에서 가른다. **두 걸음이다 — 미리보기 다음에 확인.**

    검토가 끝난 시야를 지우는 일이라(그 아래 검출·교정이 `CASCADE` 로 딸려 간다)
    한 번의 눌림으로 끝나면 안 된다. 첫 POST 는 **무엇이 사라지는지 보여 주기만**
    하고, `confirm=1` 이 실린 두 번째 POST 만 실제로 고친다.

    **읽기 전용(`?batch=`)에는 이 길이 없다.** 화면 조각을 `_detection.html`
    (읽기 전용과 공유한다)이 아니라 `group.html` 에만 두었고, 서버도 다시 막는다 —
    027 이 정확히 그 자리에서 났다: "읽기 전용" 이라 적어 놓고 CSS 로 버튼만
    감췄더니 한 번의 클릭이 교정 37건을 지웠다. **화면에서 감추는 것은 막는 것이
    아니다.**

    처리 중인 슬라이드도 막는다. 파이프라인이 쓰는 중에 시야를 지우면 SQLite 가
    잠기고(HANDOFF 3.8 — 그렇게 프레임 229장을 잃었다), 반쯤 처리된 상태 위에
    재분할을 얹으면 무엇이 옳은 상태인지 알 수 없게 된다.
    """
    slide = Slide.objects.filter(slug=slug).first()
    if slide is None:
        raise Http404(f"unknown dataset: {slug}")
    if data.review_blocked(slide):
        return HttpResponse("자동 처리가 끝나기 전에는 시야를 가를 수 없습니다.",
                            status=409)

    cuts = [c for c in request.POST.getlist("after") if c.strip()]
    ctx = {"slug": slug, "label": slide.name, "id": gid, "cuts": cuts,
           "back_url": reverse("group", args=[slug, gid])}

    if request.POST.get("confirm") == "1":
        try:
            r = regroup.apply_split(slide, cuts, source="viewer")
        except ValueError as e:
            ctx["preview"] = {"ok": False, "errors": [str(e)]}
            return render(request, "viewer/regroup_confirm.html", ctx,
                          status=400)
        # 방금 만든 첫 조각으로 보낸다 — 사람이 한 일을 눈으로 확인해야 한다.
        # 검출이 아직 없어 화면은 사진만 낸다 (stack.detection.preview_only).
        return redirect("group", slug=slug, gid=r["first_idx"])

    ctx["preview"] = regroup.preview(slide, cuts)
    return render(request, "viewer/regroup_confirm.html", ctx,
                  status=200 if ctx["preview"]["ok"] else 400)

CATALOG_PER_PAGE = 120

# 카드에 얹는 거르개. 크롭 화면(`crops`)과 같은 것을 쓰되 **동정 진행률**을
# 보는 둘을 더한다 — 이 화면에서 사람이 알고 싶은 것은 "어디까지 했나" 다.
#
# **`named`·`unnamed` 는 진행도와 같은 기준이다** (2026-08-11) — 종명·등급·자세가
# 다 차야 완료다. 둘이 다른 뜻이면 진행도가 "80%" 인데 "덜 된 것" 거르개가 빈
# 화면을 내는 상태가 생기고, 사람은 무엇을 믿어야 할지 모른다.
CATALOG_FILTERS = {
    "manual": ("수동 복구", lambda r: r.get("manual")),
    "labeled": ("사람 지정", lambda r: r.get("cls_user")),
    "noted": ("코멘트 있음", lambda r: r.get("note")),
    "named": ("동정 완료", data.catalog_done),
    "unnamed": ("덜 된 것", lambda r: not data.catalog_done(r)),
}

def _catalog_hl(rows, hl):
    """짚어 온 개체(`?obj=&img=`)의 행. 없으면 `None` (149).

    **열쇠가 `(mask_key, 이미지)` 둘이다** — 118 이 반대 방향에서 쓰는 것과
    같다. 시야 하나에 판이 여럿이고 교정도 판마다 따로라, 키만으로 짚으면
    다른 판의 같은 자리를 집는다.

    `img` 가 비어 있으면 키만으로 찾는다 — 검출이 없는 판에서 눌러 온 경우다.
    """
    if not hl:
        return None
    for r in rows:
        if r["key"] != hl["key"]:
            continue
        if hl["image"] and str(r.get("image_id")) != hl["image"]:
            continue
        return r
    return None

def catalog(request, slug):
    """개체 카탈로그 — 검출된 유공충 개체마다 카드 하나, 거기에 동정을 적는다.

    **검토 대상 묶음 하나만 따라간다** (사용자 방침 2026-08-10). 고르는 장치를
    두지 않는 이유는 `data.catalog_rows` 머리말에 있다 — 화면마다 다른 판을 보는
    상태가 051 이 난 자리다. 대신 **어느 엔진의 판인지 머리에 적는다.**
    """
    label = data.slide_label(slug)
    if label is None:
        raise Http404(f"unknown dataset: {slug}")

    rows = data.catalog_rows(slug)

    # **진행도에서 파편을 뺀다** (사용자 2026-08-11). 파편에는 등급·자세를 안
    # 매기므로 완료가 될 수 없고, 분모에 두면 진행률이 영영 100%에 못 닿는다 —
    # 실측으로 살아 있는 교정의 65%가 파편이다. **모수가 틀린 막대는 안 보는
    # 것만 못하다.** 분류가 없는 것은 남긴다(정하면 완형일 수 있다).
    scope = [r for r in rows if not data.is_fragment(r.get("cls"))]
    n_all = len(scope)
    n_named = sum(1 for r in scope if data.catalog_done(r))
    # 감춘 것이 몇 개인지 화면이 적는다 — **"없다" 와 "감췄다" 는 다르다.**
    # `catalog_rows` 를 다시 부르지 않는다: 한 판을 통째로 다시 만드는 함수다.
    n_frag = len(rows) - n_all

    # **지운 것은 따로 본다** (P16 5.1). 카탈로그에서 지울 수 있게 됐으니
    # 되살릴 자리도 같은 화면에 있어야 한다 — 섞어서 내지는 않는다.
    #
    # **진행도는 위에서 이미 냈다.** 여기서 갈아 끼우는 것은 화면에 놓을 카드
    # 뿐이라 `n_all`·`n_named`·`n_frag` 는 통과분 기준으로 남는다 — 분모가 지운
    # 것으로 바뀌면 "모수가 틀린 막대" 가 된다(파편에서 이미 겪은 자리다).
    show_gone = request.GET.get("gone") == "1"
    if show_gone:
        rows = data.catalog_rows(slug, gone=True)

    # **검토 화면이 짚어 온 개체** (149). `?obj=<mask_key>&img=<이미지>` 로 오고
    # 열쇠는 118 이 반대 방향에서 쓰는 것과 같다.
    #
    # **찾은 자리로 데려가는 것까지가 이 기능이다.** 카드가 거르개에 걸려
    # 있거나(파편은 기본으로 감춘다) 다른 쪽에 있으면 화면은 **아무 일도 안 한
    # 것처럼 보인다** — 눌러서 왔는데 늘 보던 첫 판이 뜨면 사람은 링크가 고장
    # 났다고 읽는다. 그래서 감춘 것을 펴고 그 쪽으로 넘기며, **그렇게 했다고
    # 적는다.**
    hl = _highlight_arg(request)
    hl_row = _catalog_hl(rows, hl)
    hl_say = ""
    if hl and hl_row is None and not show_gone:
        # **지운 개체일 수 있다.** 그쪽은 화면이 아예 달라서(P16 5.1) 안 찾아
        # 보면 "없다" 고 말하게 된다. `catalog_rows` 는 한 판을 통째로 다시
        # 만드는 함수라 **못 찾았을 때만** 부른다.
        gone_rows = data.catalog_rows(slug, gone=True)
        hl_row = _catalog_hl(gone_rows, hl)
        if hl_row is not None:
            rows, show_gone = gone_rows, True
            hl_say = "오검출로 지운 개체입니다 — 「지운 것」 을 열었습니다."
    if hl and hl_row is None:
        # **왜 없는지 말한다.** 표시가 없기만 하면 사람은 이 화면을 훑으며
        # 그 개체를 계속 찾는다 (118 이 반대쪽에서 겪은 자리).
        hl_say = ("짚어 온 개체의 카드가 이 카탈로그에 없습니다 — 카탈로그는 "
                  "시야마다 판 하나만 봅니다(프레임에만 있는 개체는 안 나옵니다).")

    cls = request.GET.get("cls") or ""
    # **파편은 기본으로 감춘다** (사용자 2026-08-11). 처음부터 다 내면 카드가
    # 파편으로 덮여 동정할 것이 안 보인다. 감추는 것은 화면일 뿐이라 진행도·
    # 개수는 그대로다.
    #
    # **파편 분류를 콕 집었으면 감추지 않는다** — 그 칩을 누르고 빈 화면을 보면
    # 사람은 자료가 없다고 읽는다. 눌러서 아무 일도 안 일어나는 화면을 만들지
    # 않는다.
    #
    # **지운 것을 보는 화면에서는 감추지 않는다** (P16). 파편 분류를 콕 집었을
    # 때와 같은 이유다 — 지운 것을 되살리러 온 사람에게 지운 것의 절반을 감추면
    # "지웠는데 없다" 가 된다. 실측으로 살아 있는 교정의 65%가 파편이다.
    show_frag = request.GET.get("frag") == "1" or show_gone
    # **짚어 온 개체가 파편이면 펴 준다.** 파편은 기본으로 감춰져 있어, 안 펴면
    # 눌러서 온 사람에게 빈 자리가 보인다 — 감춘 것과 없는 것을 화면이 갈라
    # 말하기로 한 그 규칙이 여기서도 걸린다.
    if hl_row is not None and data.is_fragment(hl_row.get("cls")):
        show_frag = True
    if not show_frag and not data.is_fragment(cls):
        rows = [r for r in rows if not data.is_fragment(r.get("cls"))]

    if cls in data.CLASSES:
        rows = [r for r in rows if r.get("cls") == cls]
    elif cls in CATALOG_FILTERS:
        rows = [r for r in rows if CATALOG_FILTERS[cls][1](r)]

    # **찾는 것은 번호·종명·코멘트 셋이다.** 번호를 통째로 붙여 넣는 쓰임이
    # 첫째이므로 부분 일치이고 대소문자를 안 가린다 (`catalog.parse` 와 같다).
    q = (request.GET.get("q") or "").strip()
    if q:
        low = q.lower()
        # **멤버 아무 번호나 맞춘다** (P18 "공개·인용"). 번호는 판정 하나의
        # 이름이라 한 개체에 번호가 여럿이고, **묶기 전에 적어 둔 번호가 묶은
        # 뒤에도 찾아져야 한다** — 논문에 적힌 것이 그 번호일 수 있다.
        # 카드가 내보이는 것은 앵커의 번호 하나뿐이라, 그것만 맞추면 나머지
        # 번호로 찾아온 사람에게 "없다" 고 답하게 된다.
        rows = [r for r in rows
                if low in r["catalog_no"].lower()
                or any(low in n.lower() for n in (r.get("member_nos") or []))
                or low in (r.get("species") or "").lower()
                or low in (r.get("note") or "").lower()]

    for r in rows:
        # 묶었으면 가장 큰 프레임을 그린다 — 번호는 그대로다 (`link_mains`).
        view = r.get("view") or r
        # **세워서 자른다** — 크롭 갤러리와 같다(`crops`). 방향이 통일돼야 형태를
        # 나란히 비교할 수 있고, 동정은 그 비교로 한다. 축 비율이 1에 가까우면
        # 안 돌린다(굳이 보간으로 흐릴 이유가 없다 — `crop_geometry`).
        geo = data.crop_geometry(view, rotate=True)
        r["src_rel"] = view.get("rel") or r["image_rel"]
        r["src_bbox"] = view.get("bbox_xywh") or r["bbox_xywh"]
        r["rot"], r["out"] = (geo["rot"], geo["out"]) if geo else (0, "")
        if geo:
            r["sb"] = data.scalebar_for(geo["out_w"], r.get("um_per_pixel") or 0)

    total = len(rows)
    try:
        offset = max(0, int(request.GET.get("offset", 0)))
    except ValueError:
        offset = 0
    # **그 카드가 있는 쪽을 연다.** 한 판에 120장이라 개체가 셋째 쪽에 있으면
    # 첫 판만 보고 "없다" 가 된다.
    #
    # **거르개까지 풀지는 않는다** — 사람이 걸어 둔 것을 링크가 걷으면 지금
    # 보고 있던 자리를 잃는다. 대신 걸렸다고 적는다.
    if hl_row is not None:
        # **값이 아니라 그 행 자체로 찾는다** — 행에 리스트가 매달려 있어
        # `==` 는 깊이 비교가 된다.
        idx = next((i for i, r in enumerate(rows) if r is hl_row), -1)
        if idx < 0:
            hl_row = None
            hl_say = ("짚어 온 개체가 지금 걸어 둔 거르개에 걸려 안 보입니다 — "
                      "거르개를 지우면 나옵니다.")
        else:
            offset = (idx // CATALOG_PER_PAGE) * CATALOG_PER_PAGE
    page = rows[offset:offset + CATALOG_PER_PAGE]

    def page_url(off):
        qd = {"offset": off}
        if cls:
            qd["cls"] = cls
        if q:
            qd["q"] = q
        # 다음 쪽으로 넘어가면서 파편이 도로 감춰지면 안 된다 — 사람은 켠 것이
        # 꺼진 줄 모르고 개체가 사라졌다고 읽는다.
        if show_frag:
            qd["frag"] = "1"
        # 지운 것을 보다가 다음 쪽으로 넘어가면 통과분으로 돌아가면 안 된다 —
        # 파편 체크박스와 같은 이유다.
        if show_gone:
            qd["gone"] = "1"
        return f"?{urlencode(qd)}"

    # **이미 적힌 종명이 도감의 어느 자리인가** (149). 카드에 `도판` 을 놓으려면
    # 이것이 있어야 하는데, **카드마다 물으면 한 판에 질의가 120번 난다** (105 —
    # 카탈로그가 이미 그렇게 느렸던 자리다). 한 번만 묻고 나눠 준다.
    #
    # **화면에 놓을 카드에만 건다.** 거른 뒤의 `rows` 전체에 걸면 안 보이는
    # 카드까지 묻는다.
    # 도감 연결(`atlas_for_names`)은 5단계 — 카드의 `도판` 단추는 그때까지 안 뜬다
    for r in page:
        r["atlas_hit"] = None

    # **왜 못 적는가를 화면이 말한다.** 잠가 놓고 이유를 안 적으면 사람이 같은
    # 일을 몇 번이고 다시 한다 (063).
    batch = data.review_batch_info()
    # **왜 이만큼만 나오는가를 말한다** (P18). 카드가 개체 단위가 되면서
    # **검토 안 한 시야는 카드가 없다** — 개체는 사람이 손대기 전까지 없고,
    # 완료를 누르면 그 시야의 남은 마스크가 전부 개체가 된다(`confirm_kept`).
    #
    # **조용히 줄면 자료가 사라진 것으로 읽힌다.** 파편을 감출 때 몇 개를
    # 감췄는지 적는 것과 같은 줄이다.
    n_vp = Viewpoint.objects.filter(slide__slug=slug).count()
    n_done = ViewpointReview.objects.filter(
        viewpoint__slide__slug=slug, done=True,
        batch_id=(batch or {}).get("id")).count() if batch else 0

    if batch is None:
        blocked = ("검토할 묶음이 정해져 있지 않아 개체가 하나도 안 보입니다 — "
                   "시스템 설정 · 운영에서 고르세요.")
    elif not batch["code"]:
        blocked = (f"묶음 \"{batch['label']}\" 의 카탈로그 코드가 비어 있어 "
                   f"번호를 만들 수 없습니다 — 시스템 설정 · 운영에서 채우세요.")
    else:
        blocked = data.review_blocked(
            Slide.objects.filter(slug=slug).first())

    return render(request, "viewer/catalog.html", {
        "slug": slug,
        "label": label,
        # **오프라인 동정기를 여기서 꺼낸다** (사용자 2026-09-07). 동정하는
        # 화면에서 동정 도구를 꺼낸다 — 다른 화면으로 가서 슬라이드를 다시
        # 고르게 하면 방금 보던 것과 다른 것을 꺼내는 자리가 생긴다.
        #
        # **자동 처리 중에는 안 놓는다** — 그때 꺼낸 파일로 동정해 봐야 반입이
        # 막힌다.
        # 오프라인 동정기는 5단계 — 자리만 둔다
        "off": None,
        "rows": page,
        "batch": batch,
        "review_batch": (batch or {}).get("label", ""),
        "blocked": blocked,
        "readonly": bool(blocked),
        "cls": cls,
        "filters": [{"key": k, "label": v[0]} for k, v in CATALOG_FILTERS.items()],
        "q": q,
        "show_frag": show_frag,
        "n_frag": n_frag,
        "show_gone": show_gone,
        # **등급·자세는 완형에만 매긴다.** 어느 분류가 완형인지를 화면이 알아야
        # 카드가 두 칸을 감추고, 유형을 파편으로 바꿀 때 물어볼 수 있다.
        # 서버는 `data.check_grade_pose` 가 다시 검사한다 — 화면에서 막는 것은
        # 막는 것이 아니다(063).
        "n_vp": n_vp,
        "n_done": n_done,
        "counted_keys": [r["key"] for r in data.counted_classes()],
        "grades": ForamObject.GRADE,
        "poses": ForamObject.POSE,
        "species_seen": data.species_seen(),
        # 짚어 온 개체 (149). 카드 하나에 표시가 붙고, 못 찾았으면 왜 없는지가
        # `hl_say` 에 있다 — 아무 말 없이 표시만 없으면 사람이 계속 찾는다.
        "hl_key": (hl_row or {}).get("key", ""),
        "hl_image": (hl_row or {}).get("image_id", "") or "",
        "hl_say": hl_say,
        "n_all": n_all,
        "n_named": n_named,
        "n_left": n_all - n_named,
        "pct": round(100 * n_named / n_all) if n_all else 0,
        "total": total,
        "shown_from": offset + 1 if page else 0,
        "shown_to": offset + len(page),
        "per_page": CATALOG_PER_PAGE,
        "prev_url": page_url(max(0, offset - CATALOG_PER_PAGE)) if offset else None,
        "next_url": (page_url(offset + CATALOG_PER_PAGE)
                     if offset + CATALOG_PER_PAGE < total else None),
    })

def _catalog_fields(src, *, with_note=True):
    """카드가 보낸 칸들을 고른다. **`None` 은 안 고친다, `""` 는 비운다.**

    둘을 같이 다루면 화면이 안 보내는 칸을 저장이 지운다 — `drawn` 과 같은 규칙.
    문자열이 아닌 값은 `ValueError` 다(부르는 쪽이 400 으로 낸다).

    **일괄에는 코멘트가 없다** (P16 3.3). 같은 글을 여러 개체에 붙이는 것은
    *이 개체을 두고 하는 말* (0036)이라는 그 칸의 뜻과 어긋난다.
    """
    def text(name, limit):
        v = src.get(name)
        if v is None:
            return None                      # **안 고친다** (빈 것과 다르다)
        if not isinstance(v, str):
            raise ValueError(name)
        return v[:limit]

    fields = {"species": text("species", data.SPECIES_MAX),
              "cls": text("cls", 32),
              # 값이 셋뿐이라 자르는 길이가 뜻이 없다 — 모르는 값은
              # `check_grade_pose` 가 `ValueError` 로 물린다(409).
              "grade": text("grade", 8),
              "pose": text("pose", 16)}
    if with_note:
        fields["note"] = text("note", NOTE_MAX)
    return fields

@require_POST
def save_catalog(request, slug):
    """카드 한 장을 저장한다. **개체 하나만 고친다** (`data.save_catalog_entry`).

    `/review` 처럼 범위를 갈아치우지 않으므로 017·027·053 계열의 사고가
    구조적으로 안 생긴다. 그래도 짚는 것은 그때와 같은 방식이다 —
    **`(slug, gid)` 로 시야를, id 로 이미지를** 짚고 서버가 다시 확인한다.

    문이 넷이다 (`act` · P16 5절). **주소를 늘리지 않는다** — 짚는 방식과 막는
    검사가 완전히 같아서, 주소로 가르면 같은 검사를 네 벌 적게 된다.

    | `act` | 무엇을 | 어느 층에 |
    |---|---|---|
    | `save`(기본) | 종명·유형·등급·자세·코멘트 | 개체 (`ForamObject`) |
    | `remove`·`restore` | 오검출로 지우기·되돌리기 | `(이미지, 묶음, mask_key)` |
    | `bulk` | 고른 카드들에 넷을 한 번에 | 개체 — 하나씩 저장한다 |

    **`remove` 를 `save` 와 한 payload 에 안 싣는다** (116). 층이 다른 것을 한
    요청으로 보내면 한쪽을 고르는 일이 다른 쪽까지 갈아치운다.
    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest("bad json")

    act = payload.get("act") or "save"
    if act == "bulk":
        return _save_catalog_bulk(slug, payload)
    if act not in ("save", "remove", "restore"):
        return HttpResponseBadRequest("bad act")

    try:
        gid = int(payload.get("gid"))
        image_id = int(payload.get("image"))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("bad gid/image")

    vp, why = data.find_viewpoint(slug=slug, gid=gid)
    if vp is None:
        return JsonResponse({"ok": False, "error": why}, status=409)

    blocked = data.review_blocked(vp.slide)
    if blocked:
        return JsonResponse({"ok": False, "error": blocked}, status=409)

    if act in ("remove", "restore"):
        try:
            saved = data.set_catalog_removed(vp, image_id, payload.get("key"),
                                             act == "remove")
        except ValueError as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=409)
        return JsonResponse({"ok": True, **saved})

    try:
        fields = _catalog_fields(payload)
    except ValueError:
        return HttpResponseBadRequest("bad field")

    try:
        saved = data.save_catalog_entry(vp, image_id, payload.get("key"),
                                        **fields)
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=409)
    return JsonResponse({"ok": True, **saved})

def _save_catalog_bulk(slug, payload):
    """고른 카드들에 같은 값을 넣는다 (P16 5.2).

        {"act": "bulk",
         "items": [{"gid": 3, "image": 12, "key": "10_10_50_50"}, …],
         "fields": {"cls": "diatom", "grade": "A"}}

    **한 트랜잭션으로 묶지 않는다.** 40장 중 하나가 409 면 나머지 39장의 저장까지
    되돌아가고, 사람은 무엇이 걸렸는지 모른 채 전부 다시 한다. **개체마다
    `save_catalog_entry` 를 부른다** — 저장하는 규칙이 둘이 되지 않는다.

    **결과를 항목마다 돌려준다.** "N장 중 M장 저장됨" 한 줄로 끝내면 화면이
    실패한 카드를 짚어 주지 못한다 (063 — 못 한 것은 오류로 말한다).
    """
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return HttpResponseBadRequest("no items")
    # 한 판에 놓이는 카드 수가 상한이다 — 화면에 없는 것을 고를 수는 없다.
    if len(items) > CATALOG_PER_PAGE:
        return HttpResponseBadRequest("too many items")

    try:
        fields = _catalog_fields(payload.get("fields") or {}, with_note=False)
    except ValueError:
        return HttpResponseBadRequest("bad field")
    if all(v is None for v in fields.values()):
        return HttpResponseBadRequest("no fields")

    results, n_ok = [], 0
    # **막는 검사는 슬라이드마다 한 번이다** (105 — 같은 값을 되묻지 말 것).
    blocked, seen = None, {}
    for it in items:
        if not isinstance(it, dict):
            results.append({"ok": False, "error": "항목이 객체가 아니다"})
            continue
        try:
            gid = int(it.get("gid"))
            image_id = int(it.get("image"))
        except (TypeError, ValueError):
            results.append({"ok": False, "error": "gid·image 가 없다"})
            continue
        here = {"gid": gid, "image": image_id, "key": it.get("key")}

        if gid not in seen:
            seen[gid] = data.find_viewpoint(slug=slug, gid=gid)
        vp, why = seen[gid]
        if vp is None:
            results.append({**here, "ok": False, "error": why})
            continue
        if blocked is None:
            blocked = data.review_blocked(vp.slide) or ""
        if blocked:
            return JsonResponse({"ok": False, "error": blocked}, status=409)

        try:
            saved = data.save_catalog_entry(vp, image_id, it.get("key"),
                                            **fields)
        except ValueError as e:
            results.append({**here, "ok": False, "error": str(e)})
            continue
        results.append({**here, "ok": True, **saved})
        n_ok += 1

    return JsonResponse({"ok": True, "n_ok": n_ok, "n": len(items),
                         "results": results})

def api_dataset(request, slug):
    ctx = data.dataset_detail(slug)
    if ctx is None:
        raise Http404(f"unknown dataset: {slug}")
    return JsonResponse(ctx)

@require_POST
def save_object_link(request, slug, gid):
    """같은 개체 묶음 하나를 저장하거나 푼다 (P11 2단계).

        {"act": "save",   "link_id": 3 또는 없음,
         "members": [{"image": 12, "mask_key": "10_10_50_50", "rep": true}, …]}
        {"act": "unlink", "link_id": 3}

    **`/review` 에 싣지 않는다.** 그 길은 "그 시야의 교정 전체를 갈아치운다"
    는 전제 위에 있고 두 번 사고 낸 자리다 — 전제가 다른 자료를 같은 길에
    실으면 세 번째가 된다. 여기는 묶음 하나 단위다.

    **서버가 다시 검사한다** (화면에서 막는 것은 막는 것이 아니다):
    이미지가 그 시야의 것인가 · 마스크가 실재하는가(검토 대상 묶음의 현재
    검출, 또는 사람이 그린 교정) · **지운 마스크가 아닌가** · 대표가 정확히
    하나인가 · 멤버가 둘 이상인가. 기하는 서버가 스스로 뜬다 — 화면이 보낸
    것을 믿지 않는다.
    """
    vp = (Viewpoint.objects.filter(slide__slug=slug, idx=gid)
          .select_related("slide").first())
    if vp is None:
        raise Http404(f"unknown viewpoint: {slug}/g{gid}")

    def bad(msg, status=400):
        return JsonResponse({"ok": False, "error": msg}, status=status)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return bad("JSON 이 아니다")

    act = payload.get("act") or "save"
    link_id = payload.get("link_id")

    if act == "unlink":
        link = ForamObject.objects.filter(pk=link_id, viewpoint=vp).first()
        if link is None:
            return bad("그 묶음이 이 시야에 없다", 404)
        # **개체를 지우지 않는다 — 가른다** (P12). `ObjectReview.foram_object`
        # 가 `CASCADE` 라 `link.delete()` 는 이제 **그 판들의 교정을 통째로
        # 지운다.** 풀기는 "이것들이 한 개체가 아니다" 라는 말이지 "이 판단들이
        # 없던 일" 이라는 말이 아니다.
        with transaction.atomic():
            rows = list(ObjectReview.objects.filter(foram_object=link)
                        .select_related("foram_object"))
            for row in rows[1:]:
                _split_off(row, link)
            if rows:
                # 남은 하나는 혼자이므로 자기가 대표다
                ObjectReview.objects.filter(pk=rows[0].pk).update(is_rep=True)
        return JsonResponse({"ok": True, "links": data.object_links_of(vp)})

    if act != "save":
        return bad(f"모르는 act: {act}")

    rb = data.review_batch_id()
    if rb is None:
        return bad("검토 대상 묶음이 정해져 있지 않다")

    # **묶으면서 분류·종명을 하나로 맞춘다** (사용자 요청 2026-08-10).
    #
    # 묶음은 "이 판들의 이것이 한 개체다" 라는 말이므로 분류도 종명도 하나여야
    # 하는데, 묶기 **전에** 판마다 따로 적어 둔 것이 서로 다를 수 있다. 화면이
    # 그것을 알리고 사람이 하나를 고르면 여기로 실려 온다.
    #
    # **안 보내면 안 건드린다** (`None`). `""` 는 **비운다**는 말이다 —
    # `save_catalog_entry` 와 같은 규칙이고, 둘을 같이 다루면 화면이 안 보낸
    # 칸을 저장이 지운다.
    unify_label = payload.get("label")
    if unify_label is not None:
        unify_label = str(unify_label)
        if unify_label and unify_label not in data.CLASSES:
            return bad(f"모르는 분류다: {unify_label}")
    unify_species = payload.get("species")
    if unify_species is not None:
        if not isinstance(unify_species, str):
            return bad("종명이 문자열이 아니다")
        unify_species = unify_species.strip()[:data.SPECIES_MAX]

    members = payload.get("members") or []
    if len(members) < 2:
        return bad("멤버가 둘 미만이다 — 혼자인 묶음은 뜻이 없다")
    reps = [m for m in members if m.get("rep")]
    if len(reps) != 1:
        return bad(f"대표가 {len(reps)}개다 — 정확히 하나여야 한다")

    # 이미지 → 시야 검증을 한 번에. 남의 시야 이미지는 화면에 안 그려지므로
    # 여기 걸리는 것은 화면 밖에서 만든 요청이다.
    img_ids = [m.get("image") for m in members]
    if len(set(img_ids)) != len(img_ids):
        return bad("같은 이미지의 멤버가 둘이다")
    # `ImageModel` 이다 — 이 파일은 PIL 의 `Image` 를 함수 안에서 따로
    # 임포트한다. 같은 이름을 쓰면 어느 쪽인지 읽는 사람이 매번 따져야 한다.
    imgs = {i.pk: i for i in ImageModel.objects.filter(pk__in=img_ids)}
    resolved = []
    for m in members:
        img = imgs.get(m.get("image"))
        key = m.get("mask_key") or ""
        if img is None or img.viewpoint_id != vp.pk:
            return bad(f"이미지 {m.get('image')} 가 이 시야의 것이 아니다")
        # 마스크가 실재하는가 — 검토 대상 묶음의 현재 검출에서 찾고, 없으면
        # 사람이 그린 교정에서 찾는다. 기하도 여기서 뜬다.
        cand = (Candidate.objects
                .filter(detection__image=img, detection__is_current=True,
                        detection__run__batch_id=rb, mask_key=key)
                .first())
        if cand is not None:
            # **지운 마스크는 못 묶는다.** 사람이 오검출로 지운 것을 묶으면
            # "이 개체는 오검출이면서 실재한다" 가 된다.
            gone = ObjectReview.objects.filter(
                image=img, batch_id=rb, mask_key=key, removed=True).exists()
            if gone:
                return bad(f"{key} 는 오검출로 지운 마스크다")
            # **칸 이름은 `bbox` 다** (`ObjectReview.geom` · 2026-09-03). 여기서
            # `bbox_xywh` 로 적으면 그 행을 읽는 쪽이 전부 못 읽는다 —
            # `_orphan_dict` 는 그리지 않고(그러면 다음 저장이 그 행을 지운다),
            # 화면의 `addDrawn` 은 상자를 `[0,0,1,1]` 로 놓는다. 실제로 앉힌
            # 마스크 하나가 그렇게 앉았다(rs23 g11 · 2026-09-03).
            geom = {"bbox": [cand.bbox_x, cand.bbox_y,
                             cand.bbox_w, cand.bbox_h],
                    "polygon": cand.polygon}
            # **문턱에서 떨어진 후보도 묶을 수 있다** (102). 그 프레임에서 가장
            # 좋은 마스크가 탈락해 있는 일이 실제로 있다 — 초점이 흐려 텍스처가
            # 모자란 판이 그렇다. 사람이 "이것이 그 개체다" 라고 하면 그것은
            # **검출이 맞다는 판단이기도 하므로 되살린다**(탈락 펼침판을 눌러
            # 되살리는 것과 같은 뜻이다).
            #
            # **서버가 한다.** 화면에서 하려면 그 판(이미지)에 대고 `/review` 를
            # 따로 보내야 하는데, 그 길은 "그 이미지의 교정 전체를 갈아치운다"
            # 는 전제라 다른 판의 상태를 실어 보내는 사고가 난다(027·053 계열).
            # 여기서는 행 하나만 좁게 세운다.
            # **후보를 들고 간다.** 판정 행을 새로 세울 때 이것을 안 넘기면
            # `bind_method="orphan"` · `candidate=NULL` 로 앉는다 — 짝이
            # 멀쩡히 있는데 "재검출로 짝을 잃은 교정" 으로 기록되는 것이고,
            # `check_db` 3번의 바인딩 집계와 `/orphans/`·`rebind` 가 그 값을
            # 본다. 실제로 그렇게 앉았다 (테스트 인스턴스 2026-08-11).
            resolved.append((img, rb, key, bool(m.get("rep")), geom,
                             not cand.passed, cand))
            continue
        drawn = ObjectReview.objects.filter(
            image=img, batch__isnull=True, mask_key=key).first()
        if drawn is not None and not drawn.removed:
            # 사람이 그린 것은 후보가 없다 — 행도 이미 있다
            resolved.append((img, None, key, bool(m.get("rep")), drawn.geom,
                             False, None))
            continue
        return bad(f"{key} 는 이 화면의 마스크가 아니다")

    try:
        with transaction.atomic():
            # ── 1. 멤버마다 판정 행을 확보한다 ─────────────────────────────
            #
            # **없으면 세운다.** P12 이후 판정 행이 곧 멤버이고, 개체는 그
            # 행이 생길 때 함께 생긴다 (`data.judgement_for` 가 그 문이다).
            rows, revived = [], 0
            for img, b, key, rep, geom, rej, cand in resolved:
                row = data.judgement_for(vp, img, b, key, cand)
                fields = []
                # **이미 있는 행도 다시 맺는다** (`save_review` 와 같은 줄).
                # `judgement_for` 는 있는 행을 그대로 돌려주므로, 옛 바인딩이
                # `orphan` 이면 다시 묶어도 그대로 남는다 — 그런데 여기까지 온
                # 것은 그 (이미지, 묶음, 키) 의 후보를 **방금 찾았다**는 뜻이라
                # 짝이 없다는 기록이 거짓이다. 고아 화면·`rebind` 가 그 값을 본다.
                if cand and row.candidate_id != cand.pk:
                    row.candidate = cand
                    row.bind_method = "exact"
                    row.bind_score = 1.0
                    fields += ["candidate", "bind_method", "bind_score"]
                if not row.geom:
                    row.geom = geom
                    fields.append("geom")
                if rej:
                    # 탈락 후보를 묶었다 → 되살린다. **있는 행은 안 덮는다** —
                    # 분류·코멘트가 붙어 있을 수 있고, 다른 축의 판단이다.
                    if not row.accepted:
                        row.accepted = True
                        fields.append("accepted")
                    revived += 1
                if fields:
                    row.save(update_fields=fields)
                rows.append((row, rep))

            # ── 2. 합칠 그릇을 고른다 ────────────────────────────────────
            if link_id:
                link = ForamObject.objects.filter(pk=link_id,
                                                   viewpoint=vp).first()
                if link is None:
                    raise _Reject("그 묶음이 이 시야에 없다", 404)
            else:
                # **대표의 개체를 그릇으로 쓴다.** 분류·종명이 개체에 살고,
                # 대표는 사람이 "이 개체의 얼굴" 로 고른 판이다 — 그쪽 값을
                # 남기는 것이 덮어쓰기를 가장 적게 한다.
                link = next((r.foram_object for r, rep in rows if rep),
                            rows[0][0].foram_object)

            # **이미 다른 묶음에 속한 마스크는 안 받는다** (409). P12 이후로는
            # 옮기는 것이 기술적으로 가능해졌는데, 그러면 **남의 묶음이 조용히
            # 깨진다** — 사람이 먼저 풀고 다시 묶어야 한다.
            #
            # **고치는 요청(`link_id`)일 때만 그릇 자신을 뺀다.** 새로 묶는
            # 요청이라면 그릇으로 고른 개체가 이미 묶음이어도 안 된다 — 그쪽이
            # "이미 묶인 마스크를 새 묶음의 대표로 보냈다" 는 경우다.
            others = (ForamObject.objects
                      .filter(pk__in={r.foram_object_id for r, _ in rows})
                      .annotate(n=Count("members")).filter(n__gte=2))
            if link_id:
                others = others.exclude(pk=link.pk)
            if others.exists():
                raise _Reject("멤버 중 하나가 이미 다른 묶음에 속해 있다", 409)

            # **그릇이 안 든 값은 멤버에게서 물려받는다.** 대표의 개체를 그릇으로
            # 삼으므로, 대표가 비어 있고 다른 판에만 분류·종명이 있으면 합치는
            # 순간 그것이 사라진다 — **묶는다고 남의 동정을 잃으면 안 된다.**
            # 107 의 팝업이 "하나뿐이면 미리 고른다" 로 하던 일을 서버도 한다
            # (화면을 안 거치고 들어오는 요청이 있다). 여럿이 다르면 여기서
            # 고르지 않는다 — 그것은 사람이 팝업에서 정할 일이다.
            #
            # **엇갈리는데 안 골랐으면 묶지 않는다.** 개체가 값을 하나만 들 수
            # 있으므로 "그대로" 는 성립하지 않는다 — 그릇의 값이 남의 동정을
            # 덮는다. 107 의 팝업이 그것을 물어보게 돼 있고, 여기 걸리는 것은
            # **팝업을 안 지난 요청**이다. 종명은 현미경을 보며 적는 것이라
            # 자동으로 고르면 안 된다.
            carried = []
            given = {"label": unify_label, "species": unify_species}
            for fld in ("label", "species"):
                vals = {getattr(r.foram_object, fld) for r, _ in rows
                        if getattr(r.foram_object, fld)}
                if given[fld] is not None or len(vals) < 2:
                    if not getattr(link, fld) and len(vals) == 1:
                        setattr(link, fld, next(iter(vals)))
                        carried.append(fld)
                    continue
                what = "분류" if fld == "label" else "종명"
                raise _Reject(
                    f"판마다 {what} 이 다르다 — 무엇을 남길지 골라야 묶을 수 "
                    f"있다 ({' · '.join(sorted(vals))})", 409)
            link.batch_id = rb
            # `species` 는 `taxon` FK 의 읽기 통로다 — 저장 칸 이름은 `taxon`
            link.save(update_fields=["batch", "updated_at"]
                      + ["taxon" if f == "species" else f for f in carried])

            # ── 3. 이번 목록에 없는 옛 멤버는 떼어 낸다 ──────────────────
            #
            # **지우지 않는다.** 예전에는 `link.members.all().delete()` 로
            # 갈아끼웠는데, 지금 그 줄은 교정을 지운다.
            keep = {r.pk for r, _ in rows}
            for row in (ObjectReview.objects.filter(foram_object=link)
                        .exclude(pk__in=keep).select_related("foram_object")):
                _split_off(row, link)

            # ── 4. 그릇으로 옮긴다 ──────────────────────────────────────
            #
            # **규칙은 `data.merge_into_object` 하나다** — 그린 마스크가 판마다
            # 번질 때도 같은 문을 지난다. 대표를 내렸다 세우는 순서와 빈 개체를
            # 걷는 순서가 거기 적혀 있다.
            data.merge_into_object(link, rows)

            # **고른 값을 개체에 적는다.** 묶음이 선 뒤라야 한다 — 그 전에
            # 하면 아직 합쳐지지 않은 개체에 쓰게 되고, 같은 트랜잭션 안이라
            # 묶기가 실패하면 이것도 함께 물러난다.
            unified = _unify_members(link, resolved, unify_label, unify_species)
    except _Reject as e:
        return bad(e.msg, e.status)
    except ValueError as e:
        # 목록에 없는 학명 (`Taxon.resolve`)
        return bad(str(e), 400)
    except IntegrityError:
        # 유일 제약 — 그 마스크가 이미 다른 묶음에 속해 있다
        return bad("멤버 중 하나가 이미 다른 묶음에 속해 있다", 409)

    return JsonResponse({"ok": True, "link_id": link.pk, "revived": revived,
                         "unified": unified,
                         "links": data.object_links_of(vp)})

@require_POST
def spread_detection(request, slug, gid):
    """검출 마스크 하나를 **같은 시야의 다른 판에도 앉힌다** (P19).

        {"image": 12, "mask_key": "736_615_189_258"}

    **`/review` 에 안 싣는다.** 그 길은 *"그 (이미지, 묶음)의 교정 전체를
    갈아치운다"* 는 전제 위에 있고, 116 이 층이 다른 것을 한 payload 에 실었다가
    갈래 둘을 냈다 — 여기는 마스크 하나 단위다. `/link` 와 같은 자리다.

    **서버가 다시 검사한다** (화면에서 막는 것은 막는 것이 아니다): 이미지가 그
    시야의 것인가 · 그 마스크가 **검토 대상 묶음의 살아 있는 통과 후보**인가 ·
    오검출로 지운 것이 아닌가. 겹침은 **안 본다**(P19 4.3 · 사용자 방침).

    **읽기 전용 묶음은 여기 못 온다** — `review_batch_id()` 의 묶음에만 앉히기
    때문이다. 그 라디오로 다른 회차를 보고 있으면 화면이 항목을 안 낸다(051).
    """
    vp = (Viewpoint.objects.filter(slide__slug=slug, idx=gid)
          .select_related("slide").first())
    if vp is None:
        raise Http404(f"unknown viewpoint: {slug}/g{gid}")

    def bad(msg, status=400):
        return JsonResponse({"ok": False, "error": msg}, status=status)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return bad("JSON 이 아니다")

    rb = data.review_batch_id()
    if rb is None:
        return bad("검토 대상 묶음이 정해져 있지 않다")

    img = ImageModel.objects.filter(pk=payload.get("image")).first()
    if img is None or img.viewpoint_id != vp.pk:
        return bad(f"이미지 {payload.get('image')} 가 이 시야의 것이 아니다")

    key = payload.get("mask_key") or ""
    # **검출 마스크만 앉힌다.** 사람이 그린 것은 `_spread_drawn` 이 저장할 때
    # 이미 번지게 한다 — 여기로 오면 같은 일을 두 문이 하게 된다.
    cand = (Candidate.objects
            .filter(detection__image=img, detection__is_current=True,
                    detection__run__batch_id=rb, mask_key=key)
            .first())
    if cand is None:
        return bad(f"{key} 는 이 화면의 검출 마스크가 아니다")
    gone = ObjectReview.objects.filter(
        image=img, batch_id=rb, mask_key=key, removed=True).exists()
    if gone:
        return bad(f"{key} 는 오검출로 지운 마스크다")

    # 기하는 **서버가 스스로 뜬다** — 화면이 보낸 것을 믿지 않는다(`/link` 와
    # 같은 줄).
    # 칸 이름은 `bbox` 다 — `/link` 쪽과 같은 줄이다(그 주석에 왜가 있다).
    geom = {"bbox": [cand.bbox_x, cand.bbox_y, cand.bbox_w, cand.bbox_h],
            "polygon": cand.polygon}

    try:
        with transaction.atomic():
            out = data.spread_detection(vp, img, rb, key, geom, cand)
    except IntegrityError:
        return bad("멤버 중 하나가 이미 다른 묶음에 속해 있다", 409)

    if not out["n"]:
        return bad("앉힐 판이 없다")
    return JsonResponse({"ok": True, "n": out["n"], "key": out["key"],
                         "spread": out["spread"],
                         "links": data.object_links_of(vp)})

class _Reject(Exception):
    """묶기를 거절한다 — **`atomic()` 안에서는 예외로만 물러난다.**

    `return` 은 트랜잭션을 되돌리지 않는다. 거절하기 전에 세운 판정 행(그리고
    그것이 데리고 온 개체)이 그대로 남아, **거절했는데 빈 껍데기가 생긴다** —
    103 에서 겪은 "한쪽은 거절, 한쪽은 통과" 와 같은 모양이다.
    """

    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.msg, self.status = msg, status

def _split_off(row, src) -> ForamObject:
    """판정 하나를 묶음에서 떼어 **자기 개체**로 보낸다 (P12 의 "풀기").

    사람이 적은 것은 **전부 물려준다.** 묶여 있는 동안 그 값은 이 판의 것이기도
    했고, 가른다고 해서 사람이 적은 동정이 사라질 이유가 없다 — 재생성 불가
    자료를 구조 변경의 부수 효과로 잃지 않는다.

    **칸이 늘면 여기도 는다.** 0034·0035 가 등급·자세를 개체에 앉히고 이 줄을
    안 고쳐, 풀기 한 번에 사람이 매긴 등급이 사라지고 있었다 — 예외도 경고도
    없는 종류다. 코멘트를 얹으면서(0036) 함께 채운다.
    """
    obj = ForamObject.objects.create(
        viewpoint_id=src.viewpoint_id, batch_id=src.batch_id,
        label=src.label, taxon_id=src.taxon_id, note=src.note,
        grade=src.grade, pose=src.pose)
    ObjectReview.objects.filter(pk=row.pk).update(foram_object=obj,
                                                  is_rep=True)
    # **갈라 나간 개체는 자기가 앵커다** (P18). 멤버가 하나뿐이라 고를 것이 없다.
    # 그리고 **떠나온 쪽의 앵커가 이 행이었으면 그쪽은 앵커를 잃는다** —
    # `SET_NULL` 로 비고, 다음 줄이 남은 멤버로 넘긴다.
    ForamObject.objects.filter(pk=obj.pk).update(anchor_id=row.pk)
    data.reanchor([src.pk])
    return obj

def _unify_members(link, resolved, label, species) -> dict:
    """묶음의 분류·종명을 고른 값으로 맞춘다 (107 · P12 에서 한 줄이 됐다).

    돌려주는 것은 `{이미지 id: {"label": …, "species": …}}` — **화면이 이걸
    받아야 한다.** 다른 판의 상태는 화면이 열릴 때 받은 것이라 방금 맞춘 값을
    모르고, 그 판에서 다음 저장이 나가면 자기가 아는 옛 값을 보내 되돌린다
    (104 와 같은 자리).

    **쓰는 자리는 개체 하나다.** 예전에는 멤버마다 행을 찾아 적고, 행이 없으면
    세우고, 빈 껍데기를 안 만들려고 갈래를 타야 했다 — 분류가 판마다 살았기
    때문이다. 지금은 개체가 들고 있어 그 전부가 필요 없다.

    **분류·종명 말고는 안 건드린다** — 삭제·되살림·코멘트·기하는 판마다 하는
    판단이라 묶는다고 같아질 이유가 없다.
    """
    if label is None and species is None:
        return {}
    fields = []
    if label is not None and link.label != label:
        link.label = label
        fields.append("label")
    if species is not None and link.species != species:
        # ForGIA: 이름 → `Taxon`. 없는 이름은 `ValueError` — 부르는 쪽(`save_object_link`)이
        # 400 으로 돌려준다
        link.species = species
        fields.append("taxon")
    if fields:
        link.save(update_fields=fields + ["updated_at"])

    # 바뀌지 않았어도 화면에 알린다 — 아직 교정 행이 없던 판은 화면 쪽 상태에도
    # 없어서, 안 알리면 그 판만 옛 값으로 남는다.
    ent = {}
    if label is not None:
        ent["label"] = label
    if species is not None:
        ent["species"] = species
    return {str(img.pk): dict(ent) for img, *_ in resolved}

def parse_review_payload(payload: dict) -> dict:
    """`/review` 의 **판 payload** 를 검사해 `data.save_review` 의 인자로 만든다.

    문이 둘이라 여기 하나로 모았다 (P25 5절) — 검토 화면이 보내는 것과,
    오프라인 파일이 돌아와 싣는 것이 같은 값이다. 검사가 두 벌이면 **한쪽만
    통과하는 값**이 생기고, 그 값이 앉는 자리가 하필 재생성 불가한 교정이다.

    받는 것은 남이 만든 자료다 — 오프라인 파일은 이 서버 밖에서 몇 날을 돌아
    온다. 그래서 **모양을 하나하나 본다.** 못 받는 값은 `ValueError` 로
    올린다(부르는 쪽이 400 으로 낸다).

    `done` 은 여기서 안 본다 — `{"only": "done"}` 갈래가 그 값을 먼저 쓰므로
    부르는 쪽에 남는다.
    """
    def keys(name):
        v = payload.get(name) or []
        if not isinstance(v, list):
            raise ValueError("bad keys")
        return sorted({str(k) for k in v if isinstance(k, (str, int))})

    removed, accepted = keys("removed"), keys("accepted")

    def mapping(name, clean):
        v = payload.get(name) or {}
        if not isinstance(v, dict):
            raise ValueError(name)
        out = {}
        for k, raw in v.items():
            k = str(k)
            # **그린 개체의 키는 흘린다** (2026-09-03). 그 분류는 `drawn` 이
            # 나르고, `save_review` 도 세 목록(`removed`·`accepted`·`labels`)에서
            # 같은 키를 같은 규칙으로 흘린다 — 여기서 400 으로 물리면 **그 방어에
            # 닿지도 못한 채** 저장 전체가 거절되고, 화면은 같은 payload 를 계속
            # 다시 보낸다(`postReview` 가 실패를 다시 저장할 것으로 남긴다).
            # 배포 중에 열려 있던 옛 탭이 그 키를 실어 보낸다.
            if data.MANUAL_KEY.match(k):
                continue
            if not data.CAND_KEY.match(k):
                raise ValueError(name)
            val = clean(raw)
            if val is not None:
                out[k] = val
        return dict(sorted(out.items()))

    def as_label(v):
        return str(v) if v in data.CLASSES else None

    # **개체 코멘트는 안 받는다** (0036). 적는 자리를 개체 카탈로그 하나로
    # 모았다 — 이 화면은 읽기만 한다. 옛 탭이 `notes` 를 실어 보내면 **조용히
    # 흘린다**: 오류로 물리면 그 저장에 함께 실린 삭제·되살림까지 잃는다
    # (`save_review` 머리말).
    try:
        labels = mapping("labels", as_label)
    except ValueError:
        raise ValueError("bad labels")

    # 시야 전체에 대한 메모. 개체에 붙지 않는 이야기(촬영 상태, 판정이 애매한
    # 이유 등)를 적을 곳이 있어야 한다.
    #
    # **안 실렸으면 `None` 이다** — 완료와 같은 규칙(180 B2). 지금 화면은 이것을
    # `{"only": "note"}` 로 따로 보내고, 옛 탭만 여기 싣는다.
    note = _note(payload["note"]) if "note" in payload else None
    if not isinstance(payload.get("note", ""), (str, type(None))):
        raise ValueError("bad note")

    # **어느 이미지를 보고 한 교정인가** (P09 1단계). 시야 하나에 현재 검출이
    # 여럿일 수 있으므로(합성본 하나 + 프레임마다 하나) 화면이 짚어서 보낸다.
    #
    # **이름이 아니라 id 다.** 프레임 이름은 슬라이드끼리 겹치고(143종) 053 이
    # 정확히 그 자리에서 났다 — 이름은 겹쳐도 주소는 안 겹친다. 서버는 그 id 가
    # **이 시야의 것인지** 다시 확인한다(`save_review` 안에서).
    image_id = payload.get("image")
    if image_id is not None:
        try:
            image_id = int(image_id)
        except (TypeError, ValueError):
            raise ValueError("bad image")

    # **사람이 그린 개체** (P09 3단계). `[{key, polygon, cls}]` 이고
    # 기하는 서버가 다시 잰다 — 클라이언트가 보낸 면적을 믿으면 브라우저마다
    # 다른 숫자가 DB 에 앉는다(P09 5.8).
    #
    # **없는 것과 빈 것은 다르다.** 없으면 `None` 으로 넘겨 손대지 않고, 빈
    # 목록이면 "그린 것이 하나도 없다" 로 받아 지운다 — 둘을 같이 다루면
    # **그리기를 모르는 옛 탭의 저장 한 번**이 그린 개체를 전부 지운다.
    drawn = payload.get("drawn")
    if drawn is not None:
        if not isinstance(drawn, list) or len(drawn) > 500:
            raise ValueError("bad drawn")
        clean = []
        for it in drawn:
            if not isinstance(it, dict):
                raise ValueError("bad drawn item")
            cls = as_label(it.get("cls")) or ""
            # 코멘트는 여기서도 안 받는다 — 위 `notes` 와 같은 갈래다(0036).
            clean.append({"key": it.get("key"), "polygon": it.get("polygon"),
                          "cls": cls})
        drawn = clean

    # **사람이 고친 기하** (P09 4단계). `{키: 폴리곤}` 이고 **빈 폴리곤은
    # "엔진 것으로 되돌린다"** 는 말이다. 그린 개체(`drawn`)와 달리 엔진 개체의
    # 교정이라 묶음에 속한다 — `Candidate` 는 안 건드리고 교정 행의 `geom` 만
    # 덮는다(P09 5.6).
    edits = payload.get("edits")
    if edits is not None:
        if not isinstance(edits, dict) or len(edits) > 500:
            raise ValueError("bad edits")
        for v in edits.values():
            if not isinstance(v, list):
                raise ValueError("bad edits polygon")

    return {"removed": removed, "accepted": accepted, "labels": labels,
            "note": note, "image": image_id, "drawn": drawn, "edits": edits}

@require_POST
def save_review(request):
    """
    교정 결과를 저장한다.

        {"stem": ..., "slug": ..., "gid": int,
         "done": bool, "removed": [key], "accepted": [key],
         "labels": {key: "round"|"round_frag"|"rod"|"rod_frag"},
         "note":   "이 시야에 대한 메모"}

    **`{"only": "done"}` 이면 완료 표시 하나만 쓴다** (116 덧) — 교정은 안
    싣고 안 지운다. 완료는 `(시야, 묶음)` 이고 교정은 `(이미지, 묶음)` 이라
    층이 다르다 (`data.save_done` 머리말).

    **개체 코멘트(`notes`)는 여기로 안 온다** (0036) — 카탈로그에서만 적는다.
    옛 탭이 보내면 조용히 흘린다.

    키는 bbox 에서 만든 것이라 검출을 다시 돌려도 같은 마스크면 그대로 붙는다.
    문턱만 바꾸는 refilter.py 실행에는 영향받지 않는다. 저장 위치는 DB 이고
    (예전에는 review/<stem>_review.json), git 에 남길 감사 기록은
    export_review.py 가 내보낸다.

    labels 는 자동 판정을 사람이 덮어쓴 것이다 — 조각난 개체이 봉상/원형으로
    잘못 분류되는 것을 손으로 고치는 수단이고, 학습 데이터의 정답이 된다.

    ## 어느 시야에 쓰는가는 `(slug, gid)` 가 정한다

    예전에는 `stem` 하나로 찾았다. **프레임 이름은 슬라이드끼리 겹친다** —
    싱글턴 시야는 stem 이 곧 프레임 이름이라, 12개 화면이 **다른 슬라이드의
    시야를 열고 있었다.** 이 뷰는 마지막에 그 시야의 교정을 통째로 갈아치우므로
    (`data.save_review`), "검토 완료" 만 누른 빈 payload 하나가 남의 교정 7건을
    지웠다 — 사본에서 재현했다(2026-08-05). 예외도 409 도 없이 200 이었다.

    `stem` 은 남겨 두고 **검증용**으로 쓴다: 그 시야의 현재 검출과 다르면 받지
    않는다. 화면과 저장 대상이 어긋난 채 통과하는 길을 남기지 않는다.
    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest("bad json")

    stem = str(payload.get("stem", ""))
    if not SAFE_STEM.match(stem):
        return HttpResponseBadRequest("bad stem")

    # 옛 화면(배포 중에 열려 있던 탭)은 slug·gid 를 안 보낸다. 그때는 stem 으로
    # 찾되 **모호하면 거절한다** — 아무거나 집는 길은 없앤다.
    slug = str(payload.get("slug", "") or "")
    gid = payload.get("gid")
    if gid is not None:
        try:
            gid = int(gid)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("bad gid")

    vp, why = data.find_viewpoint(stem=stem, slug=slug, gid=gid)
    if vp is None:
        return JsonResponse({"ok": False, "error": why}, status=409)

    # 자동 처리가 끝나기 전에는 저장을 받지 않는다 (P01 §1).
    # 반쯤 처리된 슬라이드를 검토하면 아직 안 돌아간 시야의 검출이 뒤늦게
    # 들어오면서 이미 본 화면이 바뀐다. 최상위 폴더 하나를 단위로 열고 닫는다.
    blocked = data.review_blocked(vp.slide)
    if blocked:
        return JsonResponse({"ok": False, "error": blocked}, status=409)

    # 검토 완료 표시. 교정이 하나도 없어도(고칠 것이 없어서) 켜질 수 있으므로
    # 삭제·복구 목록과 독립적으로 저장한다.
    #
    # **없는 것과 빈 것이 다르다** (180 B2 · `drawn`·`edits` 와 같은 규칙).
    # 지금 화면은 완료를 판 payload 에 안 싣는다 — 안 실린 것을 `False` 로 읽으면
    # **마스크 저장 한 번이 다른 탭에서 켠 완료를 끈다.** 옛 탭은 계속 싣고,
    # 그때는 그 값을 쓴다.
    done = payload.get("done")
    if done is not None:
        done = bool(done)

    # **완료만 보내는 요청** (116 덧). `{"only": "done"}` 이면 교정을 안 싣고
    # `(시야, 묶음)` 한 줄만 쓴다 — 표시 하나를 켜자고 그 판의 교정을
    # 갈아치우는 갈래를 남기지 않는다 (`data.save_done` 머리말).
    #
    # **위의 검사는 다 지난 뒤다** — `stem` 과 `(slug, gid)` 로 시야를 짚었고
    # (053), 자동 처리가 끝났는지도 봤다. 여기서 줄이는 것은 payload 뿐이다.
    if payload.get("only") == "done":
        try:
            saved = data.save_done(vp, bool(done))
        except ValueError as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=409)
        return JsonResponse({"ok": True, **saved})

    # **코멘트만 보내는 요청** (180 B2). 완료와 같은 층이라 같은 모양의 문을
    # 둔다 — 시야 코멘트는 `(시야, batch=NULL)` 한 줄이고 판마다 다르지 않다.
    if payload.get("only") == "note":
        raw = payload.get("note", "")
        if not isinstance(raw, (str, type(None))):
            return HttpResponseBadRequest("bad note")
        return JsonResponse({"ok": True, **data.save_note(vp, _note(raw))})

    # **검사는 문 하나로 모았다** (P25 5절). 화면이 보내는 것과 오프라인
    # 파일이 되돌려 싣는 것이 같은 값이라, 검사가 갈리면 **한쪽만 통과하는 값**이
    # 생긴다 — 그 값이 앉는 자리가 하필 재생성 불가한 교정이다.
    try:
        f = parse_review_payload(payload)
    except ValueError as e:
        return HttpResponseBadRequest(str(e))
    removed, accepted = f["removed"], f["accepted"]
    labels, note, image_id = f["labels"], f["note"], f["image"]
    drawn, edits = f["drawn"], f["edits"]

    try:
        saved = data.save_review(vp, done=done, note=note, removed=removed,
                                 accepted=accepted, labels=labels,
                                 image=image_id, drawn=drawn, edits=edits)
    except ValueError as e:
        # 현재 검출에 없는 키가 섞여 왔다. 아무것도 바꾸지 않고 돌려보낸다 —
        # 그대로 두면 그 시야의 교정이 통째로 지워진다 (data.save_review 주석).
        return JsonResponse({"ok": False, "error": str(e)}, status=409)
    if saved is None:
        return HttpResponseBadRequest("unknown stem")
    return JsonResponse({"ok": True, "done": done, "note": bool(note), **saved})


# ─────────────────────────────────────────────── 학명 (Taxon · ForGIA)

TAXON_SUGGEST_MAX = 30


def taxon_suggest(request):
    """`?q=` 로 시작하는 학명 — 카탈로그·검토 화면의 종명 칸이 묻는다.

    **켠 것(`active`)이 먼저, 그다음 유효명, 그다음 이름순.** 반입이 WoRMS 유공충
    전체(수만 행)라 이름 앞부분으로만 찾는다 — 가운데 글자로 찾으면 두 글자에
    수천 행이 걸려 목록이 뜻을 잃는다. 속명 뒤에 띄고 종소명을 치는 것이
    보통이라 앞부분 일치로 충분하다. 두 글자 미만은 빈 목록이다.

    돌려주는 것은 `{"names": [{name, rank, status, valid, extinct, active}]}`.
    """
    q = re.sub(r"\s+", " ", (request.GET.get("q") or "")).strip()
    if len(q) < 2:
        return JsonResponse({"names": []})
    rows = (Taxon.objects.filter(name__istartswith=q)
            .select_related("accepted")
            .order_by("-active",
                      Case(When(status="accepted", then=0), default=1),
                      "name")[:TAXON_SUGGEST_MAX])
    return JsonResponse({"names": [{
        "name": t.name, "rank": t.rank, "status": t.status,
        "valid": t.accepted.name if t.accepted_id else t.name,
        "extinct": t.extinct, "active": t.active,
    } for t in rows]})


TAXA_PER_PAGE = 200


def system_settings_taxa(request):
    """시스템 설정 · 학명 — **켠 학명 목록**과 반입 상태.

    켜고 끄는 문이 여기 하나다(동정에 쓰이면 저절로 켜지는 것 말고). 끄기는
    "자동완성 목록에서 뺀다" 는 뜻이지 행을 지우는 것이 아니다 — `ForamObject.taxon`
    이 PROTECT 로 잡고 있어 쓰인 학명은 어차피 못 지운다.
    """
    msg, ok = "", True
    if request.method == "POST":
        act = request.POST.get("act") or ""
        try:
            if act in ("on", "off"):
                pk = int(request.POST.get("id") or 0)
                n = Taxon.objects.filter(pk=pk).update(active=(act == "on"))
                msg = ("켰습니다." if act == "on" else "껐습니다.") if n else "모르는 학명이다."
                ok = bool(n)
            elif act == "on_name":
                t = data.resolve_taxon(request.POST.get("name") or "")
                msg = f"{t.name} 을(를) 켰습니다." if t else "이름이 비었다."
                ok = t is not None
            elif act == "habit":
                pk = int(request.POST.get("id") or 0)
                habit = request.POST.get("habit") or ""
                if habit not in ("", "planktonic", "benthic"):
                    raise ValueError("모르는 생활형이다")
                # **아래로 물려준다** — 과·목에 붙인 것이 그 밑 종 전부에 뜻이 있다
                n = _set_habit_down(pk, habit)
                msg = f"{n}개 행의 생활형을 바꿨습니다."
            else:
                raise ValueError("모르는 요청이다")
        except (ValueError, TypeError) as e:
            msg, ok = str(e), False
        url = reverse("system_settings_taxa")
        return redirect(f"{url}?msg={msg}&ok={int(ok)}")

    q = (request.GET.get("q") or "").strip()
    qs = Taxon.objects.select_related("accepted", "parent").order_by("name")
    if q:
        qs = qs.filter(name__icontains=q)
    else:
        qs = qs.filter(active=True)
    total = qs.count()
    rows = list(qs[:TAXA_PER_PAGE])
    stats = {
        "total": Taxon.objects.count(),
        "active": Taxon.objects.filter(active=True).count(),
        "used": ForamObject.objects.exclude(taxon__isnull=True)
                .values("taxon_id").distinct().count(),
        "species": Taxon.objects.filter(rank="Species").count(),
        "genus": Taxon.objects.filter(rank="Genus").count(),
    }
    return render(request, "viewer/system_settings_taxa.html", {
        "rows": rows, "total": total, "shown": len(rows), "q": q,
        "stats": stats,
        "msg": request.GET.get("msg") or msg,
        "ok": (request.GET.get("ok", "1") != "0") if msg == "" else ok,
        "habits": [("", "—"), ("planktonic", "부유성"), ("benthic", "저서성")],
    })


def _set_habit_down(pk: int, habit: str) -> int:
    """`pk` 와 그 아래 전부의 `habit` 을 바꾼다. 돌려주는 것은 바뀐 행 수."""
    ids, frontier = [pk], [pk]
    while frontier:
        kids = list(Taxon.objects.filter(parent_id__in=frontier)
                    .values_list("pk", flat=True))
        ids += kids
        frontier = kids
    return Taxon.objects.filter(pk__in=ids).update(habit=habit)
