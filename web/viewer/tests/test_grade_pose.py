"""DiaRUGA v0.29.0 `tests/test_grade_pose.py` 에서 왔다.

개체 **등급·자세** — 두 칸의 축이 반대라는 것이 이 파일이 지키는 전부다.

우수한 개체를 골라 먼저 학습시키려고 만든 칸이다 (2026-08-11 사용자 계획).

| | 무엇에 대한 판단인가 | 사는 곳 | 묶으면 |
|---|---|---|---|
| **등급** `A`/`B`/`C` | **판(초점면)** — 초점면마다 areolae 가 보이고 안 보인다 | `ObjectReview` | **안 번진다** |
| **자세** `valve`/`girdle`/`other` | **개체** — 스테이지가 안 움직이니 자세는 그대로다 | `ForamObject` | **나눠 갖는다** |

**한쪽으로 몰면 조용히 틀린다.** 자세를 판에 두면 묶인 판마다 N벌이 되어 어긋날
수 있고(104 가 `label` 에서 겪은 모양), 등급을 개체에 두면 초점면마다 다른 값이
하나로 뭉개진다. 예외는 안 난다 — 그래서 시험으로 못 박는다.

## 105 가 종명에서 당한 자리를 그대로 지난다

`/review` POST 는 그 `(이미지, 묶음)` 의 교정 행 중 **payload 에 없는 것을
지운다** — "뷰어는 늘 전체를 보낸다" 가 전제다. 검토 화면은 등급도 자세도
모른다. 그러니 그 두 칸만 채운 행은 사람이 **"검토 완료" 만 눌러도** 사라진다.
017·027·053 이 전부 그 삭제 줄에서 났고 두 번은 운영 자료를 잃었다(14건 · 37건).
등급·자세는 사람이 현미경을 보며 매기는 것이라 **재생성 불가**다.

**그러면서 청소는 계속 돼야 한다** — 아무 표시도 안 남은 행은 여전히 지워져야
"교정 전체 초기화" 가 동작하고, 그 행을 세는 자리(`check_db.py`)가 안 어긋난다.

## 파편에는 안 매긴다

파편(`ClassDef.counted=0`)은 완형을 유추할 수 있어도 확실하지 않고, 무엇보다
*한 개체로 인정하는 규칙을 만족하지 못한 것*이라 우수성을 물을 자리가 아니다
(사용자). 화면이 칸을 안 보여주는 것으로는 안 막은 것이다 — **서버가 다시
검사한다** (DiaRUGA 063).
"""
import json

from django.test import Client
from django.urls import reverse

from . import factories as fx
from .. import data
from .base import ForGIATestCase
from ..models import ForamObject, ObjectReview


