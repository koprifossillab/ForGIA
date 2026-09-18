"""DiaRUGA v0.29.0 `tests/browser/test_done_plate.py` 에서 왔다.

완료는 **어느 판을 고르고 있든** 판의 교정을 안 건드린다 (116 덧).

116 은 완료가 서버에 안 가는 갈래를 막으면서, 버튼 처리기 앞에
`unpreviewState()` 를 넣어 **미리보기를 걷고 판 payload 로 보내는** 길을 골랐다.
그 전제는 "걷으면 전역이 고른 판의 것으로 돌아온다" 인데, **되돌릴 것이 없는
판이 있다.**

    function unpreviewState() { …
      var back = curKey && shotState[curKey];
      if (!back) return;              // ← 아무것도 안 되돌리고 나간다
    }

`curKey` 가 null 이 되는 자리가 **둘이었다** — 깊이 맵(`_shots.html` 이 그 단추에
`data-detkey` 를 아예 안 달았다)과 **그 묶음에 검출이 없는 프레임**. 둘 다 고른
순간 `curImage` 도 비므로, 그 상태로 나간 저장은 `image: null` 이 되어 서버가
**대표 이미지**로 받고 payload 에 없는 행을 지운다.

사본에서 재현했다: 깊이 맵을 고르고 완료를 누르면 **합성본의 교정 2건이
0건**이 됐다. 그래서 116 덧은 길을 바꿨다 — **완료는 교정을 안 싣고 혼자
간다**(`{"only": "done"}`). 서버 쪽은 `tests/test_review_done_batch.py`.

**123 에서 깊이 맵을 캐러셀에서 뺐다** — 검토에 쓸 일이 없는 판이라서다. 그래서
그 갈래를 밟던 시험도 함께 걷었다(고를 단추가 없으니 늘 실패한다). **가드는
그대로 필요하다**: 남은 한 자리, *그 묶음에 검출이 없는 프레임*이 같은 판이고
아래 시험이 그것을 밟는다. 깊이 맵이 화면에 안 나온다는 것 자체는
`tests/test_depth_hidden.py` 가 본다.

**대조군을 함께 둔다** — 완료가 저장되는 것까지 봐야 "안 지운다" 가 무의미한
통과가 아니다.
"""
import json
from pathlib import Path

from django.test import Client
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import ObjectReview, Stack, ViewpointReview


class DoneOnPlateWithoutDetectionTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        # **프레임마다 현재 검출이 있는 시야** — 운영의 `yolo-3차` 가 그
        # 모양이고, 거기서만 `stackOnly()` 가 거짓이라 검출 없는 판이
        # `curKey=null` 로 간다(합성본만 있는 묶음에서는 합성본으로 겹쳐 본다 · 072).
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_viewpoints=2, n_frames=3, n_candidates=3)
        self.extra = fx.add_frame_detections(self.w.vp)
        self.hover = self.extra[0][0]
        # 그 묶음에 검출이 없는 프레임 하나 (두 번째 자리)
        bare_frame, _img, det = self.extra[-1]
        det.delete()
        self.bare = bare_frame
        # **깊이 맵도 있는 시야로 둔다** — 운영 `stacked/` 에 널려 있다. 자료는
        # 그대로 두고 화면에서만 안 내는 것이 123 이라, 여기서도 그 모양이어야
        # 한다(캐러셀에 안 뜨는 것은 `test_depth_hidden.py` 가 본다).
        st = Stack.objects.get(viewpoint=self.w.vp)
        st.depth_path = st.focused_path.replace("_focused.jpg", "_depth.jpg")
        st.save(update_fields=["depth_path"])
        fx._write(st.depth_path)

        # 합성본(대표 이미지)에 사람의 교정을 심는다 — 이것이 지워지면 안 된다
        self.stack = self.w.detection().image
        keys = [c.mask_key for c in self.w.detection().candidates.all()][:2]
        r = Client().post(reverse("save_review"), content_type="application/json",
                          data=json.dumps({
                              "stem": Path(self.stack.path).stem,
                              "slug": self.w.slug, "gid": self.w.vp.idx,
                              "image": self.stack.pk, "done": False,
                              "removed": keys, "accepted": [], "labels": {}}))
        assert r.status_code == 200, r.content[:300]

    def marks(self):
        return ObjectReview.objects.filter(image=self.stack,
                                           removed=True).count()

    def done_row(self):
        return ViewpointReview.objects.filter(
            viewpoint=self.w.vp, batch__isnull=False, done=True).first()

    def open_review(self):
        page = self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))
        page.wait_for_selector(".detview .box")
        self.assertEqual(self.marks(), 2, "심은 교정이 없다")
        return page

    def pick(self, sel):
        el = self.page.query_selector(sel)
        self.assertIsNotNone(el, f"캐러셀에 {sel} 이 없다")
        el.scroll_into_view_if_needed()
        el.click()
        self.page.wait_for_timeout(300)
        return el

    # --- 그 묶음에 검출이 없는 프레임 + 판을 스친 뒤 -------------------------

    def test_검출없는_판에서_스친_뒤_완료해도_교정이_남는다(self):
        """116 의 `unpreviewState()` 가 되돌리지 못하는 자리다.

        갈무리(`stash`)도 `curKey` 가 null 이면 아무것도 안 담으므로, 스친 판의
        상태가 전역에 남은 채로 저장이 나간다.
        """
        page = self.open_review()
        self.pick(f'.shot[data-detkey="{self.bare.name}"]')

        thumb = page.query_selector(f'.shot[data-detkey="{self.hover.name}"]')
        self.assertIsNotNone(thumb, "스칠 판이 없다")
        thumb.hover()                       # ← previewing 이 켜진다
        page.wait_for_timeout(350)
        # 커서를 안 옮기고 누른다 — 마우스로 버튼까지 옮기면 `strip` 의
        # mouseleave 가 먼저 돌아 미리보기가 풀린다.
        page.eval_on_selector("#done-stack", "el => el.click()")
        page.wait_for_timeout(900)

        self.assertEqual(self.marks(), 2,
                         "스친 판의 상태가 대표 이미지로 실려 나갔다")
        self.assertIsNotNone(self.done_row(), "완료가 저장되지 않았다")

    # --- 대조군 -------------------------------------------------------------

    def test_보통_판에서는_완료도_교정도_그대로다(self):
        """**이것이 없으면 위 둘이 무엇을 잡았는지 모른다.**"""
        self.open_review()
        self.page.click("#done-stack")
        self.page.wait_for_timeout(900)
        self.assertEqual(self.marks(), 2)
        self.assertIsNotNone(self.done_row(), "완료가 저장되지 않았다")
