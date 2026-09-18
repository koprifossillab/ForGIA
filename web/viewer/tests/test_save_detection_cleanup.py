"""DiaRUGA v0.29.0 `tests/test_save_detection_cleanup.py` 에서 왔다.

**검출 저장의 둘째 트랜잭션이 죽으면 첫째가 남긴 것을 거둔다** (198 ·
`segment_diatoms.save_detection`).

저장은 트랜잭션 둘이다 — 첫째가 `Detection`·`Candidate` 를 쓰고 잠금을 놓은
뒤, 둘째가 `is_current` 를 옮기고 교정을 다시 맺는다. 09-15 에 둘째가
`no such column` 으로 죽는 동안 첫째는 이미 커밋돼 있어 **실패마다 검출 하나와
후보 수백 행이 남았다** — 폴러가 매분 다시 부르니 이틀에 4,212개 · 90만 행.

**되살려서 잡히는 것**을 본다: `save_detection` 의 `except … delete()` 를 떼면
아래가 무너져야 한다.
"""
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

from django.conf import settings

from .base import ForGIATestCase
from . import factories as fx
from ..models import Candidate, Detection, Run, RunBatch

import judge

# `segment_diatoms` 는 cv2·torch 를 머리에서 임포트한다 — 호스트 venv 에는
# 없다. 이 시험은 **파이프라인 이미지 안에서** 돈다 (devlog 198 에 명령이 있다).
HAS_CV2 = importlib.util.find_spec("cv2") is not None and \
    importlib.util.find_spec("torch") is not None
if HAS_CV2:
    import segment_diatoms


def _payload(w):
    """`segment_diatoms.process` 가 만드는 것의 최소 모양."""
    return {
        "size": [fx.IMG_W, fx.IMG_H], "scale": 1.0,
        "um_per_pixel": 0.1, "um_per_pixel_native": 0.1,
        "um_per_pixel_source": "xml",
        "n_raw_masks": 3, "n_sized": 2, "n_candidates": 1,
        "thresholds": dict(judge.DEFAULTS),
        "candidates": [{"bbox_xywh": [10, 10, 40, 20], "center_xy": [30, 20],
                        "area_px": 600, "shape_ok": True, "polygon": [],
                        "cls": "broken"}],
        "rejected": [{"bbox_xywh": [100, 100, 5, 5], "center_xy": [102, 102],
                      "area_px": 20, "shape_ok": False, "polygon": [],
                      "cls": "", "reject": "small"}],
    }


@unittest.skipUnless(HAS_CV2, "cv2·torch 가 있는 파이프라인 이미지에서만 돈다")
class SaveDetectionCleanupTest(ForGIATestCase):

    def setUp(self):
        super().setUp()
        self.w = fx.make_world(slug="rs23", n_candidates=1)
        # 픽스처의 검출이 든 그 묶음 — 같은 묶음 안에서 현재 검출을 갈아 끼운다
        self.batch = RunBatch.objects.get(kind="detect", label="yolo-시험")
        self.run = Run.objects.create(kind="detect", slide=self.w.slide,
                                      batch=self.batch)
        self.img = Path(settings.DATA_ROOT) / self.w.vp.stack.focused_path
        self.old = self.w.detection()
        self.before = (Detection.objects.count(), Candidate.objects.count())

    def test_둘째가_죽으면_첫째의_검출과_후보를_거둔다(self):
        """**사고의 모양** — rebind 가 죽는다. 검출도 후보도 남지 않아야 한다."""
        with mock.patch.object(segment_diatoms.rebind, "rebind_viewpoint",
                               side_effect=RuntimeError("no such column")):
            with self.assertRaises(RuntimeError):
                segment_diatoms.save_detection(_payload(self.w), self.img,
                                               self.run, 0.5,
                                               slide=self.w.slide)
        self.assertEqual((Detection.objects.count(), Candidate.objects.count()),
                         self.before)
        # 원래 있던 현재 검출은 그대로다 — 둘째 트랜잭션이 통째로 물러났다
        self.assertTrue(Detection.objects.get(pk=self.old.pk).is_current)

    def test_멀쩡하면_새_검출이_현재가_된다(self):
        """거두는 갈래가 성공 갈래를 건드리지 않는다."""
        det, n, stat = segment_diatoms.save_detection(
            _payload(self.w), self.img, self.run, 0.5, slide=self.w.slide)
        self.assertEqual(n, 2)
        det.refresh_from_db()
        self.assertTrue(det.is_current)
        self.assertEqual(det.candidates.count(), 2)
        self.assertEqual(Detection.objects.count(), self.before[0] + 1)
