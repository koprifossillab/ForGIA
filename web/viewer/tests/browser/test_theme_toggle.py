"""DiaRUGA v0.29.0 `tests/browser/test_theme_toggle.py` 에서 왔다.

테마 단추 — 시스템 → 밝게 → 어둡게, 다시 열어도 남고, 시스템일 때는 OS 를 따른다 (201).

3겹은 마크업의 순서·구조만 본다. 실제로 색이 바뀌는가는 `getComputedStyle`
로만 잡힌다 — `data-theme` 이 붙어도 CSS 선택자가 어긋나면 화면은 그대로다.
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx

DARK_BG, LIGHT_BG = "rgb(15, 14, 21)", "rgb(247, 245, 251)"   # base.html 의 --bg


class ThemeToggleTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        fx.make_world(slug=f"rs23-{self.uniq}", site_code=f"RS{self.uniq}",
                      with_files=False)

    def state(self):
        return self.page.evaluate(
            "[document.documentElement.dataset.themePick,"
            " getComputedStyle(document.body).backgroundColor]")

    def test_눌러서_돌고_다시_열어도_남는다(self):
        self.page.emulate_media(color_scheme="light")
        page = self.open(reverse("index"))
        self.assertEqual(self.state(), ["system", LIGHT_BG])
        page.click("#themebtn")
        self.assertEqual(self.state(), ["light", LIGHT_BG])
        page.click("#themebtn")
        self.assertEqual(self.state(), ["dark", DARK_BG], "어둡게를 골랐는데 배경이 안 바뀐다")
        page = self.open(reverse("index"))
        self.assertEqual(self.state(), ["dark", DARK_BG], "다시 열었더니 고른 테마를 잊었다")
        page.click("#themebtn")
        self.assertEqual(self.state(), ["system", LIGHT_BG])

    def test_시스템을_따를_때는_OS_가_바뀌면_같이_바뀐다(self):
        self.page.emulate_media(color_scheme="light")
        page = self.open(reverse("index"))
        self.assertEqual(self.state()[1], LIGHT_BG)
        page.emulate_media(color_scheme="dark")
        page.wait_for_function(
            "getComputedStyle(document.body).backgroundColor === '%s'" % DARK_BG,
            timeout=3000)
        # 고른 것이 있으면 OS 가 바뀌어도 그대로다
        page.click("#themebtn")      # light
        self.assertEqual(self.state(), ["light", LIGHT_BG])
        page.emulate_media(color_scheme="dark")
        page.wait_for_timeout(200)
        self.assertEqual(self.state(), ["light", LIGHT_BG], "고른 테마를 OS 가 덮었다")
