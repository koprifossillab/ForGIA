"""DiaRUGA v0.29.0 `tests/test_review_batch.py` 에서 왔다.

교정이 **어느 검출을 보고 한 판단인지**를 지킨다 (DiaRUGA P09 0단계).

`/review` POST 는 그 화면의 교정 전체를 갈아치운다. 그 "전체" 의 범위가
`(이미지, 묶음)` 이라는 것을 여기서 못 박는다.

**왜 이것이 시험할 값이 있는가** — 이 저장소가 자료를 잃은 세 사고(DiaRUGA 017·027·053)가
전부 그 범위에서 났다. 묶음이 열쇠에 들어가면 **027 이 구조적으로 불가능해진다**:
다른 엔진을 보고 있는 화면의 payload 가 이 묶음의 교정에 닿을 수가 없다.
그 "닿을 수 없음" 을 코드를 읽어서 믿는 대신 눌러서 확인한다.

그리고 실측이 있다 — SAM2 → YOLO 로 갈아탈 때 `removed` **1,076건**이 YOLO 의
통과 후보에 옮겨 붙는다(DiaRUGA P09 4.4). 사람은 자기가 지우지 않은 것이 지워져 있는
것을 본다.
"""
import json

from django.test import Client
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from ..models import Detection, ObjectReview, RunBatch

import rebind


