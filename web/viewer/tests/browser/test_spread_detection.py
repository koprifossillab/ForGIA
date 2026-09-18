"""DiaRUGA v0.29.0 `tests/browser/test_spread_detection.py` 에서 왔다.

검출 마스크를 다른 판에도 앉힌다 — **실제로 눌러서** 배선을 본다 (DiaRUGA P19).

3겹(endpoint 시험)은 서버 검증까지만 본다. 여기서 보는 것: 메뉴에 항목이
**앉힐 판 수와 함께** 나오는가 · 눌렀을 때 정말 앉는가 · 띠가 뜨는가 ·
**앉힌 뒤에는 항목이 사라지는가**(앉힐 판이 없으므로) · 판을 넘어가도 복제본이
살아 있는가. **이벤트 배선 고장은 이것으로만 잡힌다** (CLAUDE.md).

`test_detection_js` 는 항목이 **렌더되는지**까지만 본다 — 눌러서 예외가 나는
갈래는 거기서 안 잡힌다.
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import ObjectReview


class SpreadDetectionBrowserTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        # 합성본 + 프레임 3장, **프레임마다 현재 검출** — YOLO 로 갈아탄 모습.
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_frames=3, n_candidates=2)
        self.extra = fx.add_frame_detections(self.w.vp)

    def open_group(self):
        return self.open(reverse("group", args=[self.w.slide.slug,
                                                self.w.vp.idx]))

    def menu_item(self, menu, text):
        for el in menu.query_selector_all("button, .mi"):
            if text in (el.inner_text() or ""):
                return el
        return None

    def click_shot(self, detkey):
        """캐러셀에서 판 하나를 고른다.

        **누른 뒤 사진을 다시 화면 안으로 올린다** — 단추가 아래쪽에 있어
        playwright 가 거기까지 스크롤하면 사진이 뷰포트 위로 밀려나고, 그 뒤의
        클릭이 화면 밖을 찍는다(`test_carousel_review` 의 그 주석).
        """
        el = self.page.query_selector(f'.shot[data-detkey="{detkey}"]')
        self.assertIsNotNone(el, f"캐러셀에 {detkey} 판이 없다")
        el.click()
        self.page.wait_for_timeout(250)
        self.masks_svg().scroll_into_view_if_needed()
        self.page.wait_for_timeout(150)
        return el

    def drawn_rows(self):
        return ObjectReview.objects.filter(viewpoint=self.w.vp,
                                           batch__isnull=True)

    def test_눌러서_다른_판에_앉는다(self):
        page = self.open_group()
        # 합성본 첫 후보 위에서 우클릭 (팩토리의 첫 후보 중심)
        menu = self.context_menu_at(70, 70)
        self.assertIsNotNone(menu, "우클릭 메뉴가 안 열렸다")
        item = self.menu_item(menu, "다른 판에도 앉히기")
        self.assertIsNotNone(item, "앉히기 항목이 메뉴에 없다")
        # **괄호 안이 앉힐 판 수다** — 프레임 셋
        self.assertIn("(3)", item.inner_text())

        self.assertEqual(self.drawn_rows().count(), 0)
        item.click()
        page.wait_for_selector(".errbar.spreadok", state="visible",
                               timeout=4000)
        self.assertIn("판 3곳에 앉혔습니다",
                      page.query_selector(".errbar.spreadok").inner_text())

        rows = list(self.drawn_rows())
        self.assertEqual(len(rows), 3, "프레임 셋에 복제본이 앉아야 한다")
        keys = {r.mask_key for r in rows}
        self.assertEqual(len(keys), 1, "복제본은 키 하나를 나눠 쓴다")
        self.assertRegex(keys.pop(), r"^m[0-9a-f]{8}$")

        # **묶였고 얼굴이 합성본이다** — 혼자인 개체였으므로 옮긴다
        obj = rows[0].foram_object
        self.assertEqual(obj.members.count(), 4)
        self.assertEqual(obj.members.get(is_rep=True).image.kind, "stack")

    def test_앉힌_뒤에는_항목이_사라진다(self):
        """**0이면 항목을 안 낸다** — "눌렀는데 아무 일도 안 나는" 자리를
        만들지 않는다."""
        page = self.open_group()
        self.menu_item(self.context_menu_at(70, 70),
                       "다른 판에도 앉히기").click()
        page.wait_for_selector(".errbar.spreadok", state="visible",
                               timeout=4000)
        menu = self.context_menu_at(70, 70)
        self.assertIsNone(self.menu_item(menu, "다른 판에도 앉히기"),
                          "앉힐 판이 없는데 항목이 남아 있다")
        # 묶음이 생겼으니 묶기 항목은 '묶음 열기' 가 되어 있어야 한다
        self.assertIsNotNone(self.menu_item(menu, "묶음 열기"))

    def test_판을_넘어가도_복제본이_남는다(self):
        """**반쪽으로 넣으면 자료를 잃는다** (DiaRUGA P19 4.2). 화면이 복제본을 자기
        상태에 안 얹으면 그 판의 다음 저장이 `/review` 에서 지운다 — 111 이
        이미 당한 자리다.

        **저장은 엔진 마스크를 지워서 일으킨다** (2026-09-03). 복제본은 (70,70)
        에 앉으므로 거기서 지우면 **복제본 자체를 지우는 것**이라 이 시험이 재려는
        것과 다른 일이 된다 — 그렇게 짚고 있었고, 그때는 그 저장이 409 로
        거절되던 때라 "안 지워졌다" 가 참이 됐다. 고장이 시험을 통과시키고
        있었다. 둘째 후보는 (DiaRUGA 195,155) 다(`factories`).
        """
        page = self.open_group()
        self.menu_item(self.context_menu_at(70, 70),
                       "다른 판에도 앉히기").click()
        page.wait_for_selector(".errbar.spreadok", state="visible",
                               timeout=4000)
        n_before = self.drawn_rows().count()

        # 프레임으로 넘어가 그 판에서 저장이 한 번 일어나게 한다
        frame = self.w.vp.frames.order_by("pk").first()
        self.click_shot(frame.name)
        # **uid 는 그대로 `stack` 이다** — 캐러셀은 svg 를 새로 만들지 않고
        # `swapDet` 이 그 안의 검출을 갈아 끼운다. 판 이름을 uid 로 주면
        # `#masks-<이름>` 을 기다리다 10초 뒤에 죽는다 (실제로 그랬다).
        menu = self.context_menu_at(195, 155)
        self.assertIsNotNone(menu, "프레임에서 우클릭 메뉴가 안 열렸다")
        item = self.menu_item(menu, "오검출로 삭제")
        self.assertIsNotNone(item, "삭제 항목이 없다 — 판이 안 바뀐 것이다")
        item.click()
        self.page.wait_for_timeout(1200)     # 400ms 지연 저장보다 넉넉히

        self.assertEqual(self.drawn_rows().count(), n_before,
                         "판을 넘어간 저장이 복제본을 지웠다")

    def test_앉힌_마스크를_지우면_저장이_안_막힌다(self):
        """**실사용에서 하루에 101건이 여기서 날아갔다** (2026-09-03).

        앉힌 마스크는 `batch=NULL` 이라 그 키가 `removed` 에 실려 나가면 서버가
        "현재 검출에 없는 개체" 로 보고 **저장 전체를 거절했다** — 한 번 그러면
        그 시야의 이후 저장이 계속 실패해서 사람이 같은 검토를 몇 번이고 다시
        했다. 지우기는 `drawn` 에서 빠지는 것으로 말한다 (DiaRUGA P09 5.10).
        """
        page = self.open_group()
        self.menu_item(self.context_menu_at(70, 70),
                       "다른 판에도 앉히기").click()
        page.wait_for_selector(".errbar.spreadok", state="visible",
                               timeout=4000)
        n_before = self.drawn_rows().count()

        frame = self.w.vp.frames.order_by("pk").first()
        self.click_shot(frame.name)
        self.menu_item(self.context_menu_at(70, 70), "오검출로 삭제").click()
        self.page.wait_for_timeout(1200)

        # **실패 띠가 뜨면 안 된다** — 사람이 보는 것은 이것이다
        bar = page.query_selector(".errbar.savefail")
        self.assertTrue(bar is None or not bar.is_visible(),
                        "앉힌 마스크를 지웠더니 저장이 거절됐다")
        self.assertEqual(self.drawn_rows().count(), n_before - 1,
                         "지운 복제본의 행이 안 없어졌다")
