"""DiaRUGA v0.29.0 `tests/test_catalog_edit.py` 에서 왔다.

카탈로그의 넓힌 문 — 지우기·되살리기·일괄 (DiaRUGA P16 5절).

세 문이 `POST /d/<슬러그>/catalog/save` 하나에 `act` 로 갈려 있다. 여기서 지키는
것은 넷이다.

1. **지운 것은 기본 화면에서 사라지고, `?gone=1` 에서만 난다** — 되살릴 자리가
   없으면 지우기 단추는 되돌릴 수 없는 단추가 된다 (DiaRUGA P16 8절)
2. **되돌려서 표시가 안 남으면 그 줄을 지운다** — 사람이 그린 것·묶음 멤버는 남는다
3. **묶인 개체는 못 지운다** — 묶어 놓고 지우면 *오검출이면서 실재한다* 가 된다.
   그리고 **몇 장이 걸렸는지 말한다** (063 — 무엇을 먼저 치울지 모르면 다시 누른다)
4. **일괄은 하나가 걸려도 나머지가 저장된다** — 한 트랜잭션으로 묶으면 39장이
   함께 되돌아가고, 사람은 무엇이 걸렸는지 모른 채 전부 다시 한다

**되살려서 잡히는 것을 보고 나서 "있다" 고 말한다** (DiaRUGA 064).
"""
import json

from django.test import Client
from django.urls import reverse

from . import factories as fx
from .base import ForGIATestCase
from .. import data
from ..models import ForamObject, ObjectReview, RunBatch