class ReviewBatchScopeTest(ForGIATestCase):
    """저장의 범위가 `(이미지, 묶음)` 인가."""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)

    def setUp(self):
        self.c = Client()

    def post(self, payload, expect=200):
        r = self.c.post(reverse("save_review"), data=json.dumps(payload),
                        content_type="application/json")
        self.assertEqual(r.status_code, expect, r.content[:300])
        return r

    def full(self, **over):
        p = {"stem": self.w.stem(), "slug": self.w.slug, "gid": self.w.vp.idx,
             "done": False, "removed": [], "accepted": [],
             "labels": {}, "notes": {}}
        p.update(over)
        return p

    def _other_batch_review(self, mask_key):
        """같은 이미지·같은 키인데 **다른 묶음**의 교정을 하나 둔다.

        이것이 027 의 모양이다 — 다른 엔진을 보던 화면이 남긴 판단.
        """
        det = self.w.detection()
        other, _ = RunBatch.objects.get_or_create(kind="detect",
                                                  label="yolo-다른묶음")
        return fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=other,
            mask_key=mask_key, removed=True,
            geom={"bbox": [1, 2, 3, 4], "polygon": [1, 2, 4, 2, 4, 6, 1, 6]})

    # --- 다른 묶음 ---------------------------------------------------------

    def test_빈_목록이_다른_묶음의_교정을_안_지운다(self):
        """**027 이 구조적으로 불가능해진다.**

        빈 키 목록은 여전히 "이 묶음의 교정 전체 초기화" 다(정상 경로다).
        지우는 범위가 이 묶음으로 좁혀졌다는 것만 확인한다.
        """
        k = self.w.keys()[0]
        mine = self.post(self.full(removed=[k])) and ObjectReview.objects.get(
            mask_key=k, batch__label="yolo-시험")
        theirs = self._other_batch_review(k)

        self.post(self.full())          # 빈 목록 — 전체 초기화

        self.assertFalse(ObjectReview.objects.filter(pk=mine.pk).exists(),
                         "이 묶음의 교정은 지워져야 한다 (정상 경로)")
        self.assertTrue(ObjectReview.objects.filter(pk=theirs.pk).exists(),
                        "다른 묶음의 교정이 지워졌다 — 027 이 다시 났다")

    def test_같은_키라도_묶음이_다르면_다른_행이다(self):
        k = self.w.keys()[0]
        theirs = self._other_batch_review(k)
        self.post(self.full(labels={k: "broken"}))

        rows = ObjectReview.objects.filter(mask_key=k)
        self.assertEqual(rows.count(), 2, "묶음이 다른데 한 행으로 합쳐졌다")
        self.assertTrue(rows.get(pk=theirs.pk).removed)
        self.assertEqual(rows.exclude(pk=theirs.pk).get().label, "broken")

    def test_저장한_교정이_현재_검출의_묶음을_가리킨다(self):
        k = self.w.keys()[0]
        self.post(self.full(labels={k: "broken"}))
        obj = ObjectReview.objects.get(mask_key=k,
                                         foram_object__label="broken")
        self.assertEqual(obj.batch_id, self.w.detection().batch.pk)
        self.assertEqual(obj.source, "engine")

    # --- 사람이 그린 것 ----------------------------------------------------

    def test_빈_목록이_사람이_그린_개체를_안_지운다(self):
        """`batch=NULL` 은 **어느 묶음에도 속하지 않는다** (DiaRUGA P09 5.2).

        엔진 교정의 payload 는 그것을 대표하지 않으므로 쓸어 가면 안 된다.
        여기가 무너지면 사람이 그린 마스크가 **검토 저장 한 번에** 사라진다.
        """
        det = self.w.detection()
        drawn = fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=None, source="manual",
            mask_key="m0a1b2c3", label="broken",
            geom={"bbox": [10, 10, 20, 20],
                  "polygon": [10, 10, 30, 10, 30, 30, 10, 30]})

        self.post(self.full())

        self.assertTrue(ObjectReview.objects.filter(pk=drawn.pk).exists(),
                        "사람이 그린 개체가 지워졌다 — 재생성 불가한 자료다")

    def test_사람이_그린_것은_같은_이미지에_같은_키가_둘일_수_없다(self):
        """**SQLite 는 NULL 끼리 안 부딪힌다** — 부분 제약을 따로 둔 이유다."""
        from django.db import IntegrityError
        det = self.w.detection()
        kw = dict(viewpoint=self.w.vp, image=det.image, batch=None,
                  source="manual", mask_key="m0a1b2c3",
                  geom={"bbox": [1, 1, 2, 2]})
        row = fx.new_review(**kw)
        # **모델을 직접 쓴다** — 팩토리는 이미 있는 줄을 고쳐 준다 (DiaRUGA P18).
        with self.assertRaises(IntegrityError):
            ObjectReview.objects.create(foram_object=row.foram_object, **kw)

    # --- 묶음이 없는 검출 --------------------------------------------------

    def test_묶음_없는_검출에는_저장을_거절한다(self):
        """`batch=None` 자리는 사람이 그린 개체의 것이라 섞으면 안 된다.

        **P10 이 이것을 구조로 막는다.** 화면이 보는 것은 `for_review` 와
        `is_current` 가 **둘 다 켜진 것**이라, 묶음이 없는 검출은 **애초에
        화면에 안 나온다** —
        저장이 그것을 가리킬 수도 없다. 예전에는 `save_review` 안의 가드가
        잡았고 지금은 그 앞에서 걸린다.

        **못 한 것은 오류로 말한다** — 어느 쪽이든 409 이고, 저장한 척하는
        갈래는 없다.
        """
        # `stem`·`keys` 도 검토 대상을 지나므로 **먼저 받아 둔다** — 묶음을
        # 떼고 나면 화면이 아무것도 안 보고, 그러면 payload 조차 못 만든다.
        payload = self.full(labels={self.w.keys()[0]: "broken"})
        Detection.objects.filter(viewpoint=self.w.vp,
                                 is_current=True).update(run=None)
        r = self.post(payload, expect=409)
        self.assertIn("현재 검출이 없다", r.json()["error"])
        self.assertEqual(ObjectReview.objects.count(), 0)


