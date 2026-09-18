"""폴더 이름 규칙 — `naming.py` 하나뿐이라 여기서 다 잡는다."""
from django.test import SimpleTestCase

from ..naming import base_name, parse_folder, parse_fraction, parse_obs_no


class NamingTests(SimpleTestCase):
    def test_antarctic_rule(self):
        d = parse_folder("RS23-GC03 71cm")
        self.assertEqual((d["site_code"], d["loc_code"], d["sample_code"], d["depth_cm"]),
                         ("RS23", "GC03", "71cm", 71.0))
        self.assertIsNone(d["fraction_um"])
        self.assertEqual(d["obs_no"], 0)

    def test_fraction_and_obs(self):
        d = parse_folder("rs23-gc03 369cm >125um (2)")
        self.assertEqual(d["site_code"], "RS23")
        self.assertEqual(d["fraction_um"], 125.0)
        self.assertEqual(d["obs_no"], 2)
        # 띄어쓰기·µ 도 받는다
        self.assertEqual(parse_fraction("X-Y 1cm > 63 µm"), 63.0)
        self.assertEqual(parse_fraction("X-Y 1cm >150 um"), 150.0)

    def test_base_name_keeps_fraction(self):
        """분획은 남긴다 — 같은 시료라도 분획이 다르면 다른 슬라이드다."""
        self.assertEqual(base_name("RS23-GC03 71cm >125um (1)"), "RS23-GC03 71cm >125um")
        self.assertEqual(parse_obs_no("RS23-GC03 71cm >125um (1)"), 1)

    def test_unknown_folder_writes_nothing(self):
        """규칙에 안 맞으면 전부 None — 부르는 쪽이 아무것도 안 쓴다 (DiaRUGA 063).
        육상 규칙(`BP09-0901`)은 일부러 없다 (P01 5절)."""
        d = parse_folder("BP09-0901")
        self.assertIsNone(d["site_code"])
        self.assertIsNone(d["loc_code"])
        self.assertIsNone(d["depth_cm"])

    def test_depth_code_is_human(self):
        """`71.0cm` 이 아니라 `71cm` — 사람이 부르는 이름이다."""
        self.assertEqual(parse_folder("A-B 71.0cm")["sample_code"], "71cm")
        self.assertEqual(parse_folder("A-B 71.5cm")["sample_code"], "71.5cm")
