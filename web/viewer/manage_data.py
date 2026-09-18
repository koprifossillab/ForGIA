"""DiaRUGA v0.29.0 `web/viewer/manage_data.py` 에서 1단계 것(층 넷 만들기·옮기기·지우기)만 왔다.
묶음·조리법·학습 자료·코어 자료는 2·5단계다.

관리 화면이 쓰는 것 — 층을 세어 보이고, 만들고, 고치고, 지운다.

**`data.py` 와 갈라 둔다.** 그쪽은 읽기 전용이고(DB → 뷰가 쓰는 dict) 이쪽은
쓰는 문이다. 한 파일에 섞으면 "이 함수가 쓰는가" 를 매번 확인해야 한다.

## 지우기에 문턱이 있다

`Slide.sample` 이 `SET_NULL` 이라 시료를 지우면 **관찰이 조용히 소속을 잃는다**
— 아침에 `BP09-0901 (1)` 이 그 상태였고, 500 도 404 도 아니어서 목록을 세어
보기 전에는 알 수가 없었다. 그래서:

- 딸린 것이 있으면 **막는다.** 사람이 먼저 옮기거나 지워야 한다
- 무엇이 몇 개 딸려 있는지 화면에 **미리** 적는다. 눌러 보고 알게 하지 않는다
- 관찰(`Slide`)은 여기서 안 지운다. 폴더가 만드는 것이고 그 아래에 **재생성
  불가한 교정**이 달려 있다 — 지우는 문을 아예 두지 않는다
"""
from django.db import transaction
from django.db.models import Count

from .models import Locality, Sample, Site, Slide


def overview() -> dict:
    """층 넷을 한 화면에 — 무엇이 몇 개이고 어디가 비었는가."""
    sites = list(Site.objects.annotate(n_loc=Count("localities", distinct=True))
                 .order_by("area", "code"))
    for s in sites:
        s.n_samples = Sample.objects.filter(locality__site=s).count()
        s.n_slides = Slide.objects.filter(sample__locality__site=s).count()

    locs = list(Locality.objects.select_related("site")
                .annotate(n_samples=Count("samples", distinct=True))
                .order_by("site__code", "code"))
    for c in locs:
        c.n_slides = Slide.objects.filter(sample__locality=c).count()

    samples = list(Sample.objects.select_related("locality__site")
                   .annotate(n_slides=Count("slides", distinct=True))
                   .order_by("locality__site__code", "locality__code",
                             "depth_cm", "code"))

    # 소속을 잃은 관찰. **이 화면이 있어야 하는 첫째 이유다** — 다른 어느 화면에도
    # 안 나온다(어느 권역 탭에도 안 걸린다). 붙일 곳을 추천까지 해서 낸다.
    orphans = []
    for sl in (Slide.objects.filter(sample__isnull=True)
               .order_by("name")):
        sib = next((s for s in sl.sibling_observations() if s.sample_id), None)
        orphans.append({"slide": sl, "suggest": sib.sample if sib else None,
                        "suggest_from": sib})
    return {"sites": sites, "localities": locs, "samples": samples,
            "orphans": orphans,
            "n_sites": len(sites), "n_locs": len(locs),
            "n_samples": len(samples),
            "n_slides": Slide.objects.count()}


def deletable(kind: str, pk: int) -> tuple[object | None, list[str]]:
    """지울 수 있는가. 돌려주는 것은 (행, 막는 이유들).

    **이유를 문자열로 돌려준다** — 화면이 "지울 수 없습니다" 만 내면 무엇을 먼저
    치워야 하는지 알 수 없다.
    """
    if kind == "site":
        obj = Site.objects.filter(pk=pk).first()
        if obj is None:
            return None, []
        n = obj.localities.count()
        return obj, ([f"지점 {n}개가 이 지역에 달려 있습니다"] if n else [])
    if kind == "locality":
        obj = Locality.objects.filter(pk=pk).first()
        if obj is None:
            return None, []
        why = []
        n = obj.samples.count()
        if n:
            why.append(f"시료 {n}개가 이 지점에 달려 있습니다")
        # 코어 자료(`CoreSeries` · DiaRUGA P17)는 5단계에서 온다 — 그때 여기서
        # 함께 센다. `CorePoint` 가 `CASCADE` 라 예외도 경고도 없이 같이 지워진다
        return obj, why
    if kind == "sample":
        obj = Sample.objects.filter(pk=pk).first()
        if obj is None:
            return None, []
        n = obj.slides.count()
        # **`SET_NULL` 이라 지워도 예외가 안 난다** — 관찰이 조용히 소속을 잃을
        # 뿐이다. 막는 자리는 여기밖에 없다.
        return obj, ([f"관찰 {n}개가 이 시료를 보고 있습니다 "
                      f"(지우면 소속을 잃습니다)"] if n else [])
    return None, []


