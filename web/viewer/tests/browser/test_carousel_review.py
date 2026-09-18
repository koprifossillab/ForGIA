"""DiaRUGA v0.29.0 `tests/browser/test_carousel_review.py` 에서 왔다.

캐러셀이 판을 바꾸면 **교정도 저장 대상도 따라간다** (DiaRUGA P09 1단계).

시야 하나에 현재 검출이 여럿이면(합성본 + 프레임마다) 판 하나를 그리고 나머지는
캐러셀이 갈아 끼운다. 그때 바뀌어야 하는 것이 셋이다 — 그리는 개체, 사람이 만든
교정, 그리고 **저장이 갈 이미지**.

**셋째가 빠지면 화면은 프레임을 보여 주면서 저장은 합성본으로 간다.** 예외도
경고도 없다. 사람은 프레임을 검토했다고 믿고, DB 에는 합성본의 교정이 쌓인다.
017·027·053 과 같은 계열이고, `/review` 는 그 범위를 **갈아치우므로** 반대쪽
판단이 함께 사라진다.

**3겹으로는 절대 안 걸린다** — 서버가 내는 HTML 은 판 하나뿐이고, 갈리는 것은
브라우저 안에서다. `swapDet` 이 실제로 도는지는 눌러 봐야 안다.
"""
import json

from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import ObjectReview


class CarouselPerImageTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_frames=3, n_candidates=3)
        self.extra = fx.add_frame_detections(self.w.vp)
        self.frame, self.frame_img, self.frame_det = self.extra[0]
        self.stack_det = self.w.detection()

    def open_review(self):
        return self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))

    def shot_buttons(self):
        """캐러셀의 판 단추들 — `data-detkey` 가 붙은 것만."""
        return self.page.query_selector_all(".shot[data-detkey]")

    def click_shot(self, detkey):
        """캐러셀에서 판 하나를 고른다.

        **누른 뒤 사진을 다시 화면 안으로 올린다.** 단추가 페이지 아래쪽에 있어
        playwright 가 거기까지 스크롤하고, 그러면 사진이 뷰포트 위로 밀려난다 —
        `image_point` 는 svg 의 화면 좌표로 재므로 그 뒤의 클릭이 **화면 밖을
        찍는다.** 실측으로 svg 의 y 가 105 → −268 이 됐다.

        코드가 아니라 시험이 만드는 함정이고, 여기서 안 잡으면 "프레임 판에서는
        우클릭 메뉴가 안 뜬다" 는 **없는 고장**을 쫓게 된다.
        """
        el = self.page.query_selector(f'.shot[data-detkey="{detkey}"]')
        self.assertIsNotNone(el, f"캐러셀에 {detkey} 판이 없다")
        el.click()
        self.page.wait_for_timeout(250)
        self.masks_svg().scroll_into_view_if_needed()
        self.page.wait_for_timeout(150)
        return el

    # --- 판이 여럿 뜨는가 --------------------------------------------------

    def test_합성본과_프레임이_모두_판으로_뜬다(self):
        self.open_review()
        keys = [b.get_attribute("data-detkey") for b in self.shot_buttons()]
        self.assertIn("__stack__", keys, "합성본 판에 detkey 가 없다")
        for f, _, _ in self.extra:
            self.assertIn(f.name, keys, f"{f.name} 판이 캐러셀에 없다")

    def test_판마다_검출_자료가_실린다(self):
        """`shot_dets` 가 없으면 `swapDet` 이 곧장 빠져나가 아무 일도 안 한다 —
        **화면은 멀쩡해 보이고 개체만 안 바뀐다.**"""
        page = self.open_review()
        el = page.query_selector("#shotdet-stack")
        self.assertIsNotNone(el, "검토 화면에 shot_dets 가 안 실렸다")
        data = json.loads(el.text_content())
        self.assertIn("__stack__", data)
        # **저장 대상이 실려 있어야 한다** — 이것이 빠지면 저장이 대표로 간다
        self.assertEqual(data["__stack__"]["image"], self.stack_det.image_id)
        self.assertEqual(data[self.frame.name]["image"], self.frame_img.pk)

    # --- 판을 바꾸면 저장 대상이 따라가는가 ---------------------------------

    def test_프레임_판으로_옮기면_저장_대상이_바뀐다(self):
        page = self.open_review()
        self.assertEqual(page.evaluate("document.querySelector('.detview').dataset.image"),
                         str(self.stack_det.image_id))
        self.click_shot(self.frame.name)
        # `curImage` 는 JS 안의 값이라 직접 못 본다 — POST 로 확인한다(아래).
        # 여기서는 판이 실제로 바뀌었는지만 본다.
        self.assertTrue(
            page.query_selector(f'.shot[data-detkey="{self.frame.name}"]')
                .get_attribute("class").find("on") >= 0,
            "프레임 판을 눌렀는데 선택되지 않았다")

    def test_프레임_판에서_지우면_그_이미지에_저장된다(self):
        """**이 시험이 이 파일의 이유다.**

        배선이 하나라도 끊기면 교정이 합성본 이미지에 앉는다 — 화면은
        "저장됨" 이라고 적고, 사람은 프레임을 검토했다고 믿는다.
        """
        page = self.open_review()
        self.click_shot(self.frame.name)

        # 첫 통과분 한가운데에서 "오검출로 삭제"
        menu = self.context_menu_at(40 + 60 // 2, 50 + 40 // 2)
        self.assertIsNotNone(menu, "프레임 판에서 개체 우클릭 메뉴가 안 떴다")
        page.get_by_text("오검출로 삭제", exact=False).first.click()
        page.wait_for_timeout(900)          # 지연 저장 400ms + 여유

        rows = list(ObjectReview.objects.all())
        self.assertEqual(len(rows), 1, f"교정이 하나여야 한다: {rows}")
        self.assertEqual(rows[0].image_id, self.frame_img.pk,
                         "프레임을 보고 지웠는데 합성본 이미지에 앉았다")
        self.assertTrue(rows[0].removed)

    def test_합성본_교정과_프레임_교정이_서로_안_지운다(self):
        """`/review` 는 그 범위를 갈아치운다 — 범위가 이미지로 갈려 있어야 한다."""
        page = self.open_review()

        # 합성본에서 하나 지운다
        self.context_menu_at(40 + 60 // 2, 50 + 40 // 2)
        page.get_by_text("오검출로 삭제", exact=False).first.click()
        page.wait_for_timeout(900)
        on_stack = ObjectReview.objects.get()
        self.assertEqual(on_stack.image_id, self.stack_det.image_id)

        # 프레임으로 옮겨 하나 지운다 — 합성본 것이 남아 있어야 한다
        self.click_shot(self.frame.name)
        self.context_menu_at(40 + 60 // 2, 50 + 40 // 2)
        page.get_by_text("오검출로 삭제", exact=False).first.click()
        page.wait_for_timeout(900)

        self.assertTrue(ObjectReview.objects.filter(pk=on_stack.pk).exists(),
                        "프레임 판의 저장이 합성본 교정을 지웠다")
        self.assertEqual(ObjectReview.objects.count(), 2)

    def test_판을_옮겼다_돌아와도_교정이_남아_있다(self):
        """안 보낸 교정을 들고 판을 옮길 수 있다 — 판마다 상태를 기억해야 한다.

        빠뜨리면 **돌아왔을 때 서버 값으로 되돌아가** 사람이 방금 한 일이
        사라진다. 저장은 이미 나갔으므로 DB 와 화면이 어긋난 채로 남는다.
        """
        page = self.open_review()
        self.context_menu_at(40 + 60 // 2, 50 + 40 // 2)
        page.get_by_text("오검출로 삭제", exact=False).first.click()
        page.wait_for_timeout(900)

        self.click_shot(self.frame.name)
        self.click_shot("__stack__")
        page.wait_for_timeout(200)

        # 지운 개체는 점선 흔적으로 남는다 — `.box.gone` 이 그것이다.
        self.assertTrue(page.query_selector(".box.gone"),
                        "합성본으로 돌아오니 지운 표시가 사라졌다")