class RebindScopeTest(ForGIATestCase):
    """`rebind` 가 **그 이미지·그 묶음**만 건드리는가 (DiaRUGA P09 3-(4)).

    `save_review` 는 P06 5a 에서 이미지로 좁혔는데 `rebind` 는 같이 안 좁혔다.
    시야마다 이미지가 하나라 안 드러났고, 프레임별 검토로 가면 **합성본 위의
    교정이 프레임 검출에 옮겨 붙는다** — 같은 시야라 좌표계가 같아서 IoU 가
    실제로 맞는다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3, n_frames=2)

    def test_다른_묶음의_교정은_다시_맺지_않는다(self):
        det = self.w.detection()
        other, _ = RunBatch.objects.get_or_create(kind="detect", label="yolo-딴것")
        cand = det.candidates.filter(passed=True).first()
        # 같은 이미지·같은 자리인데 묶음만 다른 교정. IoU 로는 반드시 맞는다.
        theirs = fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=other,
            mask_key="딴묶음키", removed=True,
            geom={"bbox": cand.bbox_xywh, "polygon": list(cand.polygon)})

        rebind.rebind_viewpoint(self.w.vp, det)

        theirs.refresh_from_db()
        self.assertIsNone(theirs.candidate_id,
                          "다른 묶음의 교정이 이 검출에 맺혔다")
        self.assertEqual(theirs.bind_method, "orphan")

    def test_사람이_그린_개체는_다시_맺지_않는다(self):
        det = self.w.detection()
        cand = det.candidates.filter(passed=True).first()
        drawn = fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=None, source="manual",
            mask_key="m7f3a91c2",
            geom={"bbox": cand.bbox_xywh, "polygon": list(cand.polygon)})

        rebind.rebind_viewpoint(self.w.vp, det)

        drawn.refresh_from_db()
        self.assertIsNone(drawn.candidate_id)
        self.assertEqual(drawn.source, "manual")

    def test_같은_묶음_안에서는_전처럼_다시_맺는다(self):
        """좁히느라 **하던 일까지 막지는 않았는가.**"""
        det = self.w.detection()
        cand = det.candidates.filter(passed=True).first()
        mine = fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=det.batch,
            mask_key="어긋난키", removed=True,
            geom={"bbox": cand.bbox_xywh, "polygon": list(cand.polygon)})

        stat = rebind.rebind_viewpoint(self.w.vp, det)

        mine.refresh_from_db()
        self.assertEqual(mine.candidate_id, cand.pk)
        self.assertEqual(mine.bind_method, "iou")
        self.assertEqual(stat["iou"], 1)


class ForReviewProductTest(ForGIATestCase):
    """화면이 보여줄 검출은 **`for_review` 와 `is_current` 가 둘 다 켜진 것**이다
    (DiaRUGA P10 3.1).

    `is_current` 는 "그 묶음 **안에서** 최신" 이라는 좁은 뜻이고, 어느 묶음을
    볼지는 묶음이 정한다. 묶음을 함께 안 보면 **나란히 쌓아 둔 다른 엔진의 검출이
    화면에 함께 뜬다** — 지금 자료로도 시야마다 검출이 여럿이 된다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)

    def test_검토_대상_묶음의_검출만_본다(self):
        from .. import data
        other = fx.add_other_engine(self.w.vp)          # is_current=False 로 쌓인다
        Detection.objects.filter(run=other).update(is_current=True)

        dets = data.current_detections(self.w.vp)
        self.assertEqual(len(dets), 1, "다른 묶음의 검출이 화면에 섞였다")
        self.assertEqual(dets[0].batch.label, "yolo-시험")

    def test_검토_대상을_옮기면_화면도_따라간다(self):
        from .. import data
        other = fx.add_other_engine(self.w.vp)
        Detection.objects.filter(run=other).update(is_current=True)

        RunBatch.objects.filter(for_review=True).update(for_review=False)
        RunBatch.objects.filter(pk=other.batch_id).update(for_review=True)

        dets = data.current_detections(self.w.vp)
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0].batch.pk, other.batch_id,
                         "검토 대상을 옮겼는데 화면이 안 따라왔다")

    def test_검토_대상이_없으면_빈_목록이다(self):
        """**조용히 아무 묶음이나 보여주지 않는다** (DiaRUGA P10 3.6).

        무엇을 검토하는지 모른 채 교정을 쌓는 것보다 빈 화면이 낫다.
        """
        from .. import data
        RunBatch.objects.filter(for_review=True).update(for_review=False)
        self.assertEqual(data.current_detections(self.w.vp), [])

    def test_검토_대상은_하나뿐이다(self):
        """둘이면 화면이 어느 것을 그릴지 모른다 — DB 가 막는다."""
        from django.db import IntegrityError
        other = fx.add_other_engine(self.w.vp)
        with self.assertRaises(IntegrityError):
            RunBatch.objects.filter(pk=other.batch_id).update(for_review=True)
