"""DiaRUGA v0.29.0 `tests/browser/test_carousel_preview.py` 에서 왔다.

커서가 **스치고 지나간 판**이 고른 판의 자리에 앉지 않는가 (074).

캐러셀의 썸네일에 커서를 얹으면 그리기만 바뀐다(미리보기). 그런데 그리기는
전역(`cands`·`rejects`)을 갈아 끼워서 하고, `stash()` 는 그 전역을 **고른
판(`curKey`)의 것**으로 알고 담는다. 그래서 커서가 옆 판을 스친 뒤에 판을
고르면 **두 판의 상태가 뒤바뀐다.**

사람이 본 그대로:

> 합성본이 떠 있고, 첫 프레임 썸네일에 커서를 올리면 그 프레임 검출이 뜬다.
> 다시 합성본 썸네일에 커서를 올리면 합성본 검출이 뜬다. 그런데 **합성본을
> 누르면 첫 프레임의 검출이 뜬다.**

재현해 보니 한 쪽만이 아니라 **서로 바뀐다** — 프레임 자리에 합성본의 30개가,
합성본 자리에 프레임의 3개가 앉았다.

더 나쁜 자리가 하나 더 있다. 지연 저장(400 ms)이 걸린 채로 옆 판을 스치고 다른
판을 누르면 `flushSave()` 가 **스쳐 지나간 판의 개체를 고른 판의 이미지로**
보낸다. `/review` 는 그 이미지의 교정을 통째로 갈아치우므로(027·053 계열)
**본 적 없는 판단이 앉고 있던 것이 지워진다.**
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import ObjectReview


class CarouselPreviewTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_frames=3, n_candidates=3)
        self.extra = fx.add_frame_detections(self.w.vp)
        self.frame = self.extra[0][0]
        self.frame_img = self.extra[0][1]
        self.stack_det = self.w.detection()

    def open_review(self):
        page = self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))
        page.wait_for_selector(".detview .box")
        return page

    def shot(self, detkey):
        el = self.page.query_selector(f'.shot[data-detkey="{detkey}"]')
        self.assertIsNotNone(el, f"캐러셀에 {detkey} 판이 없다")
        el.scroll_into_view_if_needed()
        return el

    def drawn(self):
        """지금 그려져 있는 개체들의 자리 — 어느 판의 것인지 이것으로 가른다."""
        return self.page.evaluate(
            """() => [...document.querySelectorAll('.detview .box')]
                 .map(e => e.style.left + '/' + e.style.top)""")

    def pick(self, detkey):
        """**커서를 먼저 얹고, 미리보기가 끝난 뒤에 누른다.**

        사람이 하는 그대로다. 곧바로 누르면 미리보기의 사진 내려받기가 아직
        안 끝나 전역이 안 바뀌어 있고, **고장이 그 사이로 빠져나간다** — 실제로
        이 시험이 고치기 전 코드에서 통과했다.
        """
        el = self.shot(detkey)
        el.hover()
        self.page.wait_for_timeout(350)
        el.click()
        self.page.wait_for_timeout(300)
        self.masks_svg().scroll_into_view_if_needed()
        self.page.wait_for_timeout(120)

    def hover(self, detkey):
        self.shot(detkey).hover()
        self.page.wait_for_timeout(300)

    # --- 판이 뒤바뀌지 않는가 ----------------------------------------------

    def test_스친_판이_고른_판의_자리에_앉지_않는다(self):
        """**사람이 본 그대로의 순서다.**"""
        self.open_review()
        stack = self.drawn()
        self.pick(self.frame.name)
        frame = self.drawn()
        self.assertNotEqual(stack, frame, "두 판의 개체가 같다 — 자료가 틀렸다")

        self.hover("__stack__")
        self.pick(self.frame.name)
        self.assertEqual(self.drawn(), frame,
                         "합성본을 스쳤더니 프레임 자리에 합성본 개체가 앉았다")

        self.hover(self.frame.name)
        self.pick("__stack__")
        self.assertEqual(self.drawn(), stack,
                         "프레임을 스쳤더니 합성본 자리에 프레임 개체가 앉았다")

    def test_스치고_지나가도_그리기는_제자리로_돌아온다(self):
        """커서가 캐러셀을 벗어나면 고른 판으로 돌아와야 한다."""
        page = self.open_review()
        stack = self.drawn()
        self.hover(self.frame.name)
        self.assertNotEqual(self.drawn(), stack, "스쳤는데 안 바뀐다")
        page.mouse.move(20, 20)             # 캐러셀 밖
        page.wait_for_timeout(350)
        self.assertEqual(self.drawn(), stack, "돌아오지 않았다")

    # --- 저장이 엉뚱한 이미지로 가지 않는가 ---------------------------------

    def test_스치며_판을_옮겨도_교정이_제_이미지로_간다(self):
        """교정이 어느 이미지에 앉는지를 끝에서 확인한다.

        **`flushSave` 가 스쳐 지나간 판의 개체를 싣는 창은 여기서 못 짚는다** —
        지연 저장이 400 ms 라 시험이 커서를 옮기기 전에 이미 나간다. 그 자리는
        같은 되돌리기(`unpreviewState`)가 `flushSave` 앞에서 막고 있고, 여기서
        보는 것은 **결과가 제 이미지로 갔는가**다. 못 짚는 것을 짚었다고 적지
        않는다 — 덮은 줄 알게 하는 시험이 없는 것보다 나쁘다(064).
        """
        page = self.open_review()

        # 합성본에서 하나 지운다 — 400 ms 지연 저장이 걸린다
        self.context_menu_at(40 + 60 // 2, 50 + 40 // 2)
        page.get_by_text("오검출로 삭제", exact=False).first.click()
        # 저장이 나가기 전에 옆 판을 스치고 다른 판을 고른다
        self.hover(self.frame.name)
        self.pick(self.frame.name)
        page.wait_for_timeout(900)

        rows = list(ObjectReview.objects.all())
        self.assertEqual(len(rows), 1, f"교정이 하나여야 한다: {rows}")
        self.assertEqual(rows[0].image_id, self.stack_det.image_id,
                         "합성본을 보고 지웠는데 다른 이미지에 앉았다")
        self.assertTrue(rows[0].removed)

    def test_돌아오면_지운_표시가_그대로다(self):
        """스치고 돌아오는 길에서도 사람이 한 일이 남아 있어야 한다."""
        page = self.open_review()
        self.context_menu_at(40 + 60 // 2, 50 + 40 // 2)
        page.get_by_text("오검출로 삭제", exact=False).first.click()
        page.wait_for_timeout(900)

        self.hover(self.frame.name)
        self.pick(self.frame.name)
        self.hover("__stack__")
        self.pick("__stack__")
        self.assertTrue(page.query_selector(".box.gone"),
                        "합성본으로 돌아오니 지운 표시가 사라졌다")
        self.assertEqual(ObjectReview.objects.filter(removed=True).count(), 1)

    def test_스친_판에서도_지운_마스크는_지워_보인다(self):
        """실사용 보고 (2026-08-10). 미리보기가 shotDets(처음 상태)에서 직접
        뽑고 `removed` 를 안 갈아 끼워서, **스친 판의 지운 마스크가 산 것처럼**
        그려졌다 — 클릭하면 맞게 나오니 hover 와 클릭이 다른 그림을 냈다."""
        from ...models import ObjectReview, RunBatch
        # 프레임 판의 마스크 하나를 지운 것으로 (서버 상태)
        det = self.extra[0][2]
        c = det.candidates.filter(passed=True).first()
        fx.new_review(
            viewpoint=self.w.vp, image=det.image,
            batch=RunBatch.objects.get(label="yolo-시험"),
            mask_key=c.mask_key, removed=True,
            geom={"bbox_xywh": c.bbox_xywh})
        self.open_review()

        # 스치기만 한다 — 지운 마스크가 살아 보이면 안 된다
        self.hover(self.frame.name)
        alive = self.page.query_selector_all(
            "#masks-stack polygon:not(.reject)")
        gone = self.page.query_selector_all(".box.gone")
        # 지운 것은 폴리곤(칠)이 없어야 하고, 지운 표시 상자로 남아야 한다
        keys_alive = len(alive)
        self.hover("__stack__")   # 원위치
        # 클릭해서 들어간 그림과 같은 수여야 한다
        self.shot(self.frame.name).click()
        self.page.wait_for_timeout(400)
        alive_committed = len(self.page.query_selector_all(
            "#masks-stack polygon:not(.reject)"))
        self.assertEqual(keys_alive, alive_committed,
                         "hover 와 클릭이 다른 그림을 낸다 — 지운 마스크가 "
                         "미리보기에서 살아 보인다")


class PreviewShowsLinksTest(BrowserTestCase):
    """스치는 미리보기에서 **사슬도 그 판의 것**이어야 한다 (DiaRUGA P12 · 사용자 2026-08-11).

    미리보기는 마스크·지움·분류를 스친 판의 것으로 갈아 끼우면서 **저장 대상
    (`curImage`)은 고른 판의 것으로 남긴다** — 그래야 스친 김에 엉뚱한 판으로
    저장되지 않는다(074 가 만든 규칙이다).

    그런데 사슬을 붙일 때도 `curImage` 로 묶음을 찾고 있었다. 그래서
    **마스크는 스친 판의 것인데 사슬은 고른 판에서 찾아** 아무것도 안 붙었다:

    > "carousel thumbnail 에 마우스오버 됐을 때 마스크는 잘 보이는데 chain 은
    > 안 보여."
    """

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_frames=3, n_candidates=3)
        self.extra = fx.add_frame_detections(self.w.vp)
        self.frame = self.extra[0][0]
        self.frame_img = self.extra[0][1]
        self.stack_det = self.w.detection()
        # **판마다 다른 키를 묶는다.** 픽스처는 판마다 같은 자리에 후보를
        # 세우므로 같은 키로 묶으면 **엉뚱한 판으로 찾아도 걸린다** — 그러면
        # 이 시험이 고장을 못 잡는다. 실제 자료는 판마다 키가 다르다
        # (운영 실측: 합성본 1170_912_344_520 · 프레임 1170_878_369_554).
        keys = sorted(c.mask_key for c in self.stack_det.candidates.all())
        self.stack_key, self.frame_key = keys[0], keys[1]
        rows = [
            fx.new_review(viewpoint=self.w.vp, image=self.stack_det.image,
                          batch=self.stack_det.batch, mask_key=self.stack_key),
            fx.new_review(viewpoint=self.w.vp, image=self.frame_img,
                          batch=self.stack_det.batch, mask_key=self.frame_key),
        ]
        fx.link_reviews(rows, rep=0)

    def open_review(self):
        page = self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))
        page.wait_for_selector(".detview .box")
        return page

    def linked_at(self):
        """사슬이 붙은 상자의 **자리**. 개수만 세면 못 잡는다 — 엉뚱한 판으로
        찾아도 그 판에 같은 키가 있어 사슬이 *다른 상자에* 하나 붙는다."""
        return self.page.evaluate(
            """() => [...document.querySelectorAll('.detview .box.linked')]
                 .map(e => e.style.left + '/' + e.style.top)""")

    def test_스친_판에도_그_판의_사슬이_붙는다(self):
        page = self.open_review()
        on_stack = self.linked_at()
        self.assertEqual(len(on_stack), 1, "고른 판(합성본)에 사슬이 없다")

        page.query_selector(f'.shot[data-detkey="{self.frame.name}"]').hover()
        page.wait_for_timeout(300)
        on_frame = self.linked_at()
        self.assertEqual(len(on_frame), 1, "스친 판에 사슬이 안 붙었다")
        self.assertNotEqual(on_frame, on_stack,
                            "고른 판의 사슬 자리 그대로다 — 스친 판이 아니라 "
                            "curImage 로 묶음을 찾고 있다")

        # 커서를 빼면 고른 판으로 돌아오고, 사슬 자리도 함께 돌아온다
        page.query_selector(".detview").hover()
        page.wait_for_timeout(300)
        self.assertEqual(self.linked_at(), on_stack, "돌아온 뒤 자리가 다르다")
