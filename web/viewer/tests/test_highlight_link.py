"""DiaRUGA v0.29.0 `tests/test_highlight_link.py` 에서 왔다.

링크가 짚어 온 개체 (DiaRUGA 118) — 주소가 개체를 나르는가.

카탈로그·크롭·계측 표에서 사진을 누르면 그 시야로 가는데, **거기 개체가 수십
개라 어느 것을 보고 눌렀는지 다시 찾아야 했다**(사용자 보고 2026-08-13).
링크가 `?obj=<개체 키>&img=<그 개체가 있는 판>` 을 함께 나르고 검토 화면이 그
자리를 표시한다.

여기서 보는 것은 **주소와 그것이 화면까지 닿는 길**이다.

1. 세 화면의 링크가 개체와 판을 **함께** 싣는가 — 키만 실으면 다른 판의 개체를
   못 찾는다(시야 하나에 판이 여럿이다)
2. 검토 화면이 그것을 받아 화면 쪽으로 내려보내는가 (`hl-stack`)
3. **모르는 값이 시야를 못 열게 하지 않는가** — 적어 둔 링크는 낡는다.
   표시는 곁들이는 것이지 이 화면이 서는 조건이 아니다

**실제로 표시가 붙는지는 여기서 못 본다** — 그것은 JS 가 하는 일이라
`browser/test_highlight.py` 가 눌러서 본다. 이 겹은 재료가 거기까지 가는지만
본다 (051 이 준 교훈: 화면에 값이 들어 있는 것과 그 값이 쓰이는 것은 다르다).
"""
import json
import re

from django.test import Client
from django.urls import reverse

from . import factories as fx
from .base import ForGIATestCase
from .. import data


class HighlightLinkTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def setUp(self):
        self.c = Client()
        self.row = data.candidate_rows("rs23")[0]

    def html(self, url, **q):
        r = self.c.get(url, q)
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.content.decode()

    def links_of(self, html):
        """`/g/<n>/` 로 가는 href 들. 화면이 실제로 그린 것만 본다."""
        return re.findall(r'href="([^"]*/g/\d+/[^"]*)"', html)

    # --- 1. 세 화면이 개체를 싣는다 ----------------------------------------

    def assert_carries_object(self, html, where):
        """그 화면의 링크 하나가 **개체와 판을 함께** 싣는가."""
        want = f"obj={self.row['key']}"
        hit = [u for u in self.links_of(html) if want in u]
        self.assertTrue(hit, f"{where}: 개체를 안 싣는다 ({want})\n"
                             f"  실린 것: {self.links_of(html)[:3]}")
        # **판이 함께 가야 한다.** 시야 하나에 판이 여럿이라(합성본 + 프레임마다
        # 하나) 키만 보내면 화면은 지금 열린 판에서만 찾는다.
        self.assertIn(f"img={self.row['image_id']}", hit[0],
                      f"{where}: 개체는 실었는데 어느 판인지가 없다")

    def test_카탈로그가_개체를_싣는다(self):
        html = self.html(reverse("catalog", args=["rs23"]))
        self.assert_carries_object(html, "카탈로그")

    def test_크롭_화면이_개체를_싣는다(self):
        html = self.html(reverse("crops", args=["rs23"]))
        self.assert_carries_object(html, "크롭")

    def test_계측_표가_개체를_싣는다(self):
        html = self.html(reverse("detections", args=["rs23"]))
        self.assert_carries_object(html, "계측 표")

    def test_합성본이_없는_관찰에서도_싣는다(self):
        """**URL 을 덮는 것과 갈래를 덮는 것은 다르다** (DiaRUGA 086). 자료를 전부
        합성본으로 세우면 프레임 갈래를 한 번도 안 밟는데, `/crops/`·
        `/detections/` 가 정확히 그래서 v0.8.0 이후 내내 500 이었다.

        그 갈래에서는 개체가 **프레임의 것**이라 `img` 가 합성본이 아니다 —
        여기가 어긋나면 화면이 판을 못 찾아 표시가 안 붙는다.
        """
        # ForGIA: 노두가 없어 코어의 싱글턴 시야로 같은 갈래(프레임 검출)를 만든다
        w = fx.make_world(slug="bp09", site_code="WAP13", loc_code="GC47",
                          sample_code="116cm", depth_cm=116.0,
                          with_stack=False, n_candidates=3)
        # 카드가 개체 단위라 판정이 있어야 줄이 난다 (DiaRUGA P18).
        for vp in w.viewpoints:
            fx.review_done(vp)
        # **파편으로 고르면 안 된다** — 카탈로그는 파편을 기본으로 감춘다
        # (등급·자세를 안 매기므로). 세 화면에 다 나오는 개체라야 셋을 비교한다.
        row = next(r for r in data.candidate_rows("bp09") if r["cls"] == "broken")
        for name in ("catalog", "crops", "detections"):
            html = self.html(reverse(name, args=["bp09"]))
            hit = [u for u in self.links_of(html) if f"obj={row['key']}" in u]
            self.assertTrue(hit, f"{name}: 프레임 갈래에서 개체를 안 싣는다")
            self.assertIn(f"img={row['image_id']}", hit[0],
                          f"{name}: 프레임 갈래에서 판이 안 실렸다")

    # --- 2. 검토 화면까지 닿는다 -------------------------------------------

    def group_url(self):
        return reverse("group", args=["rs23", self.w.vp.idx])

    def hl_json(self, html):
        """화면이 JS 쪽으로 내려보낸 것. 없으면 `None`."""
        m = re.search(r'id="hl-stack">(.*?)</script>', html, re.S)
        return json.loads(m.group(1)) if m else None

    def test_검토_화면이_받아_내려보낸다(self):
        html = self.html(self.group_url(),
                         obj=self.row["key"], img=self.row["image_id"])
        self.assertEqual(self.hl_json(html),
                         {"key": self.row["key"],
                          "image": str(self.row["image_id"])})

    def test_안_짚고_열면_아무것도_안_내려간다(self):
        """평소의 검토는 그대로다 — 표시는 링크를 타고 온 길에만 있다."""
        self.assertIsNone(self.hl_json(self.html(self.group_url())))

    def test_판을_안_주면_키만_간다(self):
        """판이 하나뿐인 시야에서는 `img` 가 없어도 찾을 수 있다."""
        html = self.html(self.group_url(), obj=self.row["key"])
        self.assertEqual(self.hl_json(html), {"key": self.row["key"],
                                              "image": ""})

    def test_숫자가_아닌_판은_버린다(self):
        """`img` 는 이미지의 pk 다. 아무 문자열이나 화면까지 흘려보내지 않는다."""
        html = self.html(self.group_url(), obj=self.row["key"], img="어쩌구")
        self.assertEqual(self.hl_json(html)["image"], "")

    # --- 3. 모르는 값이 화면을 못 열게 하지 않는다 --------------------------

    def test_없는_개체를_짚어도_시야는_열린다(self):
        """**적어 둔 링크는 낡는다.** 재검출로 키가 바뀌면 여기 걸리는데,
        그때 404 를 내면 링크 하나 때문에 시야를 아예 못 연다."""
        html = self.html(self.group_url(), obj="9999_9999_1_1")
        # 그래도 화면은 값을 그대로 내려보낸다 — "못 찾았다" 고 말하는 것은
        # 화면의 몫이다(어느 판에 무엇이 있는지는 화면이 들고 있다).
        self.assertEqual(self.hl_json(html)["key"], "9999_9999_1_1")
        self.assertIn("검출결과 검토", html)

    def test_너무_긴_키는_버린다(self):
        """`mask_key` 는 64자다. 그보다 긴 것은 이 DB 에 있을 수 없다."""
        html = self.html(self.group_url(), obj="9" * 65)
        self.assertIsNone(self.hl_json(html))

    def test_스크립트를_끊고_나올_수_없다(self):
        """`<script type="application/json">` 안에 사람이 준 문자열이 들어간다.

        `</script>` 를 그대로 흘리면 태그가 거기서 끝나고 **뒤가 마크업이 된다.**
        `json_dumps` 가 `<` 를 이스케이프하는지를 여기서 붙든다.
        """
        html = self.html(self.group_url(), obj="a</script><b>x")
        self.assertNotIn("a</script>", html, "태그가 거기서 끝났다")
        # 이스케이프된 채로 들어가 있다 — 값은 살아 있고 태그만 안 끊긴다
        self.assertIn("a\\u003c/script", html)
        self.assertEqual(self.hl_json(html)["key"], "a</script><b>x")
