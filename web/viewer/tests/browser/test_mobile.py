"""DiaRUGA v0.29.0 `tests/browser/test_mobile.py` 에서 왔다.

폰·태블릿에서 검토와 동정을 한다 (192).

**데스크탑에만 있는 것으로 만들어져 있었다** — 휠 확대, 우클릭 드래그 이동,
우클릭 메뉴, hover 말풍선, 그리고 사진 옆의 250px 짜리 칸. 손가락에는 그중
아무것도 없고, 폰에서는 사진이 반으로 준다.

## 무엇을 되살려서 잡나

- **핀치 확대**를 빼면 손가락으로는 확대할 길이 아예 없다 — areolae 를 보려면
  확대가 필요하고, 그것이 동정의 전부다
- **길게 누르기**를 빼면 삭제·유형 지정 메뉴에 닿을 수 없다. Chrome 은
  `contextmenu` 를 내주는데 화면이 그것을 **막기만 하고 있었다**
- **탭이 선택으로 가는 길**을 우리가 가로채면(손가락 이벤트를 통째로 먹으면)
  개체를 고를 수가 없다 — 그래서 이동·확대로 판정한 다음에만 막는다
- 오른쪽 칸이 **아래로 안 내려오면** 폰에서 사진이 반이 된다
"""
from pathlib import Path

from django.test import Client
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx

# 픽스처 첫 개체 (40,50,60,40) 의 한가운데
OBJ = (70, 70)


class MobileTestCase(BrowserTestCase):
    """**폰 크기 · 손가락 있는 화면**으로 연다.

    바닥이 세워 준 창을 닫고 다시 연다 — `has_touch` 는 컨텍스트를 만들 때만
    정할 수 있고, 그것이 없으면 `touchstart` 도 `pointerType: 'touch'` 도
    아예 안 난다(즉 **시험이 아무것도 안 보고 통과한다**).
    """

    def setUp(self):
        super().setUp()
        self.ctx.close()
        self.ctx = self._browser.new_context(
            viewport={"width": 412, "height": 915}, has_touch=True,
            is_mobile=True, device_scale_factor=2)
        self.page = self.ctx.new_page()
        self.page.on("pageerror",
                     lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("console", lambda m: (
            self.errors.append(f"console.error: {m.text}")
            if m.type == "error" else None))

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=3)

    def review(self):
        return self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))


class MobileLayoutTest(MobileTestCase):

    def test_오른쪽_칸이_사진_아래로_내려온다(self):
        page = self.review()
        view = page.query_selector("#dv-stack").bounding_box()
        notes = page.query_selector("#notes-stack").bounding_box()
        self.assertGreater(notes["y"], view["y"],
                           "오른쪽 칸이 아직 사진 옆에 있다 — 폰에서 사진이 반이 된다")
        self.assertGreater(notes["width"], view["width"] * 0.8,
                           "아래로 내려왔는데 폭이 안 늘었다")

    def test_카탈로그_카드가_한_줄에_하나다(self):
        fx.review_done(self.w.vp)
        page = self.open(reverse("catalog", args=[self.w.slug]))
        cards = page.query_selector_all(".catcard")
        self.assertGreater(len(cards), 1, "카드가 하나면 열이 안 갈린다")
        xs = {round(c.bounding_box()["x"]) for c in cards}
        self.assertEqual(len(xs), 1, f"폰인데 카드가 여러 열이다: {xs}")

    # **가로로 밀리면 안 된다.** 세로로만 넘기는 화면에서 가로 스크롤이 생기면
    # 사진이 화면 밖으로 나가고, 그것을 되돌릴 손잡이가 없다. 폭을 안 다스린
    # 요소 하나가 화면 전체를 그렇게 만든다.
    def assert_no_sideways(self, page, where):
        over = page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth")
        self.assertLessEqual(over, 1, f"{where} 가 가로로 {over}px 밀린다")

    def test_화면이_가로로_안_밀린다(self):
        page = self.review()
        self.assert_no_sideways(page, "검토 화면")
        fx.review_done(self.w.vp)
        page = self.open(reverse("catalog", args=[self.w.slug]))
        self.assert_no_sideways(page, "카탈로그 화면")

    # `test_꺼내기_패널을_펴도_안_밀린다` 는 오프라인 꺼내기 패널(5단계)의 것

