"""DiaRUGA v0.29.0 `tests/test_edit_geom.py` 에서 왔다.

엔진이 낸 마스크를 사람이 고친다 (DiaRUGA P09 4단계 — 서버).

**`Candidate` 는 안 건드린다.** 고친 기하는 교정 행의 `geom` 에만 산다 —
검출 이력에서 엔진이 낸 것과 사람이 손댄 것을 못 가르게 되면 회차 비교가
무의미해지고, 재검출하면 어차피 사라진다 (DiaRUGA P09 5.6).

`geom_edited` 는 **회차별 수렴 지표**다. "사람이 손댄 비율" 이 줄면 그것이
수렴이고, 기하만 조용히 덮으면 그 수를 못 센다 (DiaRUGA P09 1).

여기서 지키는 것 셋.

1. **키가 안 바뀐다** — bbox 가 바뀌는데 키가 따라 바뀌면 옛 행이 지워지고
   새 행이 서서 분류·코멘트·이력이 끊긴다
2. **지표를 다시 잰다** — 안 재면 옛 모양의 면적·장축이 새 마스크 옆에 적힌다
3. **되돌릴 수 있다** — 빈 폴리곤이 "엔진 것으로" 다
"""
import json

from django.test import Client
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from .. import data
from ..models import ForamObject, ObjectReview

# 픽스처 첫 개체는 (40,50,60,40). 그것을 절반으로 줄인 모양.
TIGHT = [50, 60, 80, 60, 80, 80, 50, 80]


class EditGeomTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)

    def setUp(self):
        self.c = Client()
        self.key = self.w.keys()[0]

    def post(self, expect=200, **over):
        p = {"stem": self.w.stem(), "slug": self.w.slug, "gid": self.w.vp.idx,
             "done": False, "removed": [], "accepted": [],
             "labels": {}, "notes": {}}
        p.update(over)
        r = self.c.post(reverse("save_review"), data=json.dumps(p),
                        content_type="application/json")
        self.assertEqual(r.status_code, expect, r.content[:300])
        return r

    def shown(self, key=None):
        d = data.detection_for_viewpoint(self.w.vp)
        key = key or self.key
        return next((c for c in d["candidates"] + d["removed_candidates"]
                     if data.cand_key(c) == key), None)

    # --- 고쳐지는가 --------------------------------------------------------

    def test_고친_기하가_저장된다(self):
        self.post(edits={self.key: TIGHT})
        o = ObjectReview.objects.get(mask_key=self.key)
        self.assertTrue(o.geom_edited)
        self.assertEqual(o.geom["polygon"], TIGHT)
        self.assertEqual(o.geom["bbox"], [50, 60, 30, 20])

    def test_Candidate_는_안_바뀐다(self):
        """**검출 이력이 사람 손을 타면 안 된다** (DiaRUGA P09 5.6)."""
        before = self.w.detection().candidates.get(mask_key=self.key)
        was = (list(before.polygon), before.bbox_x, before.bbox_y,
               before.area_px)
        self.post(edits={self.key: TIGHT})
        after = self.w.detection().candidates.get(mask_key=self.key)
        self.assertEqual((list(after.polygon), after.bbox_x, after.bbox_y,
                          after.area_px), was)

    def test_키가_안_바뀐다(self):
        """bbox 가 바뀌는데 키가 따라가면 **옛 행이 지워지고 새 행이 생긴다** —
        분류·코멘트·이력이 끊긴다.

        코멘트는 개체에 산다(0036) — 검토 화면이 안 보내는 칸이라 **기하를
        고치는 저장이 그것을 데리고 가면 안 된다.**
        """
        self.post(labels={self.key: "broken"})
        o = ObjectReview.objects.get()
        ForamObject.objects.filter(pk=o.foram_object_id).update(
            note="가장자리가 넘쳤다")
        self.post(edits={self.key: TIGHT}, labels={self.key: "broken"})

        self.assertEqual(ObjectReview.objects.count(), 1)
        o = ObjectReview.objects.get()
        self.assertEqual(o.mask_key, self.key, "키가 바뀌었다")
        self.assertEqual(o.label, "broken")
        self.assertEqual(o.note, "가장자리가 넘쳤다")

    def test_저장_직후에_안_지워진다(self):
        """고친 기하도 **표시**다. `keys` 에 안 들어가면 같은 저장의 마지막 줄이
        "표시가 사라진 행" 으로 보고 지운다."""
        self.post(edits={self.key: TIGHT})
        self.assertTrue(ObjectReview.objects.filter(mask_key=self.key).exists())

    # --- 화면이 고친 것을 보여주는가 ---------------------------------------

    def test_화면이_고친_기하를_그린다(self):
        self.post(edits={self.key: TIGHT})
        c = self.shown()
        self.assertEqual(c["polygon"], TIGHT)
        self.assertEqual(c["bbox_xywh"], [50, 60, 30, 20])
        self.assertTrue(c["geom_edited"])

    def test_지표를_다시_잰다(self):
        """안 재면 **옛 모양의 면적·장축이 새 마스크 옆에 적힌다.**"""
        before = self.shown()
        self.post(edits={self.key: TIGHT})
        after = self.shown()
        self.assertNotEqual(after["area_px"], before["area_px"])
        self.assertEqual(after["area_px"], 30 * 20)
        self.assertIsNotNone(after["area_um2"])

    def test_텍스처는_엔진_값을_그대로_둔다(self):
        """픽셀이 있어야 나오는 값이라 여기서 못 잰다 — 버리면 정보만 잃는다.
        섞였다는 사실은 `geom_edited` 가 말한다."""
        before = self.shown()
        self.post(edits={self.key: TIGHT})
        self.assertEqual(self.shown()["texture"], before["texture"])

    # --- 되돌릴 수 있는가 --------------------------------------------------

    def test_빈_폴리곤은_엔진_것으로_되돌린다(self):
        self.post(edits={self.key: TIGHT})
        self.post(edits={self.key: []})

        o = ObjectReview.objects.get(mask_key=self.key)
        self.assertFalse(o.geom_edited)
        cand = self.w.detection().candidates.get(mask_key=self.key)
        self.assertEqual(o.geom["polygon"], list(cand.polygon))
        c = self.shown()
        self.assertEqual(c["polygon"], list(cand.polygon))
        self.assertIsNone(c.get("geom_edited"))

    def test_되돌릴_엔진_개체가_없으면_거절한다(self):
        """고아에는 되돌릴 원본이 없다 — 지우면 그릴 것이 없어진다."""
        det = self.w.detection()
        fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=det.batch,
            mask_key="900_900_50_50", bind_method="orphan",
            geom={"bbox": [900, 900, 50, 50],
                  "polygon": [900, 900, 950, 900, 950, 950]})
        self.post(expect=409, edits={"900_900_50_50": []})

    # --- 못 받을 것은 오류로 ----------------------------------------------

    def test_점이_모자라면_거절한다(self):
        self.post(expect=409, edits={self.key: [50, 60, 80, 60]})
        self.assertEqual(ObjectReview.objects.count(), 0)

    def test_이미지_밖이면_거절한다(self):
        far = [9000, 9000, 9060, 9000, 9060, 9040, 9000, 9040]
        self.post(expect=409, edits={self.key: far})
        self.assertEqual(ObjectReview.objects.count(), 0)

    def test_그리기와_같은_검사를_지난다(self):
        """검사가 갈라지면 **한쪽으로만 들어오는 값**이 생긴다."""
        bad = [50, 60, 80, 60]
        self.post(expect=409, edits={self.key: bad})
        self.post(expect=409, drawn=[{"key": "m0a1b2c3", "polygon": bad,
                                      "cls": "", "note": ""}])

    # --- 화면을 나갔다 들어온 뒤 -------------------------------------------

    def test_화면이_전체를_보내면_둘_다_남는다(self):
        """**`edits` 는 전체 상태다** — `drawn` 과 같은 규칙.

        사용자가 미리보기에서 잡은 것이 이 갈래였다: "다른 마스크를 수정하고
        나갔다 들어오면 앞의 것이 되어 있는 경우도 있고 아닌 경우도 있다."
        화면이 **이번에 고친 것만** 보내고 있었고, `save_review` 는 `keys` 에
        없는 행을 지운다. 분류·코멘트가 함께 붙어 있으면 `labels`·`notes` 를
        타고 살아남아서 **되는 경우와 안 되는 경우가 갈렸다.**
        """
        k0, k1 = self.w.keys()[0], self.w.keys()[1]
        other = [170, 140, 200, 140, 200, 170, 170, 170]
        self.post(edits={k0: TIGHT})
        # 화면이 아는 전부 — 앞서 고친 것까지 함께 보낸다
        self.post(edits={k0: TIGHT, k1: other})

        self.assertTrue(
            ObjectReview.objects.filter(mask_key=k0, geom_edited=True).exists(),
            "앞서 고친 마스크가 사라졌다")
        self.assertTrue(
            ObjectReview.objects.filter(mask_key=k1, geom_edited=True).exists())

    def test_판이_줄어도_그_판을_계속_저장할_수_있다(self):
        """**안 바뀐 값을 매번 다시 재면 지난 판단이 인질이 된다** (180 A2).

        화면은 고친 기하를 매번 전부 싣고 서버는 그 전부를 다시 검사했다.
        저장될 때는 통과한 값이라도 **`--scale` 을 빠뜨린 재검출**로 검출의
        크기가 절반이 되면 전부 밖이 되고, 그때부터 그 판은 **새로고침해도**
        아무것도 저장할 수 없다 — 값이 DB 에서 다시 오기 때문이다.

        검사가 막으려는 것은 새로 들어오는 나쁜 값이다.
        """
        k0, k1 = self.w.keys()[0], self.w.keys()[1]
        self.post(edits={k0: TIGHT})

        # `--scale` 을 빠뜨린 재검출의 모양 — 검출의 크기가 절반이 된다.
        # 고쳐 둔 기하(`TIGHT`)가 그 크기 밖으로 나가게 잡는다.
        det = self.w.detection()
        det.width, det.height = 60, 70
        det.save(update_fields=["width", "height"])

        # 화면이 아는 전부를 그대로 다시 보낸다 — 안 바뀐 값이다
        self.post(edits={k0: TIGHT})
        self.assertTrue(
            ObjectReview.objects.filter(mask_key=k0, geom_edited=True).exists(),
            "안 바뀐 기하 때문에 그 판이 통째로 막혔다")

        # **새로 고치는 것은 여전히 검사한다** — 막으려던 것은 이쪽이다
        far = [9000, 9000, 9060, 9000, 9060, 9040, 9000, 9040]
        self.post(expect=409, edits={k0: TIGHT, k1: far})

    def test_edits_가_없으면_고친_것을_안_지운다(self):
        """**고치기를 모르는 옛 화면**이다 — 배포 중에 열려 있던 탭이 그렇고,
        그 저장 한 번이 사람이 고친 기하를 전부 지우면 안 된다.

        `drawn` 과 같은 규칙이다: 없는 것과 빈 것은 다른 말이다.
        """
        self.post(edits={self.key: TIGHT})
        self.post(done=True)                    # edits 를 아예 안 보낸다
        self.assertTrue(
            ObjectReview.objects.filter(mask_key=self.key,
                                        geom_edited=True).exists(),
            "옛 탭의 저장이 고친 기하를 지웠다")
