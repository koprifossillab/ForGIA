"""DiaRUGA v0.29.0 `web/viewer/manage_data.py` 에서 왔다 — 층 넷(1단계)과 묶음·조리법(2단계).
학습 자료·코어 자료는 4·5단계다.

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
from pathlib import Path

from django.db import transaction
from django.db.models import Count

from .models import Locality, RunBatch, Sample, Site, Slide


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


def batch_choices() -> list[dict]:
    """고를 수 있는 묶음들과 **고르면 무엇이 달라지는지.**

    **누르기 전에 보인다.** DiaRUGA 063 이 "지우기 문턱은 눌러 보기 전에 보여야 한다" 를
    배운 자리와 같다 — 바꾸고 나서 "시야 56개가 비었다" 를 알면 늦다.

    줄마다 셋을 센다:

    | 칸 | 무엇 |
    |---|---|
    | `n_views` | 이 묶음이 덮는 시야 수 |
    | `n_blank` | **이 묶음에 검출이 없어 빈 화면이 될 시야** |
    | `n_objects` | 이 묶음이 낸 개체 수 (엔진이 낸 그대로 — 교정 전) |

    검출이 있는 묶음만 낸다. 빈 묶음을 고르면 화면이 통째로 비는데, 그것을
    고를 이유가 없다.
    """
    from .models import Candidate, Detection, RunBatch, Viewpoint

    total = Viewpoint.objects.count()
    out = []
    for b in RunBatch.objects.filter(kind="detect").order_by("-started_at"):
        dets = Detection.objects.filter(run__batch=b, is_current=True)
        n_views = dets.values("viewpoint_id").distinct().count()
        if not n_views:
            continue
        out.append({
            "batch": b,
            "on": b.for_review,
            "n_views": n_views,
            "n_blank": total - n_views,
            "n_images": dets.count(),
            "n_objects": Candidate.objects.filter(detection__in=dets,
                                                  passed=True).count(),
        })
    return out


def set_review_batch(batch_id: int) -> tuple[bool, str]:
    """검토할 묶음을 바꾼다. **자료는 안 건드린다 — 깃발 하나다.**

    그래서 **되돌리기가 같은 동작**이다. 예전 계획(DiaRUGA P09 5단계)은 검출 2,132행을
    UPDATE 하는 것이었는데, 사본에서 해 보니 YOLO 가 없는 56 시야가 현재 검출을
    잃고 빈 화면이 됐다 — 되돌리려면 또 한 번의 대량 UPDATE 였다(DiaRUGA P10 §1).

    **서버가 다시 검사한다.** 화면이 고를 수 없게 해 두어도 그것은 막는 것이
    아니다 — DiaRUGA 051·027 이 그 자리에서 났다.
    """
    from .models import Detection, Run, RunBatch

    b = RunBatch.objects.filter(pk=batch_id, kind="detect").first()
    if b is None:
        return False, "그런 묶음이 없습니다."
    if b.for_review:
        return False, f"{b.label} 은 이미 검토 대상입니다."
    # **검출이 없는 묶음은 안 받는다.** 고르면 화면이 통째로 빈다.
    n_views = (Detection.objects.filter(run__batch=b, is_current=True)
               .values("viewpoint_id").distinct().count())
    if not n_views:
        return False, f"{b.label} 에는 검출이 없습니다 — 먼저 돌려야 합니다."

    was = RunBatch.objects.filter(for_review=True).first()
    with transaction.atomic():
        # 유일 제약이 하나만 허용하므로 **끄고 켠다** — 순서가 뒤바뀌면 막힌다.
        RunBatch.objects.filter(for_review=True).update(for_review=False)
        RunBatch.objects.filter(pk=b.pk).update(for_review=True)
        # 누가 언제 어디서 어디로 — 되짚을 수 있어야 한다.
        Run.objects.create(
            kind="reconcile", batch=b, status="done",
            params={"action": "set_review_batch",
                    "from": was.label if was else None, "to": b.label},
            counts={"views": n_views})
    return True, (f"검토할 묶음을 {b.label} 로 바꿨습니다 — 시야 {n_views}개. "
                  f"판정 캐시가 어긋날 수 있으니 refilter.py 를 돌리십시오.")


# --- 운영: 조리법 ------------------------------------------------------------
# 조리법에 담는 것. `segment_forams.py` 의 인자와 이름이 같다 — 화면에서 고친
# 값이 그대로 명령줄이 되므로 여기서 이름을 바꾸면 안 된다 (`batch_plan.py`).
RECIPE_NUM = ("scale", "min_um", "max_um", "conf_min", "yolo_conf", "yolo_imgsz")
BACKENDS = ("yolo",)


def create_batch(form) -> tuple[bool, str]:
    """새 검출 묶음을 만든다 (DiaRUGA 084).

    지금까지 묶음은 **파이프라인이 `--batch` 로 처음 쓸 때** 생겼다. 그래서
    "다음 회차를 이렇게 돌리겠다" 를 미리 적어 둘 수가 없었다 — 조리법을 적으려면
    묶음이 먼저 있어야 하는데, 묶음을 만들려면 검출을 한 번 돌려야 했다.

    **조리법을 베껴 올 수 있다.** 새 회차는 대개 지난 회차에서 가중치만 바뀐
    것이라, 빈 칸에서 시작하면 배율·크기 문턱 같은 것을 옮겨 적다가 틀린다.

    **만드는 것으로 자료가 생기지는 않는다.** 새 슬라이드가 들어오면 폴러가
    채우지만, **이미 있는 슬라이드는 사람이 한 번 돌려야 한다** — 몇 시간짜리
    GPU 작업이라 화면이 조용히 시작하면 안 된다. 그 말을 응답에 담는다.
    """
    label = (form.get("label") or "").strip()
    if not label:
        return False, "묶음 이름을 적어 주십시오."
    if RunBatch.objects.filter(kind="detect", label=label).exists():
        return False, f"이미 있는 이름입니다: {label}"

    recipe = {}
    src_id = (form.get("copy_from") or "").strip()
    src = None
    if src_id:
        src = RunBatch.objects.filter(pk=src_id).first()
        if src is None:
            return False, "베껴 올 묶음을 찾지 못했습니다."
        recipe = dict(src.recipe or {})
        # 베낀 것에는 **추측 표시를 물려주지 않는다** — 새 묶음의 가중치는
        # 사람이 다시 보는 것이 맞고, 물려주면 경고가 영영 따라다닌다.
        recipe.pop("weights_guessed", None)

    # **카탈로그 코드** (`catalog.py` · DiaRUGA P16). 비면 `batch_code_seed` 가 낸
    # 첫 제안을 앉힌다 — DiaRUGA 는 마이그레이션이 심고 화면에 자리가 없었다.
    # 번호는 논문에 적히는 것이라 **정하는 것은 사람**이고, 여기서는 자리를 준다
    ok, code_or_msg = _clean_batch_code(form.get("code"), seed_from=label)
    if not ok:
        return False, code_or_msg
    b = RunBatch.objects.create(kind="detect", label=label,
                                note=(form.get("note") or "").strip(),
                                recipe=recipe, code=code_or_msg)
    tail = f" 카탈로그 코드 {b.code}."
    if src is not None:
        tail = f" ({src.label} 의 조리법을 베꼈습니다)"
    if not recipe:
        tail += " 조리법이 비어 있어 아직 자동으로 돌지 않습니다."
    return True, (f"묶음 {b.label} 을 만들었습니다.{tail} "
                  f"이미 있는 슬라이드는 사람이 한 번 돌려야 합니다.")


def _clean_batch_code(raw, *, seed_from="") -> tuple[bool, str]:
    """묶음의 카탈로그 코드 — 비면 라벨에서 첫 제안(`catalog.batch_code_seed`)."""
    from . import catalog
    code = (raw or "").strip().upper()
    if not code and seed_from:
        code = catalog.batch_code_seed(seed_from)
    if not code:
        return True, ""
    if code == catalog.MANUAL_CODE:
        return False, f"{catalog.MANUAL_CODE} 은 사람이 그린 개체의 자리입니다 — 다른 코드를."
    if len(code) > 8 or not code.isalnum():
        return False, "코드는 영문·숫자 여덟 자 안이어야 합니다."
    if RunBatch.objects.filter(code=code).exists():
        return False, f"코드 {code} 는 다른 묶음이 쓰고 있습니다."
    return True, code


def set_batch_code(batch_id: int, raw) -> tuple[bool, str]:
    """묶음의 카탈로그 코드를 고친다. **번호가 이미 적힌 뒤에는 바꾸지 말 것** —
    화면이 그것을 적는다(개체가 있는 묶음은 경고)."""
    b = RunBatch.objects.filter(pk=batch_id).first()
    if b is None:
        return False, "묶음을 찾지 못했습니다."
    ok, code = _clean_batch_code(raw)
    if not ok:
        return False, code
    if code == b.code:
        return True, "그대로입니다."
    b.code = code
    b.save(update_fields=["code"])
    return True, f"{b.label} 의 카탈로그 코드를 {code or '(없음)'} 으로 적었습니다."


def set_recipe(batch_id: int, form) -> tuple[bool, str]:
    """묶음의 조리법을 적는다 (DiaRUGA 083).

    **엔진이 비면 조리법을 통째로 비운다** = 그 묶음은 자동으로 안 돈다. 끝난
    회차를 그대로 두는 것이 기본이고, 묶음이 늘 때마다 GPU 시간이 곱으로 늘기
    때문이다.

    **가중치 파일이 없어도 저장은 받는다.** 아직 학습이 안 끝났는데 조리법을
    먼저 적어 둘 수 있어야 한다 — 대신 목록에서 "못 돌림" 으로 뜬다. 저장을
    막으면 사람이 파일을 만들 때까지 아무것도 적어 둘 수 없다.
    """
    b = RunBatch.objects.filter(pk=batch_id).first()
    if b is None:
        return False, "그런 묶음이 없습니다."

    backend = (form.get("backend") or "").strip()
    if not backend:
        if not b.recipe:
            return False, f"{b.label} 은 이미 자동으로 돌지 않습니다."
        b.recipe = {}
        b.save(update_fields=["recipe"])
        return True, f"{b.label} 의 조리법을 비웠습니다 — 자동으로 돌지 않습니다."
    if backend not in BACKENDS:
        return False, f"모르는 엔진입니다: {backend}"

    recipe = {"backend": backend}
    for k in RECIPE_NUM:
        raw = (form.get(k) or "").strip()
        if not raw:
            continue
        try:
            recipe[k] = float(raw) if "." in raw or k == "scale" else int(raw)
        except ValueError:
            return False, f"{k} 가 숫자가 아닙니다: {raw}"
    w = (form.get("weights") or "").strip()
    if w:
        recipe["weights"] = w
    if form.get("all_images"):
        recipe["all_images"] = True

    if backend == "yolo" and not recipe.get("weights"):
        return False, "YOLO 는 가중치가 있어야 합니다."

    b.recipe = recipe
    b.save(update_fields=["recipe"])
    # 파일이 없으면 **저장은 되었지만 못 돈다** — 그것을 여기서 말해 준다.
    from django.conf import settings                                # noqa: PLC0415
    if backend == "yolo":
        path = Path(w) if Path(w).is_absolute() else Path(settings.DATA_ROOT) / w
        if not path.exists():
            return True, (f"{b.label} 의 조리법을 적었습니다 — 다만 가중치 파일이 "
                          f"아직 없습니다({path}). 생기기 전까지는 안 돕니다.")
    return True, f"{b.label} 의 조리법을 적었습니다."


def batches_with_recipe() -> list:
    """조리법 화면이 쓰는 목록 — **검출 묶음 전부**. 조리법이 없는 것도 낸다.

    `batch_choices()` 는 "고를 수 있는 것" 이라 검출이 있는 것만 내는데, 여기는
    **아직 아무것도 안 돌린 새 묶음에도 조리법을 적어야** 하므로 다르다.
    """
    rows = list(RunBatch.objects.filter(kind="detect")
                .annotate(n_detections=Count("runs__detections", distinct=True))
                .order_by("-for_review", "-started_at"))
    return rows