class MobileTouchTest(MobileTestCase):

    def tap(self, img_x, img_y):
        x, y = self.image_point(img_x, img_y)
        self.page.touchscreen.tap(x, y)
        self.page.wait_for_timeout(200)

    def test_탭이_개체를_고른다(self):
        """**탭은 가로채지 않는다** — 브라우저가 마우스 이벤트로 바꿔 주는 길을
        그대로 쓴다. 우리가 먹으면 선택·되살리기·펼침을 전부 다시 적어야 하고,
        두 벌이 되면 조용히 어긋난다."""
        page = self.review()
        self.tap(*OBJ)
        self.assertIn("선택", page.text_content("#selinfo-stack"))
        self.assertNotIn("선택 없음", page.text_content("#selinfo-stack"))

    # **조작 안내가 거짓말을 하면 안 된다.** 휠도 우클릭도 없는 자리에서
    # "휠 = 확대" 를 읽으면, 사람은 자기 손가락이 아니라 화면을 의심하지
    # 않는다 — 되는 방법을 안 찾고 안 되는 방법을 계속 시도한다.
    def test_손가락_화면에는_손가락_안내가_뜬다(self):
        page = self.review()
        self.assertTrue(page.is_visible(".touchhint"), "손가락 안내가 안 뜬다")
        self.assertFalse(page.is_visible(".mousehint"),
                         "손가락 화면에 마우스 안내가 떠 있다")
        self.assertIn("길게 누르기", page.text_content(".touchhint"))
        # 단축키 줄도 안 낸다 — 키보드가 없는 자리에서 가리킬 것이 없다
        self.assertFalse(page.is_visible("#keyhint-stack"))

    def test_두_손가락으로_확대한다(self):
        page = self.review()
        was = page.text_content("#zoom-stack")
        page.evaluate("""() => {
          const el = document.getElementById('dv-stack');
          const t = (x, y, id) => new Touch(
            {identifier: id, target: el, clientX: x, clientY: y});
          const fire = (name, a, b) => el.dispatchEvent(new TouchEvent(name, {
            touches: b ? [a, b] : [], targetTouches: b ? [a, b] : [],
            changedTouches: b ? [a, b] : [a],
            bubbles: true, cancelable: true}));
          const r = el.getBoundingClientRect();
          const cy = r.top + r.height / 2;
          fire('touchstart', t(r.left + 120, cy, 1), t(r.left + 200, cy, 2));
          fire('touchmove',  t(r.left + 40,  cy, 1), t(r.left + 280, cy, 2));
          fire('touchend',   t(r.left + 40,  cy, 1));
        }""")
        page.wait_for_timeout(200)
        self.assertNotEqual(was, page.text_content("#zoom-stack"),
                            "두 손가락을 벌렸는데 배율이 그대로다")

    def test_길게_누르면_메뉴가_뜬다(self):
        """Chrome 은 길게 누르면 `contextmenu` 를 내준다 — 화면이 그것을
        **막기만 하고 있었다.** 이 길이 없으면 손가락으로는 삭제도 유형
        지정도 할 수 없다."""
        page = self.review()
        self.tap(*OBJ)                       # 먼저 고른다 (손가락으로 눌렀다는 표시도 여기서)
        x, y = self.image_point(*OBJ)
        page.evaluate("""([x, y]) => {
          document.getElementById('dv-stack').dispatchEvent(
            new MouseEvent('contextmenu',
              {clientX: x, clientY: y, bubbles: true, cancelable: true}));
        }""", [x, y])
        page.wait_for_timeout(250)
        menu = page.query_selector(".ctxmenu")
        self.assertIsNotNone(menu, "길게 눌러도 메뉴가 안 뜬다")
        self.assertIn("오검출로 삭제", menu.text_content())



