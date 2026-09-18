"""DiaRUGA v0.29.0 `tests/test_object_link.py` 에서 왔다.

같은 개체 묶음의 스키마 약속 (DiaRUGA P11 1단계 · P12 에서 자리가 바뀌었다).

사람이 프레임마다 골라 만든 것이라 **재생성 불가**다 — 약속이 깨지면 교정과
같은 무게로 잃는다. 여기서 지키는 것:

1. **한 이미지에서 하나** — 같은 프레임의 마스크 둘이 같은 개체일 수 없다
2. **한 마스크는 한 개체에만** — 판정 행이 곧 멤버라 `(image, batch, mask_key)`
   유일 제약이 그것을 이미 보장한다
3. **대표는 둘일 수 없다** — DB 가 막는다 (0개는 저장 쪽 약속 — check_db 가 센다)
4. **RunBatch 를 지우면 개체가 막는다** (PROTECT) — 조용히 NULL 이 되어
   "어느 검출을 보고 한 판단인지" 를 잃으면 안 된다

**P12: 멤버가 곧 판정이다.** `ObjectLinkMember` 가 `ObjectReview` 로 흡수돼
`ObjectLink` 는 `ForamObject` 가 됐다. 그래서 **개체 수는 묶음 수가 아니다** —
판정마다 하나씩 서고, 묶은 것만 멤버가 둘 이상이다. 시험도 그 눈으로 센다.
"""
import json

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from .. import data
from ..models import (Candidate, Image, ForamObject, ObjectReview,
                      RunBatch)


class ObjectLinkSchemaTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)
        cls.batch = RunBatch.objects.get(label="yolo-시험")

    def imgs(self):
        return list(Image.objects.filter(viewpoint=self.w.vp, kind="frame")
                    .order_by("pk"))

    def make_link(self, keys=("10_10_50_50", "12_11_50_50")):
        """판정을 프레임마다 세우고 **한 개체로 묶는다** (DiaRUGA P12).

        `fx.link_reviews` 가 운영의 묶기와 같은 순서를 밟는다 — 그릇 하나만
        남기고 나머지 개체를 걷는다.
        """
        imgs = self.imgs()
        rows = [self.judgement(imgs[i], key) for i, key in enumerate(keys)]
        return fx.link_reviews(rows, rep=0)

    def judgement(self, img, key, batch=-1):
        b = self.batch if batch == -1 else batch
        return fx.new_review(viewpoint=self.w.vp, image=img, batch=b,
                             mask_key=key, bind_method="exact",
                             geom={"bbox": [1, 2, 3, 4],
                                   "polygon": [1, 2, 4, 2, 4, 6, 1, 6]})

    def test_묶고_대표가_생긴다(self):
        link = self.make_link()
        self.assertEqual(link.members.count(), 2)
        self.assertEqual(link.members.filter(is_rep=True).count(), 1)

    def test_한_이미지에서_둘은_못_묶는다(self):
        link = self.make_link()
        row = self.judgement(self.imgs()[0], "99_99_20_20")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ObjectReview.objects.filter(pk=row.pk).update(foram_object=link)

    def test_한_마스크는_한_묶음에만(self):
        """열쇠가 `(image, batch, mask_key)` 라 판정 행이 애초에 하나뿐이다 —
        같은 마스크를 두 개체가 물 자리가 구조적으로 없다.

        **모델을 직접 쓴다** — `fx.new_review` 는 이미 있는 줄을 고쳐 준다(DiaRUGA P18).
        제약은 DB 의 사실이라 팩토리를 지나면 안 본 것이 된다.
        """
        link = self.make_link()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ObjectReview.objects.create(
                viewpoint=self.w.vp, image=self.imgs()[0], batch=self.batch,
                mask_key="10_10_50_50", foram_object=link,
                bind_method="exact", geom={"bbox": [1, 2, 3, 4]})

    def test_대표는_둘일_수_없다(self):
        link = self.make_link()
        row = self.judgement(self.imgs()[2], "30_30_40_40")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ObjectReview.objects.filter(pk=row.pk).update(
                foram_object=link, is_rep=True)

    def test_묶음이_문_RunBatch_는_못_지운다(self):
        """PROTECT — 조용히 NULL 이 되면 어느 검출에 대한 판단인지 잃는다."""
        self.make_link()
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.batch.delete()

    def test_사람이_그린_마스크도_멤버가_된다(self):
        """batch=NULL (DiaRUGA P09 5.2 와 같은 뜻) — 짝 제약이 그쪽도 잡는가.

        **모델을 직접 쓴다.** `fx.new_review` 는 이미 있는 줄을 고쳐 주므로
        (DiaRUGA P18 — 검토 완료가 통과분에 판정을 세운다) 그것으로는 제약을 못 본다.
        제약은 DB 의 사실이지 팩토리의 성질이 아니다.
        """
        imgs = self.imgs()
        row = self.judgement(imgs[0], "5_5_30_30", batch=None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ObjectReview.objects.create(
                viewpoint=self.w.vp, image=imgs[0], batch=None,
                mask_key="5_5_30_30", foram_object=row.foram_object,
                bind_method="exact", geom=row.geom)

    def test_시야를_지우면_묶음도_간다(self):
        """CASCADE — 시야가 사라지면 그 안의 개체는 뜻이 없다."""
        self.make_link()
        self.w.vp.delete()
        self.assertEqual(ForamObject.objects.count(), 0)
        self.assertEqual(ObjectReview.objects.count(), 0)


class SaveLinkEndpointTest(ForGIATestCase):
    """묶음 저장 endpoint (DiaRUGA P11 2단계).

    **서버가 다시 검사한다** — 화면에서 막는 것은 막는 것이 아니다(DiaRUGA 027).
    여기서 어겨 보는 것: 남의 시야 이미지 · 없는 마스크 · 지운 마스크 ·
    대표 0/2개 · 멤버 1개 · 다른 묶음에 이미 속한 마스크.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)
        cls.batch = RunBatch.objects.get(label="yolo-시험")
        # 프레임에도 같은 묶음의 검출을 세운다 — 캐러셀·묶기가 성립하는 모양
        from ..models import Detection, Run
        run = Run.objects.create(kind="detect", batch=cls.batch,
                                 slide=cls.w.slide, status="done")
        cls.frame_imgs = list(Image.objects.filter(viewpoint=cls.w.vp,
                                                   kind="frame").order_by("pk"))
        from .factories import IMG_W, IMG_H
        from ..models import Candidate
        for img in cls.frame_imgs[:2]:
            det = Detection.objects.create(
                viewpoint=cls.w.vp, image=img, image_path=img.path,
                width=IMG_W, height=IMG_H, scale=1.0, um_per_pixel=0.1,
                run=run, is_current=True)
            Candidate.objects.create(
                detection=det, raw_id=0, mask_key="40_50_60_40",
                bbox_x=40, bbox_y=50, bbox_w=60, bbox_h=40,
                center_x=70, center_y=70, area_px=1200, area_um2=6.0,
                major_um=6.0, minor_um=4.0, long_side_um=6.0,
                short_side_um=4.0, aspect_ratio=1.5, fill_ratio=0.6,
                shape_ok=True, circularity=0.8, convexity=0.9, solidity=0.9,
                elongation=1.5, ellipse_iou=0.85, texture=3000.0,
                predicted_iou=0.9, stability_score=0.9,
                polygon=[40, 50, 100, 50, 100, 90, 40, 90],
                passed=True, cls="broken")
        cls.stack_img = Image.objects.get(viewpoint=cls.w.vp, kind="stack")

    def setUp(self):
        from django.test import Client
        self.c = Client()
        self.url = f"/d/{self.w.slide.slug}/g/{self.w.vp.idx}/link"

    def post(self, body):
        import json as _json
        return self.c.post(self.url, _json.dumps(body),
                           content_type="application/json")

    def good_members(self):
        return [
            {"image": self.stack_img.pk, "mask_key": "40_50_60_40",
             "rep": True},
            {"image": self.frame_imgs[0].pk, "mask_key": "40_50_60_40",
             "rep": False},
        ]

    def test_묶고_기하가_스스로_생긴다(self):
        r = self.post({"members": self.good_members()})
        self.assertEqual(r.status_code, 200, r.content[:200])
        link = fx.links().get()
        self.assertEqual(link.viewpoint, self.w.vp)
        m = link.members.get(is_rep=True)
        # **기하는 서버가 뜬다** — 화면이 보낸 것을 믿지 않는다.
        # 칸 이름은 `bbox` 다 (`ObjectReview.geom` 의 표준 · 2026-09-03) —
        # `bbox_xywh` 로 적으면 그 행을 읽는 쪽이 전부 못 읽는다.
        self.assertEqual(m.geom["bbox"], [40, 50, 60, 40])
        self.assertTrue(m.geom["polygon"])

    def test_고치면_갈아끼운다(self):
        self.post({"members": self.good_members()})
        link = fx.links().get()
        ms = self.good_members()
        ms.append({"image": self.frame_imgs[1].pk,
                   "mask_key": "40_50_60_40", "rep": False})
        r = self.post({"link_id": link.pk, "members": ms})
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertEqual(fx.links().count(), 1)
        self.assertEqual(link.members.count(), 3)

    def test_푼다(self):
        self.post({"members": self.good_members()})
        link = fx.links().get()
        r = self.post({"act": "unlink", "link_id": link.pk})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(fx.links().count(), 0)

    def test_남의_시야_이미지는_거절한다(self):
        other = fx.make_world(slug="wap13", site_code="WAP13",
                              loc_code="GC47", sample_code="116cm",
                              depth_cm=116.0)
        stranger = Image.objects.filter(viewpoint=other.vp).first()
        ms = self.good_members()
        ms[1]["image"] = stranger.pk
        r = self.post({"members": ms})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(fx.links().count(), 0)

    def test_없는_마스크는_거절한다(self):
        ms = self.good_members()
        ms[1]["mask_key"] = "1_1_1_1"
        self.assertEqual(self.post({"members": ms}).status_code, 400)

    def test_지운_마스크는_거절한다(self):
        """오검출로 지운 것을 묶으면 "오검출이면서 실재한다" 가 된다."""
        fx.new_review(
            viewpoint=self.w.vp, image=self.frame_imgs[0], batch=self.batch,
            mask_key="40_50_60_40", removed=True,
            geom={"bbox_xywh": [40, 50, 60, 40]})
        r = self.post({"members": self.good_members()})
        self.assertEqual(r.status_code, 400)
        self.assertIn("지운", r.json()["error"])

    def test_대표가_없거나_둘이면_거절한다(self):
        for reps in ((False, False), (True, True)):
            ms = self.good_members()
            ms[0]["rep"], ms[1]["rep"] = reps
            with self.subTest(reps=reps):
                self.assertEqual(self.post({"members": ms}).status_code, 400)

    def test_혼자인_묶음은_거절한다(self):
        r = self.post({"members": self.good_members()[:1]})
        self.assertEqual(r.status_code, 400)

    def test_이미_묶인_마스크는_409_다(self):
        self.post({"members": self.good_members()})
        ms = [
            {"image": self.frame_imgs[0].pk, "mask_key": "40_50_60_40",
             "rep": True},                          # ← 이미 첫 묶음의 멤버
            {"image": self.frame_imgs[1].pk, "mask_key": "40_50_60_40",
             "rep": False},
        ]
        r = self.post({"members": ms})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(fx.links().count(), 1)

    def test_사람이_그린_마스크도_묶인다(self):
        fx.new_review(
            viewpoint=self.w.vp, image=self.frame_imgs[1], batch=None,
            mask_key="7_7_30_30", source="manual",
            geom={"bbox_xywh": [7, 7, 30, 30],
                  "polygon": [7, 7, 37, 7, 37, 37, 7, 37]})
        ms = self.good_members()
        ms.append({"image": self.frame_imgs[1].pk, "mask_key": "7_7_30_30",
                   "rep": False})
        r = self.post({"members": ms})
        self.assertEqual(r.status_code, 200, r.content[:200])
        m = fx.links().get().members.get(mask_key="7_7_30_30")
        self.assertIsNone(m.batch_id)

    def test_GET_은_안_받는다(self):
        self.assertEqual(self.c.get(self.url).status_code, 405)


class LinkRejectedCandidateTest(ForGIATestCase):
    """탈락 후보를 묶으면 되살아난다 (102 · 사용자 요청).

    P11 §4 는 "탈락 후보는 1판에서 뺀다 — 묶기(정체)와 되살리기(판정)를 한
    팝업에서 겹치면 저장 의미가 복잡해진다" 로 미뤄 뒀다. 실제로 쓰다 보니
    **그 프레임에서 가장 좋은 마스크가 문턱에서 떨어져 있는 일**이 있어서
    열었다 — 다만 "고르면 되살아난다" 를 화면이 먼저 말하고, 서버가 행 하나만
    좁게 세운다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)
        cls.batch = RunBatch.objects.get(label="yolo-시험")
        cls.stack_img = Image.objects.get(viewpoint=cls.w.vp, kind="stack")
        cls.frame_img = Image.objects.filter(viewpoint=cls.w.vp,
                                             kind="frame").order_by("pk").first()
        # 프레임에 **탈락한** 후보 하나 (passed=False)
        from ..models import Candidate, Detection, Run
        from .factories import IMG_H, IMG_W
        run = Run.objects.create(kind="detect", batch=cls.batch,
                                 slide=cls.w.slide, status="done")
        det = Detection.objects.create(
            viewpoint=cls.w.vp, image=cls.frame_img,
            image_path=cls.frame_img.path, width=IMG_W, height=IMG_H,
            scale=1.0, um_per_pixel=0.1, run=run, is_current=True)
        cls.rej = Candidate.objects.create(
            detection=det, raw_id=0, mask_key="40_50_60_40",
            bbox_x=40, bbox_y=50, bbox_w=60, bbox_h=40,
            center_x=70, center_y=70, area_px=1200, area_um2=6.0,
            major_um=6.0, minor_um=4.0, long_side_um=6.0, short_side_um=4.0,
            aspect_ratio=1.5, fill_ratio=0.6, shape_ok=True, circularity=0.8,
            convexity=0.9, solidity=0.9, elongation=1.5, ellipse_iou=0.85,
            texture=100.0, predicted_iou=0.9, stability_score=0.9,
            polygon=[40, 50, 100, 50, 100, 90, 40, 90],
            passed=False, reject="텍스처부족")

    def setUp(self):
        from django.test import Client
        self.c = Client()
        self.url = f"/d/{self.w.slide.slug}/g/{self.w.vp.idx}/link"

    def post(self, members):
        import json as _json
        return self.c.post(self.url, _json.dumps({"members": members}),
                           content_type="application/json")

    def members(self):
        return [
            {"image": self.stack_img.pk, "mask_key": self.w.keys()[0],
             "rep": True},
            {"image": self.frame_img.pk, "mask_key": "40_50_60_40",
             "rep": False},
        ]

    def test_탈락_후보를_묶으면_되살아난다(self):
        r = self.post(self.members())
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r.json()["revived"], 1)
        row = ObjectReview.objects.get(image=self.frame_img,
                                       mask_key="40_50_60_40")
        self.assertTrue(row.accepted, "되살아나지 않았다")
        self.assertFalse(row.removed)
        # 묶음에도 멤버로 들어갔다
        self.assertEqual(fx.links().get().members.count(), 2)

    def test_통과분만_묶으면_되살릴_것이_없다(self):
        """되살리기가 **탈락분에서만** 일어나는가 — 통과분까지 손대면
        `accepted` 가 뜻을 잃는다(사람이 되살린 것이 아니다)."""
        from ..models import Candidate
        # 프레임에 통과 후보를 하나 더 세운다 — 탈락분과 같은 검출에
        # **`mask_key` 만으로 짚지 않는다** — 팩토리의 합성본 후보가 같은 키를
        # 쓸 수 있다(실제로 그래서 MultipleObjectsReturned 가 났다). 이 시험이
        # 세운 프레임 쪽 탈락분에서 검출을 얻는다.
        det = self.rej.detection
        Candidate.objects.create(
            detection=det, raw_id=1, mask_key="200_200_50_50",
            bbox_x=200, bbox_y=200, bbox_w=50, bbox_h=50,
            center_x=225, center_y=225, area_px=1250, area_um2=6.2,
            major_um=5.0, minor_um=5.0, long_side_um=5.0, short_side_um=5.0,
            aspect_ratio=1.0, fill_ratio=0.6, shape_ok=True, circularity=0.9,
            convexity=0.9, solidity=0.9, elongation=1.0, ellipse_iou=0.9,
            texture=3000.0, predicted_iou=0.9, stability_score=0.9,
            polygon=[200, 200, 250, 200, 250, 250, 200, 250],
            passed=True, cls="foram")
        ms = self.members()
        ms[1]["mask_key"] = "200_200_50_50"          # 통과분으로 바꾼다
        r = self.post(ms)
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r.json()["revived"], 0, "통과분을 되살렸다")
        # **P12: 행은 생긴다** — 판정 행이 곧 멤버라 묶으려면 있어야 한다.
        # 지켜야 할 것은 그 행이 **되살림 표시를 안 단다**는 쪽이다.
        row = ObjectReview.objects.get(mask_key="200_200_50_50")
        self.assertFalse(row.accepted, "통과분에 되살림 표시를 달았다")
        self.assertFalse(row.removed)

    def test_이미_붙어_있는_분류는_안_덮는다(self):
        """되살리기는 다른 축의 판단을 건드리지 않는다."""
        fx.new_review(
            viewpoint=self.w.vp, image=self.frame_img, batch=self.batch,
            mask_key="40_50_60_40", label="broken", note="사람이 적었다",
            geom={"bbox_xywh": [40, 50, 60, 40]})
        r = self.post(self.members())
        self.assertEqual(r.status_code, 200, r.content[:300])
        row = ObjectReview.objects.get(image=self.frame_img,
                                       mask_key="40_50_60_40")
        self.assertTrue(row.accepted)
        self.assertEqual(row.label, "broken", "분류가 덮였다")
        self.assertEqual(row.note, "사람이 적었다", "코멘트가 덮였다")

    def test_지운_탈락분은_여전히_거절한다(self):
        fx.new_review(
            viewpoint=self.w.vp, image=self.frame_img, batch=self.batch,
            mask_key="40_50_60_40", removed=True,
            geom={"bbox_xywh": [40, 50, 60, 40]})
        r = self.post(self.members())
        self.assertEqual(r.status_code, 400)
        self.assertIn("지운", r.json()["error"])