class GradePoseSurvivesReviewTest(ForGIATestCase):
    """검토 화면이 등급·자세를 밟고 지나가지 않는가 (`test_species_survives` 의 짝)."""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)

    def setUp(self):
        self.c = Client()
        self.key = self.w.keys()[0]
        self.det = self.w.detection()

    def post(self, expect=200, **over):
        """검토 화면이 보내는 것과 같은 payload. **등급·자세는 안 들어 있다** —
        그 화면은 두 칸을 모른다."""
        p = {"stem": self.w.stem(), "slug": self.w.slug, "gid": self.w.vp.idx,
             "done": False, "removed": [], "accepted": [],
             "labels": {}, "notes": {}}
        p.update(over)
        r = self.c.post(reverse("save_review"), data=json.dumps(p),
                        content_type="application/json")
        self.assertEqual(r.status_code, expect, r.content[:300])
        return r

    def put(self, key=None, *, grade="", pose="", label=""):
        """카탈로그 카드가 만드는 것과 같은 행 — **판정을 세우는 문 하나를
        지난다** (`data.judgement_for`). **등급도 자세도 개체에 앉는다** (0035)."""
        obj = data.judgement_for(self.w.vp, self.det.image, self.det.batch,
                                 key or self.key)
        dobj = obj.foram_object
        if grade or pose or label:
            dobj.grade, dobj.pose, dobj.label = grade, pose, label
            dobj.save()
        return obj

    # --- 사고 재현 ---------------------------------------------------------

    def test_등급만_매겨도_검토_완료에_안_사라진다(self):
        """**이 시험이 이 기능에서 제일 위험한 자리다.** 등급만 매긴 행은
        삭제·되살림·유형·코멘트가 전부 비어 있다."""
        self.put(grade="A")
        self.post(done=True)
        o = ObjectReview.objects.filter(mask_key=self.key).first()
        self.assertIsNotNone(o, "등급만 있는 행이 지워졌다")
        self.assertEqual(o.foram_object.grade, "A")

    def test_자세만_매겨도_검토_완료에_안_사라진다(self):
        """자세는 개체에 사는데 **행이 지워지면 개체도 함께 걷힌다**
        (`prune_objects`) — 사는 자리가 달라도 잃는 것은 같다."""
        self.put(pose="umbilical")
        self.post(done=True)
        o = ObjectReview.objects.filter(mask_key=self.key).first()
        self.assertIsNotNone(o, "자세만 있는 행이 지워졌다")
        self.assertEqual(o.foram_object.pose, "umbilical")

    def test_여러_번_저장해도_남는다(self):
        """한 번 살아남는 것과 계속 살아남는 것은 다르다."""
        self.put(grade="B", pose="spiral")
        for _ in range(3):
            self.post(done=True)
        o = ObjectReview.objects.filter(mask_key=self.key).first()
        self.assertIsNotNone(o)
        self.assertEqual((o.foram_object.grade, o.foram_object.pose), ("B", "spiral"))

    def test_유형을_지워도_등급_자세는_남는다(self):
        """유형(`label`)은 검토 화면이 아는 칸이라 payload 가 비면 지워지는 것이
        맞다. **두 칸은 그 화면이 대표하지 않으므로 따라 지워지면 안 된다.**"""
        self.put(grade="A", pose="umbilical", label="foram")
        self.post()
        o = ObjectReview.objects.get(mask_key=self.key)
        self.assertEqual(o.label, "", "유형은 화면이 대표하는 칸이라 비워야 한다")
        self.assertEqual((o.foram_object.grade, o.foram_object.pose), ("A", "umbilical"))

    def test_지운_개체도_등급을_들고_있는다(self):
        """지운 것도 학습의 음성 표본이다 (DiaRUGA P02 §2.7)."""
        self.put(grade="C")
        self.post(removed=[self.key])
        o = ObjectReview.objects.get(mask_key=self.key)
        self.assertTrue(o.removed)
        self.assertEqual(o.foram_object.grade, "C")

    # --- 청소는 계속 돼야 한다 ----------------------------------------------

    def test_등급_자세가_없는_빈_행은_여전히_지워진다(self):
        """**안전망이 청소를 막으면 안 된다.**"""
        self.post(labels={self.key: "broken"})
        self.assertTrue(ObjectReview.objects.filter(mask_key=self.key).exists())
        self.post()
        self.assertFalse(ObjectReview.objects.filter(mask_key=self.key).exists())

    def test_등급을_비우면_그_행도_지워진다(self):
        """카드에서 등급을 지운 뒤에는 청소해도 된다 — 더는 지킬 것이 없다.

        **완료를 안 누르고 본다** (DiaRUGA 109). 완료는 남는 개체에 서명(`auto_confirmed`)
        을 세우는데, 그러면 그 행은 등급이 아니라 서명 때문에 살아남아 **이 시험이
        무엇을 보는지 흐려진다.**
        """
        self.put(grade="A")
        self.post()
        ForamObject.objects.filter(members__mask_key=self.key).update(grade="")
        self.post()
        self.assertFalse(ObjectReview.objects.filter(mask_key=self.key).exists())

    def test_자세를_비우면_그_행도_지워진다(self):
        self.put(pose="umbilical")
        self.post()
        ForamObject.objects.filter(members__mask_key=self.key).update(pose="")
        self.post()
        self.assertFalse(ObjectReview.objects.filter(mask_key=self.key).exists())

    # --- 범위가 새지 않는가 -------------------------------------------------

    def test_다른_시야의_등급에는_안_닿는다(self):
        w2 = fx.make_world(slug="rs23-b", n_candidates=2)
        det2 = w2.detection()
        other = fx.new_review(viewpoint=w2.vp, image=det2.image,
                              batch=det2.batch, mask_key=w2.keys()[0],
                              bind_method="exact")
        ForamObject.objects.filter(pk=other.foram_object_id).update(grade="A")
        self.put(grade="B")
        self.post(done=True)
        self.assertEqual(ForamObject.objects.filter(grade="A").count(), 1,
                         "다른 시야의 등급이 이 저장에 휩쓸렸다")


