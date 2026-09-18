"""시험이 세우는 자료. DiaRUGA `tests/factories.py`(587줄)의 `make_world` 에서
층 넷만 — 시야·검출·교정은 그 단계에서 함께 온다.

**`Slide.image_dir` 는 `DATA_ROOT` 아래 상대경로다.** 파일을 실제로 심을 때는
`base.write_image` 를 지난다 — 그것이 뿌리를 확인한다.
"""
from ..models import Locality, Sample, Site, Slide


def make_site(code="RS23", **kw):
    return Site.objects.create(code=code, **kw)


def make_locality(site=None, code="GC03", **kw):
    return Locality.objects.create(site=site or make_site(), code=code, **kw)


def make_sample(locality=None, code="71cm", depth_cm=71.0, **kw):
    return Sample.objects.create(locality=locality or make_locality(),
                                 code=code, depth_cm=depth_cm, **kw)


def make_slide(sample=None, name="RS23-GC03 71cm >125um", slug=None,
               fraction_um=125.0, obs_no=0, **kw):
    """관찰 하나. `sample=None` 을 **명시**하면 소속 없는 관찰이 된다 —
    기본값이 아니라 `orphan=True` 로 말한다."""
    orphan = kw.pop("orphan", False)
    if sample is None and not orphan:
        sample = make_sample()
    slug = slug or name.lower().replace(" ", "-").replace(">", "gt")
    return Slide.objects.create(
        name=name, slug=slug, image_dir=f"photos/260918/{name}",
        sample=sample, fraction_um=fraction_um, obs_no=obs_no, **kw)


def make_world():
    """지역 하나 · 지점 하나 · 시료 둘 · 관찰 셋 (한 시료에 분획 둘)."""
    site = make_site(code="RS23", name="로스해 23", region="Ross Sea")
    loc = make_locality(site, code="GC03", collect_kind="gravity core")
    s71 = make_sample(loc, code="71cm", depth_cm=71.0, dry_weight_g=12.5)
    s231 = make_sample(loc, code="231cm", depth_cm=231.0)
    a = make_slide(s71, name="RS23-GC03 71cm >125um", fraction_um=125.0, split_denom=8)
    b = make_slide(s71, name="RS23-GC03 71cm >63um", fraction_um=63.0)
    c = make_slide(s231, name="RS23-GC03 231cm", fraction_um=None)
    return {"site": site, "loc": loc, "samples": [s71, s231], "slides": [a, b, c]}
