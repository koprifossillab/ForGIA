"""화면에서 테마를 고른다 (201) — 3겹.

예전에는 `@media (prefers-color-scheme: light)` 하나가 OS 를 따라갔고 고를 길이
없었다. 지금은 `<head>` 의 스크립트가 `data-theme` 을 붙이고 CSS 는 그것만 본다.
여기서 보는 것은 **순서와 구조**다 — 스크립트가 스타일보다 앞에 있는가(뒤에
있으면 첫 그림이 한 번 어둡게 찍힌다), 밝은 토큰이 미디어 쿼리가 아니라 속성에
걸렸는가(미디어 쿼리가 되살아나면 고른 것을 OS 가 덮는다). 실제로 눌러 바뀌는
것은 브라우저 겹(`browser/test_theme_toggle.py`)이 본다.
"""
import re

from django.urls import reverse

from . import factories as fx
from .base import ForGIATestCase


class ThemeMarkupTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        fx.make_world(slug="rs23", with_files=False)

    def html(self):
        r = self.client.get(reverse("index"))
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_테마_스크립트가_스타일보다_앞이다(self):
        html = self.html()
        head = html.split("</head>")[0]
        js = head.index("forgia.theme")
        css = head.index("<style>")
        self.assertLess(js, css, "테마를 정하는 스크립트가 <style> 뒤에 있다 — 첫 그림이 깜빡인다")

    def test_밝은_토큰은_미디어_쿼리가_아니라_속성에_걸린다(self):
        html = self.html()
        css = html.split("<style>")[1].split("</style>")[0]
        self.assertIn(':root[data-theme="light"]', css)
        self.assertNotRegex(css, r"@media\s*\(prefers-color-scheme")
        # 밝은 값이 실제로 그 블록 안에 있다
        m = re.search(r':root\[data-theme="light"\]\s*\{(.*?)\n  \}', css, re.S)
        self.assertIsNotNone(m)
        self.assertIn("--bg: #f7f5fb", m.group(1))   # ForGIA 의 밝은 바탕 (연보라 테마)
        self.assertIn("--sea-4000", m.group(1))

    def test_머리줄에_단추가_있고_세_아이콘을_싣는다(self):
        html = self.html()
        self.assertIn('id="themebtn"', html)
        for ic in ("ic-system", "ic-light", "ic-dark"):
            self.assertIn(f'class="{ic}"', html)
