"""DiaRUGA v0.29.0 `tests/test_drawn_spread.py` 에서 왔다.

그린 마스크가 같은 시야의 모든 판에 번진다 (106 2단계 · P12 위에서 새로).

**되는 근거는 촬영 방식이다** — 같은 시야는 스테이지가 안 움직이고 초점만
다르므로 좌표가 그대로 맞는다. 기계가 "같은 개체 같다" 고 판정하는 것이 아니다.

**왜 이 시험이 있어야 하나.** YOLO 로 갈아타면 시야에 판이 여럿이라, 엔진이
놓친 개체 하나를 넣으려면 사람이 **판마다 똑같은 마스크를 다시 그려야** 했다.
번지기는 그 일을 한 번으로 줄인다.

**반쪽으로 넣으면 자료를 잃는다.** 복제한 마스크는 다른 판의 화면이 모르고,
`/review` 는 payload 에 없는 그린 개체를 지운다 — 프레임 3에 그리고 프레임 5로
넘어가 저장하면 복제가 그 자리에서 사라진다. 그래서 서버가 번진 것을 응답에
싣고(`drawn_spread`) 화면이 자기 상태에 얹는다. 여기서는 **서버 쪽**을 본다.
화면 쪽은 `tests/browser/test_drawn_spread.py` 다.
"""
import json
from pathlib import Path

from django.test import Client
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from ..models import ForamObject, ObjectReview

POLY = [400, 300, 460, 300, 460, 340, 400, 340]
KEY = "m7f3a91c2"


class DrawnSpreadTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        # 프레임마다 현재 검출이 있는 시야 — YOLO 로 갈아탄 뒤의 모습이다.
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)
        cls.extra = fx.add_frame_detections(cls.w.vp)

    def setUp(self):
        self.c = Client()
        self.stack = self.w.detection().image
        self.frames = [img for _f, img, _d in self.extra]

    def post(self, image=None, expect=200, **over):
        """**판을 짚으려면 `image` 를 함께 보낸다.** `stem` 만으로는 안 된다 —
        `find_viewpoint` 가 시야의 현재 검출로 되돌리므로 프레임에 보낸 저장이
        합성본으로 간다 (`test_review_image.py` 의 그 갈래)."""
        p = {"stem": self.w.stem(), "slug": self.w.slug,
             "gid": self.w.vp.idx, "done": False, "removed": [],
             "accepted": [], "labels": {}, "notes": {}}
        if image is not None:
            p["stem"] = Path(image.path).stem
            p["image"] = image.pk
        p.update(over)
        r = self.c.post(reverse("save_review"), data=json.dumps(p),
                        content_type="application/json")
        self.assertEqual(r.status_code, expect, r.content[:400])
        return json.loads(r.content)

    def draw(self, key=KEY, cls="broken", note=""):
        return {"key": key, "polygon": POLY, "cls": cls, "note": note}

    def rows(self, key=KEY):
        return list(ObjectReview.objects.filter(mask_key=key, batch__isnull=True)
                    .select_related("foram_object").order_by("image_id"))

    # --- 번지는가 ----------------------------------------------------------

    def test_그린_마스크가_모든_판에_앉는다(self):
        """판 넷(합성본 + 프레임 셋) 전부에 행이 생겨야 한다."""
        self.post(drawn=[self.draw()])
        got = {r.image_id for r in self.rows()}
        want = {self.stack.pk} | {f.pk for f in self.frames}
        self.assertEqual(got, want, "번지지 않았다")

    def test_복제도_사람이_그린_것으로_남는다(self):
        """`source="manual"` 이라야 학습에서 양성 표본으로 쓰이고, 다음 회차에
        "여기 개체 없다" 를 가르치지 않는다 (DiaRUGA P09 5.10)."""
        self.post(drawn=[self.draw()])
        for r in self.rows():
            self.assertEqual((r.source, r.bind_method), ("manual", "manual"))
            self.assertEqual(r.geom["polygon"], POLY)

    def test_판들이_한_개체를_나눠_갖는다(self):
        """**같은 개체을 옮겨 그린 것**이라 뜻이 그렇다. 판마다 개체를 따로
        두면 자세를 판 수만큼 매겨야 하고 `check_db` 10번이 못 잡는다 (DiaRUGA 110)."""
        self.post(drawn=[self.draw()])
        objs = {r.foram_object_id for r in self.rows()}
        self.assertEqual(len(objs), 1, f"개체가 갈렸다: {objs}")

    def test_대표는_합성본이다(self):
        self.post(drawn=[self.draw()])
        reps = [r for r in self.rows() if r.is_rep]
        self.assertEqual(len(reps), 1, "대표가 하나가 아니다")
        self.assertEqual(reps[0].image_id, self.stack.pk)

    def test_프레임에서_저장해도_대표는_합성본이다(self):
        """**실사용에서 난 자리다** (2026-08-13 · AM22-GC10B 25cm g028).

        예전에는 `src`(= 저장할 때 열려 있던 판)를 대표로 삼았다. 08-09 에
        합성본에 그린 마스크를 오늘 프레임 판을 열어 둔 채 저장하니 대표가
        그 프레임으로 **조용히 옮겨갔다** — 개체 아홉이 흐린 단일 프레임을
        얼굴로 갖게 됐고, 그것이 카탈로그 크롭이자 학습 자료로 뽑힐 판이다.

        예외도 경고도 없다. 대표는 개체당 하나라는 제약을 계속 지키므로
        `check_db` 8번에도 안 걸린다.
        """
        frame = self.frames[0]
        self.post(image=frame, drawn=[self.draw()])
        reps = [r for r in self.rows() if r.is_rep]
        self.assertEqual(len(reps), 1, "대표가 하나가 아니다")
        self.assertEqual(
            reps[0].image_id, self.stack.pk,
            "프레임에서 저장했더니 대표가 합성본에서 그 프레임으로 옮겨갔다")

    def test_판을_옮겨_다시_저장해도_대표가_안_따라다닌다(self):
        """**같은 고장의 두 번째 판**. 합성본에서 그린 뒤 프레임으로 넘어가
        저장하는 것이 실사용의 순서였다 — 한 번은 맞고 다음 저장에 틀리면
        "되는 경우도 있고 아닌 경우도" 가 된다."""
        self.post(drawn=[self.draw()])                    # 합성본에서 그린다
        self.post(image=self.frames[1], drawn=[self.draw()])   # 판을 옮겨 저장
        reps = [r for r in self.rows() if r.is_rep]
        self.assertEqual([r.image_id for r in reps], [self.stack.pk],
                         "판을 옮겨 저장했더니 대표가 따라갔다")

    def test_분류는_개체에_한_벌만_앉는다(self):
        """P12 뒤로 분류는 개체에 산다 — 번져도 고칠 것이 없어야 한다."""
        self.post(drawn=[self.draw(cls="other")])
        objs = {r.foram_object.label for r in self.rows()}
        self.assertEqual(objs, {"other"})

    def test_카탈로그가_적은_코멘트는_다시_그려도_안_지워진다(self):
        """**0036 에서 갈렸다** — 개체 코멘트는 카탈로그에서만 적는다.

        여기서 지킬 것은 하나다: 판 넷이 한 개체를 나눠 갖는데 **검토 화면의
        저장이 그 개체의 코멘트에 닿으면 안 된다.** 닿으면 이 화면은 코멘트를
        모르므로(payload 에 없다) 빈 값이 가고, 카탈로그에서 적은 글이 저장
        한 번에 사라진다 — 사람이 쓴 글이라 재생성 불가다.
        """
        self.post(drawn=[self.draw()])
        obj = self.rows()[0].foram_object
        ForamObject.objects.filter(pk=obj.pk).update(note="가장자리가 깨졌다")

        self.post(drawn=[self.draw()])       # 화면이 다시 전체를 보낸다
        notes = {r.foram_object.note for r in self.rows()}
        self.assertEqual(notes, {"가장자리가 깨졌다"},
                         "검토 화면 저장이 카탈로그의 코멘트를 지웠다")

    # --- 다시 저장해도 그대로인가 -----------------------------------------

    def test_저장을_두_번_해도_늘지_않는다(self):
        """`judgement_for` 가 있는 행을 그대로 돌려주므로 멱등이라야 한다."""
        self.post(drawn=[self.draw()])
        first = len(self.rows())
        self.post(drawn=[self.draw()])
        self.assertEqual(len(self.rows()), first)
        self.assertEqual(len({r.foram_object_id for r in self.rows()}), 1)

    # --- 지울 때 -----------------------------------------------------------

    def test_한_판에서_지워도_다른_판은_남는다(self):
        """방침 2 — 어떤 초점면에서는 안 보이는 것을 반영할 수 있어야 한다."""
        self.post(drawn=[self.draw()])
        self.post(drawn=[])                      # 합성본에서 지운다
        left = {r.image_id for r in self.rows()}
        self.assertEqual(left, {f.pk for f in self.frames},
                         "묶음째 지워졌다")

    def test_대표를_지우면_대표를_다시_세운다(self):
        """**예외가 안 나는 자리다.** `is_rep` 유일 제약은 "둘 이상" 만 막고
        0개는 못 막는다 — 대표 없는 개체는 학습 자료에서 얼굴이 없다.
        """
        self.post(drawn=[self.draw()])
        self.post(drawn=[])                      # 대표(그린 판)를 지운다
        rows = self.rows()
        self.assertTrue(rows, "다 지워졌다")
        self.assertEqual(sum(1 for r in rows if r.is_rep), 1,
                         f"대표가 하나가 아니다: {[(r.image_id, r.is_rep) for r in rows]}")

    def test_모든_판에서_지우면_개체도_걷힌다(self):
        self.post(drawn=[self.draw()])
        n_before = ForamObject.objects.count()
        for img in [self.stack] + self.frames:
            self.post(image=img, drawn=[])
        self.assertEqual(self.rows(), [])
        self.assertLess(ForamObject.objects.count(), n_before,
                        "판이 다 사라졌는데 개체가 유령으로 남았다")

    # --- 화면에 알리는가 ---------------------------------------------------

    def test_응답이_번진_것을_싣는다(self):
        """**이것이 없으면 반쪽이다** — 다른 판의 화면이 복제를 모른 채 저장하면
        `/review` 청소가 그 자리에서 지운다 (104 가 분류에서 지난 함정).
        """
        out = self.post(drawn=[self.draw()])
        spread = out.get("drawn_spread")
        self.assertTrue(spread, f"응답에 안 실렸다: {out}")
        self.assertEqual(set(spread), {str(f.pk) for f in self.frames},
                         "그린 판이 섞였거나 빠진 판이 있다")
        one = spread[str(self.frames[0].pk)][0]
        self.assertEqual(one["key"], KEY)
        self.assertEqual(one["cls"], "broken")
        self.assertEqual(one["geom"]["polygon"], POLY)

    def test_그린_판은_응답에_안_들어간다(self):
        """그 화면은 이미 알고 있다 — 넣으면 자기 마스크를 두 번 그린다."""
        out = self.post(drawn=[self.draw()])
        self.assertNotIn(str(self.stack.pk), out.get("drawn_spread", {}))