class CatalogRemoveTest(ForGIATestCase):
    """지우기·되살리기 (`act=remove|restore`)."""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_viewpoints=1, n_candidates=3)
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

    def post(self, act, expect=200, key=None):
        p = {"act": act, "gid": self.w.vp.idx, "image": self.det.image_id,
             "key": key or self.key}
        r = self.c.post(self.url, data=json.dumps(p),
                        content_type="application/json")
        self.assertEqual(r.status_code, expect, r.content[:300])
        return json.loads(r.content)

    def keys_shown(self, **q):
        return [r["key"] for r in data.catalog_rows("rs23", **q)]

    def test_지우면_카탈로그에서_사라진다(self):
        self.assertIn(self.key, self.keys_shown())
        self.post("remove")
        self.assertNotIn(self.key, self.keys_shown())
        self.assertTrue(ObjectReview.objects.get(
            image=self.det.image_id, mask_key=self.key).removed)

    def test_처음_생기는_줄도_기하를_들고_앉는다(self):
        """**모든 교정 행이 `geom` 을 스스로 들고 있어야 한다** (DiaRUGA P02 §2.7 ·
        180 C1).

        `_catalog_target` 이 후보의 기하를 얹어 주는데 지우기 쪽만
        `update_fields=["removed"]` 로 저장해서 그 값이 안 써졌다 — 같은 함수가
        만든 행이 **부르는 쪽에 따라** 기하를 갖거나 못 갖는 상태였다. 기하 없는
        교정은 재검출로 후보를 잃는 순간 **폴리곤 없는 음성 표본**이 된다.
        """
        ObjectReview.objects.filter(image=self.det.image_id,
                                    mask_key=self.key).delete()
        self.post("remove")

        o = ObjectReview.objects.get(image=self.det.image_id,
                                     mask_key=self.key)
        self.assertTrue(o.geom, "지우기로 생긴 줄이 기하 없이 앉았다")
        self.assertTrue(o.geom.get("polygon"))
        self.assertEqual(len(o.geom.get("bbox") or []), 4)

    def test_지운_것은_gone_에서만_난다(self):
        self.post("remove")
        self.assertEqual(self.keys_shown(gone=True), [self.key])
        # **섞어서 내지 않는다** — 기본 화면의 카드 수가 안 늘어난다
        self.assertNotIn(self.key, self.keys_shown())

    def test_확인_표시가_있으면_되돌려도_줄이_남는다(self):
        """**검토 완료가 통과분에 남긴 표시도 표시다** (DiaRUGA P18).

        `auto_confirmed` 는 *엔진이 통과시킨 것을 사람이 확인했다* 는 기록이라
        (`confirm_kept`), 지웠다 되돌렸다고 없앨 것이 아니다.
        """
        self.post("remove")
        got = self.post("restore")
        self.assertTrue(got["kept"])
        self.assertTrue(ObjectReview.objects.filter(
            image=self.det.image_id, mask_key=self.key).exists())

    def test_되돌리면_다시_보이고_줄이_지워진다(self):
        # **확인 표시를 걷고 본다** (DiaRUGA P18). 검토 완료가 통과분에 판정을 세우면서
        # `auto_confirmed` 를 붙이는데, 그것도 표시라 줄이 안 지워진다 —
        # 여기서 보려는 것은 *표시가 하나도 없을 때* 지우는가다. 사람이 완료
        # 전에 지정했다 물린 자리가 운영의 그 모양이다.
        ObjectReview.objects.filter(
            image=self.det.image_id, mask_key=self.key).update(
                auto_confirmed=False)
        self.post("remove")
        got = self.post("restore")
        self.assertFalse(got["removed"])
        # 표시가 하나도 안 남았다 — 그 줄은 없어야 한다
        self.assertFalse(got["kept"])
        self.assertFalse(ObjectReview.objects.filter(
            image=self.det.image_id, mask_key=self.key).exists())
        # **카드도 함께 사라진다** (DiaRUGA P18). 표시가 하나도 없는 후보는 개체가 아니고
        # 개체가 아니면 카드가 없다 — 예전에는 후보가 곧 카드라 다시 나타났다.
        # 운영에서는 검토 완료가 확인 표시를 남기므로 이 자리에 잘 안 온다
        # (`test_확인_표시가_있으면_되돌려도_줄이_남는다` 가 그쪽이다).
        self.assertNotIn(self.key, self.keys_shown())

    def test_종명이_있으면_되돌려도_줄이_남는다(self):
        """되돌리기가 지우는 것은 **표시가 없는 줄**이지 사람이 적은 것이 아니다."""
        self.c.post(self.url, data=json.dumps(
            {"gid": self.w.vp.idx, "image": self.det.image_id,
             "key": self.key, "species": "Globigerina bulloides"}),
            content_type="application/json")
        self.post("remove")
        got = self.post("restore")
        self.assertTrue(got["kept"])
        row = ObjectReview.objects.get(image=self.det.image_id,
                                       mask_key=self.key)
        self.assertFalse(row.removed)
        self.assertEqual(row.foram_object.species, "Globigerina bulloides")

    def test_묶인_개체는_못_지운다(self):
        # **묶음은 판이 서로 달라야 한다** — 개체 하나에 이미지마다 한 줄이라는
        # 유일 제약이 있다(`(foram_object, image)`). 같은 판의 개체 둘을 묶는
        # 자료는 운영에 없다.
        frame = self.w.vp.images.filter(kind="frame").first()
        rows = [fx.add_review(self.w.vp, self.key, image=self.det.image),
                fx.new_review(viewpoint=self.w.vp, image=frame,
                              batch=self.det.batch, mask_key="500_500_20_20",
                              geom={"bbox_xywh": [500, 500, 20, 20],
                                    "polygon": [500, 500, 520, 500,
                                                520, 520, 500, 520]})]
        fx.link_reviews(rows)
        got = self.post("remove", expect=409)
        self.assertFalse(got["ok"])
        # **몇 장이 걸렸는지 말한다** — 무엇을 먼저 치울지 알 수 있어야 한다
        self.assertIn("2", got["error"])
        self.assertIn("묶음", got["error"])
        self.assertFalse(ObjectReview.objects.get(
            image=self.det.image_id, mask_key=self.key).removed)

    def test_현재_검출에_없는_키는_안_받는다(self):
        got = self.post("remove", expect=409, key="9999_9999_5_5")
        self.assertFalse(got["ok"])

    def test_모르는_act_는_400(self):
        r = self.c.post(self.url, data=json.dumps(
            {"act": "누가봐도아닌것", "gid": self.w.vp.idx,
             "image": self.det.image_id, "key": self.key}),
            content_type="application/json")
        self.assertEqual(r.status_code, 400)


