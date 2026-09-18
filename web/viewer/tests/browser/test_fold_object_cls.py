"""DiaRUGA v0.29.0 `tests/browser/test_fold_object_cls.py` 에서 왔다.

접어 온 유형이 **저장에 실려 나가지 않는가** (사용자 지적 2026-09-04).

서버가 접는 것은 3겹 시험(`tests/test_fold_object_cls.py`)이 본다. 여기서 보는
것은 그 값이 **화면을 거쳐 DB 로 돌아오지 않는가**이고, 그건 3겹에서 안 보인다.

접은 유형은 *엔진이 옆 판에서 본 값*이지 사람의 지정이 아니다. 그런데 손그림
판의 `drawn` payload 는 `cls` 를 그대로 `ForamObject.label` 에 적는다
(`_save_drawn`) — 화면이 접은 값을 실어 보내면 **엔진의 추측이 사람이 지정한
유형 자리에 눌러앉는다.** 사람은 지정한 적이 없는데 카탈로그에 유형이 서 있고,
한 번 앉으면 다음부터는 "사람이 지정한 것" 이라 접기가 비켜 간다.

104·106 이 지난 그 자리다 — 화면이 자기가 받은 값을 그대로 되돌려 보내는 것이
고장의 전부이고, 서버만 두고 보면 규칙대로 돈다.
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import ForamObject, ObjectReview, RunBatch

# 픽스처 개체(40,50 / 160,130)와 안 겹치는 빈 자리
BOX = [420, 300, 80, 60]
POLY = [420, 300, 500, 300, 500, 360, 420, 360]
DRAWN_KEY = "mf01d0000"


class FoldedClsNotSavedTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_frames=3, n_candidates=2)
        self.extra = fx.add_frame_detections(self.w.vp)
        self.frame, self.frame_img, _d = self.extra[0]
        self.stack_img = self.w.detection().image
        self.batch = RunBatch.objects.get(label="yolo-시험")
        self.key = self.w.keys()[0]

        # **번지기(DiaRUGA P19)가 앉힌 모양이다** — 합성본의 엔진 마스크 하나와,
        # 프레임에 복제된 손그림 하나가 한 개체다. 손그림은 후보가 없어
        # 유형이 아예 없고, 그래서 접기가 옆 판의 값을 데려온다.
        #
        # 합성본 쪽 유형을 픽스처 기본값과 다른 것으로 못 박는다 — 프레임
        # 판에 이 색이 있으면 그것은 **접어 온 것**뿐이다.
        self.w.detection().candidates.filter(mask_key=self.key).update(
            cls="other")
        eng = fx.new_review(viewpoint=self.w.vp, image=self.stack_img,
                            batch=self.batch, mask_key=self.key,
                            bind_method="exact",
                            geom={"bbox": [40, 50, 60, 40]})
        drawn = fx.new_review(viewpoint=self.w.vp, image=self.frame_img,
                              mask_key=DRAWN_KEY, source="manual",
                              bind_method="manual",
                              geom={"bbox": BOX, "polygon": POLY})
        self.obj = fx.link_reviews([eng, drawn], rep=0)

    def open_review(self):
        return self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))

    def click_shot(self, detkey):
        el = self.page.query_selector(f'.shot[data-detkey="{detkey}"]')
        self.assertIsNotNone(el, f"캐러셀에 {detkey} 판이 없다")
        el.click()
        self.page.wait_for_timeout(250)
        self.masks_svg().scroll_into_view_if_needed()
        self.page.wait_for_timeout(150)
        return el

    def label_now(self) -> str:
        return ForamObject.objects.get(pk=self.obj.pk).label

    def test_손그림_판에_접어_온_색이_놓인다(self):
        """**아래 시험의 전제다.** 안 접히면 그 다음이 헛통과한다 — 실어
        보낼 값이 애초에 없으니 저장이 깨끗한 것이 당연해진다."""
        self.open_review()
        self.click_shot(self.frame.name)
        self.assertEqual(
            len(self.masks_svg().query_selector_all("polygon.other")), 1,
            "프레임 판의 손그림이 개체의 유형을 안 받았다")

    def test_그_판에서_저장해도_유형이_안_앉는다(self):
        """저장 한 번이 지나가면 안 된다 — 사람은 유형을 지정한 적이 없다."""
        page = self.open_review()
        self.assertEqual(self.label_now(), "", "픽스처가 이미 유형을 갖고 있다")

        self.click_shot(self.frame.name)
        # 그 판에서 저장을 한 번 일으킨다 (엔진 개체 하나를 오검출로 지운다)
        menu = self.context_menu_at(40 + 60 // 2, 50 + 40 // 2)
        self.assertIsNotNone(menu, "프레임 판에서 개체 우클릭 메뉴가 안 떴다")
        page.get_by_text("오검출로 삭제", exact=False).first.click()
        page.wait_for_timeout(900)

        self.assertEqual(self.label_now(), "",
                         "접어 온 유형이 저장에 실려 개체에 앉았다")
        self.assertTrue(
            ObjectReview.objects.filter(image=self.frame_img,
                                        mask_key=DRAWN_KEY).exists(),
            "손그림 판이 저장에 지워졌다")

    def test_사람이_지정하면_그것은_저장된다(self):
        """접은 표시를 걷는 자리다 (`setLabel`). 안 걷으면 **사람이 고른 유형이
        저장에서 빠져** 화면만 바뀌고 아무것도 안 남는다."""
        page = self.open_review()
        self.click_shot(self.frame.name)
        menu = self.context_menu_at(BOX[0] + BOX[2] // 2, BOX[1] + BOX[3] // 2)
        self.assertIsNotNone(menu, "손그림 우클릭 메뉴가 안 떴다")
        self.menu_click("파손")
        page.wait_for_timeout(900)

        self.assertEqual(self.label_now(), "broken")