class DrawnSpreadNoTargetTest(ForGIATestCase):
    """복제할 판이 없는 시야 — **자동으로 지나가야 한다.**

    SAM 은 시야당 판이 하나다. "YOLO 일 때만" 을 엔진 이름으로 판정하지 않고
    *이 회차에 현재 검출이 있는 판*으로 잡았기 때문에, 그런 시야는 복제할 곳이
    없어 저절로 빠진다. **검출이 없는 판에 넣으면 안 된다** — 그 판의 화면은
    마스크를 안 그리고 저장도 못 받아 손댈 수 없는 유령이 된다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)
        # `add_frame_detections` 를 안 부른다 — 합성본에만 현재 검출이 있다

    def setUp(self):
        self.c = Client()

    def test_판이_하나면_번지지_않는다(self):
        p = {"stem": self.w.stem(), "slug": self.w.slug, "gid": self.w.vp.idx,
             "done": False, "removed": [], "accepted": [], "labels": {},
             "notes": {},
             "drawn": [{"key": KEY, "polygon": POLY, "cls": "broken", "note": ""}]}
        r = self.c.post(reverse("save_review"), data=json.dumps(p),
                        content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content[:300])
        out = json.loads(r.content)
        self.assertNotIn("drawn_spread", out)
        self.assertEqual(
            ObjectReview.objects.filter(mask_key=KEY).count(), 1,
            "검출이 없는 판에 유령이 생겼다")
