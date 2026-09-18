"""DiaRUGA v0.29.0 `tests/test_coverage_gap.py` 에서 왔다.

고른 묶음이 안 덮는 시야 — **왜 비었는지 화면이 말한다** (DiaRUGA P10 4단계).

묶음을 고를 수 있게 되면서 빈 화면의 뜻이 둘로 갈렸다.

1. **아직 안 돌렸다** — 할 일은 파이프라인을 돌리는 것
2. **이 묶음에만 없다** — 할 일은 묶음을 바꾸거나 이 묶음으로 다시 돌리는 것

둘을 한 문구로 적으면 사람이 **이미 돌아 있는 것을 다시 돌리러 간다.** 예전 문구
("합성은 끝났고 검출은 아직입니다")는 2번에서 그냥 거짓말이다.

**조용히 다른 묶음으로 물러나지 않는다**(DiaRUGA P10 3.6). 물러나면 화면은 차지만
"지금 무엇을 보고 있나" 가 흐려지고, 그 위에 쌓인 교정은 어느 묶음의 것인지
모르게 된다 — P09 0단계가 교정을 묶음에 매단 이유가 그것이다.
"""
from django.test import Client
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from .. import data
from ..models import Detection


class CoverageGapTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        # 시야 셋 — 덮인 것 · 다른 묶음에만 있는 것 · 아무 데도 없는 것
        cls.w = fx.make_world(slug="rs23", n_viewpoints=3, n_candidates=3)
        cls.covered, cls.elsewhere_vp, cls.nowhere = cls.w.viewpoints

        cls.other = fx.add_other_engine(cls.elsewhere_vp)
        Detection.objects.filter(run=cls.other).update(is_current=True)
        # 검토 대상 묶음의 검출만 걷는다 — "이 묶음에는 없다" 를 만든다
        Detection.objects.filter(viewpoint=cls.elsewhere_vp).exclude(
            run=cls.other).delete()
        Detection.objects.filter(viewpoint=cls.nowhere).delete()

    def setUp(self):
        self.c = Client()

    # --- 어느 묶음에 있는지 세는 자리 --------------------------------------

    def test_다른_묶음에_있는_것을_찾는다(self):
        m = data.batches_elsewhere(vp=self.elsewhere_vp)
        self.assertEqual(m.get(self.elsewhere_vp.id),
                         [self.other.batch.label])

    def test_아무_데도_없으면_비어_있다(self):
        self.assertEqual(data.batches_elsewhere(vp=self.nowhere), {})

    def test_검토_중인_묶음은_안_센다(self):
        """지금 보고 있는 것을 "다른 묶음에 있습니다" 라고 적으면 안 된다."""
        m = data.batches_elsewhere(vp=self.covered)
        self.assertEqual(m.get(self.covered.id, []), [])

    def test_묶음_이름을_안다(self):
        self.assertEqual(data.review_batch_label(), "yolo-시험")

    # --- 시야 화면 ---------------------------------------------------------

    def test_다른_묶음에_있다고_적는다(self):
        d = data.group_detail("rs23", self.elsewhere_vp.idx)
        self.assertTrue(d["stack"]["detection"]["preview_only"])
        self.assertEqual(d["elsewhere"], [self.other.batch.label])
        self.assertEqual(d["review_batch"], "yolo-시험")

    def test_아무_데도_없으면_안_적는다(self):
        d = data.group_detail("rs23", self.nowhere.idx)
        self.assertTrue(d["stack"]["detection"]["preview_only"])
        self.assertEqual(d["elsewhere"], [])

    def test_화면에_이유가_나온다(self):
        r = self.c.get(reverse("group", args=["rs23", self.elsewhere_vp.idx]))
        body = r.content.decode()
        self.assertIn("yolo-시험", body)
        self.assertIn("의 검출이 없습니다", body)
        self.assertIn(self.other.batch.label, body)
        self.assertNotIn("검출은 아직입니다", body,
                         "다른 묶음에 있는데 '아직' 이라고 적는다")

    def test_안_돌린_시야에는_옛_문구_그대로(self):
        """**2번이 아닌 것까지 바꾸면 안 된다** — 그쪽은 정말 안 돌린 것이다."""
        r = self.c.get(reverse("group", args=["rs23", self.nowhere.idx]))
        body = r.content.decode()
        self.assertIn("검출은 아직입니다", body)
        self.assertNotIn("의 검출이 없습니다", body)

    # --- 시야 목록 ---------------------------------------------------------

    def test_목록이_줄마다_표시한다(self):
        g = {x["id"]: x for x in data.dataset_detail("rs23")["groups"]}
        self.assertFalse(g[self.covered.idx]["missing"])
        self.assertTrue(g[self.elsewhere_vp.idx]["missing"])
        self.assertEqual(g[self.elsewhere_vp.idx]["elsewhere"],
                         [self.other.batch.label])
        self.assertTrue(g[self.nowhere.idx]["missing"])
        self.assertEqual(g[self.nowhere.idx]["elsewhere"], [])

    def test_목록이_센다(self):
        """**시야를 하나씩 열어 보고서야 아는 것은 늦다** (063 과 같은 자리)."""
        d = data.dataset_detail("rs23")
        self.assertEqual(d["missing_groups"], 2)
        self.assertEqual(d["missing_elsewhere"], 1)

    def test_목록_화면에_숫자가_나온다(self):
        body = self.c.get(reverse("dataset", args=["rs23"])).content.decode()
        self.assertIn("의 검출 없음 2", body)
        self.assertIn("다른 묶음에는 있습니다", body)

    def test_다_덮으면_아무_말도_안_한다(self):
        """**빈 자리에 늘 무언가 적혀 있으면 사람이 안 읽는다.**"""
        # 슬러그는 URL 을 만들 수 있는 모양이어야 한다 — 아니면 화면이 아니라
        # `reverse()` 가 먼저 죽는다 (DiaRUGA 057)
        w = fx.make_world(slug="rs23-b", site_code="XX", n_viewpoints=2)
        d = data.dataset_detail(w.slide.slug)
        self.assertEqual(d["missing_groups"], 0)
        body = self.c.get(
            reverse("dataset", args=[w.slide.slug])).content.decode()
        self.assertNotIn("의 검출 없음", body)
