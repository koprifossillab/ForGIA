"""DiaRUGA v0.29.0 `tests/browser/test_done_lost.py` 에서 왔다.

검토 완료를 눌렀는데 서버가 못 받는 갈래 (실사용 2026-08-13).

**사람이 본 그대로:**

> AM22-GC10B 25cm 를 시야 1부터 29까지 검토했는데 g028 하나만 미검토로
> 남아 있다. 완료를 푼 적은 없다.

기록이 말하는 것도 같다 — 그 시야는 10:57 과 10:59 에 두 번 저장됐고 마지막이
`done=0` 이었다. 판이 넷이고 그린 마스크가 여섯이라 **캐러셀을 가장 많이 오간
시야**다.

## 어디가 새는가

완료 버튼은 켜고 · 칠하고 · 저장하고 · 곧바로 다음 시야로 넘어간다:

    reviewDone = !reviewDone;
    paintDone();
    save();
    if (reviewDone) { leave(url); }

그런데 `save()` 에는 **`savePending` 을 안 세우고 돌아가는 갈래**가 있다 —
커서가 캐러셀의 옆 판을 스치는 중(`previewing`)이면 지나가는 판에 판단이 앉는
것을 막으려고 눌린 것을 버린다(074). 그러면 `leave()` 가 부르는 `flushSave()`
는 첫 줄 `if (!savePending) return` 으로 빠지고, 곧바로 `location.href` 가 돈다.

**버튼은 완료로 바뀌고 화면은 다음 시야로 넘어가는데 서버는 요청을 한 번도 못
받는다.** 저장 실패 띠도 안 뜬다 — 실패한 요청이 없기 때문이다. 051 과 같은
이야기다: 되는 것처럼 보이는 것이 안 되는 것보다 나쁘다.

`previewing` 은 **썸네일 위로 커서가 지나가기만 해도** 켜진다
(`swapDet(s, commit=false)`). 판이 여럿인 시야에서만 나므로 싱글턴 시야를
검토할 때는 한 번도 안 걸렸다.

## 두 번째 자리 — `leave()` 가 안 기다린다

`flushSave()` 는 **약속을 돌려주는데**(묶기 POST 가 그 뒤에 서려고 그렇게
만들었다) `leave()` 는 그것을 안 기다리고 `location.href` 를 준다. keepalive 가
붙어 있어 대개는 나가지만, 그 자리의 주석이 적어 둔 대로 **본문이 64 KB 를
넘으면 브라우저가 보내 보지도 않고 거절한다** — 교정과 그린 폴리곤이 많은
시야가 정확히 그 크기다. 그때도 자국이 안 남는다.
"""
import json

from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import ViewpointReview


class DoneLostTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        # **판이 여럿인 시야여야 한다** — 캐러셀이 없으면 `previewing` 이
        # 켜질 자리가 없고, 이 고장은 나지 않는다.
        #
        # **시야도 둘이어야 한다** — 완료를 누르면 곧바로 다음 시야로 넘어가고
        # (`leave`), 아래 두 시험이 보는 것이 그 넘어감이다. 하나뿐이면
        # "마지막 시야입니다" 로 빠져 `leave` 를 안 지난다.
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_viewpoints=2, n_frames=3, n_candidates=3)
        self.extra = fx.add_frame_detections(self.w.vp)
        self.frame = self.extra[0][0]

    def open_review(self):
        page = self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))
        page.wait_for_selector(".detview .box")
        return page

    def done_row(self):
        return ViewpointReview.objects.filter(
            viewpoint=self.w.vp, batch__isnull=False).first()

    def click_done(self, move=True):
        """`move=False` 면 **포인터를 안 옮기고** 누른다.

        키보드로 누르는 것과 같다. 마우스를 옮겨서 누르면 캐러셀의
        `mouseleave` 가 먼저 돌아 미리보기가 풀리므로 이 고장이 안 보인다 —
        커서가 판 위에 남아 있는 채로 눌리는 길이 따로 있다.
        """
        if move:
            self.page.click("#done-stack")
        else:
            self.page.eval_on_selector("#done-stack", "el => el.click()")
        # 저장이 나갈 틈을 준다 — 지연 저장은 400 ms 다.
        self.page.wait_for_timeout(900)

    # --- 스치지 않았을 때은 되어야 한다 (시험이 늘 통과하지 않게) ----------

    def test_그냥_누르면_완료가_저장된다(self):
        """**대조군이다.** 이것이 없으면 아래 시험이 무엇을 잡았는지 모른다."""
        self.open_review()
        self.click_done()
        row = self.done_row()
        self.assertIsNotNone(row, "완료를 눌렀는데 행이 없다")
        self.assertTrue(row.done, "완료를 눌렀는데 done 이 꺼져 있다")

    # --- 실사용에서 난 자리 ------------------------------------------------

    def test_판을_스친_뒤_완료를_누르면_잃지_않는다(self):
        """커서가 옆 판을 스친 채로 완료를 누른다 — 실사용 g028 의 자리다.

        **화면과 DB 가 갈리는 것을 본다.** 버튼은 켜지고 페이지는 넘어가는데
        서버는 못 받았다 — 그래서 버튼만 보는 시험으로는 안 잡힌다.
        """
        self.open_review()
        thumb = self.page.query_selector(
            f'.shot[data-detkey="{self.frame.name}"]')
        self.assertIsNotNone(thumb, "캐러셀에 프레임 판이 없다")
        thumb.scroll_into_view_if_needed()
        thumb.hover()                       # ← previewing 이 켜진다
        self.page.wait_for_timeout(350)

        self.click_done(move=False)         # 커서는 판 위에 남아 있다

        row = self.done_row()
        self.assertIsNotNone(
            row, "판을 스친 뒤 완료를 눌렀더니 서버가 아무것도 못 받았다")
        self.assertTrue(
            row.done,
            "판을 스친 뒤 완료를 눌렀더니 화면만 켜지고 done 이 안 갔다")

    # --- `leave()` 가 저장을 기다린다 --------------------------------------

    def block_review(self, why="테스트로 막았다"):
        self.page.route(
            "**/review",
            lambda route: route.fulfill(
                status=409, content_type="application/json",
                body=json.dumps({"ok": False, "error": why})))

    def test_저장이_거절당하면_다음_시야로_안_넘어간다(self):
        """**떠나면 자국이 안 남는다.**

        `leave()` 는 `flushSave()` 가 돌려준 약속을 안 기다리고 곧바로
        `location.href` 를 줬다. 저장이 거절당해도 화면은 다음 시야로 넘어가고,
        방금 켠 완료 표시도 실패 띠도 함께 사라진다 — 사람은 됐다고 알고 간다.
        """
        page = self.open_review()
        here = page.url
        self.block_review("판이 어긋났다 — 시험")
        self.click_done()

        self.assertEqual(page.url, here,
                         "저장이 거절당했는데 다음 시야로 넘어갔다")
        self.assertIsNotNone(page.query_selector(".errbar.savefail"),
                             "저장 실패 띠가 안 떴다")
        self.assertFalse(ViewpointReview.objects.filter(
            viewpoint=self.w.vp, batch__isnull=False, done=True).exists())

    def test_저장이_되면_다음_시야로_넘어간다(self):
        """**대조군이다.** 위 시험이 "안 넘어간다" 만 보면, 아예 못 넘어가게
        만들어 놓고도 통과한다."""
        page = self.open_review()
        here = page.url
        self.click_done()
        self.assertNotEqual(page.url, here,
                            "완료를 눌렀는데 다음 시야로 안 넘어간다")

    def tearDown(self):
        # 409 는 **이 시험이 일부러 낸 것**이다 — 크로미움이 콘솔에 적는
        # 리소스 오류까지 고장으로 세면 시험이 제 손으로 빨간불을 낸다.
        self.errors = [e for e in self.errors if "409" not in e]
        super().tearDown()
