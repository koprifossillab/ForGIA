"""DiaRUGA v0.29.0 `test_map_filter.py` 에서 왔다 — 한국 지도 갈래를 뺐다.

지도 모드의 보일 슬라이드 고르기 · 로스해 확대의 나무 (203).

여기서 잡는 것은 **배선의 재료가 화면에 있는가**다 — 줄과 마커에 열쇠
(`data-slug`·`data-site`·`data-core`)가 붙고, 로스해 틀 밖 지점에 `outross`
가 붙으며, 인라인 스크립트가 파싱되는가. 실제로 누르면 감춰지는가는
`browser/test_map_filter.py` 가 본다(`display` 는 거기서만 잡힌다).
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from .jshelp import js_blocks
from .test_map_ross import CORES
from ..models import Locality, Site


class MapFilterRenderTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        for i, ((sc, lc), (lat, lon)) in enumerate(CORES.items()):
            fx.make_world(slug=f"s{i}", site_code=sc, loc_code=lc,
                          sample_code=f"{10 * i + 1}cm", depth_cm=10 * i + 1,
                          with_files=False)
            Locality.objects.filter(site__code=sc, code=lc).update(lat=lat, lon=lon)
        # 틀 밖 — 남극반도
        fx.make_world(slug="wap", site_code="WAP13", loc_code="GC47",
                      with_files=False)
        Site.objects.filter(code="WAP13").update(lat=-65.3676, lon=-64.455)

    def html(self, area):
        r = self.client.get(reverse("index"), {"area": area})
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_틀_밖_지점만_outross_다(self):
        html = self.html("ant")
        self.assertIn('class="msite outross" data-site="WAP13"', html)
        self.assertIn('class="mcore outross" data-core="WAP13-GC47"', html)
        self.assertIn('class="msite" data-site="RS21"', html)
        self.assertIn('class="mcore" data-core="RS21-GC02"', html)
        # 마커와 같은 판정 — 확대 지도에 WAP13 마커는 없다
        self.assertNotIn('data-core="WAP13-GC47">\n      <g class="site', html)
        self.assertIn('data-core="RS21-GC02">', html)

    def test_줄과_마커와_체크박스가_같은_열쇠를_든다(self):
        html = self.html("ant")
        for slug in ("s0", "wap"):
            self.assertIn(f'data-slug="{slug}" data-vp=', html)
            self.assertIn(f'<input type="checkbox" data-slug="{slug}" checked>', html)
        self.assertIn('data-site="WAP13">', html)      # 전체 지도의 마커
        self.assertIn('id="mapfilter-all"', html)


    @unittest.skipUnless(shutil.which("node"), "node 가 없다")
    def test_인라인_스크립트가_파싱된다(self):
        blocks = [b for b in js_blocks(self.html("ant")) if "forgia.maphide" in b]
        self.assertEqual(len(blocks), 1)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(blocks[0])
            path = f.name
        try:
            r = subprocess.run(["node", "--check", path],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[:600])
        finally:
            os.unlink(path)
