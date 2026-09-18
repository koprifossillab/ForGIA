"""DiaRUGA v0.29.0 `tests/test_catalog_page.py` 에서 왔다.

개체 카탈로그 화면과 저장 (`/d/<슬러그>/catalog/`).

**URL 을 덮는 것과 갈래를 덮는 것은 다르다** (DiaRUGA 086). 자료를 전부 합성본으로 세우면
프레임 갈래를 한 번도 안 밟는데, `/crops/`·`/detections/` 가 정확히 그래서
**v0.8.0 이후 내내 500** 이었다. 그래서 여기서는 합성본 갈래와 프레임 갈래를
따로 연다 (`make_world(with_stack=False)` 가 그 반대쪽이다).

저장 쪽에서 지키는 것 셋.

1. **짚은 개체 하나만 고친다** — `/review` 처럼 범위를 갈아치우지 않는다
2. **현재 검출에 없는 키는 안 받는다** — 다른 화면을 보고 보낸 것이다
3. **읽기 전용은 저장을 막는 것으로 안 끝난다** — 화면이 되는 것처럼 보이면
   안 된다 (051 에서 그렇게 37건을 잃었다)
"""
import json

from django.test import Client
from django.urls import reverse

from . import factories as fx
from .base import ForGIATestCase
from .. import data
from ..models import (ForamObject, ObjectReview,
                      RunBatch)


class CatalogPageTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_viewpoints=2, n_candidates=3)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def setUp(self):
        self.c = Client()
        self.url = reverse("catalog", args=["rs23"])

    def get(self, **q):
        r = self.c.get(self.url, q)
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.content.decode()

    def test_열린다(self):
        html = self.get()
        self.assertIn("개체 카탈로그", html)
        self.assertIn("catcard", html)

    def test_번호가_화면에_나온다(self):
        html = self.get()
        no = data.catalog_rows("rs23")[0]["catalog_no"]
        self.assertIn(no, html)

    def test_어느_엔진의_판인지_적힌다(self):
        """번호의 꼬리가 그 코드라서, 적어 둔 번호만 보고도 어느 검출인지 안다."""
        html = self.get()
        self.assertIn("번호 꼬리 · S1", html)
        self.assertIn("yolo-시험", html)

    def test_없는_관찰은_404(self):
        self.assertEqual(self.c.get(reverse("catalog", args=["nope"])).status_code,
                         404)

    # --- 거르개·검색 --------------------------------------------------------

    def test_동정_안_한_것만_거른다(self):
        """**완료의 기준이 셋이다** (2026-08-11) — 종명만 적힌 것은 아직 "덜 된
        것" 이다. 진행도와 같은 기준을 쓴다."""
        row = data.catalog_rows("rs23")[0]
        o = fx.new_review(
            viewpoint_id=row["group_id"] and self.w.viewpoints[row["group_id"]].pk
            or self.w.vp.pk,
            image_id=row["image_id"], batch_id=row["batch_id"],
            mask_key=row["key"], bind_method="exact", species="Globigerina sp.")
        dobj = o.foram_object          # 둘 다 개체에 앉는다 (0035)
        dobj.grade, dobj.pose = "A", "umbilical"
        dobj.save()
        named = self.get(cls="named", frag="1")
        self.assertIn(row["catalog_no"], named)
        self.assertNotIn(row["catalog_no"], self.get(cls="unnamed", frag="1"))

    def test_번호로_찾는다(self):
        rows = data.catalog_rows("rs23")
        html = self.get(q=rows[0]["catalog_no"])
        self.assertIn(rows[0]["catalog_no"], html)
        self.assertNotIn(rows[-1]["catalog_no"], html)

    def test_대소문자를_안_가린다(self):
        no = data.catalog_rows("rs23")[0]["catalog_no"]
        self.assertIn(no, self.get(q=no.lower()))

    def test_종명으로_찾는다(self):
        row = data.catalog_rows("rs23")[0]
        fx.new_review(
            viewpoint=self.w.vp, image_id=row["image_id"],
            batch_id=row["batch_id"], mask_key=row["key"],
            bind_method="exact", species="Globigerina bulloides")
        self.assertIn(row["catalog_no"], self.get(q="bulloides"))

    def test_코멘트로_찾는다(self):
        row = data.catalog_rows("rs23")[0]
        fx.new_review(
            viewpoint=self.w.vp, image_id=row["image_id"],
            batch_id=row["batch_id"], mask_key=row["key"],
            bind_method="exact", note="가장자리가 넘쳤다")
        self.assertIn(row["catalog_no"], self.get(q="가장자리"))

    def test_못_찾으면_그렇다고_말한다(self):
        self.assertIn("찾은 개체가 없다", self.get(q="없는종명xyz"))

    # --- 파편은 기본으로 감춘다 (2026-08-11) --------------------------------

    def _frag_row(self):
        """첫 개체를 파편으로 지정한다 — 카드가 그렇게 그려진다."""
        row = data.catalog_rows("rs23")[0]
        fx.new_review(viewpoint_id=self.w.viewpoints[row["group_id"]].pk,
                      image_id=row["image_id"], batch_id=row["batch_id"],
                      mask_key=row["key"], bind_method="exact",
                      label="fragment")
        return row

    def test_파편은_기본으로_안_보인다(self):
        row = self._frag_row()
        self.assertNotIn(row["catalog_no"], self.get())

    def test_켜면_보인다(self):
        row = self._frag_row()
        self.assertIn(row["catalog_no"], self.get(frag="1"))

    def test_감춘_개수를_적는다(self):
        """**"없다" 와 "감췄다" 는 다르다.** 개수를 안 적으면 사람은 그 개체들이
        어디로 갔는지 알 수 없다.

        **픽스처가 이미 파편을 섞어 만든다**(`CLASSES[i % ...]`) — 세어서 맞춘다.
        "1개" 로 못 박으면 픽스처가 바뀔 때 조용히 다른 것을 보게 된다."""
        self._frag_row()
        n = sum(1 for r in data.catalog_rows("rs23")
                if data.is_fragment(r.get("cls")))
        self.assertGreater(n, 0)
        html = self.get()
        self.assertIn("파편 보기", html)
        self.assertIn(f"({n})", html)

    def test_켠_것이_검색과_거르개를_따라간다(self):
        """켠 것이 조용히 꺼지면 사람은 개체가 사라졌다고 읽는다 — 검색 폼은
        숨은 칸으로, 칩은 주소로 그것을 들고 간다."""
        html = self.get(frag="1")
        self.assertIn('name="frag" value="1"', html)      # 검색 폼이 들고 간다
        self.assertIn("&frag=1", html)                    # 칩이 들고 간다

    def test_파편_분류를_집으면_감추지_않는다(self):
        """그 칩을 누르고 빈 화면을 보면 사람은 자료가 없다고 읽는다 — 눌러서
        아무 일도 안 일어나는 화면을 만들지 않는다."""
        row = self._frag_row()
        self.assertIn(row["catalog_no"], self.get(cls="fragment"))

    def test_완형은_감추기와_무관하다(self):
        row = data.catalog_rows("rs23")[1]
        fx.new_review(viewpoint_id=self.w.viewpoints[row["group_id"]].pk,
                      image_id=row["image_id"], batch_id=row["batch_id"],
                      mask_key=row["key"], bind_method="exact", label="foram")
        self.assertIn(row["catalog_no"], self.get())

    # --- 진행도 -------------------------------------------------------------

    def _fill(self, i, **kw):
        row = data.catalog_rows("rs23")[i]
        o = fx.new_review(viewpoint_id=self.w.viewpoints[row["group_id"]].pk,
                          image_id=row["image_id"], batch_id=row["batch_id"],
                          mask_key=row["key"], bind_method="exact",
                          species=kw.get("species", ""),
                          label=kw.get("label", ""))
        # **둘 다 개체에 앉는다** (0035 로 등급이 판정에서 옮겨 왔다).
        if kw.get("grade") or kw.get("pose"):
            dobj = o.foram_object
            dobj.grade = kw.get("grade", "")
            dobj.pose = kw.get("pose", "")
            dobj.save()
        return row

    def ctx(self, **q):
        r = self.c.get(self.url, q)
        return r.context["n_named"], r.context["n_all"]

    def test_종명만으로는_완료가_아니다(self):
        """등급·자세까지 다 차야 완료다 (사용자 2026-08-11) — 진행도가 재려는
        것은 "할 말을 다 했는가" 이지 이름만 붙였는가가 아니다."""
        self._fill(0, species="Globigerina bulloides")
        self.assertEqual(self.ctx()[0], 0)

    def test_셋이_다_차면_완료다(self):
        self._fill(0, species="Globigerina bulloides", grade="A", pose="umbilical")
        self.assertEqual(self.ctx()[0], 1)

    def test_등급만_빠져도_완료가_아니다(self):
        self._fill(0, species="Globigerina bulloides", pose="umbilical")
        self.assertEqual(self.ctx()[0], 0)

    def test_파편은_분모에서_빠진다(self):
        """분모에 두면 진행률이 영영 100%에 못 닿는다 — 파편에는 등급·자세를
        안 매기므로 완료가 될 수 없다."""
        before = self.ctx()[1]
        self._frag_row()
        self.assertEqual(self.ctx()[1], before - 1)

    def test_분류가_없는_것은_분모에_남는다(self):
        """**모르는 것과 아닌 것은 다르다** — 아직 안 정했을 뿐이고, 정하면
        완형일 수 있다. 미분류를 파편으로 묶으면 분모에서 통째로 빠진다."""
        self.assertFalse(data.is_fragment(""))
        rows = data.catalog_rows("rs23")
        want = sum(1 for r in rows if not data.is_fragment(r.get("cls")))
        self.assertEqual(self.ctx()[1], want)
        self.assertLess(want, len(rows), "픽스처에 파편이 없어 시험이 무의미하다")

    def test_감추기가_진행도를_안_바꾼다(self):
        """감추는 것은 화면일 뿐이다. 켜고 끄는 것으로 숫자가 흔들리면 그 막대는
        아무것도 못 말한다."""
        self._frag_row()
        self.assertEqual(self.ctx(), self.ctx(frag="1"))

    def test_덜_된_것_거르개가_진행도와_같은_기준이다(self):
        """둘이 다르면 진행도가 "다 했다" 인데 거르개에 개체가 남는다."""
        row = self._fill(0, species="Globigerina bulloides")   # 등급·자세가 없다
        self.assertIn(row["catalog_no"], self.get(cls="unnamed"))
        self.assertNotIn(row["catalog_no"], self.get(cls="named"))


