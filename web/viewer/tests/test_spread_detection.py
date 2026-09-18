"""DiaRUGA v0.29.0 `tests/test_spread_detection.py` 에서 왔다.

검출 마스크를 **다른 판에도 앉힌다** (DiaRUGA P19 · 2026-08-25 사용자 지시).

검출이 한두 판에서만 훌륭하게 잡히는 일이 있다. P18 로 카드 하나가 개체 하나가
되면서 프레임에서만 잡힌 개체도 카탈로그에 나오는데, **멤버가 하나뿐이면 그
흐린 프레임이 곧 얼굴이다** — 152 가 얼굴을 사람 손에 돌려놨어도 고를 것이 없다.
잘 잡힌 마스크를 합성본에 앉히고 묶으면 얼굴이 선다.

되는 근거는 `_spread_drawn` 과 같다 — **같은 시야는 스테이지가 안 움직인다.**

여기서 지키는 것은 P19 6절이 적어 둔 넷이다.

1. **복제본의 키가 `MANUAL_KEY` 다** — 원본 키를 그대로 쓰면 `_save_drawn` 이
   `ValueError` 를 던져 **그 판의 다음 저장이 통째로 거절된다**
2. **복제본이 응답에 실린다** — 안 실으면 그 판으로 넘어가 저장하는 순간
   `/review` 가 "표시가 사라진 그린 개체" 로 보고 지운다
3. **이미 이 개체의 멤버가 있는 판에는 하나 더 안 앉힌다**
4. **이미 묶인 개체의 대표는 안 옮긴다** — 사람이 ★ 로 고른 것을 기계가 다시
   고르지 않는다
"""
import json

from django.test import Client
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from .. import data
from ..models import Candidate, Image, ObjectReview


class SpreadDetectionTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        # **판이 둘 이상인 시야여야 성립한다** (DiaRUGA P19 6절). 합성본 하나 +
        # 프레임 셋 — YOLO 로 갈아탄 뒤의 모습이다.
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)
        cls.extra = fx.add_frame_detections(cls.w.vp)

    def setUp(self):
        self.c = Client()
        self.stack = self.w.detection().image
        self.frames = [img for _f, img, _d in self.extra]
        self.rb = data.review_batch_id()

    # --- 거들 --------------------------------------------------------------

    def a_key(self, img):
        """그 판의 **살아 있는 통과 후보** 하나의 키."""
        c = (Candidate.objects
             .filter(detection__image=img, detection__is_current=True,
                     detection__run__batch_id=self.rb)
             .first())
        self.assertIsNotNone(c, f"{img.pk} 에 후보가 없다")
        return c.mask_key

    def post(self, img, key=None, expect=200):
        p = {"image": img.pk, "mask_key": key or self.a_key(img)}
        r = self.c.post(
            reverse("spread_detection", args=[self.w.slug, self.w.vp.idx]),
            data=json.dumps(p), content_type="application/json")
        self.assertEqual(r.status_code, expect, r.content[:400])
        return json.loads(r.content)

    def obj_of(self, img, key):
        return ObjectReview.objects.select_related("foram_object").get(
            image=img, batch_id=self.rb, mask_key=key).foram_object

    # --- 1. 복제본의 키 ----------------------------------------------------

    def test_복제본의_키가_손그림_규칙을_지킨다(self):
        """**여기가 가장 조용히 터진다** (DiaRUGA P19 4.1). 원본 키를 그대로 쓰면
        `_save_drawn` 이 `ValueError` 를 던져 그 판의 교정 전부가 안 들어간다."""
        src = self.frames[0]
        key = self.a_key(src)
        out = self.post(src, key)
        self.assertRegex(out["key"], data.MANUAL_KEY.pattern)
        self.assertNotEqual(out["key"], key)
        for row in ObjectReview.objects.filter(mask_key=out["key"]):
            self.assertIsNone(row.batch_id, "복제본은 batch=None 이어야 한다")
            self.assertEqual(row.source, "manual")

    def test_원본은_회차에_붙은_채로_남는다(self):
        """**원본은 안 건드린다** (DiaRUGA P19 3.1) — 잘 잡힌 그 판의 판정 그대로다."""
        src = self.frames[0]
        key = self.a_key(src)
        self.post(src, key)
        row = ObjectReview.objects.get(image=src, batch_id=self.rb,
                                       mask_key=key)
        self.assertEqual(row.batch_id, self.rb)

    # --- 2. 응답에 실리는가 ------------------------------------------------

    def test_복제본이_응답에_실린다(self):
        """**반쪽으로 넣으면 자료를 잃는다** (DiaRUGA P19 4.2) — 화면이 자기 상태에
        얹지 못하면 그 판의 다음 저장이 복제를 지운다."""
        src = self.frames[0]
        out = self.post(src)
        self.assertEqual(out["n"], 3, "합성본 + 나머지 프레임 둘")
        self.assertEqual(set(out["spread"]),
                         {str(i.pk) for i in
                          [self.stack] + self.frames[1:]})
        for items in out["spread"].values():
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["key"], out["key"])
            self.assertTrue(items[0]["geom"])

    def test_기하는_서버가_뜬다(self):
        """화면이 보낸 것을 안 믿는다 — 후보의 bbox 로 앉는다.

        **칸 이름은 `bbox` 다** (2026-09-03). `ObjectReview.geom` 의 표준이
        그것이고, `bbox_xywh` 로 적으면 읽는 쪽이 전부 못 읽는다 — 아래 시험이
        그 결과를 잰다.
        """
        src = self.frames[0]
        key = self.a_key(src)
        cand = Candidate.objects.get(detection__image=src,
                                     detection__is_current=True,
                                     detection__run__batch_id=self.rb,
                                     mask_key=key)
        out = self.post(src, key)
        got = next(iter(out["spread"].values()))[0]["geom"]
        self.assertEqual(got["bbox"],
                         [cand.bbox_x, cand.bbox_y, cand.bbox_w, cand.bbox_h])

    def test_앉힌_마스크가_그_판의_화면에_나온다(self):
        """**모양이 어긋나면 조용히 없어진다** (실사용 rs23 g11 · 2026-09-03).

        `_orphan_dict` 가 기하를 못 읽으면 `None` 을 돌려주어 그 개체가 화면에
        안 그려지고, 화면은 자기가 아는 키만 보내므로 **다음 저장의 청소 줄이
        그 행을 지운다.** 예외도 경고도 없다 — 앉힌 마스크가 사라질 뿐이다.

        상자 넷을 함께 본다: `[0, 0, 1, 1]` 로 앉으면 화면에는 나오지만 왼쪽 위
        구석의 점 하나가 된다(`addDrawn` 의 되돌림 값이 그것이다).
        """
        src = self.frames[0]
        key = self.a_key(src)
        cand = Candidate.objects.get(detection__image=src,
                                     detection__is_current=True,
                                     detection__run__batch_id=self.rb,
                                     mask_key=key)
        out = self.post(src, key)
        new_key = out["key"]

        for img_id in out["spread"]:
            with self.subTest(image=img_id):
                d = data.detection_for_viewpoint(self.w.vp, int(img_id))
                me = next((c for c in d["candidates"]
                           if data.cand_key(c) == new_key), None)
                self.assertIsNotNone(me, "앉힌 마스크가 그 판에 안 나온다")
                self.assertEqual(me["bbox_xywh"],
                                 [cand.bbox_x, cand.bbox_y,
                                  cand.bbox_w, cand.bbox_h])

    # --- 3. 이미 멤버가 있는 판 --------------------------------------------

    def test_이미_이_개체의_멤버가_있는_판에는_안_앉힌다(self):
        """**짐작이 아니라 사실이다** (DiaRUGA P19 4.3). 안 거르면 한 판에 같은 개체의
        마스크가 둘 선다."""
        src = self.frames[0]
        self.post(src)                       # 판 셋에 앉는다
        n_before = ObjectReview.objects.count()
        # 두 번째 누름 — 이제 모든 판에 이 개체의 멤버가 있다
        out = self.post(src, expect=400)
        self.assertIn("앉힐 판이 없다", out["error"])
        self.assertEqual(ObjectReview.objects.count(), n_before,
                         "행이 하나도 더 생기면 안 된다")

    def test_한_판에_같은_개체가_둘_서지_않는다(self):
        src = self.frames[0]
        self.post(src)
        obj = self.obj_of(src, self.a_key(src))
        seen = [r.image_id for r in obj.members.all()]
        self.assertEqual(len(seen), len(set(seen)), "판마다 멤버는 하나다")

    # --- 4. 대표 -----------------------------------------------------------

    def test_혼자인_개체는_얼굴이_합성본으로_간다(self):
        """**이 기능의 목적이 그것이다** — 흐린 프레임을 얼굴로 두지 않는다."""
        src = self.frames[0]
        self.post(src)
        obj = self.obj_of(src, self.a_key(src))
        rep = obj.members.get(is_rep=True)
        self.assertEqual(rep.image_id, self.stack.pk)

    def test_이미_묶인_개체는_대표를_안_옮긴다(self):
        """**사람이 고른 것을 기계가 다시 고르지 않는다** (152 · 사용자 방침).

        멤버가 둘 이상이라는 것은 사람이 ★ 로 골랐다는 뜻이다.
        """
        a, b = self.frames[0], self.frames[1]
        rows = [ObjectReview.objects.get_or_create(
                    viewpoint=self.w.vp, image=i, batch_id=self.rb,
                    mask_key=self.a_key(i),
                    defaults={"foram_object": data.judgement_for(
                        self.w.vp, i, self.rb, self.a_key(i)).foram_object})[0]
                for i in (a, b)]
        fx.link_reviews(rows, rep=0)         # 프레임 a 를 사람이 대표로 골랐다
        obj = self.obj_of(a, self.a_key(a))
        self.assertEqual(obj.members.get(is_rep=True).image_id, a.pk)

        self.post(a)                          # 남은 판(합성본 · 프레임 셋째)에 앉힌다
        obj.refresh_from_db()
        self.assertEqual(obj.members.get(is_rep=True).image_id, a.pk,
                         "묶여 있던 개체의 대표가 합성본으로 옮겨가면 안 된다")

    # --- 앉힌 뒤 그 판에서 저장한다 (157 이 잡은 고장) --------------------

    def test_앉힌_판에서_저장해도_되번지지_않는다(self):
        """**`(개체, 이미지)` 유일 제약을 어겨 저장이 500 이었다** (DiaRUGA 157).

        앉히고 나면 개체가 **판정 하나(원본 회차) + 그린 마스크 여럿(복제본)**
        인 섞인 모양이 된다. 그 복제본이 있는 판에서 저장하면 옛 번지기
        (`_spread_drawn`)가 **원본이 앉아 있는 판으로 되번져** 한 이미지에 멤버
        둘을 만들려 든다.

        **P19 전에는 이 갈래가 안 났다** — 그린 마스크만 번질 때는 개체의 멤버가
        곧 번진 판들이라 `judgement_for` 가 늘 있는 행을 돌려줬다.
        """
        from pathlib import Path as _P

        src = self.frames[0]
        out = self.post(src)
        new_key = out["key"]
        row = ObjectReview.objects.filter(mask_key=new_key).first()
        n_before = ObjectReview.objects.filter(
            viewpoint=self.w.vp, batch__isnull=True).count()
        self.assertEqual(n_before, 3)

        # 복제본이 앉은 판 하나를 열고 그대로 저장한다 (화면이 늘 전체를 보낸다)
        tgt = self.stack
        p = {"stem": _P(tgt.path).stem, "image": tgt.pk, "slug": self.w.slug,
             "gid": self.w.vp.idx, "done": False, "removed": [],
             "accepted": [], "labels": {}, "notes": {},
             "drawn": [{"key": new_key, "polygon": row.geom["polygon"],
                        "cls": ""}]}
        r = self.c.post(reverse("save_review"), data=json.dumps(p),
                        content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content[:400])
        self.assertEqual(
            ObjectReview.objects.filter(viewpoint=self.w.vp,
                                        batch__isnull=True).count(), n_before,
            "저장 한 번에 복제본이 늘었다 — 되번진 것이다")
        # 한 이미지에 멤버 둘이 서면 안 된다
        obj = row.foram_object
        seen = [m.image_id for m in obj.members.all()]
        self.assertEqual(len(seen), len(set(seen)))

    # --- 서버가 다시 검사한다 ----------------------------------------------

    def test_남의_시야_이미지는_거절한다(self):
        other = fx.make_world(slug="rs23-b", site_code="RS23",
                              sample_code="99cm", depth_cm=99.0)
        out = self.post(other.detection().image, key="10_10_50_50", expect=400)
        self.assertIn("이 시야의 것이 아니다", out["error"])

    def test_검출_마스크가_아니면_거절한다(self):
        out = self.post(self.frames[0], key="m7f3a91c2", expect=400)
        self.assertIn("검출 마스크가 아니다", out["error"])

    def test_지운_마스크는_거절한다(self):
        """"이 개체는 오검출이면서 실재한다" 가 되면 안 된다."""
        src = self.frames[0]
        key = self.a_key(src)
        row = data.judgement_for(self.w.vp, src, self.rb, key)
        row.removed = True
        row.save(update_fields=["removed"])
        out = self.post(src, key, expect=400)
        self.assertIn("오검출로 지운", out["error"])