# `MobileOfflineFileTest`(오프라인 파일)는 5단계에서 온다

class MobileChromeTest(MobileTestCase):
    """**껍데기**가 폰에서 자리를 안 뺏는가 (194).

    192 가 손댄 것은 화면의 *몸통*이었다. 그 둘레의 띠·단추·섬네일은 데스크탑
    값 그대로여서, 폰 한 화면(412×915)에서 사진이 294px 밖에 안 됐다.

    여기서 되살려서 잡는 것은 **자리를 도로 뺏는 갈래**다 — 붙박이 띠가
    돌아오거나, 접기로 한 판이 펴진 채로 서거나, 섬네일이 다시 커지는 것.
    """

    def test_화면_조정이_폰에서는_접혀_있다(self):
        page = self.review()
        box = page.query_selector("#adjbox-stack")
        self.assertIsNotNone(box, "화면 조정 판이 없다")
        self.assertFalse(box.get_property("open").json_value(),
                         "폰인데 밝기·대비 판이 펴진 채로 선다")
        # 접혀 있어도 **다시 펼 수 있어야 한다** — 접는 것과 감추는 것은 다르다
        summary = page.query_selector("#adjbox-stack > summary")
        self.assertTrue(summary.is_visible(), "접었는데 펼 자리가 없다")
        summary.click()
        page.wait_for_timeout(150)
        self.assertTrue(page.query_selector("#bri-stack").is_visible(),
                        "펴도 슬라이더가 안 나온다")

    def test_켜져_있으면_접혀_있어도_적힌다(self):
        """조정이 걸린 채로 접히면 **사진이 왜 어두운지 화면 어디에도 안
        적힌다** — 그러면 사람은 사진을 의심한다."""
        page = self.review()
        page.eval_on_selector(
            "#bri-stack",
            "el => { el.value = 60; el.dispatchEvent(new Event('input')); }")
        page.wait_for_timeout(150)
        self.assertIn("60%", page.text_content("#adjnow-stack"),
                      "접힌 요약에 켜진 값이 안 적힌다")

    def test_판_섬네일이_폰에서_작아진다(self):
        """사용자: *"시야 섬네일이 너무 커"*. 128px 은 데스크탑 값이다."""
        page = self.review()
        shot = page.query_selector(".strip .shot")
        if shot is None:
            self.skipTest("이 픽스처에는 캐러셀이 없다")
        self.assertLess(shot.bounding_box()["width"], 100,
                        "폰인데 판 섬네일이 데스크탑 크기 그대로다")

    def test_접은_도구가_눌러야_나오고_실제로_돈다(self):
        """**감추는 것이 아니라 접는 것이다** — 눌러서 나온 단추가 돌아야
        한다. `<details>` 안으로 들어가면서 배선(`#tools-` 위임)이 끊기면
        예외도 경고도 없이 아무 일도 안 일어난다."""
        page = self.review()
        more = page.query_selector("#tools-stack .moretools")
        self.assertIsNotNone(more, "접은 도구 판이 없다")
        allbtn = page.query_selector('#tools-stack button[data-act="all"]')
        self.assertFalse(allbtn.is_visible(), "접기로 한 도구가 그냥 보인다")
        page.click("#tools-stack .moretools > summary")
        page.wait_for_timeout(150)
        self.assertTrue(allbtn.is_visible(), "펴도 도구가 안 나온다")
        allbtn.click()
        page.wait_for_timeout(200)
        self.assertGreater(len(page.query_selector_all("#masks-stack .sel")), 0,
                           "「전체 선택」이 눌렸는데 아무것도 안 골라졌다")



# `MobileOfflineChromeTest` 도 5단계
