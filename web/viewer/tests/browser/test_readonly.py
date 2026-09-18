"""DiaRUGA v0.29.0 `tests/browser/test_readonly.py` 에서 왔다.

읽기 전용 화면이 **되는 것처럼 보이지 않는가** (051).

저장은 막혀 있는데 우클릭 메뉴가 살아 있었다. "오검출로 삭제" 를 누르면 화면에서
마스크가 지워지고, 탈락 펼침판에서 고르면 검출로 올라간다 — 저장만 안 나간다.
그렇게 한 시야를 검토하고 새로고침하면 판단이 통째로 사라지고, **사람은 자기가
무엇을 했는지 모른다.** 교정 37건이 그렇게 없어졌다.

> `readOnly` 갈래를 더할 때는 **저장 경로만 보지 말고 화면이 반응하는 자리를
> 전부 센다**: 키보드 · 우클릭 메뉴 · 펼침판 · 코멘트 칸 (CLAUDE.md).

이 파일이 그 넷을 하나씩 센다. **3겹으로는 절대 안 걸린다** — 서버는 어느
쪽이든 같은 HTML 을 내고, 갈리는 것은 브라우저 안에서다.

읽기 전용에 닿는 길은 `?batch=<run_id>` 다 — 현재 검출이 아닌 다른 엔진의
검출을 그리는 화면이고, 교정은 `mask_key`(bbox 문자열)로 붙는데 엔진이 다르면
거의 전부 어긋나므로 저장을 받으면 안 된다.
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx


class ReadOnlyScreenTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=3)
        self.run = fx.add_other_engine(self.w.vp)

    # --- 두 화면을 나란히 연다 --------------------------------------------

    def open_ro(self):
        """읽기 전용 화면 (다른 엔진의 검출)."""
        url = reverse("group", args=[self.w.slug, self.w.vp.idx])
        return self.open(f"{url}?batch={self.run.id}")

    def open_rw(self):
        """보통의 검토 화면. **대조군이다.**"""
        return self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))

    def test_읽기_전용_표식이_뜬다(self):
        """`ro-stack` 하나로 저장 차단과 화면 표시가 함께 갈린다 — 따로 두면
        한쪽만 켜진 화면이 나온다."""
        page = self.open_ro()
        self.assertIsNotNone(page.query_selector("#ro-stack"),
                             "읽기 전용인데 ro-stack 표식이 없다")
        self.assertTrue(page.is_visible("#roflag-stack"),
                        "'Read only' 안내가 안 보인다")

    def test_보통_화면에는_표식이_없다(self):
        """**대조군이 없으면 이 시험은 아무것도 증명하지 않는다** — 늘 감춰져
        있어도 통과하기 때문이다."""
        page = self.open_rw()
        self.assertIsNone(page.query_selector("#ro-stack"))

    # --- 반응하는 자리 넷 -------------------------------------------------

    def test_도구와_완료줄이_안_보인다(self):
        """**`getComputedStyle` 로 확인한다.** `.detview .tools` 가
        `display: flex` 로 특이도에서 이겨, 감춘 줄 알고 계속 내보인 적이 있다
        (051). 요소가 있느냐가 아니라 **실제로 그려지느냐**를 본다.

        **`.drawtool` 이 목록에 있는 이유** (194): 마스크 그리기가 `.tools`
        상자 밖(사진 오른쪽 위)으로 나갔다. 감추는 규칙에 그 이름을 안 적으면
        검토 대상이 아닌 묶음에서 마스크를 그릴 수 있게 된다 — 저장은 막혀
        있으니 **한 시야를 헛검토하고 새로고침 한 번에 잃는다.** 도구를 옮길
        때마다 이 목록이 함께 늘어야 한다.
        """
        page = self.open_ro()
        for sel in (".tools", ".drawtool", ".donebar"):
            for el in page.query_selector_all(sel):
                with self.subTest(sel=sel):
                    self.assertFalse(el.is_visible(),
                                     f"읽기 전용인데 {sel} 이 보인다")

    def test_단축키_안내가_감춰진다(self):
        """안내가 남아 있으면 눌러 보고 "안 되네" 가 된다 — 있는 기능인 줄 알고
        헤매는 쪽이 더 나쁘다."""
        page = self.open_ro()
        hint = page.query_selector("#keyhint-stack")
        if hint is not None:
            self.assertFalse(hint.is_visible(), "읽기 전용인데 단축키 안내가 보인다")

    def test_시야_코멘트_칸이_잠긴다(self):
        """적을 수는 있는데 저장이 안 되면 적은 것을 잃는다."""
        page = self.open_ro()
        gnote = page.query_selector("#gnote-stack")
        if gnote is None:
            self.skipTest("이 화면에 시야 코멘트 칸이 없다")
        self.assertTrue(gnote.get_property("readOnly").json_value(),
                        "읽기 전용인데 코멘트 칸에 적을 수 있다")
        self.assertIn("읽기 전용", gnote.get_attribute("placeholder") or "")

    # 읽기 전용 메뉴에 **있어도 되는** 것. 보는 일이지 고치는 일이 아니다.
    #   · 왜 안 되는지 적는 안내 (아무 일도 안 일어나면 고장으로 읽힌다)
    #   · 이 자리의 탈락 후보 보기 (펼친 판에서 고르는 것은 openSpread 가 막는다)
    #   · 선택 해제 (고르는 것은 지표를 읽는 일이다)
    #
    # **없어야 하는** 것 — 누르면 화면이 바뀌는데 저장은 안 나가는 것들.
    고치는_항목 = ["오검출로 삭제", "되살리기", "실행취소", "코멘트"]

    def test_개체_위_우클릭에_고치는_항목이_없다(self):
        """**051 이 정확히 여기서 났다.** 저장은 잠갔는데 메뉴는 살아 있어서,
        "오검출로 삭제" 를 누르면 마스크가 지워지고 분류를 고르면 색이 바뀌었다.

        메뉴가 아예 안 뜨는 것까지 요구하지 않는다 — **왜 안 되는지는 적어야
        한다.** 아무 일도 안 일어나면 그것도 고장으로 읽힌다.
        """
        self.open_ro()
        cx, cy = 300 + 55 // 2, 250 + 45 // 2      # 첫 통과분 한가운데
        menu = self.context_menu_at(cx, cy)
        self.assertIsNotNone(menu, "개체 위 우클릭인데 메뉴가 안 떴다")

        text = menu.inner_text()
        self.assertIn("Read only", text, f"왜 안 되는지 안 적혀 있다:\n{text}")
        found = [w for w in self.고치는_항목 if w in text]
        self.assertEqual(found, [],
                         f"읽기 전용 메뉴에 고치는 항목이 있다: {found}\n{text}")
        # 분류 이름도 없어야 한다 — 고르면 색이 바뀐다.
        for _, label, *_ in fx.CLASSES:
            with self.subTest(label=label):
                self.assertNotIn(label, text)

    def test_보통_화면의_같은_자리에는_고치는_항목이_있다(self):
        """**대조군.** 위 시험이 "메뉴가 원래 안 뜬다"·"내가 빈 자리를 눌렀다"
        로 통과하는 것을 막는다 — 그러면 배선이 끊겨도 초록이다.

        **같은 좌표를 쓴다.** 다른 자리를 누르면 비교가 아니다.
        """
        self.open_rw()
        # 보통 화면은 현재 검출을 그린다 — 그쪽 첫 통과분의 자리다.
        cx, cy = 40 + 60 // 2, 50 + 40 // 2
        menu = self.context_menu_at(cx, cy)
        self.assertIsNotNone(menu, "보통 화면인데 개체 위 우클릭 메뉴가 안 뜬다")

        text = menu.inner_text()
        self.assertIn("오검출로 삭제", text, f"고치는 항목이 없다:\n{text}")
        self.assertNotIn("Read only", text)

    def review_posts(self):
        """이 페이지가 낸 `/review` POST 를 모으는 목록을 돌려준다."""
        posts = []
        self.page.on("request", lambda r: (
            posts.append(r.url)
            if r.method == "POST" and r.url.rstrip("/").endswith("/review")
            else None))
        return posts

    def edit_something(self, cx, cy):
        """**실제로 저장을 부르는 조작**을 한다 — 개체를 고르고 분류 키를 누른다.

        **마스크 클릭만으로는 저장이 안 나간다** — 그것은 고르는 일이라
        `save()` 를 안 부른다. 처음에 클릭만으로 시험을 짰다가 저장 차단을
        통째로 풀어도 안 걸리는 것을 보고 알았다. **실패할 수 없는 시험은
        없는 것보다 나쁘다** — 덮은 줄 알게 한다.
        """
        self.click_image(cx, cy)                    # 고른다
        for key in ("q", "w", "Space"):             # 분류 · 오검출 삭제
            self.page.keyboard.press(key)
            self.page.wait_for_timeout(120)
        self.page.wait_for_timeout(1200)            # 지연 저장 400 ms

    def test_키보드가_안_듣는다(self):
        """분류 단축키와 스페이스(오검출 삭제) 둘 다. 삭제는 교정 6,753건 중
        5,467건이라 **가장 많이 하는 조작**이다."""
        posts = self.review_posts()
        self.open_ro()
        self.edit_something(300 + 27, 250 + 22)
        self.assertEqual(posts, [], f"읽기 전용인데 저장이 나갔다: {posts}")

    def test_보통_화면은_같은_조작에_저장을_보낸다(self):
        """**대조군. 이것이 없으면 위 시험은 아무것도 증명하지 않는다.**

        조작이 애초에 저장을 안 부르는 것이었다면 읽기 전용이든 아니든 POST 가
        0이고, 차단을 통째로 풀어도 초록이다 — 실제로 한 번 그렇게 짰다.
        """
        posts = self.review_posts()
        self.open_rw()
        self.edit_something(40 + 30, 50 + 20)       # 현재 검출의 첫 통과분
        self.assertTrue(posts, "보통 화면인데 저장이 안 나갔다 — 대조군이 성립하지 않는다")

    def open_spread(self):
        """탈락 펼침판을 연다. **우클릭 메뉴를 거친다** — 평범한 클릭으로는 안 열린다.

        그 메뉴 항목은 **빈 자리를 눌렀을 때만** 나온다(`d.target === null`).
        그래서 픽스처가 탈락 후보를 통과분이 안 덮는 자리에 둔다
        (`factories.REJECT_BOX`).
        """
        menu = self.context_menu_at(*fx.REJECT_CENTER)
        self.assertIsNotNone(menu, "빈 자리 우클릭인데 메뉴가 안 떴다")

        item = None
        for el in menu.query_selector_all("*"):
            if "탈락 후보 보기" in (el.inner_text() or ""):
                item = el
        self.assertIsNotNone(item,
                             f"'탈락 후보 보기' 항목이 없다:\n{menu.inner_text()}")
        self.assertNotIn("disabled", (item.get_attribute("class") or ""),
                         "그 자리에 탈락 후보가 없다고 나온다 — 픽스처 좌표를 볼 것")
        item.click()
        self.page.wait_for_timeout(300)
        spread = self.page.query_selector(".spread")
        self.assertIsNotNone(spread, "'탈락 후보 보기' 를 눌렀는데 펼침판이 안 열렸다")
        return spread

    def test_탈락_펼침판_칸이_disabled_다(self):
        """고르면 그 자리에서 검출로 올라가는데(`accepted` 에 넣고 `cands` 에
        밀어 넣는다) 저장이 안 나가므로 새로고침 한 번에 사라진다.

        **핸들러만 빼면 눌리는 것처럼 보인다** — `disabled` 여야 한다.
        """
        self.open_ro()
        spread = self.open_spread()

        self.assertIn("읽기 전용", spread.query_selector(".shead").inner_text())
        cells = spread.query_selector_all(".scell")
        self.assertTrue(cells, "펼침판이 열렸는데 칸이 없다")
        for cell in cells:
            self.assertTrue(cell.is_disabled(),
                            "읽기 전용인데 탈락 후보 칸을 누를 수 있다")

    def test_보통_화면의_펼침판_칸은_누를_수_있다(self):
        """**대조군.** 위 시험이 "칸이 원래 다 disabled" 로 통과하면 안 된다."""
        self.open_rw()
        # 보통 화면은 현재 검출을 그린다 — 그쪽 탈락분 자리다 (factories).
        menu = self.context_menu_at(500 + 10, 400 + 9)
        self.assertIsNotNone(menu, "빈 자리 우클릭인데 메뉴가 안 떴다")
        item = None
        for el in menu.query_selector_all("*"):
            if "탈락 후보 보기" in (el.inner_text() or ""):
                item = el
        self.assertIsNotNone(item, f"항목이 없다:\n{menu.inner_text()}")
        item.click()
        self.page.wait_for_timeout(300)

        spread = self.page.query_selector(".spread")
        self.assertIsNotNone(spread, "보통 화면인데 펼침판이 안 열렸다")
        self.assertNotIn("읽기 전용", spread.query_selector(".shead").inner_text())
        cells = spread.query_selector_all(".scell")
        self.assertTrue(cells)
        self.assertFalse(cells[0].is_disabled(),
                         "보통 화면인데 탈락 후보 칸을 누를 수 없다")