class GradePoseAxisTest(ForGIATestCase):
    """**축이 반대다** — 묶었을 때 무엇이 나뉘고 무엇이 안 나뉘는가.

    이 시험이 없으면 두 칸을 한 표로 몰아넣는 고침이 조용히 통과한다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2, n_frames=2)

    def setUp(self):
        fx.add_frame_detections(self.w.vp, n_candidates=2)

    def _linked_pair(self):
        """**서로 다른 판**의 판정 둘을 하나의 개체로 묶는다 — 이미지가 달라야
        한다(`uniq_objreview_object_image`). 그것이 이 기능이 다루는 모양이다."""
        dets = list(self.w.vp.detections.filter(is_current=True))[:2]
        self.assertEqual(len(dets), 2, "판이 둘이어야 묶는 것을 볼 수 있다")
        rows = [data.judgement_for(self.w.vp, d.image, d.batch,
                                   d.candidates.first().mask_key)
                for d in dets]
        fx.link_reviews(rows)
        return [ObjectReview.objects.get(pk=r.pk) for r in rows]

    def test_자세는_묶인_판들이_나눠_갖는다(self):
        """개체 하나에 살기 때문이다 — **번지게 하는 코드가 없다**(DiaRUGA P12)."""
        a, b = self._linked_pair()
        dobj = a.foram_object
        dobj.pose = "spiral"
        dobj.save()
        self.assertEqual(
            ObjectReview.objects.get(pk=b.pk).foram_object.pose, "spiral",
            "자세가 묶인 다른 판에 안 보인다 — 개체의 성질이어야 한다")

    def test_등급도_묶인_판들이_나눠_갖는다(self):
        """**0035 로 뒤집힌 자리다.** 처음에는 판마다 따로 두었다 — 초점면마다
        다르게 보이니 그 판에 대한 판단이라고 봤다. 그런데 *어느 판이 잘
        보이는가* 는 `is_rep`(대표)이 이미 말하고 있었고, 등급이 답해야 하는 것은
        *이 개체이 얼마나 좋은 표본인가* 였다.
        """
        a, b = self._linked_pair()
        dobj = a.foram_object
        dobj.grade = "A"
        dobj.save()
        self.assertEqual(
            ObjectReview.objects.get(pk=b.pk).foram_object.grade, "A",
            "등급이 묶인 다른 판에 안 보인다 — 개체의 성질이어야 한다")

    def test_대표가_어느_판인지는_따로_남는다(self):
        """등급을 개체로 올려도 **판을 고르는 축이 사라지면 안 된다** — 학습
        자료를 뽑을 때 "이 개체은 이 판으로 본다" 가 그 값이다."""
        a, b = self._linked_pair()
        reps = [r for r in (a, b)
                if ObjectReview.objects.get(pk=r.pk).is_rep]
        self.assertEqual(len(reps), 1, "대표가 하나여야 한다")


class GradePoseFragmentTest(ForGIATestCase):
    """**파편에는 등급도 자세도 주지 않는다** — 서버가 다시 검사한다 (DiaRUGA 063).

    화면이 칸을 안 보여주는 것으로는 안 막은 것이다. `.tools` 를 CSS 로 감췄다고
    믿었다가 세 화면이 계속 도구를 내보이고 있던 적이 있다(DiaRUGA 051).
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)

    def test_완형에는_매길_수_있다(self):
        """막는 쪽만 시험하면 전부 막아도 통과한다."""
        data.check_grade_pose("foram", grade="A", pose="umbilical")
        # ForGIA 에서 세는 분류는 온전·파손 둘이다 (비유공충 `other` 는 파편처럼 안 센다)
        data.check_grade_pose("broken", grade="C", pose="other")

    def test_분류가_없어도_매길_수_있다(self):
        """유형을 아직 안 정한 개체가 흔하다 — 매기는 순서를 강제하지 않는다."""
        data.check_grade_pose("", grade="B", pose="spiral")

    def test_파편에는_등급을_못_매긴다(self):
        with self.assertRaises(ValueError):
            data.check_grade_pose("fragment", grade="A", pose="")

    def test_파편에는_자세를_못_매긴다(self):
        with self.assertRaises(ValueError):
            data.check_grade_pose("other", grade="", pose="umbilical")

    def test_파편이어도_비우는_것은_된다(self):
        """이미 매긴 값을 걷어내는 길이 막히면 고칠 방법이 없다."""
        data.check_grade_pose("fragment", grade="", pose="")

    def test_모르는_등급은_안_받는다(self):
        with self.assertRaises(ValueError):
            data.check_grade_pose("foram", grade="S", pose="")

    def test_모르는_자세는_안_받는다(self):
        with self.assertRaises(ValueError):
            data.check_grade_pose("foram", grade="", pose="옆으로")

    def test_카탈로그_저장이_파편을_거절한다(self):
        """검사가 실제로 쓰이는 자리 — 함수만 있고 아무도 안 부르면 없는 것이다."""
        det = self.w.detection()
        key = self.w.keys()[0]
        with self.assertRaises(ValueError):
            data.save_catalog_entry(self.w.vp, det.image, key,
                                    cls="fragment", grade="A")

    def test_카탈로그_저장이_등급_자세를_적는다(self):
        det = self.w.detection()
        key = self.w.keys()[0]
        data.save_catalog_entry(self.w.vp, det.image, key,
                                cls="foram", grade="A", pose="umbilical")
        o = ObjectReview.objects.get(mask_key=key)
        self.assertEqual((o.foram_object.grade, o.foram_object.pose), ("A", "umbilical"))

    def test_카탈로그_저장이_등급만_남은_행을_안_지운다(self):
        """`save_catalog_entry` 의 "아무것도 안 남으면 지운다" 는 줄이 두 칸을
        세지 않으면, 종명을 비우는 순간 등급까지 함께 사라진다 (105 가 같은
        자리에서 실패 둘을 봤다)."""
        det = self.w.detection()
        key = self.w.keys()[0]
        data.save_catalog_entry(self.w.vp, det.image, key, grade="B")
        data.save_catalog_entry(self.w.vp, det.image, key, species="")
        self.assertTrue(ObjectReview.objects.filter(mask_key=key).exists(),
                        "등급만 남은 행이 지워졌다")