class FramePageTest(ForGIATestCase):
    """합성본이 없는 시야 — **086 이 난 갈래다.** URL 만 덮으면 안 밟힌다."""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2, with_stack=False)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def test_프레임_갈래도_열린다(self):
        r = Client().get(reverse("catalog", args=["rs23"]))
        self.assertEqual(r.status_code, 200, r.content[:300])
        html = r.content.decode()
        self.assertIn("catcard", html)
        self.assertIn("-f", data.catalog_rows("rs23")[0]["catalog_no"])


class SaveCatalogTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def setUp(self):
        self.c = Client()
        self.url = reverse("save_catalog", args=["rs23"])
        self.rows = data.catalog_rows("rs23")
        self.r0 = self.rows[0]

    def post(self, expect=200, row=None, **fields):
        row = row or self.r0
        p = {"gid": row["group_id"], "image": row["image_id"], "key": row["key"]}
        p.update(fields)
        r = self.c.post(self.url, data=json.dumps(p),
                        content_type="application/json")
        self.assertEqual(r.status_code, expect, r.content[:300])
        return json.loads(r.content)

    def shown(self, key):
        return next(x for x in data.catalog_rows("rs23") if x["key"] == key)

    # --- 적히는가 -----------------------------------------------------------

    def test_종명이_저장된다(self):
        self.post(species="Globigerina bulloides")
        self.assertEqual(self.shown(self.r0["key"])["species"],
                         "Globigerina bulloides")

    def test_유형과_코멘트도_저장된다(self):
        self.post(cls="broken", note="가장자리가 넘쳤다")
        r = self.shown(self.r0["key"])
        self.assertEqual((r["cls"], r["note"]), ("broken", "가장자리가 넘쳤다"))

    def test_안_보낸_칸은_안_고친다(self):
        """`None` 은 "안 고친다" 이고 `""` 는 "비운다" 다 — 둘을 같이 다루면
        카드가 안 보내는 칸을 저장이 지운다."""
        self.post(species="Globigerina bulloides", note="메모")
        self.post(cls="broken")
        r = self.shown(self.r0["key"])
        self.assertEqual((r["species"], r["note"]), ("Globigerina bulloides", "메모"))

    def test_빈_문자열은_비운다(self):
        self.post(species="Globigerina bulloides")
        self.post(species="")
        self.assertEqual(self.shown(self.r0["key"])["species"], "")

    def test_다_비우면_그_줄을_지운다(self):
        """표시가 사라진 행을 남기면 "교정 전체 초기화" 가 안 되고 그 행을 세는
        자리가 어긋난다 (`save_review` 와 같은 규칙)."""
        # **확인 표시를 걷고 본다** (DiaRUGA P18). 검토 완료가 통과분에 판정을 세우면서
        # `auto_confirmed` 를 붙이는데, 그것도 표시라 줄이 안 지워진다 —
        # 여기서 보려는 것은 *표시가 하나도 없을 때* 지우는가다. 사람이 완료
        # 전에 지정했다 물린 자리가 운영의 그 모양이다.
        ObjectReview.objects.filter(mask_key=self.r0["key"]).update(
            auto_confirmed=False)
        self.post(species="Globigerina bulloides")
        self.assertTrue(ObjectReview.objects.filter(mask_key=self.r0["key"]).exists())
        out = self.post(species="")
        self.assertFalse(out["kept"])
        self.assertFalse(ObjectReview.objects.filter(mask_key=self.r0["key"]).exists())

    # --- 옆 개체를 안 건드리는가 --------------------------------------------

    def test_짚은_개체_하나만_고친다(self):
        """**`/review` 와 다른 점이 이것이다.** 그쪽은 그 (이미지, 묶음) 의 교정
        전체를 갈아치운다 — 017·027·053 이 전부 그 줄에서 났다."""
        other = self.rows[1]
        self.post(row=other, species="Globorotalia sp.", cls="foram")
        self.post(species="Globigerina bulloides")
        r = self.shown(other["key"])
        self.assertEqual((r["species"], r["cls"]), ("Globorotalia sp.", "foram"))

    def test_새로_만든_줄이_기하를_들고_있다(self):
        """**모든 교정 행이 `geom` 을 스스로 든다** (DiaRUGA P02 §2.7). 없으면 검출기가
        바뀌었을 때 그릴 것이 없어지고 — 그것이 교정을 `Candidate` 에 안 매는
        이유다 — `check_db.py` 의 "교정이 기하를 갖고 있다" 가 그것을 센다.
        """
        self.post(species="Globigerina bulloides")
        o = ObjectReview.objects.get(mask_key=self.r0["key"])
        self.assertTrue(o.geom.get("bbox"), o.geom)
        self.assertTrue(o.geom.get("polygon"), o.geom)

    def test_삭제_되살림을_안_건드린다(self):
        """그것은 검토 화면이 하는 판단이다."""
        o = fx.new_review(
            viewpoint=self.w.vp, image_id=self.r0["image_id"],
            batch_id=self.r0["batch_id"], mask_key=self.r0["key"],
            bind_method="exact", removed=True)
        self.post(species="Globigerina bulloides")
        o.refresh_from_db()
        self.assertTrue(o.removed)

    # --- 받지 않는 것 -------------------------------------------------------

    def test_현재_검출에_없는_키는_409(self):
        out = self.post(409, key="9999_9999_10_10", species="Orbulina universa")
        self.assertIn("현재 검출에 없는", out["error"])

    def test_남의_이미지를_짚으면_409(self):
        """**조용히 대표 이미지에 안 앉힌다** — 사람이 보고 있던 것과 다른 자리에
        판단이 쌓인다 (`save_review` 와 같은 이유)."""
        w2 = fx.make_world(slug="rs23-b", n_candidates=2)
        # 카드가 개체 단위라 판정이 있어야 줄이 난다 (DiaRUGA P18).
        for _vp in w2.viewpoints:
            fx.review_done(_vp)
        other_img = data.catalog_rows("rs23-b")[0]["image_id"]
        out = self.post(409, image=other_img, species="Orbulina universa")
        self.assertIn("현재 검출이 없다", out["error"])
        self.assertFalse(ForamObject.objects.filter(taxon__name="Orbulina universa").exists())

    def test_모르는_유형은_409(self):
        out = self.post(409, cls="없는분류")
        self.assertIn("모르는 유형", out["error"])

    def test_모르는_시야는_409(self):
        out = self.post(409, gid=999, species="Orbulina universa")
        self.assertIn("모르는 시야", out["error"])
        self.assertFalse(ForamObject.objects.filter(taxon__name="Orbulina universa").exists())

    def test_GET_은_안_받는다(self):
        self.assertEqual(self.c.get(self.url).status_code, 405)


