"""DiaRUGA v0.29.0 `tests/browser/test_tip_species.py` 에서 왔다.

검토 화면 말풍선의 **맨 위에 동정한 종명** (사용자 방침 2026-08-10).

개체 카탈로그에서 적은 종명은 검토 화면에서도 읽혀야 한다 — 커서를 올렸을 때
가장 먼저 보이는 것이 그것이고, 형태(원형·봉상)는 그 다음이다. 학명이라 **이탤릭
굵게** 쓴다.

**모양이 먹는지까지 본다.** `.dettip b { font-size: 12px }` 가 이미 있어서, 새
규칙을 아무 이름으로 적으면 HTML 은 멀쩡하고 3겹도 통과하는데 화면에는 **맨
글자**가 나온다. `.tools`(051) · `.nowrow`(083) · `.nowtag`(088) 가 그 자리였고
`getComputedStyle` 로 확인하는 것이 그 답이다.
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ... import data
from ...models import ForamObject, ObjectReview, RunBatch

SPECIES = "Globigerina bulloides"


class TipSpeciesTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=2)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        det = self.w.detection()
        self.key = self.w.keys()[0]
        self.cand = det.candidates.get(mask_key=self.key)
        fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=det.batch,
            mask_key=self.key, bind_method="exact",
            label="broken", species=SPECIES)

    def open_view(self):
        page = self.open(reverse("group", args=[self.w.slide.slug,
                                                self.w.vp.idx]))
        page.wait_for_selector(".detview .box")
        return page

    def hover_it(self, page):
        """**그 개체의 한가운데를 짚는다.** 화면 한가운데를 찍어 보면 개체 위에
        떨어질지가 운이고, 못 맞히면 시험이 조용히 넘어간다 (`image_point`)."""
        x, y = self.image_point(self.cand.center_x, self.cand.center_y)
        page.mouse.move(x, y)
        page.wait_for_timeout(150)
        self.assertTrue(page.is_visible(".dettip"), "말풍선이 안 떴다")

    def test_종명이_말풍선에_있다(self):
        page = self.open_view()
        self.hover_it(page)
        self.assertIn(SPECIES, page.inner_text(".dettip"))

    def test_맨_위에_있다(self):
        """형태보다 먼저 읽혀야 한다 — 그것이 이 요청의 요점이다."""
        page = self.open_view()
        self.hover_it(page)
        txt = page.inner_text(".dettip")
        self.assertLess(txt.index(SPECIES), txt.index("파손"),
                        f"종명이 형태보다 아래에 있다: {txt[:120]}")
        self.assertTrue(txt.lstrip().startswith(SPECIES), txt[:120])

    def test_이탤릭_굵게가_먹는다(self):
        """**모양이 실제로 먹는가.** `.dettip b` 가 이미 있어 이름을 잘못 고르면
        맨 글자가 나온다 — HTML 만 보는 시험은 그것을 통과시킨다."""
        page = self.open_view()
        self.hover_it(page)
        st = page.query_selector(".dettip .sp").evaluate("""e => {
            const s = getComputedStyle(e);
            return {style: s.fontStyle, weight: s.fontWeight, display: s.display};
        }""")
        self.assertEqual(st["style"], "italic", st)
        self.assertGreaterEqual(int(st["weight"]), 700, st)
        self.assertEqual(st["display"], "block", st)

    def test_동정_안_한_개체에는_안_나온다(self):
        """빈 줄이 서면 말풍선 머리가 한 줄 밀려 형태가 아래로 내려간다."""
        page = self.open_view()
        other = self.w.detection().candidates.exclude(mask_key=self.key).first()
        x, y = self.image_point(other.center_x, other.center_y)
        page.mouse.move(x, y)
        page.wait_for_timeout(150)
        self.assertTrue(page.is_visible(".dettip"))
        self.assertIsNone(page.query_selector(".dettip .sp"))

    def test_꺽쇠가_든_종명도_그대로_보인다(self):
        """**글자를 그대로 심으면 `<` 하나가 말풍선을 통째로 망가뜨린다** —
        예외도 안 나고 그냥 안 보인다. 종명은 자유 입력이다."""
        ForamObject.objects.filter(members__mask_key=self.key).update(
            taxon=fx.make_taxon("Globigerina <sp.> aff. antarctica"))
        page = self.open_view()
        self.hover_it(page)
        self.assertIn("Globigerina <sp.> aff. antarctica",
                      page.inner_text(".dettip .sp"))
