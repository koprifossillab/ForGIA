"""`pipeline/scale.py` — 스케일을 어디서 어떤 차례로 읽나 (P01 5절).

**차례가 시험 대상이다.** 하나라도 뒤바뀌면 예외도 경고도 없이 다른 값이
계측 전체에 곱해진다 (DiaRUGA 015 가 그 사고다).
"""
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import scale


def _jpg(path: Path, desc: str | None = None):
    im = Image.new("RGB", (32, 24), (200, 200, 200))
    if desc is None:
        im.save(path, quality=80)
        return
    ex = Image.Exif()
    ex[0x010E] = desc
    im.save(path, quality=80, exif=ex.tobytes())


class ScaleOrderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="forgia-scale-"))
        scale._warned.clear()

    def test_default_when_nothing(self):
        p = self.tmp / "a.jpg"; _jpg(p)
        r = scale.scaling_for(p)
        self.assertEqual(r["source"], "default")
        self.assertEqual(r["um_per_pixel"], scale.DEFAULT_UM_PER_PIXEL)

    def test_toml_is_the_human_fallback(self):
        p = self.tmp / "a.jpg"; _jpg(p)
        (self.tmp / "scale.toml").write_text("um_per_pixel = 2.5\n", encoding="utf-8")
        r = scale.scaling_for(p)
        self.assertEqual((r["source"], r["um_per_pixel"]), ("toml", 2.5))

    def test_exif_beats_toml_unless_override(self):
        p = self.tmp / "a.jpg"
        _jpg(p, json.dumps({"microscope": {"um_per_pixel": 3.125}}))
        (self.tmp / "scale.toml").write_text("um_per_pixel = 2.5\n", encoding="utf-8")
        r = scale.scaling_for(p)
        self.assertEqual((r["source"], r["um_per_pixel"]), ("exif", 3.125))
        # **사람이 못 박으면 그것이 이긴다** — 장비 메타가 실물과 어긋날 때 (DiaRUGA 015)
        (self.tmp / "scale.toml").write_text("um_per_pixel = 2.5\noverride = true\n",
                                             encoding="utf-8")
        r = scale.scaling_for(p)
        self.assertEqual((r["source"], r["um_per_pixel"]), ("toml", 2.5))

    def test_exif_top_level_key_too(self):
        p = self.tmp / "a.jpg"; _jpg(p, json.dumps({"um_per_pixel": 0.78125}))
        self.assertEqual(scale.read_exif(p), 0.78125)

    def test_exif_that_is_not_json_is_ignored(self):
        p = self.tmp / "a.jpg"; _jpg(p, "Foram physical-scale synthetic v2")
        self.assertIsNone(scale.read_exif(p))

    def test_sidecar_for_derived_images(self):
        p = self.tmp / "g000_focused.jpg"; _jpg(p)
        scale.write_scale_sidecar(p, 1.25, source="exif", native_um_per_pixel=1.25)
        r = scale.scaling_for(p)
        self.assertEqual((r["source"], r["um_per_pixel"]), ("sidecar", 1.25))

    def test_implausible_values_are_refused(self):
        p = self.tmp / "a.jpg"; _jpg(p, json.dumps({"um_per_pixel": 12345.0}))
        self.assertIsNone(scale.read_exif(p))
        (self.tmp / "scale.toml").write_text("um_per_pixel = 0\n", encoding="utf-8")
        self.assertIsNone(scale.read_toml(p))

    def test_leica_file_is_noticed_but_not_read_yet(self):
        """실사진 전에는 짐작으로 읽지 않는다 — 있다는 것만 알리고 넘어간다."""
        p = self.tmp / "a.jpg"; _jpg(p)
        (self.tmp / "a_Properties.xml").write_text("<x/>", encoding="utf-8")
        self.assertIsNone(scale.read_leica(p))
        self.assertEqual(scale.scaling_for(p)["source"], "default")

    def test_scale_log_warns_once_per_value(self):
        log = scale.ScaleLog()
        log.add("a", 1.0); log.add("b", 1.0005); log.add("c", 2.0); log.add("d", 2.0)
        self.assertEqual(log.seen, {2.0})