class SpreadLabelTest(ForGIATestCase):
    """**묶인 개체는 유형을 함께 받는다** (104 · 사용자 보고 2026-08-10).

    묶음은 "이 판들의 이것이 같은 개체다" 라는 말이므로 봉상인지 원형인지는
    **개체의 성질**이지 판의 성질이 아니다. 그런데 번지게 하는 코드가
    `save_review` 안에만 있어서, **카드에서 유형을 바꾸면 그 판에만 앉고 묶음의
    다른 프레임은 그대로였다.** 저장은 성공으로 보이고 묶음 안이 어긋난다 —
    학습 자료로 내보내면 같은 개체가 봉상이면서 원형인 표본이 된다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def setUp(self):
        self.c = Client()
        self.url = reverse("save_catalog", args=["rs23"])
        self.det = self.w.detection()
        self.key = self.w.keys()[0]
        self.frame_img = self.w.vp.images.filter(kind="frame").first()
        self.sib_key = "500_500_20_20"
        # **실제 저장이 쓰는 기하 모양이다** (`bbox_xywh`).
        rows = [
            fx.new_review(viewpoint=self.w.vp, image=self.det.image,
                          batch=self.det.batch, mask_key=self.key,
                          geom={"bbox_xywh": [10, 10, 30, 30],
                                "polygon": [10, 10, 40, 10, 40, 40, 10, 40]}),
            fx.new_review(viewpoint=self.w.vp, image=self.frame_img,
                          batch=self.det.batch, mask_key=self.sib_key,
                          geom={"bbox_xywh": [20, 20, 40, 40],
                                "polygon": [20, 20, 60, 20, 60, 60, 20, 60]}),
        ]
        fx.link_reviews(rows, rep=0)

    def post(self, expect=200, **fields):
        p = {"gid": self.w.vp.idx, "image": self.det.image_id, "key": self.key}
        p.update(fields)
        r = self.c.post(self.url, data=json.dumps(p),
                        content_type="application/json")
        self.assertEqual(r.status_code, expect, r.content[:300])
        return json.loads(r.content)

    def sib(self):
        return ObjectReview.objects.filter(image=self.frame_img,
                                           mask_key=self.sib_key).first()

    def test_유형이_묶음의_다른_판에도_앉는다(self):
        """**이 시험이 이 클래스의 이유다.**"""
        out = self.post(cls="broken")
        self.assertEqual(out["spread"], 1, out)
        self.assertEqual(self.sib().label, "broken")

    def test_물러도_함께_물린다(self):
        """지정을 물렀는데 다른 판에만 남아 있으면 묶음 안에서 어긋난다."""
        self.post(cls="broken")
        self.post(cls="")
        self.assertEqual(self.sib().label, "")

    def test_종명도_함께_앉는다(self):
        """**P12 에서 뒤집혔다.** 예전에는 "종명은 안 번진다" 가 규칙이었다 —
        분류만 번지게 해 놓고 종명은 판마다 따로 살았기 때문이다. 지금은 둘 다
        개체의 성질이라 같은 자리에 있고, **번질 것이 없다.**

        P12 가 "그때까지의 규칙 셋" 으로 적어 둔 것 중 하나가 이것이었다.
        """
        out = self.post(species="Globigerina bulloides")
        self.assertEqual(out["spread"], 1, out)
        self.assertEqual(self.sib().species, "Globigerina bulloides")

    def test_코멘트도_함께_앉는다(self):
        """**0036 에서 뒤집혔다.** 예전에는 "코멘트는 안 번진다" 가 규칙이었다 —
        판마다 하는 말("이 판에서는 초점이 안 맞는다")로 봤기 때문이다. 사람이
        실제로 적은 것은 그 개체에 대한 말이었고, 지금은 개체의 성질이라
        **번질 것이 없다** (종명이 P12 에서 지난 자리와 같다).

        `spread` 가 세어져야 한다 — 화면이 그 수로 "묶음 N장에 함께" 를 적고,
        서버가 안 세면 **다른 판의 화면이 옛 값을 들고 있다가 도로 써 넣는다.**
        """
        out = self.post(note="가장자리가 넘쳤다")
        self.assertEqual(out["spread"], 1, out)
        self.assertEqual(self.sib().note, "가장자리가 넘쳤다")

    def test_안_묶인_개체는_번질_곳이_없다(self):
        other = data.catalog_rows("rs23")[1]
        r = self.c.post(self.url, data=json.dumps(
            {"gid": other["group_id"], "image": other["image_id"],
             "key": other["key"], "cls": "foram"}),
            content_type="application/json")
        self.assertEqual(json.loads(r.content)["spread"], 0)


class ReadOnlyTest(ForGIATestCase):
    """**읽기 전용은 저장을 막는 것으로 끝나지 않는다** (DiaRUGA 051).

    화면이 되는 것처럼 보이면 안 된다 — 저장은 잠갔는데 도구가 살아 있어서 한
    시야를 헛검토하고 새로고침하면 판단이 통째로 사라졌다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2, state="processing")
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def test_왜_못_적는지_적혀_있다(self):
        """잠가 놓고 이유를 안 적으면 사람이 같은 일을 몇 번이고 다시 한다 (DiaRUGA 063)."""
        html = Client().get(reverse("catalog", args=["rs23"])).content.decode()
        self.assertIn("자동 처리가 아직 끝나지 않았습니다", html)

    def test_입력칸이_잠겨_있다(self):
        """**반응하는 자리를 전부 센다** — 종명·유형·코멘트 셋 다."""
        import re
        html = Client().get(reverse("catalog", args=["rs23"])).content.decode()
        tags = re.findall(r"<(?:input|select|textarea)\b[^>]*"
                          r'class="(?:species|cls|note)"[^>]*>', html)
        self.assertTrue(tags, "입력칸을 하나도 못 찾았다 — 시험이 헛돌고 있다")
        for t in tags:
            self.assertIn("disabled", t, t)

    def test_배선을_아예_안_건다(self):
        """`disabled` 만 걸고 배선을 남겨 두면 나중에 누가 그것을 떼는 순간
        조용히 저장이 나간다 — **막는 자리를 하나로 둔다.**"""
        html = Client().get(reverse("catalog", args=["rs23"])).content.decode()
        self.assertIn("var READONLY = true;", html)

    def test_서버가_다시_막는다(self):
        """화면에서 막는 것은 막는 것이 아니다 (DiaRUGA 063)."""
        row = data.catalog_rows("rs23")[0]
        r = Client().post(
            reverse("save_catalog", args=["rs23"]),
            data=json.dumps({"gid": row["group_id"], "image": row["image_id"],
                             "key": row["key"], "species": "Globorotalia inflata"}),
            content_type="application/json")
        self.assertEqual(r.status_code, 409)
        self.assertFalse(ForamObject.objects.filter(taxon__name="Globorotalia inflata").exists())


class NoCodeTest(ForGIATestCase):
    """묶음 코드가 없으면 번호를 못 만든다 — **무엇을 채워야 하는지 적는다.**"""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)
        RunBatch.objects.filter(for_review=True).update(code="")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def test_이유가_화면에_있다(self):
        html = Client().get(reverse("catalog", args=["rs23"])).content.decode()
        self.assertIn("카탈로그 코드가 비어 있어", html)

    def test_그때는_읽기_전용이다(self):
        """번호 없이 동정을 적으면 그 판단을 나중에 무엇으로 부를지가 없다."""
        html = Client().get(reverse("catalog", args=["rs23"])).content.decode()
        self.assertIn("var READONLY = true;", html)
