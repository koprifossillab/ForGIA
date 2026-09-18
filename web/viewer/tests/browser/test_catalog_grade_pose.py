"""DiaRUGA v0.29.0 `tests/browser/test_catalog_grade_pose.py` 에서 왔다.

카탈로그 카드의 **등급·자세** — 실제로 눌러 본다.

3겹(`tests/test_grade_pose.py`)은 서버가 무엇을 받으면 무엇을 하는지까지 본다.
여기서 보는 것은 그 밖이다.

**칸이 다섯이 됐다** (종명·유형·코멘트 + 등급·자세). 105 에서 셋일 때 이미
**먼저 보낸 요청의 응답이 나중에 친 글자를 덮은** 적이 있다 — 그때 코멘트가
사라졌고 화면에는 "저장됨" 이 적혀 있었다. 칸이 늘면 그 자리가 늘어난다.
`known` 을 응답에서 다시 적을 때 **새 칸을 빠뜨리면** 그 칸은 `undefined` 와
비교되어 매번 요청이 나가고, 그 요청이 남의 칸을 덮는다.

**파편에는 두 칸이 아예 안 뜬다.** 그리고 감췄다는 것은 `hidden` 을 붙였다는
말이 아니라 **실제로 안 보인다**는 말이다 — `[hidden]` 은 특이도 (0,0,1) 이라
`.row { display: flex }` 에 진다. `.tools` 와 타일에서 두 번 당한 자리다.

**유형을 파편으로 바꾸는 길에서는 묻는다.** 조용히 지우면 사람이 눈으로 매긴
것이 사라지고(재생성 불가), 안 지우고 보내면 서버가 409 로 물려 저장이 통째로
안 된다 — 화면에는 "저장 실패" 만 뜬다.
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import ForamObject, ObjectReview, RunBatch


class CatalogGradePoseTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=3)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        self.key = self.w.keys()[0]
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in self.w.viewpoints:
            fx.review_done(_vp)

    def open_catalog(self):
        return self.open(reverse("catalog", args=[self.w.slide.slug]))

    def card_of(self, page, key=None):
        """**키로 짚는다** — 카드 차례는 번호 순이라 첫 카드가 그 키라는 보장이
        없다. 차례에 기대는 시험은 픽스처가 바뀌면 조용히 다른 것을 본다."""
        return page.locator(f'.catcard[data-key="{key or self.key}"]')

    def seed(self, *, label="foram", grade="", pose=""):
        o = fx.add_review(self.w.vp, self.key, label=label)
        # **둘 다 개체에 앉는다** (0035 로 등급이 판정에서 옮겨 왔다).
        if grade or pose:
            dobj = o.foram_object
            dobj.grade, dobj.pose = grade, pose
            dobj.save()
        return o

    # --- 적히는가 -----------------------------------------------------------

    def test_등급과_자세를_고르면_저장된다(self):
        page = self.open_catalog()
        card = self.card_of(page)
        card.locator(".grade").select_option("A")
        card.locator(".pose").select_option("umbilical")
        page.wait_for_timeout(1500)

        o = ObjectReview.objects.get(mask_key=self.key)
        self.assertEqual((o.foram_object.grade, o.foram_object.pose),
                         ("A", "umbilical"))

    def test_다섯_칸을_잇달아_채워도_다_남는다(self):
        """**칸이 셋일 때 이미 당한 자리다** (105). 늘어난 둘이 남의 칸을 덮지
        않는지를 본다 — 응답이 겹치는 그 순간을 그대로 밟는다."""
        page = self.open_catalog()
        card = self.card_of(page)
        card.locator(".species").fill("Globigerina bulloides")
        card.locator(".cls").select_option("broken")
        card.locator(".grade").select_option("B")
        card.locator(".pose").select_option("spiral")
        card.locator(".note").fill("가장자리가 넘쳤다")
        page.locator("body").click(position={"x": 5, "y": 5})
        page.wait_for_timeout(2000)

        page.reload(wait_until="load")
        card = self.card_of(page)
        self.assertEqual(card.locator(".species").input_value(),
                         "Globigerina bulloides")
        self.assertEqual(card.locator(".cls").input_value(), "broken")
        self.assertEqual(card.locator(".grade").input_value(), "B")
        self.assertEqual(card.locator(".pose").input_value(), "spiral")
        self.assertEqual(card.locator(".note").input_value(), "가장자리가 넘쳤다")

    def test_등급만_매긴_행이_새로고침_뒤에도_있다(self):
        """등급만 있는 행은 다른 칸이 전부 비어 있어 **청소에 가장 가깝다.**"""
        page = self.open_catalog()
        self.card_of(page).locator(".grade").select_option("C")
        page.wait_for_timeout(1500)

        page.reload(wait_until="load")
        self.assertEqual(self.card_of(page).locator(".grade").input_value(), "C")

    # --- 파편에는 안 뜬다 ---------------------------------------------------

    def test_파편_카드에는_두_칸이_안_보인다(self):
        """**`hidden` 이 붙었는가가 아니라 실제로 안 보이는가**를 본다 —
        `[hidden]` 은 `.row { display: flex }` 에 특이도로 진다.

        **파편은 기본으로 감춰져 있으므로 켜고 연다** — 안 켜면 카드 자체가 없어
        "안 보인다" 가 저절로 참이 된다(실패할 수 없는 시험이 된다)."""
        self.seed(label="fragment")
        page = self.open(reverse("catalog", args=[self.w.slide.slug]) + "?frag=1")
        card = self.card_of(page)
        self.assertFalse(card.locator(".grade").is_visible(),
                         "파편인데 등급 칸이 보인다")
        self.assertFalse(card.locator(".pose").is_visible(),
                         "파편인데 자세 칸이 보인다")
        # 다른 칸은 그대로 있어야 한다 — 파편도 종명은 적는다.
        self.assertTrue(card.locator(".species").is_visible())

    def test_완형_카드에는_보인다(self):
        """감추는 시험만 있으면 전부 감춰도 통과한다."""
        self.seed(label="foram")
        page = self.open_catalog()
        card = self.card_of(page)
        self.assertTrue(card.locator(".grade").is_visible())
        self.assertTrue(card.locator(".pose").is_visible())

    def test_분류가_없어도_보인다(self):
        """매기는 순서를 강제하지 않는다 — 유형을 아직 안 정한 개체가 흔하다."""
        page = self.open_catalog()
        self.assertTrue(self.card_of(page).locator(".grade").is_visible())

    def test_유형을_파편으로_바꾸면_칸이_사라진다(self):
        page = self.open_catalog()
        card = self.card_of(page)
        card.locator(".cls").select_option("other")
        page.wait_for_timeout(300)
        self.assertFalse(card.locator(".grade").is_visible())

    # --- 지우기 전에 묻는다 -------------------------------------------------

    def test_파편으로_바꿀_때_묻고_취소하면_등급이_남는다(self):
        """**취소하면 유형도 안 바뀌어야 한다.** 유형만 파편으로 바뀌고 등급이
        남으면 서버가 409 로 물려 그 카드는 그때부터 아무것도 저장이 안 된다."""
        self.seed(label="foram", grade="A")
        page = self.open_catalog()
        card = self.card_of(page)

        asked = []
        page.on("dialog", lambda d: (asked.append(d.message), d.dismiss()))
        card.locator(".cls").select_option("fragment")
        page.wait_for_timeout(1200)

        self.assertTrue(asked, "묻지 않고 그냥 지나갔다")
        self.assertEqual(card.locator(".cls").input_value(), "foram",
                         "취소했는데 유형이 파편으로 바뀌어 있다")
        o = ObjectReview.objects.get(mask_key=self.key)
        self.assertEqual((o.foram_object.grade, o.foram_object.label),
                         ("A", "foram"))

    def test_파편으로_바꿀_때_받아들이면_등급이_지워진다(self):
        self.seed(label="foram", grade="A", pose="umbilical")
        page = self.open_catalog()
        card = self.card_of(page)

        page.on("dialog", lambda d: d.accept())
        card.locator(".cls").select_option("fragment")
        page.wait_for_timeout(1500)

        o = ObjectReview.objects.get(mask_key=self.key)
        self.assertEqual((o.foram_object.grade, o.foram_object.pose), ("", ""))
        self.assertEqual(o.foram_object.label, "fragment")

    def test_등급이_없으면_안_묻는다(self):
        """지울 것이 없는데 물으면 사람이 매번 확인 창을 닫는다."""
        self.seed(label="foram")
        page = self.open_catalog()
        asked = []
        page.on("dialog", lambda d: (asked.append(d.message), d.accept()))
        self.card_of(page).locator(".cls").select_option("fragment")
        page.wait_for_timeout(1200)
        self.assertFalse(asked, "지울 것이 없는데 물었다")
        self.assertEqual(
            ForamObject.objects.get(members__mask_key=self.key).label,
            "fragment")


class CatalogFragToggleTest(BrowserTestCase):
    """**파편 보기 체크박스** — 누르는 즉시 가는가 (2026-08-11).

    거르개 칩은 누르면 바로 가는데 이것만 "적용" 을 눌러야 하면 사람은 안 바뀐
    화면을 보고 고장으로 읽는다. **배선이 빠져도 화면은 멀쩡해 보인다** — 3겹은
    `?frag=1` 을 직접 붙여 열기 때문에 이 갈래를 한 번도 안 밟는다.
    """

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=3)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        self.frag_key = self.w.keys()[0]
        fx.add_review(self.w.vp, self.frag_key, label="fragment")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in self.w.viewpoints:
            fx.review_done(_vp)

    def test_기본은_꺼져_있고_누르면_파편이_나온다(self):
        """**그 파편 카드가 나오는지**를 본다 — 카드 수를 세면 픽스처가 만드는
        다른 파편에 묻혀 무슨 일이 났는지 알 수 없다."""
        page = self.open(reverse("catalog", args=[self.w.slide.slug]))
        box = page.locator(".fragtoggle input")
        self.assertFalse(box.is_checked(), "파편이 기본으로 켜져 있다")
        card = f'.catcard[data-key="{self.frag_key}"]'
        self.assertEqual(page.locator(card).count(), 0, "파편이 그냥 보인다")

        # **이동을 기다린다.** `wait_for_load_state` 로는 안 된다 — 옛 페이지가
        # 이미 `load` 라 그 자리에서 참이고, 그러면 아직 안 바뀐 화면을 세게
        # 된다(처음에 그렇게 짰다가 "켰는데 안 나온다" 로 헛다리를 짚었다).
        with page.expect_navigation():
            box.check()
        self.assertTrue(page.locator(".fragtoggle input").is_checked(),
                        "눌렀는데 안 갔다 — 배선이 빠졌다")
        self.assertEqual(page.locator(card).count(), 1, "켰는데 파편이 안 나온다")


class CatalogGradePoseReadOnlyTest(BrowserTestCase):
    """읽기 전용은 **되는 것처럼 보이면 안 된다** (051)."""

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=2,
                               state="processing")
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in self.w.viewpoints:
            fx.review_done(_vp)

    def test_다섯_칸이_다_안_눌린다(self):
        page = self.open(reverse("catalog", args=[self.w.slide.slug]))
        card = page.locator(".catcard").first
        for sel in (".species", ".cls", ".note", ".grade", ".pose"):
            self.assertTrue(card.locator(sel).is_disabled(), sel)

    def test_몰래_밀어_넣어도_안_저장된다(self):
        page = self.open(reverse("catalog", args=[self.w.slide.slug]))
        card = page.locator(".catcard").first
        card.locator(".grade").evaluate("""e => {
            e.value = 'A';
            e.dispatchEvent(new Event('change', {bubbles: true}));
            e.dispatchEvent(new Event('blur', {bubbles: true}));
        }""")
        page.wait_for_timeout(1500)
        self.assertFalse(ForamObject.objects.filter(grade="A").exists())
