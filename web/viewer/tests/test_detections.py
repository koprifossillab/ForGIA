"""2단계 — 검출 층과 화면. 검토 대상 묶음(`RunBatch.for_review`)이 무엇을 정하는가."""
import json

from django.urls import reverse

from . import factories as fx
from .base import ForGIATestCase
from .. import data, thresholds as th
from ..models import Candidate, Detection, RunBatch


class DetectionDataTest(ForGIATestCase):
    def setUp(self):
        self.w = fx.make_world(slug="rs23", n_viewpoints=2, n_candidates=3)

    def test_summary_counts_only_counted_classes(self):
        """목록의 "검출" 은 `ClassDef.counted` 만 더한다 — 파편·비유공충은 개체가 아니다."""
        s = data._slide_summary(self.w.slide)
        # 시야 둘 × (foram, broken, fragment) — fragment 는 안 센다
        self.assertEqual(s["n_detected"], 6)
        self.assertEqual(s["n_counted"], 4)
        self.assertEqual(s["detected_groups"], 2)
        self.assertEqual(s["mean_counted"], 2.0)
        self.assertEqual([c["key"] for c in s["counted"]], ["foram", "broken"])

    def test_no_review_batch_means_nothing_is_shown(self):
        """묶음이 안 켜져 있으면 뷰어는 검출을 **하나도** 안 그린다 (DiaRUGA P10 3.6)."""
        RunBatch.objects.update(for_review=False)
        self.assertEqual(data.candidate_rows("rs23"), [])
        self.assertEqual(data._slide_summary(self.w.slide)["n_detected"], 0)
        r = self.client.get(reverse("dataset", args=["rs23"]))
        self.assertContains(r, "검토할 묶음</span>없음")
        self.assertContains(r, "검출 없음")

    def test_other_batch_is_not_counted_but_named(self):
        fx.add_other_engine(self.w.vp, label="yolo-다른회차", n_candidates=5, current=True)
        self.assertEqual(data._slide_summary(self.w.slide)["n_detected"], 6)
        RunBatch.objects.update(for_review=False)
        d = data.dataset_detail("rs23")
        self.assertEqual(d["groups"][0]["elsewhere"], ["yolo-시험", "yolo-다른회차"])
        self.assertContains(self.client.get(reverse("dataset", args=["rs23"])), "다른 묶음")

    def test_dataset_page_draws_masks_and_badges(self):
        r = self.client.get(reverse("dataset", args=["rs23"]))
        self.assertContains(r, "3개 검출")
        self.assertContains(r, '<polygon class="foram"')
        self.assertContains(r, "검출 결과만 보기")
        self.assertContains(r, "검토 중</span>yolo-시험")

    def test_group_page_overlays_masks_only_on_stack(self):
        r = self.client.get(reverse("group", args=["rs23", 0]))
        self.assertContains(r, 'class="masks"')
        self.assertEqual(r.context["stack"]["detection"]["n_candidates"], 3)
        # 싱글턴(합성본 없음)은 프레임 검출 — 그 프레임의 검출을 그린다
        fx.make_world(slug="single", site_code="AM22", with_stack=False, n_candidates=1)
        r = self.client.get(reverse("group", args=["single", 0]))
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context["stack"])
        self.assertEqual(r.context["frames"][0]["detection"]["n_candidates"], 1)


