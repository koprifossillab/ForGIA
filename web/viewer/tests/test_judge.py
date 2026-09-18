"""`pipeline/judge.py` — 관문 둘(크기·확신도)과 중첩 정리. Django 를 안 쓴다."""
import unittest

import judge


def rec(**kw):
    base = {"bbox_xywh": [0, 0, 100, 80], "area_px": 5000, "shape_ok": True,
            "major_um": 200.0, "predicted_iou": 0.8}
    base.update(kw)
    return base


class ClassifyTest(unittest.TestCase):
    def test_gates(self):
        th = judge.Thresholds()
        self.assertEqual(judge.classify(rec(), th), ("foram", None))
        self.assertEqual(judge.classify(rec(shape_ok=False), th), (None, "형태측정불가"))
        self.assertEqual(judge.classify(rec(major_um=40.0), th), (None, "장축범위밖"))
        self.assertEqual(judge.classify(rec(major_um=3000.0), th), (None, "장축범위밖"))
        self.assertEqual(judge.classify(rec(predicted_iou=0.1), th), (None, "확신도부족"))
        # conf 가 없으면(다른 검출기) 확신도 관문은 건너뛴다
        self.assertEqual(judge.classify(rec(predicted_iou=None), th), ("foram", None))

    def test_thresholds_object(self):
        th = judge.Thresholds(conf_min=0.5)
        self.assertEqual(th.as_dict(), {"min_um": 63.0, "max_um": 2000.0, "conf_min": 0.5})
        self.assertEqual(judge.classify(rec(predicted_iou=0.45), th), (None, "확신도부족"))

    def test_collapse_keeps_larger(self):
        a, b = rec(area_px=100), rec(area_px=300)
        out, dropped = judge.collapse_boxes([a, b])
        self.assertEqual((len(out), dropped), (1, 1))
        self.assertIs(out[0], b)

    def test_dedupe_drops_aggregate_and_near_duplicates(self):
        big = rec(bbox_xywh=[0, 0, 100, 100], area_px=10000, predicted_iou=0.9)
        k1 = rec(bbox_xywh=[5, 5, 40, 40], area_px=1600, predicted_iou=0.8)
        k2 = rec(bbox_xywh=[55, 55, 40, 40], area_px=1600, predicted_iou=0.8)
        k3 = rec(bbox_xywh=[5, 55, 40, 40], area_px=1600, predicted_iou=0.7)
        k4 = rec(bbox_xywh=[55, 5, 40, 40], area_px=1600, predicted_iou=0.7)
        kept, rej = judge.apply([big, k1, k2, k3, k4], judge.Thresholds())
        self.assertNotIn(big, kept)                      # 자식 넷이 면적의 64% 를 설명한다
        self.assertEqual([r["reject"] for r in rej], ["중첩정리"])
        # 거의 같은 마스크 둘 — 확신도 높은 쪽이 남는다
        p, q = rec(predicted_iou=0.6), rec(bbox_xywh=[1, 1, 100, 80], predicted_iou=0.9)
        kept, _ = judge.apply([p, q], judge.Thresholds())
        self.assertEqual(kept, [q])
