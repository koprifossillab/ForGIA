"""DiaRUGA v0.29.0 `tests/browser/test_map_filter.py` 에서 왔다.

보일 슬라이드 고르기 · 로스해 확대의 나무 (203) — 배선.

3겹은 열쇠와 클래스가 **있다**는 것까지 본다. 여기가 더하는 것은 체크를
끄면 실제로 줄·마커가 사라지고 숫자가 따라오는가, 다시 열어도 남는가,
확대 상태에서 틀 밖 지점이 나무에서 빠지는가다. `.mhid` 가 `.maptree
.mslide` 의 display 에 지는 종류의 고장(063)은 `getComputedStyle` 로만 잡힌다.
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import Locality, Site


class MapFilterTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        u = self.uniq
        self.site = f"RS{u}"
        self.a = f"a-{u}"
        self.b = f"b-{u}"
        fx.make_world(slug=self.a, site_code=self.site, loc_code="GC02",
                      sample_code="10cm", depth_cm=10, n_viewpoints=2,
                      with_files=False)
        fx.make_world(slug=self.b, site_code=self.site, loc_code="GC02",
                      sample_code="20cm", depth_cm=20, n_viewpoints=3,
                      with_files=False)
        Locality.objects.filter(site__code=self.site).update(
            lat=-77.399902, lon=176.299317)
        # 틀 밖 — 남극반도
        self.wap = f"wap-{u}"
        fx.make_world(slug=self.wap, site_code=f"WAP{u}", loc_code="GC47",
                      with_files=False)
        Site.objects.filter(code=f"WAP{u}").update(lat=-65.3676, lon=-64.455)

    def display(self, sel):
        # 선택자에 따옴표가 든다 — 문자열에 끼워 넣지 않고 인자로 넘긴다.
        return self.page.evaluate(
            "s => getComputedStyle(document.querySelector(s)).display", sel)

    def open_map(self):
        page = self.open(reverse("index") + "?area=ant")
        page.click("#viewtoggle button[data-view=map]")
        return page

    def test_체크를_끄면_줄이_사라지고_숫자가_따라온다(self):
        page = self.open_map()
        row = f".maptree .mslide[data-slug='{self.a}']"
        self.assertEqual(self.display(row), "flex")
        self.assertEqual(page.inner_text(
            f".maptree .msite[data-site='{self.site}'] .nsl"), "2")

        page.click("#mapfilter summary")
        page.uncheck(f"#mapfilter input[data-slug='{self.a}']")
        self.assertEqual(self.display(row), "none")
        self.assertEqual(self.display(
            f".maptree .mslide[data-slug='{self.b}']"), "flex")
        self.assertEqual(page.inner_text(
            f".maptree .msite[data-site='{self.site}'] .nsl"), "1")
        # 마커의 시야 수도 남은 줄로 다시 센다 (2 + 3 → 3)
        # SVG 글자라 `inner_text` 가 안 된다 — `text_content` 로 읽는다
        self.assertEqual(page.text_content(
            f".antmap:not(.rossmap) a[data-site='{self.site}'] .sub"), "시야 3")
        self.assertEqual(page.inner_text("#mapfilter-n"), "감춤 1장")

        # 다시 열어도 남는다
        page = self.open_map()
        self.assertEqual(self.display(row), "none",
                         "감춘 것을 다시 열었을 때 잊었다")
        page.click("#mapfilter summary")
        page.click("#mapfilter-all")
        self.assertEqual(self.display(row), "flex")
        self.assertEqual(page.inner_text("#mapfilter-n"), "")

    def test_전부_끄면_지점과_마커까지_사라진다(self):
        page = self.open_map()
        marker = f".antmap:not(.rossmap) a[data-site='{self.site}']"
        self.assertNotEqual(self.display(marker), "none")
        page.click("#mapfilter summary")
        page.uncheck(f"#mapfilter input[data-slug='{self.a}']")
        page.uncheck(f"#mapfilter input[data-slug='{self.b}']")
        self.assertEqual(self.display(
            f".maptree .msite[data-site='{self.site}']"), "none")
        self.assertEqual(self.display(marker), "none")
        self.assertEqual(self.display(
            f".rossmap a[data-core='{self.site}-GC02']"), "none")

    def test_로스해_확대에서는_틀_밖_지점이_나무에서_빠진다(self):
        page = self.open_map()
        wap = f".maptree .msite[data-site='WAP{self.uniq}']"
        self.assertNotEqual(self.display(wap), "none")
        page.click("#mapzoom button[data-zoom=ross]")
        self.assertEqual(self.display(wap), "none")
        self.assertNotEqual(self.display(
            f".maptree .msite[data-site='{self.site}']"), "none")
        page.click("#mapzoom button[data-zoom=all]")
        self.assertNotEqual(self.display(wap), "none")
