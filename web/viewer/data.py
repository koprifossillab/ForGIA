"""DB → 뷰가 쓰는 dict. **읽기 전용이라는 약속이 있다** — 쓰는 문은 `manage_data`.

DiaRUGA v0.29.0 `web/viewer/data.py`(5,632줄)에서 1단계 화면이 쓰는 것만 왔다:
목록(`datasets`·`area_tabs`·`datasets_total`·`datasets_by_locality`) · 지도
(`map_points`) · 시야 목록(`dataset_detail`) · 시야 하나(`group_photos`) · 지점
(`locality_detail`) · 파이프라인 상태(`pipeline_status`) · 이미지 경로
(`safe_image_path`·`stamp`).

**검출·교정에 걸린 값은 아직 0 이다.** `_slide_summary` 가 DiaRUGA 에서는
`_summary_by_sql`(원시 SQL · DiaRUGA 058)로 개체를 세는데, 그 테이블이 2·3단계에
온다. 그때 그 함수를 가져와 이 자리의 `0` 을 갈아 끼운다 — 열쇠 이름은 그쪽과
같게 두었다(`n_detected`·`n_counted`·`reviewed_groups` …) 화면이 안 바뀌게.
"""
import json
import re
from pathlib import Path

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from . import antarctica
from .models import Frame, Run, Site, Slide, Stack, Viewpoint


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

    DiaRUGA 는 검출 행(`Detection.um_per_pixel`)에서 셌다 — 그 테이블이 2단계에
    온다. 그때까지는 **프레임**에서 센다(`Frame.um_per_pixel`, `scale.py` 가
    적은 값). 한 슬라이드 안에 값이 갈리면 가장 많은 쪽을 쓴다 — 갈린 것
    자체는 `check_db` 가 따로 잡는다.
    """
    per: dict[str, dict[float, int]] = {}
    for slug, um in (Frame.objects.filter(um_per_pixel__isnull=False)
                     .values_list("slide__slug", "um_per_pixel")):
        per.setdefault(slug, {})
        k = round(um, 9)
        per[slug][k] = per[slug].get(k, 0) + 1
    return {slug: max(c, key=c.get) for slug, c in per.items()}


# --- 집계 --------------------------------------------------------------------
def _slide_summary(slide: Slide) -> dict:
    """목록 화면의 집계. **검출·교정 값은 2·3단계까지 0 이다** (머리말)."""
    vps = Viewpoint.objects.filter(slide=slide)
    n_groups = vps.count()
    sizes = list(vps.values_list("n_frames", flat=True))
    n_img = sum(sizes)
    return {
        "n_groups": n_groups,
        "n_images": n_img,
        "mean_size": round(n_img / n_groups, 1) if n_groups else 0,
        "singletons": sum(1 for s in sizes if s == 1),
        "max_size": max(sizes) if sizes else 0,
        "n_stacks": Stack.objects.filter(viewpoint__slide=slide).count(),
        # ↓ 2단계(검출)·3단계(교정)에서 `_summary_by_sql` 이 채운다
        "detected_groups": 0,
        "n_auto": 0,
        "n_detected": 0,
        "mean_detected": None,
        "n_counted": 0,
        "mean_counted": None,
        "counted": [],
        "class_counts": [],
        "reviewed_groups": 0,
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
    total["counted"] = []
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
    """시야 목록. 검출 마스크·완료 표시는 2·3단계에서 붙는다."""
    slide = Slide.objects.filter(slug=slug).select_related(
        "sample__locality__site").first()
    if slide is None:
        return None

    groups = []
    for vp in (Viewpoint.objects.filter(slide=slide)
               .select_related("sharpest_frame", "stack")
               .prefetch_related("frames")):
        st = getattr(vp, "stack", None)
        groups.append({
            "id": vp.idx,
            "cell": vp.cell,
            "n": vp.n_frames,
            "tag": vp.tag,
            "span_sec": round(vp.span_sec or 0, 1),
            "sharpest": vp.sharpest_frame.name if vp.sharpest_frame else None,
            "cover_rel": _cover_of(vp),
            "has_stack": st is not None,
            "n_detected": None,     # 2단계
            "reviewed": False,      # 3단계
        })

    return {
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
            "n_det_vps": 0,             # 2단계
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
