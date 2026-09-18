"""DiaRUGA v0.29.0 `tests/browser/test_pages_load.py` 에서 왔다.

화면이 **그려지고 JS 가 살아 있는가.** 4겹의 첫 발이다.

3겹(테스트 클라이언트)이 이미 200 을 확인한다. 여기가 더하는 것은 하나뿐인데
그것이 크다 — **JS 가 죽어 있어도 200 은 뜬다.** 045 에서 `?shot=last` 가
그랬고, 화면은 멀쩡해 보였다.

오류 확인 자체는 `base.BrowserTestCase.tearDown` 이 한다. 여기서는 어느 화면을
열지만 고른다.
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx


class PagesLoadTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        # 슬러그·지역 코드를 시험마다 가른다 — 이유는 `base.setUp` 주석에.
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_viewpoints=2, n_candidates=3)

    def test_목록이_열린다(self):
        page = self.open(reverse("index"))
        self.assertIn("ForGIA", page.title())

    def test_시야_검토_화면이_열린다(self):
        """가장 무거운 화면이고 JS 가 가장 많다."""
        w = self.w
        page = self.open(reverse("group", args=[w.slug, w.vp.idx]))
        # 마스크가 실제로 그려졌는가. **속성에 값이 있는 것과 그 값이 유효한
        # 것은 다르다** — SVG 경로에 10 KB 가 멀쩡히 들어 있는데 화면이 백지였던
        # 적이 있다(CLAUDE.md). 그래서 개수가 아니라 화면에 보이는지를 본다.
        page.wait_for_selector("svg", state="attached", timeout=10_000)

    def test_지점_화면이_열린다(self):
        w = self.w
        self.open(reverse("core", args=[w.site.code, w.locality.code]))

    def test_관리_화면이_열린다(self):
        self.open(reverse("system_settings"))

    def test_문턱_화면이_열린다(self):
        self.open(reverse("thresholds", args=[self.w.slug]))

    def test_개체_갤러리가_열린다(self):
        self.open(reverse("crops", args=[self.w.slug]))
