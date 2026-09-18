"""DiaRUGA v0.29.0 `tests/test_catalog_pane.py` 에서 왔다.

검토 화면 오른쪽 칸의 **개체 카탈로그** (DiaRUGA 183).

여기서 보는 것은 **화면이 그릴 재료가 실려 오는가**와 **번호가 카탈로그 화면과
같은 값인가**다. 번호는 논문·표에 적히는 것이라 두 화면이 다른 값을 내밀면
그때는 이미 되돌릴 수 없다 (`catalog.py` 머리말).

칸을 눌러 저장하는 것은 브라우저 겹이 본다 (`browser/test_catalog_pane.py`) —
`disabled` 와 배선은 렌더한 HTML 로는 안 보인다.
"""
import json

from django.test import Client
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from ..models import ForamObject, ObjectReview, RunBatch
from .. import data


class CatalogPaneDataTest(ForGIATestCase):
    """검출 payload 에 카탈로그 정보가 실려 오는가."""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        # **시야를 둘 세운다** — 아래 `test_시야_하나만_묻는다` 가 옆 시야의
        # 개체를 안 들고 오는지 보는데, 시야가 하나면 그 시험은 실패할 수가
        # 없다(064: 실패할 수 없는 시험은 없는 것보다 나쁘다).
        cls.w = fx.make_world(slug="rs23", n_viewpoints=2, n_frames=3,
                              n_candidates=3)
        # **번호는 묶음 코드가 있어야 선다** — 사람이 정하는 값이고, 없으면
        # `catalog_no_for` 가 그렇게 말한다(빈 값이 아니라 이유를 낸다).
        RunBatch.objects.filter(for_review=True).update(code="S1")
        cls.keys = cls.w.keys()
        # 첫 개체만 사람이 봤다 — 나머지는 아직 카드가 없는 자리다
        cls.row = fx.add_review(cls.w.vp, cls.keys[0], species="Globigerina sp.",
                                label="other")

    def cands(self):
        ctx = data.group_detail(self.w.slug, self.w.vp.idx)
        return {c["key"]: c for c in ctx["base_det"]["candidates"]}

    def test_개체가_있는_후보에만_번호가_붙는다(self):
        """**아직 아무도 안 본 후보에는 카드가 없다** (사용자 방침 2026-08-25).

        여기에 "번호가 될 값" 을 미리 실으면 화면이 **없는 카드를 있는 것처럼**
        말한다 — 그 자리에서 적으면 그때 생긴다.
        """
        rows = self.cands()
        mine = rows[self.keys[0]]
        self.assertTrue(mine["catalog_no"], "번호가 안 실렸다")
        self.assertEqual(mine["linked_n"], 1)
        self.assertTrue(mine["no_here"], "이 판이 앵커인데 남의 판이라고 한다")
        others = [c for k, c in rows.items() if k != self.keys[0]]
        self.assertTrue(others, "비교할 후보가 없다 — 시험이 헛돈다")
        for c in others:
            self.assertNotIn("catalog_no", c,
                             "카드가 없는 후보에 번호가 붙었다")

    def test_번호가_카탈로그_화면과_같다(self):
        """**규칙이 갈라지면 같은 개체가 두 자리에서 다른 번호를 받는다.**"""
        card = next(r for r in data.catalog_rows(self.w.slug)
                    if r["key"] == self.keys[0])
        self.assertEqual(self.cands()[self.keys[0]]["catalog_no"],
                         card["catalog_no"])

    def test_묶인_개체는_앵커의_번호를_함께_든다(self):
        """번호는 판정 하나의 이름이라 **다른 판의 것일 수 있다** (DiaRUGA P18).

        그때 화면이 "어느 판 기준" 이라고 적어야 하므로, 그 재료(`no_here`·
        `no_from`)가 실려야 한다.
        """
        extra = fx.add_frame_detections(self.w.vp)
        frame, frame_img, _det = extra[0]
        other = fx.new_review(viewpoint=self.w.vp, image=frame_img,
                              batch=self.row.batch, mask_key=self.keys[0],
                              bind_method="exact",
                              geom={"bbox": [40, 50, 60, 40]})
        # 합성본 줄(더 오래된 것)이 앵커다 — 프레임 판에서 보면 남의 번호다
        fx.link_reviews([self.row, other], rep=0)

        ctx = data.group_detail(self.w.slug, self.w.vp.idx)
        shot = ctx["shot_dets"][frame.name]
        c = next(c for c in shot["candidates"] if c["key"] == self.keys[0])
        self.assertEqual(c["linked_n"], 2)
        self.assertFalse(c["no_here"], "남의 판 번호인데 이 판 것이라고 한다")
        self.assertIsNone(c["no_from"], "앵커가 합성본인데 프레임이라고 한다")

    def test_시야_하나만_묻는다(self):
        """검토 화면이 슬라이드 전체의 개체를 훑으면 안 된다 (DiaRUGA 105).

        **옆 시야에도 개체를 세워 둔다** — 안 그러면 무엇을 걸러도 결과가 같아
        이 시험이 통과만 한다.
        """
        other = self.w.viewpoints[1]
        fx.add_review(other, self.w.keys(other)[0], species="Neogloboquadrina pachyderma")
        self.assertEqual(len(data.object_facts(self.w.slide)), 2,
                         "픽스처가 개체 둘을 안 세웠다")
        facts = data.object_facts(self.w.slide, viewpoint=self.w.vp)
        self.assertEqual(list(facts), [self.row.foram_object_id],
                         "옆 시야의 개체까지 들고 왔다")


