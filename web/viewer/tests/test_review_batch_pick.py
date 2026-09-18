"""DiaRUGA v0.29.0 `tests/test_review_batch_pick.py` 에서 왔다.

관리 화면에서 검토할 묶음을 고른다 (DiaRUGA P10 3단계).

**자료를 안 건드린다 — 깃발 하나다.** 그래서 되돌리기가 같은 동작이고, 이것이
P09 5단계(검출 2,132행 UPDATE)를 이것으로 바꾼 이유다. 사본에서 그 UPDATE 를
해 보니 YOLO 가 없는 56 시야가 현재 검출을 잃고 빈 화면이 됐다.

여기서 지키는 것 셋.

1. **누르기 전에 무엇이 달라지는지 안다** — 몇 시야를 덮고 몇이 빈 화면이 될지
2. **서버가 다시 검사한다** — 화면이 고를 수 없게 해 두는 것은 막는 것이 아니다
3. **되짚을 수 있다** — 누가 언제 어디서 어디로 (`Run`)
"""
import json

from django.test import Client
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from .. import data, manage_data
from ..models import Detection, ObjectReview, Run, RunBatch


class PickReviewBatchTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)

    def setUp(self):
        self.c = Client()
        self.other = fx.add_other_engine(self.w.vp)     # yolo-재학습-…
        Detection.objects.filter(run=self.other).update(is_current=True)

    def post(self, batch_id):
        # 묶음 고르기는 **운영 화면**으로 옮겼다 (DiaRUGA 083)
        return self.c.post(reverse("system_settings_ops"),
                           {"act": "review_batch", "batch": batch_id})

    # --- 누르기 전에 보이는가 ----------------------------------------------

    def test_고르기_전에_무엇이_달라지는지_센다(self):
        """**바꾸고 나서 "56 시야가 비었다" 를 알면 늦다** (063 과 같은 자리)."""
        rows = {r["batch"].label: r for r in manage_data.batch_choices()}
        self.assertIn("yolo-시험", rows)
        self.assertIn(self.other.batch.label, rows)

        now = rows["yolo-시험"]
        self.assertTrue(now["on"], "지금 검토 중인 것이 표시되지 않는다")
        self.assertEqual(now["n_views"], 1)
        self.assertEqual(now["n_blank"], 0)
        self.assertGreater(now["n_objects"], 0)

    def test_빈_화면이_될_시야를_센다(self):
        """다른 슬라이드가 있으면 그 묶음이 안 덮는 시야가 생긴다."""
        fx.make_world(slug="다른것", site_code="XX", n_viewpoints=2)
        rows = {r["batch"].label: r for r in manage_data.batch_choices()}
        self.assertEqual(rows[self.other.batch.label]["n_blank"], 2,
                         "안 덮는 시야를 안 셌다")

    def test_검출이_없는_묶음은_목록에_없다(self):
        """고르면 화면이 통째로 빈다 — 고를 이유가 없다."""
        RunBatch.objects.create(kind="detect", label="빈묶음")
        labels = [r["batch"].label for r in manage_data.batch_choices()]
        self.assertNotIn("빈묶음", labels)

    # --- 바꾸면 화면이 따라가는가 ------------------------------------------

    def test_고르면_화면이_그_묶음을_본다(self):
        r = self.post(self.other.batch_id)
        self.assertEqual(r.status_code, 302)

        self.assertEqual(
            list(RunBatch.objects.filter(for_review=True)
                 .values_list("label", flat=True)), [self.other.batch.label])
        dets = data.current_detections(self.w.vp)
        self.assertEqual([d.pk for d in dets],
                         [Detection.objects.get(run=self.other).pk])

    def test_자료를_안_건드린다(self):
        """**깃발 하나다.** 검출도 교정도 그대로여야 되돌리기가 공짜다."""
        fx.add_review(self.w.vp, self.w.keys()[0], removed=True)
        before = {d.pk: d.is_current for d in Detection.objects.all()}
        n_rev = ObjectReview.objects.count()

        self.post(self.other.batch_id)

        self.assertEqual({d.pk: d.is_current for d in Detection.objects.all()},
                         before, "검출의 is_current 가 바뀌었다")
        self.assertEqual(ObjectReview.objects.count(), n_rev)

    def test_되돌리기가_같은_동작이다(self):
        was = data.review_batch_id()
        self.post(self.other.batch_id)
        self.post(was)
        self.assertEqual(data.review_batch_id(), was)
        self.assertEqual(len(data.current_detections(self.w.vp)), 1)

    # --- 서버가 다시 검사하는가 --------------------------------------------

    def test_모르는_묶음은_거절한다(self):
        ok, m = manage_data.set_review_batch(99999)
        self.assertFalse(ok)
        self.assertIn("없습니다", m)

    def test_검출_없는_묶음은_거절한다(self):
        """**화면이 안 내는 것과 서버가 안 받는 것은 다르다** (DiaRUGA 051·027)."""
        empty = RunBatch.objects.create(kind="detect", label="빈묶음")
        ok, m = manage_data.set_review_batch(empty.pk)
        self.assertFalse(ok)
        self.assertIn("검출이 없습니다", m)
        self.assertFalse(RunBatch.objects.get(pk=empty.pk).for_review)

    def test_이미_검토_중인_것은_거절한다(self):
        ok, m = manage_data.set_review_batch(data.review_batch_id())
        self.assertFalse(ok)
        self.assertIn("이미", m)

    def test_검토_대상은_늘_하나다(self):
        self.post(self.other.batch_id)
        self.assertEqual(RunBatch.objects.filter(for_review=True).count(), 1)

    # --- 되짚을 수 있는가 --------------------------------------------------

    def test_기록이_남는다(self):
        """누가 언제 어디서 어디로 — 숫자가 달라졌을 때 되짚을 자리가 있어야 한다."""
        self.post(self.other.batch_id)
        run = Run.objects.filter(kind="reconcile").latest("started_at")
        self.assertEqual(run.params["action"], "set_review_batch")
        self.assertEqual(run.params["from"], "yolo-시험")
        self.assertEqual(run.params["to"], self.other.batch.label)
        self.assertEqual(run.counts["views"], 1)

    def test_바꾼_뒤_할_일을_알린다(self):
        """판정 캐시가 어긋날 수 있다 — 실측으로 9건이 그랬다(DiaRUGA P10 1·2단계)."""
        ok, m = manage_data.set_review_batch(self.other.batch_id)
        self.assertTrue(ok)
        self.assertIn("refilter", m)
