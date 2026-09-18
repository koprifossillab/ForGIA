"""시험이 세우는 자료. DiaRUGA v0.29.0 `tests/factories.py` 의 `make_world` 와 같은
꼴이다 — 그쪽 시험을 옮겨 올 때 그대로 돌게. 검출·교정 자리(`_make_detection` ·
`add_review` …)는 2·3단계에서 온다.

**파일을 실제로 심을 때는 `base.write_image` 를 지난다** — 그것이 뿌리를 확인한다.
"""
from dataclasses import dataclass, field

from ..images import ensure_frame_image, ensure_stack_images
from ..models import Frame, Locality, Sample, Site, Slide, Stack, Viewpoint
from .base import write_image

IMG_W, IMG_H = 64, 48


@dataclass
class World:
    site: Site
    locality: Locality
    sample: Sample
    slide: Slide
    viewpoints: list = field(default_factory=list)

    @property
    def vp(self) -> Viewpoint:
        return self.viewpoints[0]

    @property
    def slug(self) -> str:
        return self.slide.slug


def _write(rel):
    """픽스처가 파일을 쓰는 유일한 자리 — `base.write_image` 를 지난다."""
    return write_image(rel, size=(IMG_W, IMG_H))


def make_world(slug="rs23", *, name=None, area="ant", site_code="RS23",
               loc_code="GC03", sample_code="71cm", depth_cm=71.0,
               fraction_um=125.0, split_denom=None, dry_weight_g=None,
               n_viewpoints=1, n_frames=3, frame_name=None, state="done",
               with_stack=True, with_files=True, cells=True,
               n_candidates=None, kind="core") -> World:
    """지점 하나 · 시료 하나 · 관찰 하나와 그 아래 전부 (시야 · 프레임 · 합성본).

    `with_stack=False` 면 합성본을 안 만들고 싱글턴 시야가 된다.
    `cells=False` 면 시야에 격자 칸 번호를 안 매긴다 (옛 자료 꼴).

    `n_candidates`·`kind` 는 DiaRUGA 시험이 넘기는 인자다 — 검출은 2단계에서
    붙고, 지점 유형은 코어뿐이라 여기서는 받기만 하고 안 쓴다.
    """
    assert kind == "core", "ForGIA 는 코어뿐이다 (P01 5절)"
    site, _ = Site.objects.get_or_create(
        code=site_code, defaults={"name": f"{site_code} 지역", "area": area})
    loc, _ = Locality.objects.get_or_create(site=site, code=loc_code)
    smp, _ = Sample.objects.get_or_create(
        locality=loc, code=sample_code,
        defaults={"depth_cm": depth_cm, "dry_weight_g": dry_weight_g})
    slide = Slide.objects.create(
        name=name or f"{site_code}-{loc_code} {sample_code}"
                     + (f" >{fraction_um:g}um" if fraction_um else ""),
        slug=slug, image_dir=f"photos/260918/{slug}", sample=smp, state=state,
        fraction_um=fraction_um, split_denom=split_denom)
    w = World(site=site, locality=loc, sample=smp, slide=slide)
    for idx in range(n_viewpoints):
        w.viewpoints.append(_make_viewpoint(
            slide, idx, n_frames=n_frames, frame_name=frame_name,
            with_stack=with_stack, with_files=with_files,
            cell=(idx + 1) if cells else None))
    return w


def _make_viewpoint(slide, idx, *, n_frames, frame_name, with_stack,
                    with_files, cell):
    tag = f"g{idx:03d}_Snap-{21000 + idx * 10}"
    vp = Viewpoint.objects.create(slide=slide, idx=idx, tag=tag,
                                  n_frames=n_frames, cell=cell)
    frames = []
    for s in range(n_frames):
        fname = (frame_name if (frame_name and s == 0)
                 else f"Snap-{21000 + idx * 10 + s}")
        rel = f"{slide.image_dir}/{fname}.jpg"
        f = Frame.objects.create(slide=slide, viewpoint=vp, name=fname,
                                 path=rel, width=IMG_W, height=IMG_H, seq=s,
                                 sharpness=100.0 - s, is_sharpest=(s == 0),
                                 um_per_pixel=1.5625, um_per_pixel_source="toml")
        if with_files:
            _write(rel)
        ensure_frame_image(f)
        frames.append(f)
    vp.sharpest_frame = frames[0]
    vp.save(update_fields=["sharpest_frame"])
    if with_stack:
        rel = f"stacked/{slide.slug}/{tag}_focused.jpg"
        st = Stack.objects.create(viewpoint=vp, focused_path=rel,
                                  um_per_pixel=1.5625, native_um_per_pixel=1.5625,
                                  resize_scale=1.0, um_per_pixel_source="toml",
                                  ref_frame=frames[0])
        if with_files:
            _write(rel)
        ensure_stack_images(st)
    return vp


# --- 층만 (0단계 시험이 쓴다) -------------------------------------------------

def make_site(code="RS23", **kw):
    return Site.objects.create(code=code, **kw)


def make_locality(site=None, code="GC03", **kw):
    return Locality.objects.create(site=site or make_site(), code=code, **kw)


def make_sample(locality=None, code="71cm", depth_cm=71.0, **kw):
    return Sample.objects.create(locality=locality or make_locality(),
                                 code=code, depth_cm=depth_cm, **kw)


def make_slide(sample=None, name="RS23-GC03 71cm >125um", slug=None,
               fraction_um=125.0, obs_no=0, **kw):
    """관찰 하나. 시야는 없다. 소속 없는 관찰은 `orphan=True` 로 말한다."""
    orphan = kw.pop("orphan", False)
    if sample is None and not orphan:
        sample = make_sample()
    slug = slug or name.lower().replace(" ", "-").replace(">", "gt")
    return Slide.objects.create(
        name=name, slug=slug, image_dir=f"photos/260918/{name}",
        sample=sample, fraction_um=fraction_um, obs_no=obs_no, **kw)


def make_layers():
    """지역 하나 · 지점 하나 · 시료 둘 · 관찰 셋 (한 시료에 분획 둘). 시야는 없다."""
    site = make_site(code="RS23", name="로스해 23", region="Ross Sea")
    loc = make_locality(site, code="GC03", collect_kind="gravity core")
    s71 = make_sample(loc, code="71cm", depth_cm=71.0, dry_weight_g=12.5)
    s231 = make_sample(loc, code="231cm", depth_cm=231.0)
    a = make_slide(s71, name="RS23-GC03 71cm >125um", fraction_um=125.0, split_denom=8)
    b = make_slide(s71, name="RS23-GC03 71cm >63um", fraction_um=63.0)
    c = make_slide(s231, name="RS23-GC03 231cm", fraction_um=None)
    return {"site": site, "loc": loc, "samples": [s71, s231], "slides": [a, b, c]}


def make_classes():
    """분류표. **2단계에서 `ClassDef` 와 함께 온다** — 지금은 아무것도 안 한다.
    옮겨 온 DiaRUGA 시험이 이것을 부르므로 이름만 둔다."""
    return None
