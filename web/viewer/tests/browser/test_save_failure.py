"""DiaRUGA v0.29.0 `tests/browser/test_save_failure.py` 에서 왔다.

저장이 거절당했을 때 화면이 무엇을 하는가 (실사용 2026-08-10).

**실제로 이렇게 잃었다.** 프레임 판을 보며 오검출 둘을 지웠는데 서버가 그
저장을 409 로 거절했다(`find_viewpoint` 가 시야의 현재 검출 하나만 보고 판이
어긋났다고 했다). 화면은 마스크를 지운 채로 두고 회색 글씨 한 줄만 지나갔고,
사람은 이어서 개체를 묶었다 — **묶기는 그 문을 안 지나서 200 이었다.** 남은
것은 "묶기는 됐는데 지운 것이 살아 있는" 상태다.

서버 쪽 원인은 `test_review_image.py` 가 잡는다. 여기서 보는 것은 **화면이
실패를 어떻게 다루는가**이고, 그건 3겹에서 안 보인다.

1. 실패가 **크게** 뜬다 — 회색 한 줄이 아니라 안 사라지는 띠
2. 서버가 말한 이유가 그대로 나온다 (409 를 전부 "아직 검토를 열지
   않았습니다" 로 적고 있었다 — 사람이 안 겪은 문제를 찾으러 갔다)
3. **저장이 안 된 상태에서는 묶지 않는다** — 묶기는 "화면의 마스크가 DB 에
   있다" 를 전제로 한다. 한쪽만 남는 길을 막는다
"""
import json

from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import ForamObject, ObjectReview


class SaveFailureTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_frames=3, n_candidates=2)
        # 프레임마다 현재 검출 — 묶을 상대가 있어야 팝업이 성립한다
        self.extra = fx.add_frame_detections(self.w.vp)

    def block_review(self, why="테스트로 막았다"):
        """`/review` 만 409 로 막는다. **묶기(`/link`)는 안 막는다** — 실사용이
        정확히 그 모양이었다(한쪽은 거절, 한쪽은 통과)."""
        self.page.route(
            "**/review",
            lambda route: route.fulfill(
                status=409, content_type="application/json",
                body=json.dumps({"ok": False, "error": why})))

    def tearDown(self):
        # 409 는 **이 시험이 일부러 낸 것**이라 크로미움의 리소스 오류가 따라
        # 온다. 그것까지 고장으로 세면 시험이 제 손으로 빨간불을 낸다.
        self.errors = [e for e in self.errors if "409" not in e]
        super().tearDown()

    def open_review(self):
        return self.open(reverse("group", args=[self.w.slide.slug,
                                                self.w.vp.idx]))

    def delete_first(self):
        """첫 후보를 오검출로 지운다 — 픽스처의 (40,50,60,40)."""
        self.context_menu_at(40 + 60 // 2, 50 + 40 // 2)
        self.page.get_by_text("오검출로 삭제", exact=False).first.click()
        self.page.wait_for_timeout(900)

    # --- 1·2. 실패가 크게, 서버의 말로 -------------------------------------

    def test_저장이_거절당하면_안_사라지는_띠가_뜬다(self):
        page = self.open_review()
        self.block_review("판이 어긋났다 — 시험")
        self.delete_first()

        bar = page.query_selector(".errbar.savefail")
        self.assertIsNotNone(
            bar, "저장이 거절당했는데 화면에 아무 띠도 없다 — "
                 "회색 한 줄로는 사람이 못 본다")
        text = bar.inner_text()
        self.assertIn("판이 어긋났다", text,
                      f"서버가 말한 이유가 안 나온다: {text}")
        self.assertIn("DB 에 없습니다", text)
        self.assertEqual(ObjectReview.objects.count(), 0,
                         "막아 뒀는데 저장됐다 — 시험이 틀렸다")

    def test_다시_저장에_성공하면_띠가_사라진다(self):
        page = self.open_review()
        self.block_review()
        self.delete_first()
        self.assertIsNotNone(page.query_selector(".errbar.savefail"))

        page.unroute("**/review")
        self.context_menu_at(140 + 50 // 2, 150 + 40 // 2)   # 둘째 후보
        page.get_by_text("오검출로 삭제", exact=False).first.click()
        page.wait_for_timeout(900)

        self.assertIsNone(page.query_selector(".errbar.savefail"),
                          "저장에 성공했는데 실패 띠가 남아 있다")
        self.assertTrue(ObjectReview.objects.filter(removed=True).exists())

    # --- 3. 저장이 안 된 상태에서는 묶지 않는다 -----------------------------

    def test_저장이_안_된_상태에서는_묶기가_거절된다(self):
        """실사용의 그 자리 — 한쪽만 남는 길을 막는다."""
        page = self.open_review()
        self.block_review("판이 어긋났다 — 시험")
        self.delete_first()

        # 남아 있는 개체 위에서 묶기를 연다
        menu = self.context_menu_at(140 + 50 // 2, 150 + 40 // 2)
        self.assertIsNotNone(menu, "우클릭 메뉴가 안 열렸다")
        item = None
        for el in menu.query_selector_all("button, .mi"):
            if "동일 개체 묶기" in (el.inner_text() or ""):
                item = el
                break
        self.assertIsNotNone(item, "묶기 항목이 없다")
        item.click()
        page.wait_for_selector(".linkpanel", state="visible", timeout=3000)

        btn = page.query_selector(".linkpanel .lfoot .btn")
        self.assertIsNotNone(btn, "묶기 단추가 없다")
        btn.click()
        page.wait_for_timeout(1200)

        self.assertEqual(fx.links().count(), 0,
                         "앞선 교정이 저장 안 됐는데 묶음이 만들어졌다 — "
                         "한쪽만 남는다")
        err = page.query_selector(".linkpanel .lerr, .linkpanel .err")
        self.assertIsNotNone(err, "거절해 놓고 아무 말도 안 한다")
        self.assertIn("저장되지 않았습니다", err.inner_text())
