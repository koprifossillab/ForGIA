"""DiaRUGA v0.29.0 `tests/browser/test_no_detection_shot.py` 에서 왔다.

**검출이 없는 판에서는 저장할 곳이 없다** (180 B1 · 2026-09-03).

캐러셀은 프레임마다 판을 내는데, 그 묶음에 검출이 없는 프레임이 섞일 수 있다.
그 판을 고르면 화면이 `curImage` 를 비우는데(그래야 옛 마스크가 엉뚱한 그림
위에 안 얹힌다), **저장은 그대로 나갔다.** `image` 가 비면 서버는 **대표
이미지(합성본)** 로 받고, 그 요청이 합성본의 교정을 갈아치운다 — `save_review`
의 마지막 줄은 payload 에 없는 키를 지운다.

**116 이 이 갈래를 알고 완료만 떼어 냈다**(`save_done` 머리말에 "검출이 없는
판을 고르면 … 그 판의 교정이 지워진다 — 사본에서 재현했다" 가 그대로 있다).
교정 쪽은 안 고쳐져 있었다.

화면 배선이라 눌러서만 잡힌다 — 서버는 `image: null` 을 정상 요청으로 받는다
(판이 하나뿐인 화면과 옛 탭이 그 갈래로 온다).
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import Detection, ObjectReview

# 픽스처 개체(40,50 / 160,130)와 안 겹치는 빈 자리
PTS = [(420, 300), (500, 300), (500, 360), (420, 360)]


class NoDetectionShotTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        # 합성본 + 프레임 셋. 프레임마다 검출을 붙였다가 **하나를 걷는다** —
        # `stackOnly()` 가 참이면 화면이 합성본을 겹쳐 보여 이 갈래를 안 밟는다.
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_frames=3, n_candidates=2)
        self.extra = fx.add_frame_detections(self.w.vp)
        self.bare = self.w.vp.frames.order_by("pk").first()
        Detection.objects.filter(viewpoint=self.w.vp,
                                 image__path=self.bare.path).delete()

    def open_group(self):
        return self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))

    def click_shot(self, detkey):
        el = self.page.query_selector(f'.shot[data-detkey="{detkey}"]')
        self.assertIsNotNone(el, f"캐러셀에 {detkey} 판이 없다")
        el.click()
        self.page.wait_for_timeout(250)
        self.masks_svg().scroll_into_view_if_needed()
        self.page.wait_for_timeout(150)
        return el

    def stack_rows(self):
        return ObjectReview.objects.filter(viewpoint=self.w.vp,
                                           image=self.w.detection().image_id)

    def test_검출이_없는_판에_그려도_합성본이_안_갈린다(self):
        page = self.open_group()

        # 합성본에 판단을 하나 남긴다 — 이것이 갈릴 자리다
        menu = self.context_menu_at(70, 70)
        self.assertIsNotNone(menu, "우클릭 메뉴가 안 열렸다")
        item = None
        for el in menu.query_selector_all("button, .mi"):
            if "오검출로 삭제" in (el.inner_text() or ""):
                item = el
                break
        self.assertIsNotNone(item, "삭제 항목이 없다")
        item.click()
        page.wait_for_timeout(1200)
        before = set(self.stack_rows().values_list("mask_key", flat=True))
        self.assertTrue(before, "합성본에 교정이 안 남았다 — 시험이 성립 안 한다")

        # 검출이 없는 판으로 간다
        self.click_shot(self.bare.name)
        self.assertIn("검출이 없", page.query_selector("#tabs-stack .tabmeta")
                      .inner_text())

        # 거기서 마스크를 그린다 — 예전에는 이 저장이 합성본으로 갔다
        page.click('#dv-stack button[data-act="draw"]')
        page.wait_for_timeout(150)
        for x, y in PTS:
            self.click_image(x, y)
        self.click_image(*PTS[0])
        page.wait_for_timeout(1200)

        self.assertEqual(
            set(self.stack_rows().values_list("mask_key", flat=True)), before,
            "검출 없는 판의 저장이 합성본의 교정을 갈아치웠다")
        self.assertFalse(
            ObjectReview.objects.filter(viewpoint=self.w.vp,
                                        source="manual").exists(),
            "저장할 곳이 없는 판의 그림이 다른 판에 앉았다")
        self.assertIn("저장할 곳이 없",
                      page.query_selector("#savestate-stack").inner_text(),
                      "왜 저장이 안 되는지 화면이 말하지 않는다")
