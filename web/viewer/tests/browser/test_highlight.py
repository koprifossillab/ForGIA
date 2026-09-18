"""DiaRUGA v0.29.0 `tests/browser/test_highlight.py` 에서 왔다.

링크가 짚어 온 개체를 화면이 표시하는가 (118) — **실제로 열어서** 본다.

3겹(`tests/test_highlight_link.py`)은 주소가 개체를 나르고 화면까지 닿는지까지만
본다. 표시를 얹는 것은 JS 라 거기서는 안 보인다 — **속성에 값이 들어 있는 것과
그 값이 쓰이는 것은 다르다**(CLAUDE.md). 여기서 보는 것:

- 표시가 실제로 그 개체에 붙는가
- **다른 판의 개체면 캐러셀이 그 판으로 옮겨 가는가** — 시야 하나에 판이 여럿이고
  (합성본 + 프레임마다 하나) 개체는 그중 한 판의 것이다. 안 옮기면 합성본에서
  찾다가 "못 찾았다" 가 난다
- 못 찾았으면 **그렇게 말하는가** — 표시 없는 화면과 원래 표시가 없는 화면이
  똑같이 생겨서, 아무 말도 안 하면 사람은 그 시야를 계속 훑는다
- 사람이 자기 일을 시작하면 표시가 걷히는가 — 골라 둔 개체(`.sel`)와 헷갈리면 안 된다
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import Image


class HighlightBrowserTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        # 합성본 + 프레임 3장. 프레임에도 제 검출을 세운다 — 캐러셀이 서야
        # "다른 판의 개체" 갈래를 밟는다.
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_frames=3, n_candidates=2)
        self.stack_det = self.w.detection()
        # **통과분에서 고른다** — 탈락분은 `cands` 에 없어 표시할 자리가 없다.
        self.stack_cand = (self.stack_det.candidates.filter(passed=True)
                           .order_by("raw_id").first())
        self.stack_key = self.stack_cand.mask_key
        self.stack_img = self.stack_det.image_id
        self.frames = fx.add_frame_detections(self.w.vp, n_candidates=2)

    def open_with(self, **q):
        url = reverse("group", args=[self.w.slug, self.w.vp.idx])
        query = "&".join(f"{k}={v}" for k, v in q.items())
        return self.open(f"{url}?{query}" if query else url)

    def flag_text(self):
        el = self.page.query_selector(".hlflag:not([hidden])")
        return el.inner_text() if el else ""

    def zoom_btn(self):
        """보이는 '이 개체로' 버튼. 감춰져 있으면 `None`."""
        return self.page.query_selector(".hlflag button:not([hidden])")

    def zoom_pct(self):
        """좌하단 배율 표시의 숫자. 화면이 사람에게 말하는 그 값이다.

        **원본 화소 기준이다** — 화면에 맞춘 상태(k=1)가 100%가 아니다
        (`_detection.html` 의 `zoomPct` 머리말).
        """
        text = self.page.query_selector("#zoom-stack").inner_text()
        return float(text.split("%")[0])

    def scale(self):
        """캔버스에 걸린 배율(k). 1 이면 사진 전체가 보이는 처음 상태다."""
        return self.page.evaluate(
            "() => new DOMMatrix(getComputedStyle("
            "document.getElementById('canvas-stack')).transform).a")

    def open_narrow(self, **q):
        """**사진보다 좁은 창으로 연다.**

        픽스처 이미지는 640×480 이라 넓은 창에서는 이미 원본보다 늘어나 있고,
        그 상태에서는 "원본 화소를 넘겨 늘리지 않는다" 는 규칙이 확대를 아예
        막는다 — **그게 맞는 동작이다.** 확대 갈래를 밟으려면 창이 사진보다
        좁아야 하고, 운영이 그 모양이다(2752px 사진을 1100px 남짓에 띄운다).

        **넓은 창으로 시험하면 이 갈래를 한 번도 안 지나면서 통과한다** — 086
        이 정확히 그 자리였다.
        """
        self.page.set_viewport_size({"width": 620, "height": 800})
        return self.open_with(**q)

    def hl_center(self):
        """표시된 개체의 화면 중심."""
        b = self.page.query_selector(".box.hl").bounding_box()
        return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

    def view_center(self):
        """**사진이 놓인 자리의 한가운데** — `.detview` 의 한가운데가 아니다.

        좁은 창(여기가 620px 이다)에서는 도구·레이어 줄이 사진 위가 아니라
        `.detview` 안 **흐름**으로 들어온다(192) — 바깥 상자로 재면 사진의
        가운데가 아니라 도구까지 포함한 가운데를 재게 된다. 화면 코드가
        `canvasBox()` 로 재는 것과 같은 상자다.
        """
        b = self.page.evaluate("""() => {
          const v = document.getElementById('dv-stack');
          const c = document.getElementById('canvas-stack');
          const r = v.getBoundingClientRect();
          return {x: r.left + v.clientLeft + c.offsetLeft,
                  y: r.top + v.clientTop + c.offsetTop,
                  w: c.offsetWidth, h: c.offsetHeight};
        }""")
        return b["x"] + b["w"] / 2, b["y"] + b["h"] / 2

    # --- 표시가 붙는다 ------------------------------------------------------

    def test_짚어_온_개체에_표시가_붙는다(self):
        page = self.open_with(obj=self.stack_key, img=self.stack_img)
        marks = page.query_selector_all(".box.hl")
        self.assertEqual(len(marks), 1,
                         "짚어 온 개체 하나에만 표시가 붙어야 한다")
        # **그 개체가 맞는가.** 개수만 세면 엉뚱한 것에 붙어도 통과한다 —
        # 짚은 개체의 중심이 표시된 상자 안에 들어야 한다. 픽스처의 개체 둘은
        # 자리가 멀어(40,50 과 160,130) 옆 개체에 붙으면 여기서 걸린다.
        c = self.stack_cand
        x, y = self.image_point(c.center_x, c.center_y)
        b = marks[0].bounding_box()
        self.assertTrue(b["x"] <= x <= b["x"] + b["width"]
                        and b["y"] <= y <= b["y"] + b["height"],
                        f"다른 개체에 표시가 붙었다 (개체 중심 {x:.0f},{y:.0f} · 표시 {b})")

    def test_무엇을_표시했는지_적는다(self):
        self.open_with(obj=self.stack_key, img=self.stack_img)
        self.assertIn("눌러서 오신 개체", self.flag_text())

    def test_그냥_열면_표시가_없다(self):
        """평소의 검토는 그대로다 — 표시는 링크를 타고 온 길에만 있다."""
        page = self.open_with()
        self.assertEqual(page.query_selector_all(".box.hl"), [])
        self.assertEqual(self.flag_text(), "")

    # --- 다른 판의 개체 -----------------------------------------------------

    def test_다른_판의_개체면_그_판으로_옮겨_간다(self):
        """**여기가 이 기능의 고비다.** 안 옮기면 합성본에서 찾다가 못 찾는다."""
        frame, img, det = self.frames[0]
        key = det.candidates.first().mask_key
        page = self.open_with(obj=key, img=img.pk)

        # 캐러셀이 그 판에 서 있어야 한다
        on = page.query_selector(".shot.on")
        self.assertEqual(on.get_attribute("data-detkey"), frame.name,
                         "짚어 온 개체가 있는 판으로 안 옮겨 갔다")
        self.assertEqual(len(page.query_selector_all(".box.hl")), 1,
                         "판은 옮겼는데 표시가 안 붙었다")
        self.assertIn("눌러서 오신 개체", self.flag_text())

    # --- 못 찾았을 때 -------------------------------------------------------

    def test_없는_개체를_짚으면_그렇게_말한다(self):
        page = self.open_with(obj="9999_9999_1_1")
        self.assertEqual(page.query_selector_all(".box.hl"), [],
                         "없는 개체인데 무엇엔가 표시가 붙었다")
        self.assertIn("찾지 못했습니다", self.flag_text())
        self.assertIsNotNone(page.query_selector(".hlflag.bad"),
                             "못 찾은 것과 찾은 것이 화면에서 같아 보인다")
        # **시야는 열려 있다.** 낡은 링크 하나로 검토를 못 하게 되면 안 된다
        self.assertGreater(len(page.query_selector_all(".box")), 0)

    # --- 이 개체로 (센터 + 확대) --------------------------------------------

    def test_누르면_개체가_가운데로_오고_확대된다(self):
        """**확대는 누를 때만 한다** — 도착만으로 배율이 바뀌면 사람이 정하지
        않은 배율에서 화면이 시작된다."""
        page = self.open_narrow(obj=self.stack_key, img=self.stack_img)
        self.assertEqual(self.scale(), 1,
                         "도착만 했는데 배율이 이미 바뀌어 있다")

        btn = self.zoom_btn()
        self.assertIsNotNone(btn, "'이 개체로' 버튼이 없다")
        btn.click()
        page.wait_for_timeout(200)

        self.assertGreater(self.scale(), 1, "눌렀는데 배율이 그대로다")
        # 개체가 화면 한가운데로 온다. 여백은 `clampPan` 이 잡아 줄 수 있어
        # 화면 크기의 5% 만큼 느슨하게 본다.
        cx, cy = self.hl_center()
        vx, vy = self.view_center()
        box = page.query_selector("#canvas-stack").bounding_box()
        self.assertLess(abs(cx - vx), box["width"] * 0.05, "가로로 안 맞았다")
        self.assertLess(abs(cy - vy), box["height"] * 0.05, "세로로 안 맞았다")

    def test_다시_누르면_전체로_돌아온다(self):
        page = self.open_narrow(obj=self.stack_key, img=self.stack_img)
        self.zoom_btn().click()
        page.wait_for_timeout(200)
        self.assertIn("전체 보기", self.zoom_btn().inner_text())

        self.zoom_btn().click()
        page.wait_for_timeout(200)
        self.assertEqual(self.scale(), 1, "전체로 안 돌아왔다")
        self.assertIn("이 개체로", self.zoom_btn().inner_text())

    def test_버튼을_눌러도_표시는_안_걷힌다(self):
        """**버튼이 자기를 누른 손에 사라지면 안 된다.** 표시를 걷는 것은 사진에
        `mousedown` 인데, 알림 줄도 그 안에 있고 캡처 단계라 버튼이 스스로
        `stopPropagation` 을 해도 늦다."""
        page = self.open_narrow(obj=self.stack_key, img=self.stack_img)
        self.zoom_btn().click()
        page.wait_for_timeout(200)
        self.assertEqual(len(page.query_selector_all(".box.hl")), 1,
                         "버튼을 눌렀더니 표시가 걷혔다")
        self.assertIsNotNone(self.zoom_btn(), "버튼이 제 손에 사라졌다")

    def test_원본_화소를_넘겨_늘리지_않는다(self):
        """축소본을 넘겨 확대하면 없는 화소를 만들어 보여 주는 셈이다."""
        page = self.open_narrow(obj=self.stack_key, img=self.stack_img)
        self.zoom_btn().click()
        page.wait_for_timeout(200)
        self.assertLessEqual(self.zoom_pct(), 100.5,
                             "원본 화소(100%)를 넘겨 늘렸다")

    def test_못_찾으면_버튼이_없다(self):
        """갈 곳이 없는 버튼을 내보이면 안 된다."""
        self.open_with(obj="9999_9999_1_1")
        self.assertIsNone(self.zoom_btn())

    # --- 자기 일을 시작하면 걷힌다 ------------------------------------------

    def test_화면을_누르면_표시가_걷힌다(self):
        """계속 깜빡이면 골라 둔 개체(`.sel`)와 헷갈린다 — 한 번 말하고 물러난다."""
        page = self.open_with(obj=self.stack_key, img=self.stack_img)
        self.assertEqual(len(page.query_selector_all(".box.hl")), 1)

        c = self.stack_cand
        self.click_image(c.center_x, c.center_y)
        self.assertEqual(page.query_selector_all(".box.hl"), [],
                         "누른 뒤에도 표시가 남아 있다")
        self.assertEqual(self.flag_text(), "",
                         "표시는 걷혔는데 말은 남아 있다")

    def test_걷힌_표시는_다시_그려도_안_돌아온다(self):
        """`build()` 가 키로 표시를 얹으므로, 걷을 때 키까지 지워야 한다.

        판을 옮기거나 개체를 고르면 `build()` 가 다시 도는데, 그때 표시가
        되살아나면 걷은 것이 아니다.
        """
        page = self.open_with(obj=self.stack_key, img=self.stack_img)
        c = self.stack_cand
        self.click_image(c.center_x, c.center_y)
        # 판을 한 번 옮겼다 온다 — `build()` 가 두 번 돈다
        shots = page.query_selector_all(".shot")
        self.assertGreater(len(shots), 1, "판이 하나뿐이면 이 시험이 아무것도 안 본다")
        shots[1].click()
        page.wait_for_timeout(200)
        shots[0].click()
        page.wait_for_timeout(200)
        self.assertEqual(page.query_selector_all(".box.hl"), [],
                         "걷은 표시가 다시 그리면서 되살아났다")
