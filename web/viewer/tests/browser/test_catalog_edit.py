"""DiaRUGA v0.29.0 `tests/browser/test_catalog_edit.py` 에서 왔다.

개체 카탈로그에 **실제로 쳐 넣어 본다.**

3겹(`tests/test_catalog_page.py`)은 서버가 무엇을 받으면 무엇을 하는지까지 본다.
여기서 보는 것은 그 밖이다 — **화면이 실제로 그것을 보내는가, 그리고 보낸 뒤에
화면이 사람이 친 글자를 지키는가.**

이 시험이 있는 이유가 구체적이다. 세 칸을 잇달아 채우면 **먼저 보낸 요청의
응답이 나중에 친 글자를 덮었다** — 종명을 저장한 응답이 돌아오면서 그 사이에 친
코멘트를 서버가 아는 빈 값으로 되돌렸다. 화면에는 "저장됨" 이 적혀 있고,
새로고침하면 코멘트만 없다.

**3겹으로는 안 잡힌다.** 서버는 받은 것을 정확히 저장했고 200 을 냈다. 고장은
브라우저 안에서만 났다 — 045 가 브라우저 겹을 만든 바로 그 종류다.
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import ForamObject, ObjectReview, RunBatch


class CatalogEditTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=3)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in self.w.viewpoints:
            fx.review_done(_vp)

    def open_catalog(self):
        return self.open(reverse("catalog", args=[self.w.slide.slug]))

    def first_card(self, page):
        return page.locator(".catcard").first

    def test_세_칸을_잇달아_채워도_다_남는다(self):
        """**이 시험이 이 파일의 이유다.** 응답이 겹치는 그 자리를 그대로 밟는다."""
        page = self.open_catalog()
        card = self.first_card(page)
        card.locator(".species").fill("Globigerina bulloides")
        card.locator(".cls").select_option("broken")
        card.locator(".note").fill("가장자리가 넘쳤다")
        page.locator("body").click(position={"x": 5, "y": 5})
        page.wait_for_timeout(1800)

        page.reload(wait_until="load")
        card = self.first_card(page)
        self.assertEqual(card.locator(".species").input_value(),
                         "Globigerina bulloides")
        self.assertEqual(card.locator(".cls").input_value(), "broken")
        self.assertEqual(card.locator(".note").input_value(), "가장자리가 넘쳤다")

    def test_저장하는_동안_더_쳐도_안_덮인다(self):
        """응답이 오는 사이에 사람이 더 친다. **보낸 뒤로 바뀐 칸은 안 건드린다.**"""
        page = self.open_catalog()
        card = self.first_card(page)
        # ForGIA: 첫 값도 `Taxon` 에 있는 이름이어야 저장이 나간다
        card.locator(".species").fill("Globigerina sp.")
        card.locator(".species").blur()
        card.locator(".species").fill("Globigerina bulloides")
        card.locator(".species").blur()
        page.wait_for_timeout(2000)

        page.reload(wait_until="load")
        self.assertEqual(self.first_card(page).locator(".species").input_value(),
                         "Globigerina bulloides")

    def test_옆_카드를_안_건드린다(self):
        """`/review` 는 범위를 갈아치우지만 여기는 짚은 개체 하나만 고친다."""
        page = self.open_catalog()
        cards = page.locator(".catcard")
        cards.nth(1).locator(".species").fill("Globorotalia sp.")
        cards.nth(1).locator(".species").blur()
        page.wait_for_timeout(1200)
        cards.nth(0).locator(".species").fill("Globigerina bulloides")
        cards.nth(0).locator(".species").blur()
        page.wait_for_timeout(1500)

        page.reload(wait_until="load")
        cards = page.locator(".catcard")
        self.assertEqual(cards.nth(1).locator(".species").input_value(),
                         "Globorotalia sp.")
        self.assertEqual(cards.nth(0).locator(".species").input_value(),
                         "Globigerina bulloides")

    def test_비우면_그_줄이_사라진다(self):
        page = self.open_catalog()
        card = self.first_card(page)
        card.locator(".species").fill("Globigerina bulloides")
        card.locator(".species").blur()
        page.wait_for_timeout(1500)
        self.assertTrue(ForamObject.objects.filter(taxon__isnull=False).exists())

        card.locator(".species").fill("")
        card.locator(".species").blur()
        page.wait_for_timeout(1500)
        self.assertFalse(ForamObject.objects.filter(taxon__isnull=False).exists())

    def test_그림을_누르면_그_시야로_간다(self):
        """크롭 화면과 같은 자리로 간다. **그림과 g번호 둘 다** — 그림만 링크면
        말풍선으로만 알 수 있어서, 있는 줄 모르고 안 쓴다.

        **`endswith` 로 보지 않는다** (118). 링크가 개체까지 들고 가면서
        (`?obj=…&img=…`) 주소 끝이 시야가 아니게 됐다 — 무엇을 짚어 가는지는
        `tests/test_highlight_link.py` 가 본다. 여기서 보는 것은 **그 시야로
        가는가** 뿐이다.
        """
        page = self.open_catalog()
        card = self.first_card(page)
        want = f"/d/{self.w.slide.slug}/g/0/"
        self.assertIn(want, card.locator(".pic").get_attribute("href"))
        self.assertIn(want, card.locator(".meta .togroup").get_attribute("href"))
        card.locator(".meta .togroup").click()
        page.wait_for_load_state("load")
        self.assertIn(want, page.url)

    def test_적다_말고_뒤로_가도_안_잃는다(self):
        """**글자 칸은 900ms 쉬거나 자리를 떠야 보낸다.** 그 사이에 페이지가
        사라지면 방금 적은 종명이 없던 일이 된다 — 저장됐다는 표시도 못 봤으니
        사람은 적은 줄 안다.

        **링크를 누르는 것은 이 갈래가 아니다.** 누르면 칸이 먼저 blur 되어
        그때 이미 보내진다(처음엔 그것을 시험이라고 적었는데, 떠날 때 마저
        보내는 코드를 빼도 통과했다 — 실패할 수 없는 시험이었다).

        빈 자리는 **blur 가 안 나는 이동**이다: 뒤로 가기 · 주소창 · 탭 닫기.
        그때는 `pagehide` 밖에 안 오고, `keepalive` 가 없으면 요청이 문서와
        함께 죽는다.
        """
        # 뒤로 갈 곳을 만들어 둔다 — 시야 목록에서 카탈로그로 들어온 것처럼.
        self.open(reverse("dataset", args=[self.w.slide.slug]))
        page = self.open_catalog()
        card = self.first_card(page)
        card.locator(".species").type("Globigerina bulloides", delay=10)
        # 디바운스가 돌기 전에 뒤로 간다. blur 는 안 난다.
        page.go_back(wait_until="load")
        page.wait_for_timeout(1500)

        # **빈 것을 세지 않는다** (DiaRUGA P18). 검토 완료가 통과분마다 개체를 세우므로
        # 개체가 후보 수만큼 있다 — 여기서 보려는 것은 *적은 종명이 남았는가*다.
        self.assertEqual(
            [x for x in ForamObject.objects.values_list("taxon__name", flat=True)
             if x],
            ["Globigerina bulloides"])

    def test_저장되면_카드가_그렇게_말한다(self):
        """아무 표시도 없으면 사람이 같은 일을 다시 한다."""
        page = self.open_catalog()
        card = self.first_card(page)
        card.locator(".species").fill("Globigerina bulloides")
        card.locator(".species").blur()
        page.wait_for_timeout(1200)
        self.assertIn("saved", card.get_attribute("class"))


class CatalogReadOnlyTest(BrowserTestCase):
    """**되는 것처럼 보이면 안 된다** (051).

    저장만 잠그고 화면을 살려 두면 사람이 한 판을 헛동정하고, 새로고침하면
    통째로 사라진다. 그래서 **반응하는 자리를 전부 센다.**
    """

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

    def test_세_칸이_다_안_눌린다(self):
        page = self.open(reverse("catalog", args=[self.w.slide.slug]))
        card = page.locator(".catcard").first
        for sel in (".species", ".cls", ".note"):
            self.assertTrue(card.locator(sel).is_disabled(), sel)

    def test_쳐_넣어도_아무것도_안_저장된다(self):
        """`disabled` 를 브라우저가 실제로 막는가 — CSS 로 감춘 것과 다르다."""
        page = self.open(reverse("catalog", args=[self.w.slide.slug]))
        card = page.locator(".catcard").first
        # `fill` 은 disabled 를 못 채운다 — JS 로 값을 밀어 넣고 이벤트까지 쏜다.
        card.locator(".species").evaluate("""e => {
            e.value = '몰래';
            e.dispatchEvent(new Event('input', {bubbles: true}));
            e.dispatchEvent(new Event('change', {bubbles: true}));
            e.dispatchEvent(new Event('blur', {bubbles: true}));
        }""")
        page.wait_for_timeout(1500)
        self.assertFalse(ForamObject.objects.filter(taxon__name="Globorotalia inflata").exists())

    def test_왜_못_적는지_보인다(self):
        page = self.open(reverse("catalog", args=[self.w.slide.slug]))
        self.assertTrue(page.is_visible(".note.warn"))
        self.assertIn("자동 처리가 아직 끝나지 않았습니다",
                      page.inner_text(".note.warn"))

    def test_지우기도_일괄도_눌러_볼_자리가_없다(self):
        """**반응하는 자리를 전부 센다** (051). 넓힌 문 셋도 같은 줄에 선다."""
        page = self.open(reverse("catalog", args=[self.w.slide.slug]))
        self.assertGreaterEqual(page.locator(".catcard").count(), 1,
                                "카드가 없으면 이 시험은 아무것도 안 본다")
        for sel in (".catcard .remove", ".catcard .restore", ".catcard .pick",
                    ".catcard .unlink", ".bulkbar", ".pickall"):
            self.assertEqual(page.locator(sel).count(), 0, sel)


class CatalogRemoveBrowserTest(BrowserTestCase):
    """지우기·되살리기가 **실제로 배선돼 있는가** (DiaRUGA P16 5.1).

    3겹은 서버가 `act=remove` 를 받으면 무엇을 하는지까지 본다. 여기서 보는 것은
    그 밖이다 — **단추가 그것을 보내는가, 그리고 보낸 뒤 화면이 지운 카드를
    잠그는가.** 안 잠그면 지워 놓은 개체에 종명을 적을 수 있고, 그 저장은
    되살리지도 않는다(051 과 같은 갈래다).
    """

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=3)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in self.w.viewpoints:
            fx.review_done(_vp)

    def test_지우면_카드가_잠기고_되살리기로_바뀐다(self):
        page = self.open(reverse("catalog", args=[self.w.slide.slug]))
        card = page.locator(".catcard").first
        key = card.get_attribute("data-key")
        card.locator(".remove").click()
        page.wait_for_selector(".catcard .restore", timeout=5000)

        self.assertTrue(ObjectReview.objects.get(mask_key=key).removed)
        for sel in (".species", ".cls", ".note"):
            self.assertTrue(card.locator(sel).is_disabled(), sel)

    def test_그_자리에서_되살린다(self):
        """**되돌릴 자리가 같은 카드에 있어야 한다** — 잘못 누를 수 있다(DiaRUGA P16 8절).

        **기다리는 것을 그 카드로 좁힌다.** `.catcard .remove` 를 기다리면 옆
        카드의 단추에 곧바로 걸려서, 되살리기가 끝나기도 전에 다음 줄이 돈다 —
        시험이 경합으로 갈리고, 그런 시험은 고장을 못 잡는다.
        """
        page = self.open(reverse("catalog", args=[self.w.slide.slug]))
        card = page.locator(".catcard").first
        key = card.get_attribute("data-key")
        here = f'.catcard[data-key="{key}"]'
        card.locator(".remove").click()
        page.wait_for_selector(f"{here} .restore", timeout=5000)
        card.locator(".restore").click()
        page.wait_for_selector(f"{here} .remove", timeout=5000)

        self.assertFalse(ObjectReview.objects.filter(mask_key=key,
                                                     removed=True).exists())
        self.assertFalse(card.locator(".species").is_disabled())

    def test_등급이_붙은_개체를_지울_때_묻는다(self):
        """**되돌릴 수 있는 일에 되돌릴 수 없는 일을 얹지 않는다.**

        등급·자세가 붙은 개체를 지우면 `check_db` 의 "등급·자세가 살아 있는
        개체에만 붙어 있다" 에 걸린다 — 판이 전부 오검출인데 등급이 남기 때문이다.
        그래도 여기서 등급을 지우지는 않는다(사람이 눈으로 매긴 재생성 불가한
        값이다). **무엇이 남는지 말하고 사람이 고른다.**
        """
        page = self.open(reverse("catalog", args=[self.w.slide.slug]))
        card = page.locator(".catcard").first
        key = card.get_attribute("data-key")
        card.locator(".grade").select_option("A")
        page.wait_for_timeout(1200)

        asked = []
        page.on("dialog", lambda d: (asked.append(d.message), d.dismiss()))
        card.locator(".remove").click()
        page.wait_for_timeout(600)
        self.assertTrue(asked, "안 물었다")
        self.assertIn("등급·자세", asked[0])
        # 취소했으니 아무것도 안 지워졌다
        self.assertFalse(ObjectReview.objects.get(mask_key=key).removed)

    def test_지운_것은_지운_화면에서_난다(self):
        page = self.open(reverse("catalog", args=[self.w.slide.slug]))
        card = page.locator(".catcard").first
        key = card.get_attribute("data-key")
        card.locator(".remove").click()
        page.wait_for_selector(".catcard .restore", timeout=5000)

        page.goto(page.url + "?gone=1", wait_until="load")
        self.assertEqual(page.locator(f'.catcard[data-key="{key}"]').count(), 1)
        # 지운 화면에는 고르는 칸도 일괄 띠도 없다
        self.assertEqual(page.locator(".catcard .pick").count(), 0)
        self.assertEqual(page.locator(".bulkbar").count(), 0)


class CatalogBulkBrowserTest(BrowserTestCase):
    """일괄이 **고른 것에만** 앉는가 (DiaRUGA P16 5.2)."""

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=3)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in self.w.viewpoints:
            fx.review_done(_vp)

    def test_고른_둘에만_앉는다(self):
        # **파편도 함께 낸다** — 기본 화면은 파편을 감추므로 카드가 모자란다.
        page = self.open(reverse("catalog", args=[self.w.slide.slug]) + "?frag=1")
        cards = page.locator(".catcard")
        self.assertGreaterEqual(cards.count(), 3, "카드가 셋은 있어야 한다")
        picked = [cards.nth(i).get_attribute("data-key") for i in (0, 1)]
        left = cards.nth(2).get_attribute("data-key")

        # 띠는 고르기 전에는 안 뜬다 — 고른 것이 없는데 `적용` 이 보이면 안 된다
        self.assertTrue(page.locator(".bulkbar").is_hidden())
        cards.nth(0).locator(".pick").check()
        cards.nth(1).locator(".pick").check()
        page.wait_for_selector(".bulkbar:not([hidden])", timeout=5000)
        self.assertIn("2", page.inner_text(".bulkbar .n"))

        page.locator(".bulkbar .cls").select_option("broken")
        page.locator(".bulkbar .go").click()
        page.wait_for_timeout(1500)

        got = {o.mask_key: o.foram_object.label
               for o in ObjectReview.objects.select_related("foram_object")}
        for k in picked:
            self.assertEqual(got.get(k), "broken", k)
        # **안 고른 카드는 안 건드린다.** 줄은 있다 — 검토 완료가 통과분마다
        # 판정을 세우므로(DiaRUGA P18) *없는 것*이 아니라 *빈 것*이어야 한다.
        self.assertFalse(got.get(left),
                         f"안 고른 카드에 분류가 앉았다: {got.get(left)!r}")

    def test_쪽_전부_고르기가_이_쪽을_전부_고른다(self):
        """**눌러서 되는가** (185). 배선이 죽어 있으면 3겹은 통과한다.

        고른 수가 카드 수와 같아야 한다 — 하나라도 빠지면 사람은 전부 앉은
        줄 알고 넘어가고, 빠진 개체는 종명 없이 남는다.
        """
        page = self.open(reverse("catalog", args=[self.w.slide.slug]) + "?frag=1")
        cards = page.locator(".catcard")
        n = cards.count()
        self.assertGreaterEqual(n, 3, "카드가 셋은 있어야 이 시험이 뜻이 있다")
        self.assertTrue(page.locator(".bulkbar").is_hidden())

        page.locator(".pickall").click()
        page.wait_for_selector(".bulkbar:not([hidden])", timeout=5000)
        self.assertIn(f"고른 것 {n}개", page.inner_text(".bulkbar .n"))
        self.assertEqual(page.locator(".catcard .pick:checked").count(), n)
        # **단추가 두 방향으로 돈다** — 이제 할 일은 푸는 것이다
        self.assertEqual(page.locator(".pickall").get_attribute("aria-pressed"),
                         "true")
        self.assertIn("해제", page.inner_text(".pickall"))

    def test_다시_누르면_고르기가_풀린다(self):
        page = self.open(reverse("catalog", args=[self.w.slide.slug]) + "?frag=1")
        page.locator(".pickall").click()
        page.wait_for_selector(".bulkbar:not([hidden])", timeout=5000)
        page.locator(".pickall").click()
        page.wait_for_timeout(300)
        self.assertEqual(page.locator(".catcard .pick:checked").count(), 0)
        self.assertTrue(page.locator(".bulkbar").is_hidden())
        self.assertIn("전부 고르기", page.inner_text(".pickall"))

    def test_반쯤_고른_채로_누르면_나머지를_채운다(self):
        """**고른 것을 뺏지 않는다.** 반쯤 고른 상태에서 눌러 전부 풀리면
        사람이 방금 한 일이 사라진다 — 그때 다시 누르면 되지만, 무엇이
        골라져 있었는지는 화면에 안 남는다."""
        page = self.open(reverse("catalog", args=[self.w.slide.slug]) + "?frag=1")
        n = page.locator(".catcard").count()
        page.locator(".catcard").first.locator(".pick").check()
        page.wait_for_selector(".bulkbar:not([hidden])", timeout=5000)
        page.locator(".pickall").click()
        page.wait_for_timeout(300)
        self.assertEqual(page.locator(".catcard .pick:checked").count(), n)

    def test_전부_고르고_종명을_앉히면_전부에_간다(self):
        """**이 단추가 있는 이유가 이것이다** — 203개에 같은 종명을 앉히는 일."""
        page = self.open(reverse("catalog", args=[self.w.slide.slug]) + "?frag=1")
        keys = [page.locator(".catcard").nth(i).get_attribute("data-key")
                for i in range(page.locator(".catcard").count())]
        page.locator(".pickall").click()
        page.wait_for_selector(".bulkbar:not([hidden])", timeout=5000)
        page.locator(".bulkbar .species").fill("Globorotalia sp.")
        page.locator(".bulkbar .go").click()
        page.wait_for_timeout(2000)

        got = {o.mask_key: o.foram_object.species
               for o in ObjectReview.objects.select_related("foram_object")
               if o.foram_object_id}
        for k in keys:
            self.assertEqual(got.get(k), "Globorotalia sp.", k)

    def test_고칠_칸을_안_채우면_말한다(self):
        """아무것도 안 하고 "저장됨" 이 뜨는 갈래를 안 만든다 (063)."""
        page = self.open(reverse("catalog", args=[self.w.slide.slug]))
        page.locator(".catcard").first.locator(".pick").check()
        page.wait_for_selector(".bulkbar:not([hidden])", timeout=5000)
        page.locator(".bulkbar .go").click()
        page.wait_for_timeout(400)
        self.assertIn("고칠 칸", page.inner_text(".bulkbar .say"))
        self.assertFalse(ForamObject.objects.exclude(label="").exists())

    def test_고른_것이_새로고침을_넘어_산다(self):
        """**이 화면의 일상이 새로고침이다** (187). 카드를 눌러 그 시야를 보고
        돌아오면 `base.html` 이 bfcache 를 늘 다시 받는다(교정·검토 표시가
        옛것이면 안 되므로) — 그 새로고침이 **고른 칸까지 비웠다.**

        120개를 골라 놓고 하나를 확인하러 갔다 오면 처음부터 다시 누른다.
        사용자가 "다중 선택이 계속 풀린다" 고 말한 자리다 (2026-09-07).
        """
        page = self.open(reverse("catalog", args=[self.w.slide.slug]) + "?frag=1")
        cards = page.locator(".catcard")
        self.assertGreaterEqual(cards.count(), 3, "카드가 셋은 있어야 한다")
        cards.nth(0).locator(".pick").check()
        cards.nth(1).locator(".pick").check()
        page.wait_for_selector(".bulkbar:not([hidden])", timeout=5000)

        page.reload(wait_until="load")
        page.wait_for_timeout(300)
        cards = page.locator(".catcard")
        self.assertTrue(cards.nth(0).locator(".pick").is_checked())
        self.assertTrue(cards.nth(1).locator(".pick").is_checked())
        self.assertFalse(cards.nth(2).locator(".pick").is_checked(),
                         "안 고른 것까지 골라졌다")
        # **띠도 함께 돌아와야 한다** — 칸만 켜져 있고 띠가 없으면 `적용` 이
        # 없어 고른 것을 쓸 수가 없다.
        self.assertTrue(page.locator(".bulkbar").is_visible())
        self.assertIn("2", page.inner_text(".bulkbar .n"))

    def test_거르개가_다르면_되살리지_않는다(self):
        """**화면에 없는 것을 고를 수는 없다** (DiaRUGA P16 5.2). 거르개·쪽이 달라지면
        그 쪽에 없는 카드를 고른 채로 두는 셈이라 되살리지 않는다.
        """
        page = self.open(reverse("catalog", args=[self.w.slide.slug]) + "?frag=1")
        page.locator(".pickall").click()
        page.wait_for_selector(".bulkbar:not([hidden])", timeout=5000)

        page.goto(self.live_server_url
                  + reverse("catalog", args=[self.w.slide.slug]))
        page.wait_for_timeout(300)
        picks = page.locator(".catcard .pick")
        for i in range(picks.count()):
            self.assertFalse(picks.nth(i).is_checked(), f"{i}번이 골라져 있다")
        self.assertTrue(page.locator(".bulkbar").is_hidden())

    def test_고르는_중에는_카드를_눌러_고른다(self):
        """**고르는 중에는 카드가 통째로 고르는 자리다** (188, 사용자 2026-09-07).

        카드 하나가 440px 인데 고르는 칸은 왼쪽 위 13px 이라, 여럿을 고르는
        동안 손이 자꾸 그림(→ 시야로 이동)이나 칸(→ 편집)에 떨어진다.
        시야로 넘어가면 고른 것을 잃고, 칸에 떨어지면 **그 개체에만** 값이
        앉아 일괄과 어긋난다.
        """
        page = self.open(reverse("catalog", args=[self.w.slide.slug]) + "?frag=1")
        cards = page.locator(".catcard")
        self.assertGreaterEqual(cards.count(), 3, "카드가 셋은 있어야 한다")
        was = page.url
        cards.nth(0).locator(".pick").check()
        page.wait_for_selector(".bulkbar:not([hidden])", timeout=5000)

        # 그림을 눌러도 시야로 안 간다 — 고르는 것으로 받는다.
        # **`force` 로 누른다** — 그림은 `pointer-events: none` 이라 플레이라이트가
        # 「누를 수 있는 자리」로 안 치는데, 사람이 누르는 것은 그 좌표이고
        # 클릭은 그 자리에 놓인 카드가 받는다. 그 자리를 그대로 누른다.
        cards.nth(1).locator(".pic").click(force=True)
        page.wait_for_timeout(200)
        self.assertEqual(page.url, was, "고르는 중에 시야로 넘어갔다")
        self.assertTrue(cards.nth(1).locator(".pick").is_checked())

        # 편집칸 자리를 눌러도 같다.
        cards.nth(2).locator(".fld").click(position={"x": 5, "y": 5}, force=True)
        page.wait_for_timeout(200)
        self.assertTrue(cards.nth(2).locator(".pick").is_checked())
        self.assertIn("3", page.inner_text(".bulkbar .n"))

        # 다시 누르면 풀린다 — 잘못 고른 것을 그 자리에서 되돌린다.
        cards.nth(2).locator(".fld").click(position={"x": 5, "y": 5}, force=True)
        page.wait_for_timeout(200)
        self.assertFalse(cards.nth(2).locator(".pick").is_checked())

    def test_고르는_중에는_칸을_못_친다(self):
        """**잠그는 것으로 끝나지 않는다** (051) — 풀리기도 해야 한다.
        「고르기 해제」 뒤에 못 치면 사람이 화면이 죽은 줄로 읽는다.
        """
        page = self.open(reverse("catalog", args=[self.w.slide.slug]) + "?frag=1")
        card = page.locator(".catcard").first
        self.assertTrue(card.locator(".species").is_enabled())

        card.locator(".pick").check()
        page.wait_for_selector(".bulkbar:not([hidden])", timeout=5000)
        self.assertTrue(card.locator(".species").is_disabled(),
                        "고르는 중인데 종명 칸이 살아 있다")
        self.assertTrue(card.locator(".cls").is_disabled())

        page.locator(".bulkbar .clear").click()
        page.wait_for_timeout(200)
        self.assertTrue(card.locator(".species").is_enabled(),
                        "고르기를 풀었는데 칸이 안 돌아왔다")