class ScreensTest(ForGIATestCase):
    def setUp(self):
        self.w = fx.make_world(slug="rs23", n_viewpoints=2, n_candidates=3)

    def test_crops_and_filters(self):
        r = self.client.get(reverse("crops", args=["rs23"]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content.decode().count('<a class="crop'), 6)
        r = self.client.get(reverse("crops", args=["rs23"]), {"cls": "fragment"})
        self.assertEqual(r.content.decode().count('<a class="crop fragment"'), 2)
        r = self.client.get(reverse("crops", args=["rs23"]), {"cls": "rejected"})
        self.assertEqual(r.content.decode().count('<a class="crop'), 2)
        self.assertContains(r, "장축범위밖")

    def test_crop_endpoint(self):
        rel = self.w.vp.stack.focused_path
        r = self.client.get(reverse("crop"), {"p": rel, "b": "4,6,14,10", "w": 64})
        self.assertEqual(r.status_code, 200)
        r = self.client.get(reverse("crop"), {"p": rel, "b": "4,6,14,10", "w": 64,
                                              "rot": "30", "out": "20,16"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(reverse("crop"), {"p": rel, "b": "x"}).status_code, 400)
        self.assertEqual(self.client.get(reverse("crop"), {"p": "../x", "b": "0,0,1,1"}).status_code, 404)

    def test_detections_table(self):
        r = self.client.get(reverse("detections", args=["rs23"]))
        self.assertContains(r, "검출 후보")
        self.assertContains(r, "확신도")
        self.assertEqual(r.content.decode().count('title="이 개체가 있는 시야를 연다"'), 6)

    def test_ops_tab_and_review_batch_switch(self):
        other = fx.add_other_engine(self.w.vp, label="yolo-다른회차", n_candidates=2, current=True)
        r = self.client.get(reverse("system_settings_ops"))
        self.assertContains(r, "yolo-다른회차")
        self.assertContains(r, "이것으로 검토")
        # 검출 없는 묶음은 서버가 거절한다
        empty = RunBatch.objects.create(kind="detect", label="빈묶음")
        r = self.client.post(reverse("system_settings_ops"),
                             {"act": "review_batch", "batch": empty.pk}, follow=True)
        self.assertContains(r, "검출이 없습니다")
        r = self.client.post(reverse("system_settings_ops"),
                             {"act": "review_batch", "batch": other.batch_id}, follow=True)
        self.assertContains(r, "검토할 묶음을 yolo-다른회차 로")
        self.assertEqual(data.review_batch_label(), "yolo-다른회차")
        self.assertEqual(data._slide_summary(self.w.slide)["n_detected"], 2)

    def test_recipe_round_trip(self):
        b = RunBatch.objects.get(label="yolo-시험")
        r = self.client.post(reverse("system_settings_ops"), {
            "act": "recipe", "batch": b.pk, "backend": "yolo",
            "weights": "models/none.pt", "conf_min": "0.4", "min_um": "80",
            "max_um": "1500", "yolo_conf": "0.05", "yolo_imgsz": "1024"}, follow=True)
        self.assertContains(r, "가중치 파일이 아직 없습니다")
        b.refresh_from_db()
        self.assertEqual(b.recipe["conf_min"], 0.4)
        self.assertEqual(b.recipe["yolo_imgsz"], 1024)
        plan = data.batches_to_run()
        self.assertEqual((plan[0]["batch"].pk, plan[0]["ready"]), (b.pk, False))
        r = self.client.get(reverse("system_settings_ops"))
        self.assertContains(r, "--conf-min 0.4")
        self.assertContains(r, "못 돌림")


class ThresholdTest(ForGIATestCase):
    def setUp(self):
        self.w = fx.make_world(slug="rs23", n_viewpoints=1, n_candidates=3)

    def test_page_and_preview_and_apply(self):
        r = self.client.get(reverse("thresholds", args=["rs23"]))
        self.assertContains(r, "확신도 하한")
        pool = th.load_pool("rs23")
        self.assertEqual(sum(len(v) for v in pool.values()), 4)
        # conf 0.85 로 올리면 conf 0.8·0.7 둘이 빠지고, 탈락분(0.4)은 그대로다
        r = self.client.post(reverse("threshold_preview"),
                             json.dumps({"slug": "rs23", "values": {"conf_min": 0.85}}),
                             content_type="application/json")
        d = r.json()
        self.assertEqual((d["total"]["before"], d["total"]["after"]), (3, 1))
        self.assertEqual(d["classes"], {"foram": 1})
        self.assertEqual(Candidate.objects.filter(passed=True).count(), 3)   # 저장 안 했다
        r = self.client.post(reverse("threshold_apply"),
                             json.dumps({"slug": "rs23", "values": {"conf_min": 0.85}}),
                             content_type="application/json")
        self.assertEqual(r.json()["after"], 1)
        self.assertEqual(Candidate.objects.filter(passed=True).count(), 1)
        det = Detection.objects.get()
        self.assertEqual(det.thresholds.conf_min, 0.85)
        self.assertEqual(det.thresholds.name, "conf 0.85 · 63~2000 µm")
        h = self.client.get(reverse("threshold_history")).json()
        self.assertEqual(h["rows"][0]["changed"], {"conf_min": 0.85})

    def test_bad_values_rejected(self):
        r = self.client.post(reverse("threshold_preview"),
                             json.dumps({"slug": "rs23", "values": {"conf_min": 1.5}}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        r = self.client.post(reverse("threshold_preview"),
                             json.dumps({"slug": "rs23", "values": {"min_um": 500, "max_um": 100}}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_masks_endpoint_only_reviewing(self):
        det = Detection.objects.get()
        r = self.client.get(reverse("threshold_masks"), {"det": det.pk})
        self.assertEqual(len(r.json()["masks"]), 4)
        RunBatch.objects.update(for_review=False)
        self.assertEqual(self.client.get(reverse("threshold_masks"), {"det": det.pk}).status_code, 404)