def delete(kind: str, pk: int) -> tuple[bool, str]:
    """지운다. 문턱을 통과할 때만."""
    obj, why = deletable(kind, pk)
    if obj is None:
        return False, "찾지 못했습니다."
    if why:
        return False, " · ".join(why)
    label = str(obj)
    obj.delete()
    return True, f"{label} 을(를) 지웠습니다."


def move_slide(slide_id: int, sample_id: int | None) -> tuple[bool, str]:
    """관찰의 소속을 옮긴다. `sample_id` 가 없으면 떼어 낸다.

    **떼어 내는 것도 길로 둔다.** 잘못 붙은 것을 고치려면 일단 떼야 할 때가 있고,
    막아 두면 사람이 시료를 지워서 떼려 든다 — 그쪽이 훨씬 위험하다.
    """
    sl = Slide.objects.filter(pk=slide_id).first()
    if sl is None:
        return False, "관찰을 찾지 못했습니다."
    if sample_id:
        sm = Sample.objects.select_related("locality__site").filter(
            pk=sample_id).first()
        if sm is None:
            return False, "시료를 찾지 못했습니다."
        sl.sample = sm
        sl.save(update_fields=["sample"])
        return True, f"{sl.name} 을(를) {sm} 에 붙였습니다."
    sl.sample = None
    sl.save(update_fields=["sample"])
    return True, f"{sl.name} 의 소속을 뗐습니다."


def move_sample(sample_id: int, locality_id: int) -> tuple[bool, str]:
    """시료를 다른 지점으로 옮긴다. 딸린 관찰이 함께 간다.

    **같은 코드가 이미 그 지점에 있으면 막는다.** `(locality, code)` 가 유일
    제약이라 그냥 저장하면 IntegrityError 로 죽는데, 그 화면에는 무엇이 부딪혔는지
    가 안 나온다.
    """
    sm = Sample.objects.filter(pk=sample_id).first()
    loc = Locality.objects.filter(pk=locality_id).first()
    if sm is None or loc is None:
        return False, "찾지 못했습니다."
    if Sample.objects.filter(locality=loc, code=sm.code).exclude(
            pk=sm.pk).exists():
        return False, f"{loc} 에 이미 시료 {sm.code} 가 있습니다."
    n = sm.slides.count()
    with transaction.atomic():
        sm.locality = loc
        sm.save(update_fields=["locality"])
    return True, f"시료 {sm.code} 를 {loc} 로 옮겼습니다 (관찰 {n}개 함께)."


def create(kind: str, data: dict) -> tuple[bool, str]:
    """지역·지점·시료를 새로 만든다. 관찰은 여기서 안 만든다 — 폴더가 만든다."""
    try:
        if kind == "site":
            code = (data.get("code") or "").strip()
            if not code:
                return False, "지역 코드가 비었습니다."
            if Site.objects.filter(code=code).exists():
                return False, f"지역 {code} 가 이미 있습니다."
            area = (data.get("area") or "").strip()
            Site.objects.create(
                code=code, name=(data.get("name") or "").strip(),
                region=(data.get("region") or "").strip(),
                area=area if area in dict(Site.AREA) else "ant")
            return True, f"지역 {code} 를 만들었습니다."
        if kind == "locality":
            site = Site.objects.filter(pk=data.get("site")).first()
            code = (data.get("code") or "").strip()
            if site is None or not code:
                return False, "지역과 지점 코드가 필요합니다."
            if Locality.objects.filter(site=site, code=code).exists():
                return False, f"{site.code} 에 지점 {code} 가 이미 있습니다."
            Locality.objects.create(
                site=site, code=code,
                collect_kind=(data.get("collect_kind") or "").strip())
            return True, f"지점 {site.code}-{code} 를 만들었습니다."
        if kind == "sample":
            loc = Locality.objects.filter(pk=data.get("locality")).first()
            code = (data.get("code") or "").strip()
            if loc is None or not code:
                return False, "지점과 시료 코드가 필요합니다."
            if Sample.objects.filter(locality=loc, code=code).exists():
                return False, f"{loc} 에 시료 {code} 가 이미 있습니다."
            Sample.objects.create(
                locality=loc, code=code,
                depth_cm=_num(data.get("depth_cm")),
                dry_weight_g=_num(data.get("dry_weight_g")))
            return True, f"시료 {loc}-{code} 를 만들었습니다."
    except ValueError as e:
        return False, f"값을 읽지 못했습니다: {e}"
    return False, "모르는 층입니다."


def _num(raw, cast=float):
    raw = (raw or "").strip()
    return cast(raw) if raw else None


# --- 검토할 묶음 (P10 3단계) ------------------------------------------------
    """빈 칸은 None 으로. 잘못된 값은 예외를 올려 보낸다 — 조용히 0 이 되면 안 된다."""
    raw = (raw or "").strip()
    if not raw:
        return None
    return cast(raw)
