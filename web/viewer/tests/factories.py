"""시험 자료를 세우는 자리. **모든 시험이 여기를 지난다.** DiaRUGA v0.29.0
`tests/factories.py` 에서 왔다 — 분류표가 ForGIA 의 넷이고, 관찰에 분획·시료에
건시료·시야에 격자 칸이 붙는다. 육상(`kind="outcrop"`)은 없다.

모델이 16개고 그래프가 깊다.

    Site → Locality → Sample → Slide → Viewpoint → Frame → Image → Detection
                                            ↓                 ↓        ↓
                                    ViewpointReview     ObjectReview  Candidate

매 시험이 자기 자료를 손으로 세우면 **픽스처가 시험보다 길어지고**, 스키마가
바뀔 때 고칠 자리가 시험 수만큼 는다. 여기 하나만 고치면 되게 한다.

## 무엇을 그대로 따르는가

- **`Image` 는 `images.ensure_image` 로만 만든다.** 그것이 "문 하나" 다(DiaRUGA P06 4).
  시험이 옆문으로 만들면 시험만 통과하는 규칙이 생긴다
- **`mask_key` 는 `data.cand_key` 로 만든다.** 손으로 `"10_20_30_40"` 이라
  적으면 키 규칙이 바뀌었을 때 시험이 옛 규칙을 증언한다
- **파일도 실제로 심는다.** 뷰가 디스크에서 읽으므로(`/img`·`/crop`) 행만 있고
  파일이 없으면 화면 시험이 진짜 화면을 안 본 것이 된다

## 쓰는 법

    world = make_world()                      # 한 벌 전부
    world = make_world(slug="rs23", n_viewpoints=2, n_candidates=3)

    a = make_world(slug="a", frame_name="Snap-1")   # 053 재현 —
    b = make_world(slug="b", frame_name="Snap-1")   # 프레임 이름이 겹친다
"""
from dataclasses import dataclass, field

from django.db.models import Count

from .base import write_image
from .. import data
from ..images import ensure_frame_image, ensure_stack_images
from ..models import (Candidate, ClassDef, Detection, Frame, Image, Locality,
                      ForamObject, ObjectReview, Run, RunBatch, Sample, Site,
                      Slide, Stack, Taxon,
                      ThresholdSet, Viewpoint, ViewpointReview)


# 픽스처 이미지의 크기. **실물(2752x2208)보다 작게 잡되, 기록한 값과 실제 파일이
# 반드시 같아야 한다.** 처음에 64x48 파일을 만들어 놓고 `Frame.width` 에는
# 2752 를 적었더니 개체 bbox 가 이미지 밖에 앉아 `/crop` 이 잘라 낼 것을 못
# 찾았다 — **현실에서 생길 수 없는 상태를 시험한 것**이다. 크기는 여기 하나로
# 정하고 행·파일·bbox 가 전부 이 값을 따른다.
IMG_W, IMG_H = 640, 480
# 픽스처의 배율 (µm/px). `scale.py` 의 기본값과 같다 — 후보의 µm 값이 전부 이것을 따른다
UMPP = 1.5625

