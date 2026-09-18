"""DiaRUGA v0.29.0 `tests/browser/test_draw_mask.py` 에서 왔다.

점을 찍어 마스크를 그린다 (DiaRUGA P09 3단계 — 화면).

서버는 이미 `drawn` 을 받는다(3겹 시험 18개). 여기서 보는 것은 **그 사이의
배선**이다 — 점을 찍는 것부터 저장이 나가기까지가 전부 브라우저 안에서 일어나고,
서버가 내는 HTML 에는 그 흐름이 없다.

**밟기 쉬운 자리가 셋이다.**

- 그리는 중의 클릭이 **선택으로 새면** 개체가 골라지고, 그 상태에서 분류
  단축키가 먹는다 — 그리다가 엉뚱한 개체의 분류가 바뀐다
- 그리는 중의 Backspace 가 `history` 로 가면 **저장된 교정을 무른다**
- 그린 개체의 분류가 `labels` 지도로 가면 그 키가 되돌아오는데 `batch=NULL`
  이라 서버가 거절한다 — **한 번 그리면 그 시야를 더 저장할 수 없게 된다**
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import ObjectReview

# 픽스처 개체(40,50 / 160,130 / 280,210)와 안 겹치는 빈 자리
PTS = [(420, 300), (500, 300), (500, 360), (420, 360)]


class DrawMaskTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=3)

    def open_review(self):
        return self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))

    def start(self):
        self.page.click('#dv-stack button[data-act="draw"]')
        self.page.wait_for_timeout(150)

    def put(self, pts=None):
        for x, y in (pts or PTS):
            self.click_image(x, y)

    def close_here(self):
        """첫 점을 다시 눌러 닫는다."""
        self.click_image(*PTS[0])
        self.page.wait_for_timeout(900)

    # --- 그려지는가 --------------------------------------------------------

    def test_그리기를_켜면_점이_보인다(self):
        page = self.open_review()
        self.start()
        self.put(PTS[:2])
        self.assertEqual(len(page.query_selector_all("svg.masks g.drawing circle")),
                         2, "찍은 점이 안 그려진다")

    def test_첫_점을_다시_누르면_닫히고_저장된다(self):
        page = self.open_review()
        self.start()
        self.put()
        self.close_here()

        o = ObjectReview.objects.get(source="manual")
        self.assertIsNone(o.batch_id, "그린 개체가 회차에 묶였다")
        self.assertRegex(o.mask_key, r"^m[0-9a-f]{8}$")
        # **화소 단위로 딱 맞기를 요구하지 않는다.** 클릭은 화면 화소로
        # 반올림되고 화면이 이미지의 1.72배라, 되돌린 이미지 좌표가 1px 안팎
        # 흔들린다. 여기서 볼 것은 **누른 자리에 생겼는가**이지 반올림이 아니다.
        for got, want in zip(o.geom["bbox"], [420, 300, 80, 60]):
            self.assertAlmostEqual(got, want, delta=2)
        self.assertEqual(len(o.geom["polygon"]), 8)
        # 그리는 중이던 점은 치운다 — 남으면 다음에 그릴 때 이어 그린다
        self.assertEqual(page.query_selector_all("svg.masks g.drawing circle"), [])

    def test_그린_개체가_화면에_남는다(self):
        page = self.open_review()
        self.start()
        self.put()
        self.close_here()
        # 2단계가 낸 길 — 후보가 없어도 그려지고, 엔진 것과 달라 보인다
        self.assertTrue(page.query_selector(".box.orphan"),
                        "그린 개체가 화면에 안 남는다")

    # --- 안 새는가 ---------------------------------------------------------

    def test_그리는_중_클릭이_선택으로_안_샌다(self):
        """새면 개체가 골라지고 그 상태에서 분류 단축키가 먹는다."""
        page = self.open_review()
        self.start()
        self.click_image(40 + 60 // 2, 50 + 40 // 2)      # 개체 한가운데
        page.wait_for_timeout(200)
        self.assertEqual(page.query_selector_all(".box.sel"), [],
                         "그리는 중인데 개체가 골라졌다")

    def test_Esc_로_취소하면_아무것도_안_남는다(self):
        page = self.open_review()
        self.start()
        self.put()
        page.keyboard.press("Escape")
        page.wait_for_timeout(700)
        self.assertEqual(page.query_selector_all("svg.masks g.drawing circle"), [])
        self.assertEqual(ObjectReview.objects.filter(source="manual").count(), 0)

    def test_Backspace_는_점_하나만_무른다(self):
        """**저장된 교정을 무르면 안 된다** — 그리다 만 것과는 다른 층이다."""
        page = self.open_review()
        # 먼저 엔진 개체를 하나 지워 저장해 둔다
        self.context_menu_at(40 + 60 // 2, 50 + 40 // 2)
        page.get_by_text("오검출로 삭제", exact=False).first.click()
        page.wait_for_timeout(900)
        before = ObjectReview.objects.filter(removed=True).count()
        self.assertEqual(before, 1)

        self.start()
        self.put(PTS[:3])
        page.keyboard.press("Backspace")
        page.wait_for_timeout(200)
        self.assertEqual(len(page.query_selector_all("svg.masks g.drawing circle")),
                         2, "Backspace 가 점을 안 물렀다")
        self.assertEqual(ObjectReview.objects.filter(removed=True).count(), before,
                         "Backspace 가 저장된 교정을 물렀다")

    def test_점이_모자라면_안_만든다(self):
        page = self.open_review()
        self.start()
        self.put(PTS[:2])
        page.keyboard.press("Enter")
        page.wait_for_timeout(700)
        self.assertEqual(ObjectReview.objects.filter(source="manual").count(), 0)

    # --- 그린 뒤에도 저장이 되는가 -----------------------------------------

    def test_분류를_지정해도_저장이_막히지_않는다(self):
        """**한 번 그리면 그 시야를 더 저장할 수 없게 되는** 갈래를 막는다.

        그린 개체의 분류가 `labels` 지도로 가면 그 키가 서버로 되돌아오는데,
        `batch=NULL` 이라 엔진 쪽 `known` 에 없어 409 가 된다.
        """
        page = self.open_review()
        self.start()
        self.put()
        self.close_here()

        # 방금 그린 것이 골라져 있다 — 단축키로 분류를 준다
        page.keyboard.press("w")                     # 봉상
        page.wait_for_timeout(900)
        self.assertEqual(ObjectReview.objects.get(source="manual").label, "broken")

        # 그 뒤에 엔진 개체를 지워도 저장이 나가야 한다
        self.context_menu_at(160 + 70 // 2, 130 + 50 // 2)
        page.get_by_text("오검출로 삭제", exact=False).first.click()
        page.wait_for_timeout(900)
        self.assertEqual(ObjectReview.objects.filter(removed=True).count(), 1,
                         "그린 뒤로 엔진 교정 저장이 막혔다")
        self.assertTrue(ObjectReview.objects.filter(source="manual").exists(),
                        "엔진 교정 저장이 그린 개체를 지웠다")

    def test_단추가_상태를_따라간다(self):
        """따로 두면 Esc 로 끄고도 단추가 눌린 채로 남는다 — 켜져 있는 줄 알고
        사진을 누르면 선택이 되고, 사람은 왜 안 그려지는지 모른다.

        **미리보기를 띄워 보고서야 눈에 들어왔다.** 시험이 통과해도 화면은
        다를 수 있다는 자리다.
        """
        page = self.open_review()
        btn = 'button[data-act="draw"].on'
        self.assertIsNone(page.query_selector(btn), "처음부터 켜져 있다")

        self.start()
        self.assertIsNotNone(page.query_selector(btn), "켰는데 단추가 안 눌린다")

        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        self.assertIsNone(page.query_selector(btn),
                          "Esc 로 껐는데 단추가 눌린 채로 남았다")

    def test_다_그리고_나면_단추가_꺼진다(self):
        page = self.open_review()
        self.start()
        self.put()
        self.close_here()
        self.assertIsNone(page.query_selector('button[data-act="draw"].on'),
                          "다 그렸는데 단추가 켜진 채로 남았다")


class RubberBandTest(BrowserTestCase):
    """마지막 점에서 **커서까지** 점선이 따라오는가 (082).

    점만 찍히면 다음 변이 어디로 갈지 **찍어 봐야** 안다. 개체의 윤곽은 굽은
    선이라 그 한 걸음이 보여야 형태를 따라갈 수 있다.

    JS 가 SVG 를 만드는 일이라 3겹은 못 본다 — 서버가 내는 HTML 에는 이 선이
    아예 없다.
    """

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=2)

    def start_drawing(self):
        page = self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))
        page.wait_for_selector(".detview .box")
        page.keyboard.press("d")
        page.wait_for_timeout(120)
        return page

    def rubber(self):
        return self.page.query_selector_all("#masks-stack g.drawing polyline.rubber")

    def test_점을_찍기_전에는_없다(self):
        self.start_drawing()
        self.assertEqual(len(self.rubber()), 0, "점도 없는데 선이 있다")

    def test_한_점을_찍으면_커서까지_따라온다(self):
        page = self.start_drawing()
        self.click_image(120, 120)
        page.wait_for_timeout(80)
        # **`steps` 를 준다.** 한 번에 옮기면 브라우저가 mousemove 를 한 번도
        # 안 내서, 사람이 쓸 때는 멀쩡한 것이 시험에서만 안 따라온다.
        x, y = self.image_point(400, 300)
        page.mouse.move(x, y, steps=3)
        page.wait_for_timeout(120)

        r = self.rubber()
        self.assertEqual(len(r), 1, "마지막 점에서 커서까지 오는 선이 없다")
        pts = r[0].get_attribute("points")
        self.assertTrue(pts.startswith("120,120"),
                        f"마지막 점에서 시작하지 않는다: {pts}")

    def test_커서를_옮기면_따라온다(self):
        page = self.start_drawing()
        self.click_image(120, 120)
        page.mouse.move(*self.image_point(400, 300), steps=3)
        page.wait_for_timeout(100)
        first = self.rubber()[0].get_attribute("points")
        # **그림 안에서 옮긴다** (자료의 이미지가 640×480 이다). 밖으로 나가면
        # 안 따라오는 것이 맞는 동작이라 시험이 헛돈다.
        page.mouse.move(*self.image_point(520, 160), steps=3)
        page.wait_for_timeout(100)
        self.assertNotEqual(self.rubber()[0].get_attribute("points"), first,
                            "커서를 옮겼는데 선이 그대로다")

    def test_점이_셋이면_닫히는_변도_보인다(self):
        """지금 닫으면 어떤 모양이 되는지가 보여야 한다."""
        page = self.start_drawing()
        for x, y in ((120, 120), (500, 140), (460, 380)):
            self.click_image(x, y)
            page.wait_for_timeout(60)
        page.mouse.move(*self.image_point(180, 360), steps=3)
        page.wait_for_timeout(120)
        self.assertEqual(len(self.rubber()), 2,
                         "닫히는 변이 안 보인다 (따라오는 선만 있다)")
        close = self.page.query_selector("#masks-stack g.drawing polyline.rubber.close")
        self.assertIsNotNone(close)
        # **눈에 달라야 한다** — 찍은 변과 굵기·색이 같으면 구별이 안 된다
        drawn = self.page.query_selector("#masks-stack g.drawing polyline:not(.rubber)")
        self.assertIsNotNone(drawn, "찍은 변이 없다")
        self.assertNotEqual(
            close.evaluate("e => getComputedStyle(e).stroke"),
            drawn.evaluate("e => getComputedStyle(e).stroke"),
            "닫히는 변이 찍은 변과 같은 색이다")

    def test_그리기를_끝내면_사라진다(self):
        page = self.start_drawing()
        for x, y in ((120, 120), (500, 140), (460, 380)):
            self.click_image(x, y)
            page.wait_for_timeout(60)
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        self.assertEqual(len(self.rubber()), 0, "취소했는데 선이 남았다")
