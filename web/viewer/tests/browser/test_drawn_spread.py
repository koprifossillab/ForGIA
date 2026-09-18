"""DiaRUGA v0.29.0 `tests/browser/test_drawn_spread.py` 에서 왔다.

그린 마스크가 다른 판으로 번진 뒤 **거기서 저장해도 살아 있는가** (106 3단계).

**이 파일이 이 기능의 나머지 반쪽이다.** 서버가 복제를 만드는 것은 3겹 시험
(`tests/test_drawn_spread.py`)이 본다. 여기서 보는 것은 그것만으로는 자료를
잃는다는 사실이다.

복제한 마스크는 **다른 판의 화면이 모른다.** 그 화면은 열릴 때 받은 상태를 들고
있고, `/review` 는 "뷰어는 늘 전체를 보낸다" 를 전제로 payload 에 없는 그린
개체를 지운다. 그래서 프레임에 번진 복제는, 사람이 그 판으로 넘어가 **아무것도
안 하고 저장 한 번만 나가도** 그 자리에서 사라진다.

104 가 분류 번지기에서 정확히 이 함정을 지났다. 그때는 값이 옛것으로 되돌아가는
것이었고, 여기서는 **마스크가 통째로 없어진다.**

**3겹으로는 못 잡는다.** 서버만 두고 보면 복제도 잘 만들어지고 청소도 규칙대로
돈다 — 그 사이에 화면이 있다는 것이 이 고장의 전부다.
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import ForamObject, ObjectReview

# 픽스처 개체(40,50 / 160,130 / 280,210)와 안 겹치는 빈 자리
PTS = [(420, 300), (500, 300), (500, 360), (420, 360)]


class DrawnSpreadOnScreenTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_frames=3, n_candidates=3)
        self.extra = fx.add_frame_detections(self.w.vp)
        self.frame, self.frame_img, self.frame_det = self.extra[0]
        self.stack_img = self.w.detection().image

    def open_review(self):
        return self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))

    def click_shot(self, detkey):
        """캐러셀에서 판 하나를 고른다 — 누른 뒤 사진을 화면 안으로 되올린다
        (`test_carousel_review.py` 가 적어 둔 그 함정)."""
        el = self.page.query_selector(f'.shot[data-detkey="{detkey}"]')
        self.assertIsNotNone(el, f"캐러셀에 {detkey} 판이 없다")
        el.click()
        self.page.wait_for_timeout(250)
        self.masks_svg().scroll_into_view_if_needed()
        self.page.wait_for_timeout(150)
        return el

    def draw_one(self):
        """합성본 판에 마스크 하나를 그리고 저장까지 기다린다."""
        self.page.click('#dv-stack button[data-act="draw"]')
        self.page.wait_for_timeout(150)
        for x, y in PTS:
            self.click_image(x, y)
        self.click_image(*PTS[0])           # 첫 점을 다시 눌러 닫는다
        self.page.wait_for_timeout(900)     # 지연 저장 400ms + 여유

    def drawn_rows(self):
        return list(ObjectReview.objects.filter(batch__isnull=True)
                    .order_by("image_id"))

    # --- 번진 것이 화면에 놓이는가 -----------------------------------------

    def test_그린_뒤_다른_판으로_가면_복제가_보인다(self):
        """서버가 응답에 실어 준 것을 화면이 자기 상태에 얹어야 한다.
        안 얹으면 **여기서는 안 보이고**, 다음 저장에 DB 에서도 사라진다.
        """
        page = self.open_review()
        self.draw_one()
        self.click_shot(self.frame.name)
        self.assertTrue(page.query_selector(".box.orphan"),
                        "프레임 판에 번진 마스크가 안 그려졌다")

    def test_그린_그_자리에서_사슬이_뜬다(self):
        """**새로고침을 기다리면 안 된다** (사용자, 2026-08-12).

        사람이 "이게 다른 판에도 들어갔다" 를 알아채는 근거가 사슬 배지다.
        화면의 `links` 는 페이지가 열릴 때 한 번 받는 값이라, 저장 응답이 그것을
        갈아 주지 않으면 **복제는 놓였는데 묶였다는 표시만 없는** 상태가 된다 —
        그러면 사람이 판마다 다시 그리려 든다.
        """
        page = self.open_review()
        self.draw_one()
        # 사슬은 `.box.linked` 의 `::after` 가 그린다 (`base.html`).
        self.assertIsNotNone(page.query_selector(".box.orphan.linked"),
                             "그린 자리에 사슬 표시가 안 떴다 (새로고침 전)")

    def test_묶인_그_순간에_잠깐_밝힌다(self):
        """**"방금 내가 그린 것이 묶였는가" 하나만 답하면 된다** (사용자,
        2026-08-12). 영구 표시로 출처를 가르지 않는 이유가 둘이다: 사슬은 컬러
        이모지라 색이 안 먹고, 사람이 그린 것과 엔진 것을 손으로 섞어 묶는 길이
        있어 **그 묶음이 무슨 색인지**를 답해야 한다.

        판을 넘어온 묶음의 짝을 밝히는 `flashFollowed` 와 같은 `linkflash` 를
        쓴다 — 뜻이 같은 것을 두 가지로 그리면 사람이 둘을 다른 일로 읽는다.
        """
        page = self.open_review()
        page.click('#dv-stack button[data-act="draw"]')
        page.wait_for_timeout(150)
        for x, y in PTS:
            self.click_image(x, y)
        self.click_image(*PTS[0])
        # **기다리지 않고 나타나기를 본다** — 1.4초 뒤 스스로 걷히므로 고정된
        # 시간을 재면 시험이 흔들린다.
        page.wait_for_selector(".box.linkflash", timeout=4000)

    # --- 핵 — 거기서 저장해도 살아 있는가 ----------------------------------

    def test_다른_판에서_저장해도_복제가_안_지워진다(self):
        """**이것이 이 기능의 시험대다.**

        프레임으로 넘어가 개체 하나를 지우면 그 판의 저장이 나간다. 그때 화면이
        복제를 모르면 payload 에 그 키가 없고, 서버는 "표시가 사라진 그린 개체"
        로 보고 지운다.
        """
        page = self.open_review()
        self.draw_one()
        before = {r.image_id for r in self.drawn_rows()}
        self.assertIn(self.frame_img.pk, before, "번지지 않았다 — 서버 쪽 문제")

        self.click_shot(self.frame.name)
        # 그 판에서 저장을 한 번 일으킨다 (첫 통과분을 오검출로 지운다)
        menu = self.context_menu_at(40 + 60 // 2, 50 + 40 // 2)
        self.assertIsNotNone(menu, "프레임 판에서 개체 우클릭 메뉴가 안 떴다")
        page.get_by_text("오검출로 삭제", exact=False).first.click()
        page.wait_for_timeout(900)

        after = {r.image_id for r in self.drawn_rows()}
        self.assertEqual(after, before,
                         "다른 판에서 저장했더니 그린 마스크가 사라졌다")

    def test_저장이_거듭돼도_복제가_안_쌓인다(self):
        """서버는 저장할 때마다 같은 것을 다시 실어 보낸다 — 화면이 그때마다
        더하면 같은 마스크가 저장 횟수만큼 쌓이고, 그 판에서 저장이 나가면
        그대로 DB 로 간다.
        """
        page = self.open_review()
        self.draw_one()
        n = len(self.drawn_rows())

        self.click_shot(self.frame.name)
        menu = self.context_menu_at(40 + 60 // 2, 50 + 40 // 2)
        self.assertIsNotNone(menu)
        page.get_by_text("오검출로 삭제", exact=False).first.click()
        page.wait_for_timeout(900)

        self.assertEqual(len(self.drawn_rows()), n, "복제가 쌓였다")
        boxes = page.query_selector_all(".box.orphan")
        self.assertEqual(len(boxes), 1,
                         f"화면에도 겹쳐 그려졌다: {len(boxes)}개")

    def test_판들이_한_개체로_묶여_있다(self):
        """화면을 거쳐 들어와도 뜻이 같아야 한다 — 같은 개체을 옮겨 그린 것이다."""
        self.open_review()
        self.draw_one()
        objs = {r.foram_object_id for r in self.drawn_rows()}
        self.assertEqual(len(objs), 1, f"개체가 갈렸다: {objs}")
        obj = ForamObject.objects.get(pk=objs.pop())
        self.assertEqual(obj.members.filter(is_rep=True).count(), 1,
                         "대표가 하나가 아니다")
