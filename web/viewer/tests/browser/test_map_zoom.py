"""DiaRUGA v0.29.0 `tests/browser/test_map_zoom.py` 에서 왔다.

남극 지도의 전체 ↔ 로스해 확대 전환 (199).

3겹이 두 SVG 가 **있다**는 것까지 본다. 여기가 더하는 것은 **배선**이다 —
단추·틀 사각형을 누르면 실제로 한쪽이 보이고 다른 쪽이 감춰지는가, 그리고
다시 열어도 고른 것이 남는가. `[hidden]` 이 `.antmap` 의 display 에 지는
종류의 고장(063)은 `getComputedStyle` 로만 잡힌다.
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import Locality


class MapZoomTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        u = self.uniq
        fx.make_world(slug=f"rs21-{u}", site_code=f"RS{u}", loc_code="GC02",
                      with_files=False)
        Locality.objects.filter(site__code=f"RS{u}").update(
            lat=-77.399902, lon=176.299317)

    def display(self, sel):
        return self.page.evaluate(
            f"getComputedStyle(document.querySelector('{sel}')).display")

    def open_map(self):
        page = self.open(reverse("index") + "?area=ant")
        page.click("#viewtoggle button[data-view=map]")
        return page

    def test_단추로_갈아타고_다시_열어도_남는다(self):
        page = self.open_map()
        self.assertEqual(self.display(".rossmap"), "none")
        self.assertEqual(self.display(".antmap:not(.rossmap)"), "block")

        page.click("#mapzoom button[data-zoom=ross]")
        self.assertEqual(self.display(".rossmap"), "block")
        self.assertEqual(self.display(".antmap:not(.rossmap)"), "none")
        self.assertEqual(page.get_attribute("#mapzoom button[data-zoom=ross]",
                                            "aria-pressed"), "true")

        page = self.open_map()
        self.assertEqual(self.display(".rossmap"), "block",
                         "고른 범위를 다시 열었을 때 잊었다")
        page.click("#mapzoom button[data-zoom=all]")
        self.assertEqual(self.display(".rossmap"), "none")

    def test_틀_사각형을_눌러도_확대로_간다(self):
        page = self.open_map()
        page.click(".zoomframe", force=True)
        self.assertEqual(self.display(".rossmap"), "block")
        # 확대 쪽에 마커가 있고 실제로 그려진다
        box = page.query_selector(".rossmap .site .dot").bounding_box()
        self.assertGreater(box["width"], 4)