class CatalogPaneRenderTest(ForGIATestCase):
    """화면에 칸이 놓이는가 · 걷은 것이 남아 있지 않은가."""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)

    def html(self):
        r = Client().get(reverse("group", args=[self.w.slug, self.w.vp.idx]))
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_칸이_놓인다(self):
        html = self.html()
        for mark in ('id="catbox-', 'id="catsp-', 'id="catcls-',
                     'id="catgrade-', 'id="catpose-', 'id="catnote-'):
            self.assertIn(mark, html, f"카탈로그 칸이 덜 놓였다: {mark}")

    def test_종명_자동완성_목록이_함께_간다(self):
        """카탈로그 카드와 **같은 목록**이다 — 같은 종을 두 가지로 적는 것을
        막는 자리라, 한쪽에만 있으면 그 화면에서만 갈린다."""
        fx.add_review(self.w.vp, self.w.keys()[0], species="Globigerina bulloides")
        self.assertIn("Globigerina bulloides", self.html())

    def test_시야_메모_상자가_없다(self):
        """183 에서 걷었다 — **DB 의 기록과 서버의 문은 그대로 둔다.**"""
        html = self.html()
        self.assertNotIn('id="gnote-', html)
        self.assertNotIn('"only": "note"', html)


class CatalogSaveAnswersTest(ForGIATestCase):
    """저장 응답이 **화면이 다시 그릴 것**을 들고 오는가 (DiaRUGA 183)."""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)
        RunBatch.objects.filter(for_review=True).update(code="S1")

    def save(self, key, **fields):
        r = Client().post(
            reverse("save_catalog", args=[self.w.slug]),
            data=json.dumps({"act": "save", "gid": self.w.vp.idx,
                             "image": self.w.detection().image_id,
                             "key": key, **fields}),
            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.json()

    def test_처음_적으면_번호와_개체_id_가_온다(self):
        """**그 순간 개체가 생긴다** (`_catalog_target`). 번호가 안 오면 칸은
        "아직 카드가 없습니다" 인 채로 남아 방금 만든 카드를 없는 것처럼
        말한다."""
        key = self.w.keys()[0]
        self.assertFalse(ObjectReview.objects.filter(mask_key=key).exists())
        j = self.save(key, species="Globigerina sp.")
        self.assertTrue(j["ok"])
        self.assertTrue(j["catalog_no"], "번호가 안 왔다")
        obj = ObjectReview.objects.get(mask_key=key).foram_object
        self.assertEqual(j["obj_id"], obj.pk)
        # 화면이 스스로 지어내지 않게 **서버가 만든 그 번호**여야 한다
        card = next(r for r in data.catalog_rows(self.w.slug)
                    if r["key"] == key)
        self.assertEqual(j["catalog_no"], card["catalog_no"])

    def test_다_비우면_카드가_없어졌다고_말한다(self):
        """표시가 하나도 안 남으면 그 줄을 지운다 (`_catalog_prune`) — 화면도
        그렇게 말해야 다음에 적을 때 개체를 다시 세운다."""
        key = self.w.keys()[0]
        self.save(key, species="Globigerina sp.")
        j = self.save(key, species="")
        self.assertFalse(j["kept"])
        self.assertEqual(j["catalog_no"], "")
        self.assertIsNone(j["obj_id"])
        self.assertFalse(ForamObject.objects.filter(viewpoint=self.w.vp)
                         .exists())