# `add_other_engine` 의 탈락 후보 자리 (x, y, w, h). **통과분과 안 겹친다.**
# 시험이 이 값을 짚어 그 자리를 누르므로 여기 하나로 정해 둔다 — 시험에 좌표를
# 베껴 두면 자리를 옮길 때 시험만 옛 자리를 누르고 조용히 건너뛴다.
REJECT_BOX = (480, 380, 44, 40)
REJECT_CENTER = (REJECT_BOX[0] + REJECT_BOX[2] // 2,
                 REJECT_BOX[1] + REJECT_BOX[3] // 2)


@dataclass
class World:
    """`make_world` 가 세운 것들. 시험이 짚어 쓸 수 있게 전부 들고 있다."""

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

    def detection(self, vp=None) -> Detection:
        """그 시야의 **대표** 현재 검출 — 합성본이 있으면 합성본.

        예전에는 `.get(is_current=True)` 였다. **시야마다 현재 검출이 하나**라는
        전제인데, 프레임별 검출을 올리면 깨진다(DiaRUGA P09 1단계 · `add_frame_detections`).
        깨진 채로 두면 시험이 `MultipleObjectsReturned` 로 서는데, 그것은
        **시험이 못 쓰게 된 것이지 코드가 틀렸다는 말이 아니라** 무엇이 문제인지를
        가린다.

        집계가 세는 것과 같은 규칙이다(`data.representative_detection`) — 규칙이
        둘이 되면 시험이 화면과 다른 것을 증언한다.
        """
        return data.representative_detection(vp or self.vp)

    def keys(self, vp=None) -> list[str]:
        """그 시야 개체들의 `mask_key`. 화면이 보내는 것과 같은 것들이다."""
        return [c.mask_key for c in self.detection(vp).candidates.all()]

    def stem(self, vp=None) -> str:
        """화면이 `/review` 에 보내는 `stem`."""
        from pathlib import Path
        return Path(self.detection(vp).image_path).stem


# --- 분류표 ----------------------------------------------------------------

# `ClassDef` 는 여덟 칸을 전부 채워야 한다 — 하나라도 비면 **예외는 안 나고 그
# 분류만 조용히 다르게 구른다**(DiaRUGA 038~040).
#
# **운영의 분류표를 그대로 옮겨 적는다.** 아무 값이나 쓰면 안 되는 이유가 하나
# 있다 — `badge` 와 `color` 는 `base.html` 의 CSS 와 짝이고(`.badge.<badge>`),
# 그 짝이 맞는지가 `test_classdef_css.py` 의 시험 대상이다. 지어낸 배지를 쓰면
# 그 시험이 **늘 실패하거나 늘 통과하거나** 둘 중 하나가 되어 아무것도 안 본다.
#
# **옮겨 적은 것이라 한계가 있다** — 운영에 분류가 늘어도 여기는 모른다.
# 그쪽은 `check_db.py` 의 "4. 분류" 가 본다(운영 DB 를 직접 읽는다).
# 여기가 잡는 것은 반대 방향이다: **`base.html` 의 CSS 가 지워지거나 바뀌는 것.**
CLASSES = [
    # key,        label,     short, badge,      color,          hotkey, counted, taxon
    # `migrations/0004` 가 심는 넷 그대로다 — 색·배지는 `base.html` 의 CSS 와 짝
    ("foram",     "온전",     "온전",  "foram",    "196,181,253",  "q",   True,   False),
    ("broken",    "파손",     "파손",  "broken",   "255,180,84",   "w",   True,   False),
    ("fragment",  "파편",     "파편",  "fragment", "150,140,190",  "e",   False,  False),
    ("other",     "비유공충",  "기타",  "other",    "130,130,130",  "r",   False,  False),
]
# 시험이 종명으로 보내는 학명들. **`make_world` 가 심는다** (켜지 않은 채).
TEST_TAXA = ("Globigerina bulloides", "Globigerina sp.", "Globorotalia sp.",
             "Globorotalia inflata", "Neogloboquadrina pachyderma", "Neogloboquadrina",
             "Globigerinita glutinata", "Orbulina universa")

# 후보가 도는 분류 차례. **표의 차례와 다르다** — DiaRUGA 픽스처는 후보를
# `원형(센다) · 원형조각(안 센다) · 봉상(센다) · 봉상조각(안 센다)` 로 돌렸고,
# 그쪽 시험이 "몇 번째 후보가 세어지나" 를 그 무늬로 짚는다. 같은 무늬로 둔다:
# 온전(센다) · 파편(안 센다) · 파손(센다) · 비유공충(안 센다)
CAND_CLASSES = ("foram", "fragment", "broken", "other")


def make_classes():
    """분류표. **시험마다 새로 만들지 말고 이것을 부른다.**

    `data.py` 가 분류표를 캐시하므로(`_class_rows`) 만든 뒤 반드시 무효화한다 —
    안 하면 앞 시험의 표가 다음 시험에 남는다.
    """
    for i, (key, label, short, badge, color, hot, counted, taxon) in enumerate(CLASSES):
        ClassDef.objects.update_or_create(
            key=key,
            defaults={"label": label, "short": short, "badge": badge,
                      "color": color, "hotkey": hot, "counted": counted,
                      "is_taxon": taxon, "sort_order": i, "active": True})
    data.invalidate_classes()


# --- 한 벌 -----------------------------------------------------------------

def make_world(slug="rs23", *, name=None, area="ant", kind="core",
               site_code="RS23", loc_code="GC03", sample_code="71cm",
               depth_cm=71.0, fraction_um=125.0, split_denom=None,
               dry_weight_g=None, cells=True,
               n_viewpoints=1, n_frames=3, n_candidates=2,
               frame_name=None, state="done", with_stack=True,
               with_files=True) -> World:
    """지점 하나 · 시료 하나 · 관찰 하나와 그 아래 전부.

    `frame_name` 을 주면 프레임 이름을 그것으로 못 박는다 — **슬라이드끼리
    프레임 이름이 겹치는 상황**(DiaRUGA 053)을 만들 때 쓴다.

    `with_stack=False` 면 합성본을 안 만들고 **싱글턴 시야**가 된다(프레임 한
    장이 곧 검출 대상). 053 이 난 자리가 거기다.
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
        w.viewpoints.append(
            _make_viewpoint(slide, idx, n_frames=n_frames,
                            n_candidates=n_candidates, frame_name=frame_name,
                            with_stack=with_stack, with_files=with_files,
                            cell=(idx + 1) if cells else None))
    # **학명 몇 줄을 심는다.** 운영에서는 WoRMS 반입이 `Taxon` 을 채우고 화면은
    # 그 목록에서만 고른다(`resolve_taxon` 이 없는 이름을 거절한다) — DiaRUGA 의
    # 시험은 종명을 자유 문자열로 보내므로, 그 이름들이 표에 있어야 같은 시험이
    # 돈다. 켜지 않은 채 둔다 — 쓰이면 켜지는 것(`active`)도 시험 대상이다
    for name in TEST_TAXA:
        make_taxon(name, active=False)
    return w


def _make_viewpoint(slide, idx, *, n_frames, n_candidates, frame_name,
                    with_stack, with_files, cell=None):
    tag = f"g{idx:03d}_Snap-{21000 + idx * 10}"
    vp = Viewpoint.objects.create(slide=slide, idx=idx, tag=tag,
                                  n_frames=n_frames, cell=cell)

    frames = []
    for s in range(n_frames):
        # 이름을 못 박으면 첫 장만 그 이름을 쓴다 — 겹치게 만들려는 것이 그
        # 한 장이고, 나머지까지 같으면 `(slide, name)` 유일 제약에 걸린다.
        fname = (frame_name if (frame_name and s == 0)
                 else f"Snap-{21000 + idx * 10 + s}")
        rel = f"{slide.image_dir}/{fname}.jpg"
        f = Frame.objects.create(slide=slide, viewpoint=vp, name=fname,
                                 path=rel, width=IMG_W, height=IMG_H, seq=s,
                                 sharpness=100.0 - s,
                                 um_per_pixel=1.5625, um_per_pixel_source="toml",
                                 is_sharpest=(s == 0))
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
        img = ensure_stack_images(st)
    else:
        # 싱글턴 시야 — 프레임 한 장이 곧 검출 대상이다.
        img = Image.objects.get(path=frames[0].path)

    det = _make_detection(vp, img, n_candidates=n_candidates)
    # **완료 표시는 묶음에 붙는다** (DiaRUGA 073). 묶음 없는 줄은 시야 코멘트 자리라
    # 거기에 만들면 **운영에 없는 상태**가 된다 — 픽스처가 그런 상태를 만들면
    # 그 위에서 도는 시험이 전부 헛통과한다 (DiaRUGA P10 0단계에서 두 번 당했다).
    ViewpointReview.objects.create(viewpoint=vp, batch=det.batch, done=False)
    return vp


def _make_detection(vp, img, *, n_candidates):
    # 문턱 11개는 전부 모델 기본값이 있다 — **여기서 베끼지 않는다.** 베껴 두면
    # 기본값이 바뀔 때 시험만 옛 값을 증언한다.
    ts, _ = ThresholdSet.objects.get_or_create(name="시험 기본",
                                               defaults={"is_default": True})
    # **현재 검출은 묶음에 들어 있다** (DiaRUGA P09 0단계). 교정의 열쇠가
    # `(image, batch, mask_key)` 라 묶음 없는 검출에는 저장을 받지 않는다 —
    # `batch=None` 은 사람이 그린 개체의 자리이기 때문이다(DiaRUGA P09 5.2).
    #
    # 운영 DB 의 현재 검출 508개가 전부 `yolo-시험` 에 들어 있다. 픽스처가 그
    # 사실을 안 지키면 **시험이 현실에 없는 상태를 만들어 놓고 통과한다** —
    # 실제로 그렇게 짜여 있었고, 0단계에서 가드가 5개를 세워 드러났다.
    # **검토 대상 묶음이 정해져 있다** (DiaRUGA P10). 운영에는 늘 하나가 켜져 있고,
    # 없으면 화면이 빈 목록을 본다 — 픽스처가 그 사실을 안 지키면 시험이
    # 현실에 없는 상태를 만들어 놓고 통과한다(0단계에서 같은 일이 있었다).
    batch, made = RunBatch.objects.get_or_create(kind="detect", label="yolo-시험")
    if made and not RunBatch.objects.filter(for_review=True).exists():
        batch.for_review = True
        batch.save(update_fields=["for_review"])
    run = Run.objects.create(kind="detect", batch=batch, slide=vp.slide,
                             status="done")
    det = Detection.objects.create(
        viewpoint=vp, image=img, image_path=img.path,
        width=img.width or IMG_W, height=img.height or IMG_H,
        scale=1.0, um_per_pixel=1.5625, um_per_pixel_source="toml",
        n_raw_masks=n_candidates + 1, n_sized=n_candidates,
        thresholds=ts, run=run, is_current=True)

    for i in range(n_candidates):
        # bbox 는 전부 이미지 안이어야 한다 (IMG_W x IMG_H).
        x, y = 40 + i * 120, 50 + i * 80
        w, h = 60 + i * 10, 40 + i * 10
        cls = CAND_CLASSES[i % len(CAND_CLASSES)]
        Candidate.objects.create(
            detection=det, raw_id=i,
            mask_key=data.cand_key({"bbox_xywh": [x, y, w, h]}),
            bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h,
            center_x=x + w // 2, center_y=y + h // 2,
            # ForGIA 배율 1.5625 µm/px — 60px 이면 94 µm 로 판정(63~2000)을 통과한다
            area_px=w * h // 2, area_um2=float(w * h) * UMPP ** 2 / 2,
            major_um=w * UMPP, minor_um=h * UMPP,
            long_side_um=w * UMPP, short_side_um=h * UMPP,
            aspect_ratio=w / h, fill_ratio=0.62,
            shape_ok=True, circularity=0.85, convexity=0.95, solidity=0.93,
            elongation=1.2, ellipse_iou=0.88, texture=None,
            predicted_iou=0.9 - i * 0.1, stability_score=0.9 - i * 0.1,
            polygon=[x, y, x + w, y, x + w, y + h, x, y + h],
            passed=True, cls=cls)

    # 탈락분 하나. 통과분만 있으면 "탈락 펼침판" 이 도는지 알 수 없다.
    x, y, w, h = 500, 400, 20, 18
    Candidate.objects.create(
        detection=det, raw_id=n_candidates,
        mask_key=data.cand_key({"bbox_xywh": [x, y, w, h]}),
        bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h,
        center_x=x + w // 2, center_y=y + h // 2,
        # 20px = 31 µm — 하한 63 µm 아래라 `장축범위밖` 이다
        area_px=w * h // 2, area_um2=440.0, major_um=w * UMPP, minor_um=h * UMPP,
        long_side_um=w * UMPP, short_side_um=h * UMPP, aspect_ratio=1.1,
        fill_ratio=0.5, shape_ok=False, texture=None, predicted_iou=0.4,
        polygon=[x, y, x + w, y, x + w, y + h, x, y + h],
        passed=False, reject="장축범위밖")
    return det


def add_frame_detections(vp, *, n_candidates=2):
    """그 시야의 **프레임마다 현재 검출**을 하나씩 더 만든다 (DiaRUGA P09 1단계).

    합성본에 하나 + 프레임마다 하나 — **시야 하나에 현재 검출이 여럿인 상태**다.
    YOLO 는 합성본이 아니라 원본 프레임을 보므로 갈아타면 그 모양이 된다
    (실측: `yolo-재학습` 는 시야 452개에 프레임 검출 1,310개).

    **운영 DB 에는 아직 이 상태가 없다.** 그래서 시험이 먼저 만든다 — 없으면
    "시야마다 이미지가 하나" 를 전제한 코드가 전부 통과한 채로 남고, 갈아타는
    날 한꺼번에 드러난다. 그 날 잃는 것은 재생성 불가한 교정이다.

    돌려주는 것은 `[(frame, image, detection), …]`.
    """
    out = []
    for f in vp.frames.all():
        img = Image.objects.get(path=f.path)
        if vp.detections.filter(image=img, is_current=True).exists():
            continue                     # 싱글턴 시야 — 이미 그 프레임이 대상이다
        out.append((f, img, _make_detection(vp, img,
                                            n_candidates=n_candidates)))
    return out


def add_other_engine(vp, *, label=None, n_candidates=2, frames=False,
                     current=False, code="") -> Run:
    """같은 시야에 **다른 엔진의 검출**을 하나 더 쌓는다. `Run` 을 돌려준다.

    **검출은 덮어쓰지 않고 쌓는다** — `is_current` 가 뷰어가 볼 것을 가리킨다
    (CLAUDE.md). 이 함수가 만드는 것은 `is_current=False` 라 검토 화면에는
    안 나오고, `?batch=<run.id>` 로 골라야 보인다.

    **그리고 그때가 읽기 전용이다** (DiaRUGA 051). 교정은 `mask_key`(bbox 문자열)로
    붙는데 엔진이 다르면 거의 전부 어긋나므로 저장을 받으면 안 된다. 읽기
    전용 화면을 시험하려면 이 자료가 있어야 한다.

    `current=True` 면 **그 묶음 안에서 현재 검출**로 세운다. 운영이 그 모양이다 —
    `is_current` 는 "그 묶음 안에서 최신" 이라 묶음마다 따로 켜져 있고(실측으로
    여러 묶음이 다 켜져 있다), 개체 카탈로그처럼 **묶음을 짚어
    여는 화면**은 그 자료라야 밟힌다. 기본값이 `False` 인 것은 051 계열 시험이
    "옛 검출" 을 필요로 하기 때문이다.

    `code` 는 그 묶음의 카탈로그 코드 (`RunBatch.code`).
    """
    batch, _ = RunBatch.objects.get_or_create(
        kind="detect", label=label or f"yolo-시험-{vp.slide.slug}",
        defaults={"code": code})
    if code and batch.code != code:
        batch.code = code
        batch.save(update_fields=["code"])
    run = Run.objects.create(kind="detect", batch=batch, slide=vp.slide,
                             status="done")

    cur = vp.detections.filter(is_current=True).first()
    if cur is None:
        cur = vp.detections.order_by("id").first()

    imgs = [(cur.image, cur.image_path)]
    if frames:
        for f in vp.frames.all():
            img = Image.objects.get(path=f.path)
            if img.pk != cur.image_id:
                imgs.append((img, f.path))

    dets = []
    for img, path in imgs:
        dets.append(Detection.objects.create(
            viewpoint=vp, image=img, image_path=path,
            width=cur.width, height=cur.height, scale=1.0,
            um_per_pixel=cur.um_per_pixel, um_per_pixel_source="toml",
            n_raw_masks=n_candidates, n_sized=n_candidates,
            run=run, is_current=current))

    # **현재 검출과 다른 자리에 둔다.** 같은 bbox 를 쓰면 `mask_key` 가 겹쳐
    # 교정이 우연히 붙고, "엔진이 다르면 키가 어긋난다" 는 전제가 시험 자료에서
    # 만 성립하지 않게 된다. 판마다도 조금씩 어긋나게 둔다 — 프레임끼리 키가
    # 같으면 "어느 판의 교정인지" 를 가르는 자리가 시험에서 안 눌린다.
    for det_i, det in enumerate(dets):
        for i in range(n_candidates):
            x, y = 300 + i * 90 + det_i * 5, 250 + i * 60 + det_i * 5
            w, h = 55 + i * 7, 45 + i * 7
            Candidate.objects.create(
                detection=det, raw_id=i,
                mask_key=data.cand_key({"bbox_xywh": [x, y, w, h]}),
                bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h,
                center_x=x + w // 2, center_y=y + h // 2,
                area_px=w * h // 2, area_um2=float(w * h) * UMPP ** 2 / 2,
                major_um=w * UMPP, minor_um=h * UMPP,
                long_side_um=w * UMPP, short_side_um=h * UMPP,
                aspect_ratio=w / h, fill_ratio=0.6,
                shape_ok=True, circularity=0.8, convexity=0.9, solidity=0.9,
                elongation=1.2, ellipse_iou=0.86, texture=None,
                predicted_iou=0.9,
                polygon=[x, y, x + w, y, x + w, y + h, x, y + h],
                passed=True, cls=CAND_CLASSES[i % len(CAND_CLASSES)])

        # 탈락분 하나 — 읽기 전용에서 **탈락 펼침판**이 어떻게 구는지 보려면
        # 있어야 한다.
        #
        # **통과분이 안 덮는 자리에 둔다.** 펼침판은 우클릭 메뉴의 "이 자리의
        # 탈락 후보 보기" 로 여는데, 그 항목은 **빈 자리를 눌렀을 때만**
        # (`d.target === null`) 나온다. 통과분 위에 겹쳐 두면 개체 메뉴가 떠서
        # 영영 못 연다.
        x, y, w, h = REJECT_BOX
        Candidate.objects.create(
            detection=det, raw_id=n_candidates,
            mask_key=data.cand_key({"bbox_xywh": [x, y, w, h]}),
            bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h,
            center_x=x + w // 2, center_y=y + h // 2,
            area_px=w * h // 2, area_um2=2100.0, major_um=w * UMPP, minor_um=h * UMPP,
            long_side_um=w * UMPP, short_side_um=h * UMPP, aspect_ratio=1.2,
            fill_ratio=0.5, shape_ok=False, texture=None, predicted_iou=0.4,
            polygon=[x, y, x + w, y, x + w, y + h, x, y + h],
            passed=False, reject="장축범위밖")
    return run


def add_review(vp, mask_key, *, image=None, removed=False, accepted=False,
               label="", note="", species="") -> ObjectReview:
    """교정 한 줄. **`(image, batch, mask_key)` 가 열쇠다.**

    `geom` 을 반드시 채운다 — 교정은 기하를 스스로 들고 있어야 검출기가 바뀌어도
    읽힌다(CLAUDE.md). 빈 `geom` 으로 만들면 시험이 그 규칙을 안 지키는 자료를
    만들어 놓고 통과한다.

    `image` 를 주면 그 이미지의 현재 검출에 붙인다. 안 주면 대표 이미지다 —
    `World.detection()` 과 같은 규칙을 본다(둘로 갈라지면 어긋난다).

    **개체(`ForamObject`)를 함께 세운다** (DiaRUGA P12). 판정은 개체 없이 설 수 없고,
    분류·종명·코멘트는 그쪽에 산다(0036) — 여기서 갈래를 만들면 시험 자료가
    운영과 다른 모양이 되고, 그런 시험은 덮은 줄 알게 한다.
    """
    if image is None:
        det = data.representative_detection(vp)
    else:
        image_id = getattr(image, "pk", image)
        det = vp.detections.get(is_current=True, image_id=image_id)
    cand = det.candidates.filter(mask_key=mask_key).first()
    geom = {}
    if cand is not None:
        geom = {"bbox": cand.bbox_xywh, "polygon": list(cand.polygon)}
    # **이미 있으면 그것을 고친다** (DiaRUGA P18). 검토 완료(`review_done`)가 통과분에
    # 판정을 세워 두므로 그 뒤에 부르면 열쇠가 부딪힌다 —
    # `(image, batch, mask_key)` 는 유일 제약이다. 운영의 문(`judgement_for`)도
    # get-or-create 라, 여기서 늘 새로 만들면 **시험 자료가 운영과 다른 모양**이
    # 된다(이 파일 머리말).
    row = ObjectReview.objects.filter(image=det.image, batch=det.batch,
                                      mask_key=mask_key).first()
    if row is not None:
        obj = row.foram_object
        for f, v in (("label", label), ("note", note)):
            if v:
                setattr(obj, f, v)
        if species:
            obj.taxon = make_taxon(species)
        obj.save()
        row.removed, row.accepted = removed, accepted
        if geom and not row.geom:
            row.geom = geom
        row.save(update_fields=["removed", "accepted", "geom"])
        return row
    obj = ForamObject.objects.create(viewpoint=vp, batch=det.batch,
                                     label=label, note=note,
                                     taxon=make_taxon(species) if species else None)
    row = ObjectReview.objects.create(
        viewpoint=vp, image=det.image, batch=det.batch, mask_key=mask_key,
        candidate=cand, bind_method="exact" if cand else "orphan", geom=geom,
        foram_object=obj, is_rep=True,
        removed=removed, accepted=accepted)
    ForamObject.objects.filter(pk=obj.pk).update(anchor=row)
    return row


def new_review(**kw) -> ObjectReview:
    """판정 행을 손으로 세우는 자리 — **개체를 함께 세운다** (DiaRUGA P12).

    `ObjectReview.objects.create(...)` 를 시험이 직접 부르면 개체가 없어
    `NOT NULL` 로 죽는다. 그리고 죽지 않게 고치더라도, 시험만 옆문으로 만드는
    자료는 **운영에 없는 모양**이 된다(이 파일 머리말의 그 규칙이다).

    `label`·`species`·`note` 는 개체로 넘긴다(0036). `add_review` 는 후보를 찾아
    `geom` 까지 채우는 정식 문이고, 이쪽은 **고아·다른 묶음처럼 후보가 없는
    자료**를 세울 때 쓴다.
    """
    label = kw.pop("label", "")
    species = kw.pop("species", "")
    note = kw.pop("note", "")
    obj = kw.pop("foram_object", None)
    if obj is None:
        vp = kw.get("viewpoint") or kw.get("viewpoint_id")
        if vp is None:
            # 이미지에서 시야를 얻는다 — 부르는 자리마다 시야를 다시 적게
            # 하면 그 값이 이미지와 어긋날 자리가 생긴다.
            img = kw["image"]
            vp = getattr(img, "viewpoint", None) or Image.objects.get(
                pk=getattr(img, "pk", img)).viewpoint
            kw["viewpoint"] = vp
        vp_id = getattr(vp, "pk", vp)
        # **둘을 같이 주면 안 된다** — `batch=<객체>` 와 `batch_id=None` 을 함께
        # 넘기면 뒤엣것이 이겨 묶음이 조용히 비워진다.
        b = kw.get("batch")
        obj = ForamObject.objects.create(
            viewpoint_id=vp_id, label=label, note=note,
            taxon=make_taxon(species) if species else None,
            **({"batch": b} if b is not None else
               {"batch_id": kw.get("batch_id")}))
    kw.setdefault("is_rep", True)
    # **이미 있으면 그것을 고친다** — `add_review` 와 같은 이유다 (DiaRUGA P18). 검토
    # 완료(`review_done`)가 통과분에 판정을 세워 두므로 그 뒤에 부르면
    # `(image, batch, mask_key)` 유일 제약에 부딪힌다. 운영의 문
    # (`judgement_for`)도 get-or-create 라 이쪽이 운영과 같은 모양이다.
    #
    # **유일 제약 자체를 보는 시험은 모델을 직접 쓴다** — 제약은 DB 의 사실이지
    # 이 함수의 성질이 아니다. 여기서 재사용해 주면 그 시험이 아무것도 안 본다.
    have = ObjectReview.objects.filter(
        image_id=getattr(kw.get("image"), "pk", kw.get("image"))
        or kw.get("image_id"),
        batch_id=getattr(kw.get("batch"), "pk", kw.get("batch"))
        or kw.get("batch_id"),
        mask_key=kw.get("mask_key")).first()
    if have is not None:
        dobj = have.foram_object
        for f, v in (("label", label), ("note", note)):
            if v:
                setattr(dobj, f, v)
        if species:
            dobj.taxon = make_taxon(species)
        dobj.save()
        for f, v in kw.items():
            if f in ("image", "image_id", "batch", "batch_id", "mask_key",
                     "viewpoint", "viewpoint_id", "is_rep"):
                continue
            setattr(have, f, v)
        have.save()
        return have
    row = ObjectReview.objects.create(foram_object=obj, **kw)
    if obj.anchor_id is None:
        ForamObject.objects.filter(pk=obj.pk).update(anchor=row)
    return row


def links(vp=None):
    """**묶음** — 멤버가 둘 이상인 개체만 (DiaRUGA P12).

    P12 뒤로는 판정마다 개체가 하나씩 서므로 `ForamObject.objects.count()` 는
    "묶음 몇 개" 가 아니다. 화면·감사 기록이 말하는 묶음은 여전히 *여러 판이
    한 개체* 인 것이고, 시험도 그 눈으로 세야 한다.
    """
    qs = (ForamObject.objects.annotate(_n=Count("members"))
          .filter(_n__gte=2))
    return qs if vp is None else qs.filter(viewpoint=vp)


def review_done(vp, batch=None) -> int:
    """그 시야를 **검토 완료로 표시한 것과 같은 상태**로 만든다 (DiaRUGA P18).

    돌려주는 것은 새로 선 판정 수.

    카탈로그 카드가 개체 단위가 되면서(DiaRUGA P18) **판정이 없는 후보는 카드가 없다** —
    사람이 손대기 전까지 개체는 없는 것이 맞고, 완료를 누르면 남은 마스크가
    전부 개체가 된다(`confirm_kept`, 2026-08-11). 그래서 카탈로그를 보는 시험은
    이 문을 지나야 **운영에 있는 모양**이 된다.

    **운영이 쓰는 그 함수를 그대로 부른다** — 옆문으로 행을 심으면 시험 자료가
    운영과 다른 모양이 되고, 그런 시험은 덮은 줄 알게 한다(이 파일 머리말).
    """
    if batch is None:
        batch = RunBatch.objects.filter(for_review=True).first()
    made = data.confirm_kept(vp, batch)
    # **완료 표시도 함께 남긴다.** 실제 완료(`save_review`)는 둘을 한 자리에서
    # 한다 — 판정을 세우고(`confirm_kept`), `(시야, 묶음)` 에 완료를 적는다.
    # 하나만 하면 카탈로그는 차는데 화면은 "덜 봤다" 고 말한다.
    ViewpointReview.objects.update_or_create(
        viewpoint=vp, batch=batch, defaults={"done": True})
    return sum(len(v) for v in made.values())


def link_reviews(rows, rep=0) -> ForamObject:
    """판정 여럿을 **한 개체로 묶는다** (DiaRUGA P12). 돌려주는 것은 그 개체.

    묶기는 개체를 합치는 일이라, 그릇 하나만 남기고 나머지는 유령이 된다 —
    `data.prune_objects` 와 같은 순서로 **옮긴 뒤에** 걷는다.
    """
    rows = list(rows)
    target = rows[rep].foram_object
    ghosts = []
    for i, row in enumerate(rows):
        if row.foram_object_id != target.pk:
            ghosts.append(row.foram_object_id)
        row.foram_object = target
        row.is_rep = (i == rep)
        row.save(update_fields=["foram_object", "is_rep"])
    ForamObject.objects.filter(pk__in=ghosts, members__isnull=True).delete()
    # **앵커는 가장 오래된 멤버다** (DiaRUGA P18). 운영에서 새로 묶으면 그릇이 빈 개체라
    # `data.reanchor` 가 그렇게 세운다(`merge_into_object` 끝) — 여기서 그릇의
    # 옛 앵커를 그대로 두면 **`rep` 을 무엇으로 주느냐에 따라 번호가 달라져**
    # 시험 자료가 운영과 다른 모양이 된다.
    ForamObject.objects.filter(pk=target.pk).update(
        anchor=min(rows, key=lambda r: r.pk))
    return ForamObject.objects.get(pk=target.pk)


def _write(rel):
    """픽스처가 파일을 쓰는 유일한 자리.

    **`base.write_image` 를 지난다** — 거기서 뿌리가 임시 디렉토리인지 확인한다.
    픽스처가 자기 손으로 쓰면 그 확인을 건너뛴다: 실제로 그렇게 짰다가 시험이
    `/data3/ForGIA` 에 사진을 썼다 (base.py 머리말).

    크기는 `IMG_W x IMG_H` 하나뿐이다 — 행에 적은 값과 파일이 어긋나면 안 된다.
    """
    return write_image(rel, size=(IMG_W, IMG_H))


# --- ForGIA 의 것 ---------------------------------------------------------------

def make_taxon(name, *, rank="Species", active=True, **kw) -> Taxon:
    """학명 하나. **종명 문자열을 받던 DiaRUGA 시험이 그대로 돌게** — `species="…"`
    로 준 이름을 `Taxon` 행으로 세운다(없으면 만들고, 있으면 그것). 운영에서는
    WoRMS 반입이 이 행을 만들고 `resolve_taxon` 은 없는 이름을 거절한다 —
    시험은 반입이 없으니 여기서 심는다."""
    t, _ = Taxon.objects.get_or_create(name=name, authority=kw.pop("authority", ""),
                                       defaults={"rank": rank, "active": active, **kw})
    if active and not t.active:
        t.active = True
        t.save(update_fields=["active"])
    return t


def mask_key(bbox) -> str:
    """0~2단계 시험이 쓰던 이름 — 규칙은 `data.cand_key` 하나다."""
    return data.cand_key({"bbox_xywh": list(bbox)})


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