class CatalogBulkTest(ForGIATestCase):
    """일괄 (`act=bulk`)."""

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
        self.url = reverse("save_catalog", args=["rs23"])
        self.rows = data.catalog_rows("rs23")

    def item(self, r):
        return {"gid": r["group_id"], "image": r["image_id"], "key": r["key"]}

    def post(self, items, expect=200, **fields):
        r = self.c.post(self.url, data=json.dumps(
            {"act": "bulk", "items": items, "fields": fields}),
            content_type="application/json")
        self.assertEqual(r.status_code, expect, r.content[:300])
        # 400 은 본문이 JSON 이 아니다 (`HttpResponseBadRequest`).
        return json.loads(r.content) if expect != 400 else None

    def now(self):
        """**`mask_key` 는 시야끼리 겹친다** — 좌표에서 나온 이름이라 같은 자리에
        잡힌 개체는 시야가 달라도 이름이 같다. `key` 만으로 표를 만들면 다른
        시야의 행이 덮어써서, **고치지도 않은 행을 보고 통과·실패를 말한다**
        (053 과 같은 줄이다).
        """
        return {(r["group_id"], r["key"]): r for r in data.catalog_rows("rs23")}

    def test_고른_것에_한_번에_앉는다(self):
        items = [self.item(r) for r in self.rows[:3]]
        got = self.post(items, cls="foram", grade="A")
        self.assertEqual(got["n_ok"], 3)
        now = self.now()
        for it in items:
            row = now[(it["gid"], it["key"])]
            self.assertEqual(row["cls"], "foram")
            self.assertEqual(row["grade"], "A")

    def test_하나가_걸려도_나머지는_저장된다(self):
        """**한 트랜잭션으로 묶지 않는다** — 39장이 함께 되돌아가면 안 된다."""
        good = self.item(self.rows[0])
        bad = {**self.item(self.rows[1]), "key": "9999_9999_5_5"}
        got = self.post([good, bad], cls="broken")
        self.assertEqual(got["n_ok"], 1)
        self.assertEqual(got["n"], 2)
        self.assertTrue(got["results"][0]["ok"])
        self.assertFalse(got["results"][1]["ok"])
        # **실패한 카드를 짚을 수 있어야 한다** — 화면이 그 카드에만 적는다
        self.assertEqual(got["results"][1]["key"], "9999_9999_5_5")
        self.assertEqual(self.now()[(good["gid"], good["key"])]["cls"], "broken")

    def test_파편에_등급을_함께_보내면_그것만_걸린다(self):
        """서버가 다시 검사한다 — 화면에서 막는 것은 막는 것이 아니다."""
        got = self.post([self.item(self.rows[0])], cls="fragment", grade="A")
        self.assertEqual(got["n_ok"], 0)
        self.assertFalse(got["results"][0]["ok"])

    def test_코멘트는_일괄로_안_간다(self):
        """같은 글을 여러 개체에 붙이는 칸이 아니다 (0036 · P16 3.3)."""
        r0 = self.rows[0]
        got = self.post([self.item(r0)], cls="foram", note="한꺼번에")
        self.assertEqual(got["n_ok"], 1)
        self.assertFalse(ForamObject.objects.filter(note="한꺼번에").exists())

    def test_고른_것이_없으면_400(self):
        self.post([], expect=400, cls="foram")

    def test_고칠_칸이_없으면_400(self):
        self.post([self.item(self.rows[0])], expect=400)

    def test_한_판보다_많이_못_보낸다(self):
        one = self.item(self.rows[0])
        self.post([one] * 121, expect=400, cls="foram")