class LinkLabelSpreadTest(ForGIATestCase):
    """묶인 개체는 **분류를 함께 받는다** (사용자 요청 2026-08-10).

    묶음은 "이 판들의 이것이 같은 개체다" 라는 말이다. 그런데 분류는 판마다
    따로 앉아 있어서, 한 판에서 봉상이라고 정해도 나머지는 그대로였다 —
    사람이 판 수만큼 같은 판단을 되풀이해야 하고, 되풀이하다 하나를 빠뜨리면
    **묶음 안에서 분류가 어긋난다**(학습 자료에서는 모순이다).

    여기서 지키는 것:

    1. 한 판에서 정하면 나머지 판에 같은 분류가 앉는다
    2. **물리는 것도 번진다** — 한 판만 지정이 남아 있으면 안 된다
    3. 분류 말고는 안 건드린다 — 삭제·되살림·코멘트는 판마다 따로 하는 판단이다
    4. 묶이지 않은 개체는 아무 데도 안 번진다
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)
        cls.batch = RunBatch.objects.get(label="yolo-시험")
        cls.extra = fx.add_frame_detections(cls.w.vp)
        cls.stack_img = Image.objects.get(viewpoint=cls.w.vp, kind="stack")
        cls.f1_img = cls.extra[0][1]
        cls.f2_img = cls.extra[1][1]

    def setUp(self):
        from django.test import Client
        self.c = Client()
        self.key = self.w.keys()[0]
        self.other = self.w.keys()[1]
        # 합성본·프레임 둘을 한 개체로 묶는다. 픽스처가 판마다 같은 자리에
        # 후보를 세우므로 `mask_key` 가 그대로 맞는다.
        rows = [fx.new_review(viewpoint=self.w.vp, image=img, batch=self.batch,
                              mask_key=self.key, bind_method="exact",
                              geom={"bbox_xywh": [40, 50, 60, 40]})
                for img in (self.stack_img, self.f1_img, self.f2_img)]
        self.link = fx.link_reviews(rows, rep=0)

    def save(self, labels, image=None, **over):
        import json as _json
        payload = {"stem": self.w.stem(), "slug": self.w.slide.slug,
                   "gid": self.w.vp.idx, "done": False,
                   "removed": [], "accepted": [], "notes": {},
                   "labels": labels,
                   "image": image or self.stack_img.pk}
        payload.update(over)
        from django.urls import reverse
        r = self.c.post(reverse("save_review"), _json.dumps(payload),
                        content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.json()

    def label_of(self, img):
        row = ObjectReview.objects.filter(image=img, mask_key=self.key).first()
        return row.label if row else None

    # --- 1. 번진다 ----------------------------------------------------------

    def test_한_판에서_정하면_나머지_판도_같은_분류다(self):
        self.save({self.key: "broken"})
        self.assertEqual(self.label_of(self.stack_img), "broken")
        self.assertEqual(self.label_of(self.f1_img), "broken",
                         "묶인 판에 분류가 안 번졌다")
        self.assertEqual(self.label_of(self.f2_img), "broken")

    def test_화면에_알려_준다(self):
        """다른 판의 상태는 화면이 열릴 때 받은 것이라, 안 알려 주면 그 판의
        다음 저장이 도로 지운다 — 뷰어는 늘 전체를 보낸다."""
        out = self.save({self.key: "broken"})
        linked = out.get("linked") or {}
        self.assertIn(str(self.f1_img.pk), linked)
        self.assertEqual(linked[str(self.f1_img.pk)][self.key], "broken")
        self.assertNotIn(str(self.stack_img.pk), linked,
                         "내가 보낸 판까지 되돌려줄 이유가 없다")

    def test_프레임에서_정해도_합성본으로_번진다(self):
        """방향이 없다 — 어느 판에서 정하든 묶음 전체가 같아진다."""
        self.save({self.key: "foram"}, image=self.f1_img.pk,
                  stem=self.f1_img.path.rsplit("/", 1)[-1].rsplit(".", 1)[0])
        self.assertEqual(self.label_of(self.stack_img), "foram")
        self.assertEqual(self.label_of(self.f2_img), "foram")

    # --- 2. 물리는 것도 번진다 ---------------------------------------------

    def test_지정을_물리면_다른_판에서도_지워진다(self):
        self.save({self.key: "broken"})
        self.save({})
        self.assertEqual(self.label_of(self.f1_img), "",
                         "한 판만 지정이 남았다 — 묶음 안에서 어긋난다")
        # **P12: 다른 판의 행은 남는다.** 그 행이 곧 묶음의 멤버이기 때문이다 —
        # 지우면 분류를 물렸다는 이유로 **묶음이 깨진다.** 예전에는 멤버가 따로
        # 있어서 빈 교정 행을 치울 수 있었다.
        self.assertTrue(
            ObjectReview.objects.filter(image=self.f1_img,
                                        mask_key=self.key).exists(),
            "묶음 멤버가 분류를 물렸다는 이유로 사라졌다")

    def test_다른_표시가_있으면_행은_남는다(self):
        """분류만 지운다 — 카탈로그가 적은 칸은 그대로여야 한다.

        코멘트는 0036 으로 개체에 갔고 **검토 화면이 안 보내는 칸**이 됐다 —
        종명·등급·자세와 같은 자리다. 이 화면이 대표하지 않는 값을 이 화면의
        저장이 지우면 안 된다.
        """
        self.save({self.key: "broken"})
        row = ObjectReview.objects.get(image=self.f1_img, mask_key=self.key)
        ForamObject.objects.filter(pk=row.foram_object_id).update(
            note="가장자리가 깨졌다")

        self.save({})
        row.refresh_from_db()
        self.assertEqual(row.label, "")
        self.assertEqual(row.note, "가장자리가 깨졌다",
                         "분류를 물리면서 개체의 코멘트를 지웠다")

    # --- 3·4. 넘지 않는 선 --------------------------------------------------

    def test_삭제는_안_번진다(self):
        """묶음은 정체(같은 개체)에 대한 말이고, 오검출 판정은 판마다 다르다 —
        한 프레임에서만 흐릿하게 잡힌 것을 지울 수 있어야 한다."""
        self.save({}, removed=[self.key])
        self.assertTrue(ObjectReview.objects
                        .get(image=self.stack_img, mask_key=self.key).removed)
        row = ObjectReview.objects.filter(image=self.f1_img,
                                          mask_key=self.key).first()
        self.assertTrue(row is None or not row.removed,
                        "삭제가 다른 판으로 번졌다")

    def test_안_묶인_개체는_안_번진다(self):
        self.save({self.other: "broken"})
        # 묶인 키(`self.key`)는 그대로 비어 있다. **행은 있다** — 그것이 멤버다.
        self.assertEqual(self.label_of(self.f1_img), "")
        self.assertFalse(ObjectReview.objects
                         .filter(image=self.f1_img, mask_key=self.other)
                         .exists())


class LinkBindsToCandidateTest(ForGIATestCase):
    """묶으면서 세운 판정 행이 **후보에 제대로 맺히는가** (DiaRUGA P12 에서 놓쳤다).

    `bind_method` 는 "이 교정이 지금 검출의 어느 후보를 가리키는가" 다.
    `orphan` 은 **짝을 못 찾았다**는 뜻이고, `check_db` 3번의 바인딩 집계와
    `rebind.py`·`/orphans/` 가 그 값을 본다.

    묶기가 `judgement_for` 에 후보를 안 넘겨 **짝이 멀쩡히 있는데 `orphan` 으로
    앉았다** — 테스트 인스턴스에서 실제로 그렇게 앉은 것을 보고 찾았다.
    거짓 고아는 나중에 고아 화면에 멀쩡한 개체를 띄운다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)

    def setUp(self):
        from django.test import Client
        self.c = Client()
        self.det = self.w.detection()
        self.imgs = list(Image.objects.filter(viewpoint=self.w.vp, kind="frame")
                         .order_by("pk"))

    def test_묶으면서_세운_행이_후보에_맺힌다(self):
        # 프레임에도 현재 검출을 깐다 — 그래야 그 판의 마스크를 묶을 수 있다
        fx.add_frame_detections(self.w.vp)
        keys = [c.mask_key for c in
                self.det.candidates.all().order_by("mask_key")]
        key = keys[0]
        ms = [{"image": self.det.image_id, "mask_key": key, "rep": True}]
        for img in self.imgs[:1]:
            cand = Candidate.objects.filter(
                detection__image=img, detection__is_current=True,
                mask_key=key).first()
            self.assertIsNotNone(cand, "픽스처가 프레임에 같은 키를 안 냈다")
            ms.append({"image": img.pk, "mask_key": key, "rep": False})

        r = self.c.post(
            reverse("save_link", args=[self.w.slug, self.w.vp.idx]),
            data=json.dumps({"members": ms}),
            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content[:300])

        for m in ms:
            row = ObjectReview.objects.get(image_id=m["image"], mask_key=key)
            self.assertEqual(row.bind_method, "exact",
                             f"짝이 있는데 {row.bind_method} 로 앉았다")
            self.assertIsNotNone(row.candidate_id, "후보를 안 물었다")

    def test_이미_있는_고아_행도_다시_맺는다(self):
        """`judgement_for` 는 있는 행을 그대로 돌려준다 — 옛 바인딩이 `orphan`
        이면 다시 묶어도 남는다. 그런데 묶기가 여기까지 왔다는 것은 그 마스크의
        후보를 **방금 찾았다**는 뜻이라, 짝이 없다는 기록은 거짓이다.

        **테스트 인스턴스에서 풀었다 다시 묶어도 안 고쳐지는 것을 보고 찾았다.**
        """
        fx.add_frame_detections(self.w.vp)
        key = sorted(c.mask_key for c in self.det.candidates.all())[0]
        img = self.imgs[0]
        # 짝이 있는데 고아로 앉아 있는 행 (고치기 전의 묶기가 남긴 모양)
        stale = fx.new_review(viewpoint=self.w.vp, image=img,
                              batch=self.det.batch, mask_key=key,
                              bind_method="orphan")
        self.assertIsNone(stale.candidate_id)

        r = self.c.post(
            reverse("save_link", args=[self.w.slug, self.w.vp.idx]),
            data=json.dumps({"members": [
                {"image": self.det.image_id, "mask_key": key, "rep": True},
                {"image": img.pk, "mask_key": key, "rep": False}]}),
            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content[:300])

        stale.refresh_from_db()
        self.assertEqual(stale.bind_method, "exact", "고아 기록이 남았다")
        self.assertIsNotNone(stale.candidate_id)


