"""DiaRUGA v0.29.0 `tests/browser/test_batch_tag.py` 에서 왔다.

검토 묶음 표시가 **실제로 보이는가** (088).

3겹(`tests/test_batch_tag.py`)은 "이런 HTML 이 나온다" 까지만 본다. 여기서 보는
것은 그 밖이다 — **CSS 가 먹는가.**

이 시험이 있는 이유가 구체적이다. 관리 화면에는 같은 뜻의 `.nowtag` 가 이미
있는데 그것은 `_managenav.html` 안에 있고, 그 조각은 **관리 화면 셋만
include 한다.** 목록에서 `.nowtag` 라고 적었으면 HTML 은 멀쩡하고 3겹도
통과하는데 화면에는 **테두리도 색도 없는 맨 글자**가 나온다.

`.tools` 가 세 화면을 그렇게 속였고(051), `.nowrow` 가 관리 화면에서 같은 일을
했다(083). **세 번째다.**
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import RunBatch


class BatchTagCssTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=3)

    def screens(self):
        slug = self.w.slide.slug
        return [("데이터셋 목록", reverse("index")),
                ("시야 목록", reverse("dataset", args=[slug])),
                ("검출 갤러리", reverse("crops", args=[slug])),
                ("계측 표", reverse("detections", args=[slug]))]

    def test_네_화면에서_표시가_보이고_모양이_먹는다(self):
        for name, url in self.screens():
            with self.subTest(화면=name):
                page = self.open(url)
                self.assertTrue(page.is_visible(".batchtag"),
                                f"{name} 에서 표시가 안 보인다")

                st = page.query_selector(".batchtag").evaluate("""e => {
                    const s = getComputedStyle(e);
                    return {border: s.borderTopWidth, radius: s.borderTopLeftRadius,
                            display: s.display, color: s.color};
                }""")
                # **테두리와 둥근 모서리가 이 표시의 모양이다.** 규칙이 한 번도
                # 안 먹으면 셋 다 브라우저 기본값(0px·0px·inline)으로 나온다.
                self.assertNotEqual(st["border"], "0px",
                                    f"{name}: 테두리가 없다 — CSS 가 안 먹었다")
                self.assertNotEqual(st["radius"], "0px",
                                    f"{name}: 둥근 모서리가 없다 — CSS 가 안 먹었다")
                # **`inline-flex` 를 그대로 기대하면 안 된다.** 이 표시는
                # `.pagehead`(flex 컨테이너)의 자식이라 브라우저가 `flex` 로
                # 블록화한다 — 표준 동작이고, 규칙은 먹은 것이다. `<a>` 의
                # 기본값 `inline` 이 아닌 것이 여기서 볼 것이다.
                self.assertIn(st["display"], ("inline-flex", "flex"),
                              f"{name}: display 가 안 먹었다 ({st['display']})")

    def test_묶음이_없으면_경고색으로_갈린다(self):
        """`none` 갈래도 규칙이 따로다 — 함께 확인하지 않으면 그쪽만 맨 글자가 된다."""
        page = self.open(reverse("index"))
        normal = page.query_selector(".batchtag").evaluate(
            "e => getComputedStyle(e).borderTopColor")

        RunBatch.objects.filter(for_review=True).update(for_review=False)
        page = self.open(reverse("index"))
        el = page.query_selector(".batchtag.none")
        self.assertIsNotNone(el, "묶음이 없는데 경고 모양이 아니다")
        self.assertNotEqual(el.evaluate("e => getComputedStyle(e).borderTopColor"),
                            normal, "경고 갈래가 보통 갈래와 같아 보인다")

    def test_눌러서_운영_화면으로_간다(self):
        page = self.open(reverse("index"))
        page.click(".batchtag")
        page.wait_for_load_state("load")
        self.assertIn(reverse("system_settings_ops"), page.url)