class CatalogEditScreenTest(ForGIATestCase):
    """화면에 무엇이 놓이는가.

    **되는 것처럼 보이는 화면을 만들지 않는다** (DiaRUGA 051·063). 여기서 보는 것은
    단추가 있느냐 없느냐이고, 눌렀을 때 배선이 도는지는 브라우저 시험이 본다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_viewpoints=1, n_candidates=3)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        det = cls.w.detection()
        keys = cls.w.keys()
        # 묶음 하나 (판이 둘이어야 한다) · 지운 것 하나
        frame = cls.w.vp.images.filter(kind="frame").first()
        fx.link_reviews([
            fx.add_review(cls.w.vp, keys[0], image=det.image),
            fx.new_review(viewpoint=cls.w.vp, image=frame, batch=det.batch,
                          mask_key="500_500_20_20",
                          geom={"bbox_xywh": [500, 500, 20, 20],
                                "polygon": [500, 500, 520, 500,
                                            520, 520, 500, 520]})])
        fx.add_review(cls.w.vp, keys[1], image=det.image, removed=True)
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

    def test_카드에_지우기와_일괄_띠가_있다(self):
        html = self.get()
        self.assertIn("class=\"danger remove\"", html)
        self.assertIn("class=\"bulkbar\"", html)
        self.assertIn("class=\"pick\"", html)

    def test_쪽_전부_고르기_단추가_있고_몇_개인지_적는다(self):
        """**고르는 수단이 체크박스뿐이면 한 쪽에 120번을 누른다** (DiaRUGA 185).

        수를 함께 적는 것이 이 단추의 절반이다 — "전부" 가 거르개에 걸린 것
        전체인지 이 쪽인지를 사람이 누르기 전에 알아야 한다. 서버의 `bulk` 가
        한 쪽을 상한으로 두고 있어(DiaRUGA P16 5.2) 쪽 밖은 애초에 못 간다.
        """
        html = self.get()
        self.assertIn("class=\"chip pickall\"", html)
        n = html.count("class=\"catcard")
        self.assertGreaterEqual(n, 1, "카드가 없으면 이 시험은 아무것도 안 본다")
        self.assertIn(f"({n})", html)

    def test_묶음은_푸는_단추만_있다(self):
        """**묶는 단추는 없다** (DiaRUGA P16 3.2) — 묶을 상대가 이 화면에 없다."""
        html = self.get()
        self.assertIn("class=\"badgelink unlink\"", html)
        self.assertIn(reverse("save_link", args=["rs23", self.w.vp.idx]), html)

    def test_지운_화면에는_되살리기만_있다(self):
        html = self.get(gone="1")
        self.assertIn("되살린다</button>", html)
        # 지운 것을 또 지우는 단추도, 일괄도, 고르는 칸도 없다
        self.assertNotIn("class=\"danger remove\"", html)
        self.assertNotIn("class=\"bulkbar\"", html)
        self.assertNotIn("class=\"pick\"", html)
        # 고를 것이 없는 화면에 「전부 고르기」가 있으면 눌러서 아무 일도
        # 안 일어난다 — 이 화면이 파편 칩에서 이미 피한 자리다
        self.assertNotIn("class=\"chip pickall\"", html)

    def test_지운_화면은_파편을_안_감춘다(self):
        """**지운 것의 절반을 또 감추면 "지웠는데 없다" 가 된다.**

        기본 화면은 파편을 감춘다(2026-08-11) — 그 규칙이 그대로 걸리면
        파편으로 지운 개체는 되살릴 자리가 없다. 실제로 그렇게 만들었다가
        여기서 잡았다.
        """
        keys = self.w.keys()
        gone_key = keys[1]
        # 지운 그 개체가 파편이어야 이 시험이 뜻이 있다
        row = next(r for r in data.catalog_rows("rs23", gone=True)
                   if r["key"] == gone_key)
        self.assertTrue(data.is_fragment(row["cls"]),
                        "픽스처가 파편을 안 지웠다 — 이 시험은 아무것도 안 본다")
        self.assertIn(gone_key, self.get(gone="1"))
        # 파편 체크박스는 안 뜬다 — 눌러서 아무 일도 안 일어나는 자리를 안 만든다
        self.assertNotIn("파편 보기", self.get(gone="1"))

    def test_진행도_분모가_지운_화면에서_안_바뀐다(self):
        """**모수가 틀린 막대는 안 보는 것만 못하다.**"""
        r = self.c.get(self.url)
        r_gone = self.c.get(self.url, {"gone": "1"})
        for k in ("n_all", "n_named", "n_frag"):
            self.assertEqual(r.context[k], r_gone.context[k], k)

    def test_거르개가_지운_화면을_안_잃는다(self):
        """칩을 눌러도 지운 것을 보던 자리로 돌아온다 — 파편 체크박스와 같은 규칙."""
        html = self.get(gone="1")
        self.assertIn("gone=1", html)


class CatalogEditReadOnlyTest(ForGIATestCase):
    """**읽기 전용은 저장을 막는 것으로 끝나지 않는다** (DiaRUGA 051).

    여기서 보는 것은 서버 쪽이다 — 화면이 배선을 안 거는지는 브라우저 시험이 본다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        # **자동 처리가 안 끝난 슬라이드다** (`review_blocked`). 검토 대상을 다른
        # 묶음으로 옮기는 쪽으로 세우면 **카드가 한 장도 안 나와서** 이 시험이
        # 실패할 수 없게 된다 — 실제로 그렇게 짰다가 되살려 보고 잡았다(DiaRUGA 064).
        cls.w = fx.make_world(slug="rs23", n_viewpoints=1, n_candidates=2,
                              state="running")
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        # **줄을 뜨기 전에 해야 한다** — 순서를 바꾸면 `cls.rows` 가 비어
        # 이 시험이 실패할 수 없게 된다(064 가 겪은 그 자리다).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)
        cls.rows = data.catalog_rows("rs23")

    def setUp(self):
        self.c = Client()
        self.url = reverse("save_catalog", args=["rs23"])

    def body(self, **extra):
        r = self.rows[0]
        return json.dumps({"gid": r["group_id"], "image": r["image_id"],
                           "key": r["key"], **extra})

    def test_지우기가_막힌다(self):
        r = self.c.post(self.url, data=self.body(act="remove"),
                        content_type="application/json")
        self.assertEqual(r.status_code, 409)
        self.assertFalse(ObjectReview.objects.filter(removed=True).exists())

    def test_화면에_단추가_아예_안_놓인다(self):
        """**저장을 막는 것으로 끝나지 않는다** (DiaRUGA 051). 눌러 볼 자리가 없어야 한다."""
        html = self.c.get(reverse("catalog", args=["rs23"])).content.decode()
        # **카드가 있어야 이 시험이 뜻이 있다** — 없으면 늘 통과한다
        self.assertIn("class=\"catcard", html)
        # **표시가 아니라 요소를 짚는다** — "풀기" 는 CSS 주석에도 있어서
        # 그것으로 세면 늘 걸린다(실패할 수 없는 시험은 없는 것보다 나쁘다).
        for mark in ("class=\"danger remove\"", "class=\"bulkbar\"",
                     "class=\"pick\"", "class=\"badgelink unlink\"",
                     "class=\"chip pickall\""):
            self.assertNotIn(mark, html, mark)

    def test_일괄이_막힌다(self):
        """**슬라이드 층의 사정이라 요청 전체를 거절한다.**

        항목마다 다른 결과가 나올 수 있는 것(키를 못 짚는 것 따위)과 다르다 —
        검토가 안 열린 슬라이드는 어느 항목도 저장될 수 없으므로, 반쯤 저장한
        것처럼 보이게 두지 않는다. 화면이 배선을 안 거는 것(DiaRUGA 051)과 두 겹이다.
        """
        r0 = self.rows[0]
        r = self.c.post(self.url, data=json.dumps(
            {"act": "bulk",
             "items": [{"gid": r0["group_id"], "image": r0["image_id"],
                        "key": r0["key"]}],
             "fields": {"cls": "foram"}}),
            content_type="application/json")
        self.assertEqual(r.status_code, 409, r.content[:300])
        self.assertFalse(ForamObject.objects.filter(label="foram").exists())