class CleanupKeepsMembersTest(ForGIATestCase):
    """**청소가 묶음 멤버를 지우면 안 된다** (DiaRUGA P12 에서 생긴 구멍).

    `save_review` 의 마지막 줄은 payload 가 대표하지 않는 행을 지운다 — "뷰어는
    늘 전체를 보낸다" 가 전제다. P12 에서 **소속이 곧 판정 행**이 되면서 그
    청소가 묶음 멤버까지 집게 됐다: 표시가 하나도 없는 멤버(다른 판에서 분류를
    받고 이 판은 비어 있는 경우가 흔하다)가 저장 한 번에 사라지고, **묶음에서
    한 판이 조용히 빠진다.**

    묶음을 푸는 문은 `/link` 이고 검토 화면이 아니다 — payload 에 없다는 것이
    "묶음에서 뺐다" 는 뜻일 수가 없다.

    **되살려서 잡히는 것을 봤다**: 이 시험은 고치기 전 `1 != 2` 로 실패한다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)

    def setUp(self):
        from django.test import Client
        self.c = Client()
        self.det = self.w.detection()
        self.key = self.w.keys()[0]
        self.frame = self.w.vp.images.filter(kind="frame").first()
        rows = [
            fx.new_review(viewpoint=self.w.vp, image=self.det.image,
                          batch=self.det.batch, mask_key=self.key),
            fx.new_review(viewpoint=self.w.vp, image=self.frame,
                          batch=self.det.batch, mask_key="500_500_20_20"),
        ]
        self.link = fx.link_reviews(rows, rep=0)

    def save_review(self, **over):
        p = {"stem": self.w.stem(), "slug": self.w.slug, "gid": self.w.vp.idx,
             "done": False, "removed": [], "accepted": [],
             "labels": {}, "notes": {}}
        p.update(over)
        r = self.c.post(reverse("save_review"), data=json.dumps(p),
                        content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content[:300])

    def test_빈_payload_가_멤버를_안_지운다(self):
        self.save_review()
        self.assertEqual(self.link.members.count(), 2, "청소가 멤버를 지웠다")

    def test_분류를_물려도_멤버로_남는다(self):
        """분류를 지정했다 물리면 그 행에 표시가 하나도 안 남는다 — 예전이라면
        지울 행이지만 지금은 그것이 소속이다."""
        self.save_review(labels={self.key: "broken"})
        self.save_review()
        self.assertEqual(self.link.members.count(), 2)
        row = ObjectReview.objects.get(image=self.det.image, mask_key=self.key)
        self.assertEqual(row.label, "", "분류는 물려야 한다")

    def test_안_묶인_빈_행은_예전처럼_지운다(self):
        """혼자인 개체는 소속이 아니라 1:1 껍데기다 — 개체까지 함께 걷힌다."""
        solo = fx.new_review(viewpoint=self.w.vp, image=self.det.image,
                             batch=self.det.batch, mask_key=self.w.keys()[1],
                             label="broken")
        oid = solo.foram_object_id
        self.save_review()
        self.assertFalse(ObjectReview.objects.filter(pk=solo.pk).exists(),
                         "표시가 사라진 1:1 행이 남았다")
        self.assertFalse(ForamObject.objects.filter(pk=oid).exists(),
                         "멤버 없는 개체가 유령으로 남았다")

    def test_카탈로그에서_비워도_멤버로_남는다(self):
        """카드가 값을 비우면 그 줄을 지우는데, 멤버면 지우면 안 된다."""
        data.save_catalog_entry(self.w.vp, self.det.image, self.key,
                                species="Globigerina bulloides")
        data.save_catalog_entry(self.w.vp, self.det.image, self.key,
                                species="", cls="", note="")
        self.assertEqual(self.link.members.count(), 2,
                         "카탈로그가 비우면서 멤버를 지웠다")


class LinkUnifyEndpointTest(ForGIATestCase):
    """묶으면서 분류·종명을 **하나로 맞춘다** (사용자 요청 2026-08-10).

    묶기 전에 판마다 따로 적어 둔 것이 서로 다를 수 있다. 화면이 그것을 알리고
    사람이 하나를 고르면 여기로 실려 온다. 여기서 지키는 것:

    1. 고른 값이 **멤버 전부**에 앉는다 (행이 없던 판에는 새로 만든다)
    2. **안 보낸 칸은 안 건드린다** — `None` 과 `""` 는 다른 말이다
    3. 분류·종명 말고는 안 건드린다 — 삭제·되살림·코멘트는 판마다 하는 판단이다
    4. 모르는 분류는 거절한다 (화면에서 막는 것은 막는 것이 아니다)
    5. **묶기가 실패하면 맞추기도 함께 물러난다** — 한쪽만 남으면 안 된다
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)
        cls.batch = RunBatch.objects.get(label="yolo-시험")
        cls.extra = fx.add_frame_detections(cls.w.vp)
        cls.stack_img = Image.objects.get(viewpoint=cls.w.vp, kind="stack")
        cls.f1_img = cls.extra[0][1]

    def setUp(self):
        from django.test import Client
        self.c = Client()
        self.url = f"/d/{self.w.slide.slug}/g/{self.w.vp.idx}/link"
        self.key = self.w.keys()[0]

    def post(self, **over):
        import json as _json
        body = {"members": [
            {"image": self.stack_img.pk, "mask_key": self.key, "rep": True},
            {"image": self.f1_img.pk, "mask_key": self.key, "rep": False}]}
        body.update(over)
        return self.c.post(self.url, _json.dumps(body),
                           content_type="application/json")

    def row(self, img):
        return ObjectReview.objects.filter(image=img,
                                           mask_key=self.key).first()

    def test_고른_분류가_멤버_전부에_앉는다(self):
        r = self.post(label="broken")
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertEqual(self.row(self.stack_img).label, "broken")
        self.assertEqual(self.row(self.f1_img).label, "broken",
                         "행이 없던 판에 안 만들어졌다")
        self.assertEqual(r.json()["unified"][str(self.f1_img.pk)]["label"],
                         "broken", "화면에 알려 주지 않는다")

    def test_종명도_같이_맞춘다(self):
        self.post(species="Globigerina bulloides")
        self.assertEqual(self.row(self.f1_img).species, "Globigerina bulloides")
        self.assertEqual(self.row(self.stack_img).species,
                         "Globigerina bulloides")

    def test_안_보낸_칸은_안_건드린다(self):
        """`그대로` 를 고르면 그 칸은 payload 에 아예 없다."""
        pre = fx.new_review(
            viewpoint=self.w.vp, image=self.f1_img, batch=self.batch,
            mask_key=self.key, label="foram", species="Neogloboquadrina")
        self.post(label="broken")            # 종명은 안 보낸다
        pre.refresh_from_db()
        self.assertEqual(pre.label, "broken")
        self.assertEqual(pre.species, "Neogloboquadrina",
                         "안 보낸 종명이 지워졌다")

    def test_빈_문자열은_비운다(self):
        pre = fx.new_review(
            viewpoint=self.w.vp, image=self.f1_img, batch=self.batch,
            mask_key=self.key, label="foram")
        self.post(label="")
        pre.refresh_from_db()
        self.assertEqual(pre.label, "")

    def test_비우라는_말은_개체를_비운다(self):
        """**P12 에서 뜻이 옮겨 갔다.** 예전에는 "행을 안 만든다" 가 지킬 것이었다
        — 분류가 판마다 살아서 빈 껍데기가 생길 수 있었기 때문이다. 지금은 값이
        개체 하나에 있으므로 지킬 것은 **그 개체가 비었는가**다."""
        self.post(label="", species="")
        row = ObjectReview.objects.get(image=self.f1_img, mask_key=self.key)
        self.assertEqual((row.label, row.species), ("", ""))

    def test_삭제_코멘트는_안_건드린다(self):
        pre = fx.new_review(
            viewpoint=self.w.vp, image=self.f1_img, batch=self.batch,
            mask_key=self.key, removed=True, note="흐릿하다")
        self.post(label="broken")
        pre.refresh_from_db()
        self.assertTrue(pre.removed, "묶으면서 삭제 판정이 뒤집혔다")
        self.assertEqual(pre.note, "흐릿하다")

    def test_모르는_분류는_거절한다(self):
        r = self.post(label="없는분류")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(fx.links().count(), 0,
                         "거절했는데 묶음은 만들어졌다")

    def test_묶기가_실패하면_맞추기도_물러난다(self):
        """대표가 둘이면 묶기가 서고, 그때 분류도 안 남아야 한다."""
        r = self.post(label="broken", members=[
            {"image": self.stack_img.pk, "mask_key": self.key, "rep": True},
            {"image": self.f1_img.pk, "mask_key": self.key, "rep": True}])
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(self.row(self.f1_img))
