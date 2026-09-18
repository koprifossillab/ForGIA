"""DiaRUGA v0.29.0 `tests/browser/test_edit_mask.py` 에서 왔다.

엔진이 낸 마스크의 점을 끌어 고친다 (DiaRUGA P09 4단계 — 화면).

서버는 이미 `edits` 를 받는다(3겹 시험 12개). 여기서 보는 것은 **그 사이의
배선**이다 — 점을 집고 끄는 것부터 저장이 나가기까지가 전부 브라우저 안이다.

**가장 무서운 자리는 키다.** 기하를 고치면 bbox 가 바뀌는데, 화면이 키를 기하에서
다시 만들면 **다른 개체가 되어 옛 행이 지워지고 새 행이 생긴다** — 분류·코멘트가
끊긴다. `keyOf` 가 `c.key` 를 먼저 보는 것이 그것을 막는 유일한 장치이고,
3겹으로는 안 걸린다(서버는 화면이 보낸 키를 그대로 믿는다).
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import ObjectReview

# 픽스처 첫 개체 (40,50,60,40) — 폴리곤은 그 네 꼭짓점이다
CORNER = (40, 50)
CENTER = (40 + 60 // 2, 50 + 40 // 2)


class EditMaskTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=3)
        self.key = self.w.keys()[0]

    def open_review(self):
        return self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))

    def start_edit(self):
        """개체를 고르고 우클릭 메뉴로 편집을 켠다."""
        menu = self.context_menu_at(*CENTER)
        self.assertIsNotNone(menu, "개체 위 우클릭 메뉴가 안 떴다")
        self.page.get_by_text("마스크 고치기", exact=False).first.click()
        self.page.wait_for_timeout(250)

    def drag(self, frm, to):
        x0, y0 = self.image_point(*frm)
        x1, y1 = self.image_point(*to)
        self.page.mouse.move(x0, y0)
        self.page.mouse.down()
        self.page.mouse.move(x1, y1, steps=6)
        self.page.mouse.up()
        self.page.wait_for_timeout(150)

    # --- 켜지는가 ----------------------------------------------------------

    def test_편집을_켜면_점_핸들이_보인다(self):
        page = self.open_review()
        self.start_edit()
        self.assertEqual(len(page.query_selector_all("svg.masks g.drawing circle")),
                         4, "폴리곤 꼭짓점 핸들이 안 보인다")

    # --- 키가 안 바뀌는가 (이 파일의 이유) ---------------------------------

    def test_점을_끌어도_키가_안_바뀐다(self):
        """**bbox 가 바뀌는데 키가 따라가면 옛 행이 지워지고 새 행이 생긴다.**

        분류를 먼저 주고 고친 뒤에도 그 분류가 같은 행에 남아 있어야 한다.
        """
        page = self.open_review()
        # 분류를 먼저 준다 — 키가 바뀌면 이 판단이 끊긴다
        self.context_menu_at(*CENTER)
        self.menu_click("파손", exact=True)
        page.wait_for_timeout(900)

        self.start_edit()
        self.drag(CORNER, (20, 30))            # 왼쪽 위 꼭짓점을 바깥으로
        page.keyboard.press("Enter")
        page.wait_for_timeout(900)

        rows = list(ObjectReview.objects.all())
        self.assertEqual(len(rows), 1, f"행이 늘거나 줄었다: {rows}")
        o = rows[0]
        self.assertEqual(o.mask_key, self.key, "키가 바뀌었다")
        self.assertEqual(o.label, "broken", "분류가 끊겼다")
        self.assertTrue(o.geom_edited)
        self.assertLess(o.geom["bbox"][0], 40, "끈 점이 반영되지 않았다")

    def test_고친_모양이_저장된다(self):
        page = self.open_review()
        self.start_edit()
        self.drag(CORNER, (20, 30))
        page.keyboard.press("Enter")
        page.wait_for_timeout(900)

        o = ObjectReview.objects.get(mask_key=self.key)
        self.assertTrue(o.geom_edited)
        self.assertEqual(len(o.geom["polygon"]), 8, "점 수가 달라졌다")

    # --- 점을 늘리고 줄이는가 ----------------------------------------------

    def test_선분을_누르면_점이_는다(self):
        page = self.open_review()
        self.start_edit()
        # 위쪽 변의 한가운데
        self.click_image(40 + 30, 50)
        page.wait_for_timeout(200)
        self.assertEqual(len(page.query_selector_all("svg.masks g.drawing circle")),
                         5, "선분을 눌렀는데 점이 안 늘었다")

    def test_점을_더블클릭하면_준다(self):
        page = self.open_review()
        self.start_edit()
        x, y = self.image_point(*CORNER)
        page.mouse.dblclick(x, y)
        page.wait_for_timeout(200)
        self.assertEqual(len(page.query_selector_all("svg.masks g.drawing circle")),
                         3, "더블클릭했는데 점이 안 줄었다")

    def test_세_점_아래로는_안_준다(self):
        """도형이 아니게 된다."""
        page = self.open_review()
        self.start_edit()
        for _ in range(3):
            x, y = self.image_point(*CORNER)
            page.mouse.dblclick(x, y)
            page.wait_for_timeout(150)
        self.assertGreaterEqual(
            len(page.query_selector_all("svg.masks g.drawing circle")), 3)

    # --- 안 새는가 ---------------------------------------------------------

    def test_끌기가_범위선택으로_안_샌다(self):
        """새면 끌기 한 번에 개체가 우수수 골라지고, 그 상태에서 분류
        단축키가 먹는다."""
        page = self.open_review()
        self.start_edit()
        self.drag(CORNER, (20, 30))
        self.assertLessEqual(len(page.query_selector_all(".box.sel")), 1,
                             "끌기가 범위선택이 됐다")

    def test_Esc_로_취소하면_안_바뀐다(self):
        page = self.open_review()
        self.start_edit()
        self.drag(CORNER, (20, 30))
        page.keyboard.press("Escape")
        page.wait_for_timeout(700)
        self.assertFalse(ObjectReview.objects.filter(geom_edited=True).exists(),
                         "취소했는데 저장됐다")

    def test_고친_뒤에_분류를_줘도_같은_행이다(self):
        """**이것이 `keyOf` 가 `c.key` 를 먼저 보는 이유다.**

        고치고 나면 `bbox_xywh` 가 바뀐다. 그 뒤에 분류를 주면 `setLabel` 이
        `keyOf(c)` 로 키를 만드는데, 기하에서 만들면 **새 bbox 문자열**이 나온다 —
        서버가 모르는 키라 409 이거나, 통과하면 같은 개체에 행이 둘이 된다.

        앞의 시험은 **분류를 먼저** 줬기 때문에 이 갈래를 못 잡았다(옛 키가
        payload 에 그대로 실려 나간다). 순서가 시험의 전부였다.
        """
        page = self.open_review()
        self.start_edit()
        self.drag(CORNER, (20, 30))
        page.keyboard.press("Enter")
        page.wait_for_timeout(900)

        # 고친 **뒤에** 분류를 준다 — 개체는 아직 골라져 있다
        page.keyboard.press("w")                    # 봉상
        page.wait_for_timeout(900)

        rows = list(ObjectReview.objects.all())
        self.assertEqual(len(rows), 1, f"행이 늘었다 — 키가 바뀌었다: {rows}")
        self.assertEqual(rows[0].mask_key, self.key)
        self.assertEqual(rows[0].label, "broken")
        self.assertTrue(rows[0].geom_edited, "고친 표시가 풀렸다")
