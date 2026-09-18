"""DiaRUGA v0.29.0 `tests/browser/test_coverage_gap.py` 에서 왔다.

빈 시야가 **왜 비었는지** 화면에 실제로 보이는가 (DiaRUGA P10 4단계).

3겹은 "본문에 이 글자가 있다" 까지 본다. 여기서 보는 것은 그 밖이다.

- **배지가 눈에 띄는가** — `.badge miss` 라고 적었다가 있는 클래스가 아니어서
  한 번도 안 먹을 뻔했다. `.tools` 가 그렇게 세 화면을 속인 적이 있다(051)
- **카드에서 시야로 이어지는가** — 목록에서 표를 보고 눌러 들어가면 같은 말이
  거기 있어야 한다. 두 화면이 다른 말을 하면 어느 쪽을 믿을지 모른다
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import Detection


class CoverageGapTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_viewpoints=2, n_candidates=3)
        self.covered, self.gap = self.w.viewpoints

        self.other = fx.add_other_engine(self.gap)
        Detection.objects.filter(run=self.other).update(is_current=True)
        Detection.objects.filter(viewpoint=self.gap).exclude(
            run=self.other).delete()

    def test_목록에_숫자와_배지가_보인다(self):
        page = self.open(reverse("dataset", args=[self.w.slide.slug]))
        self.assertIn("의 검출 없음 1", page.inner_text("body"))

        badge = page.query_selector(".tile .badge.warn")
        self.assertIsNotNone(badge, "빈 시야 카드에 배지가 없다")
        self.assertTrue(badge.is_visible())

        # **`getComputedStyle` 로 확인한다.** 없는 클래스를 적으면 규칙이 한 번도
        # 안 먹는데 예외도 경고도 없다 — 색이 보통 배지와 같으면 안 붙은 것이다
        warn = badge.evaluate("e => getComputedStyle(e).color")
        plain = page.query_selector(".tile .badge.on")
        self.assertNotEqual(warn, plain.evaluate(
            "e => getComputedStyle(e).color"), "배지가 보통 배지와 같아 보인다")

    def test_덮인_카드에는_배지가_없다(self):
        page = self.open(reverse("dataset", args=[self.w.slide.slug]))
        tiles = page.query_selector_all(".tile")
        marked = [t for t in tiles if t.query_selector(".badge.warn")]
        self.assertEqual(len(marked), 1, "표시가 시야 전부에 붙었다")

    def test_눌러_들어가면_같은_말이_있다(self):
        page = self.open(reverse("group",
                                 args=[self.w.slide.slug, self.gap.idx]))
        body = page.inner_text("body")
        self.assertIn("의 검출이 없습니다", body)
        self.assertIn(self.other.batch.label, body)
        self.assertNotIn("검출은 아직입니다", body)

        # 사진은 그대로 보이고 도구만 잠긴다 — 빈 화면이라고 사진까지 없애지 않는다
        self.assertTrue(page.is_visible(".detview img, .detview .view img"),
                        "사진이 안 보인다")
        tools = page.query_selector(".detview .tools")
        if tools is not None:
            self.assertEqual(tools.evaluate("e => getComputedStyle(e).display"),
                             "none", "검출이 없는데 교정 도구가 보인다")

    def test_묶음_바꾸러_가는_길이_있다(self):
        """**막다른 화면을 만들지 않는다.** 무엇을 해야 하는지까지 적는다."""
        page = self.open(reverse("group",
                                 args=[self.w.slide.slug, self.gap.idx]))
        link = page.query_selector(f'.note a[href="{reverse("system_settings")}"]')
        self.assertIsNotNone(link, "관리 화면으로 가는 길이 없다")
        self.assertTrue(link.is_visible())
