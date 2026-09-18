"""DiaRUGA v0.29.0 `tests/test_auto_confirm.py` 에서 왔다.

검토 완료가 남는 개체에 확인 표시를 단다 (`confirm_kept` · 2026-08-11).

지금까지 "이건 개체이 맞다" 는 판단은 **행의 부재로만** 기록됐다. 사람이
틀린 것을 지우고 완료를 누르면 남은 것이 곧 맞다고 본 것인데 DB 에는 아무것도
안 남아, 학습 자료의 양성 표본이 암묵적이고 개체 수에 통과분이 안 잡혔다.

여기서 지키는 것:

1. 완료를 눌러야 붙는다 (그냥 저장으로는 안 붙는다)
2. **사람이 지운 것·판단한 것은 안 건드린다**
3. 확인 표시가 붙은 행이 **다음 저장의 청소에 안 지워진다** — 화면은 `auto_confirmed` 를 모른다
4. 두 번 눌러도 늘지 않는다
5. 시야의 **모든 판**에 붙는다 (완료 표시가 시야 단위라 그 주장도 시야 단위다)
"""
import json

from django.test import Client
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from ..models import ForamObject, ObjectReview


class ConfirmPassedTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=3)

    def setUp(self):
        self.c = Client()
        self.det = self.w.detection()
        # **통과분만 본다.** 픽스처는 탈락 후보도 하나 세우는데, 확인 표시는
        # 통과분에만 붙는다(되살리는 것은 사람이 눌러서 하는 일이다).
        self.keys = sorted(c.mask_key for c in self.det.candidates.all()
                           if c.passed)
        self.reject = next(c.mask_key for c in self.det.candidates.all()
                           if not c.passed)

    def post(self, expect=200, **over):
        p = {"stem": self.w.stem(), "slug": self.w.slug, "gid": self.w.vp.idx,
             "done": False, "removed": [], "accepted": [],
             "labels": {}, "notes": {}}
        p.update(over)
        r = self.c.post(reverse("save_review"), data=json.dumps(p),
                        content_type="application/json")
        self.assertEqual(r.status_code, expect, r.content[:300])
        return json.loads(r.content)

    def confirmed(self):
        return set(ObjectReview.objects.filter(auto_confirmed=True)
                   .values_list("mask_key", flat=True))

    def test_완료를_눌러야_붙는다(self):
        self.post()
        self.assertEqual(self.confirmed(), set(), "완료도 안 눌렀는데 붙었다")
        out = self.post(done=True)
        self.assertEqual(self.confirmed(), set(self.keys))
        self.assertEqual(out.get("auto_confirmed"), len(self.keys))
        self.assertNotIn(self.reject, self.confirmed(),
                         "탈락 후보에 확인 표시를 달았다 — 되살리는 것은 사람이 한다")

    def test_지운_것에는_안_붙는다(self):
        gone = self.keys[0]
        self.post(done=True, removed=[gone])
        self.assertNotIn(gone, self.confirmed(), "지운 마스크에 확인 표시를 달았다")
        self.assertIn(self.keys[1], self.confirmed())

    def test_분류를_붙인_행에도_붙는다(self):
        """**처음엔 "이미 있는 행은 안 건드린다" 로 짰다가 뒤집었다.**

        분류를 붙인 마스크야말로 사람이 가장 확실하게 개체이라고 본 것이다.
        건너뛰면 실측으로 `yolo-시험` 에서 211/921 만 잡혔다(23%).

        **다른 칸은 안 건드린다** — 확인 표시는 덮어쓰는 것이 아니라 더해지는 사실이다.
        """
        self.post(done=True, labels={self.keys[0]: "broken"})
        row = ObjectReview.objects.get(mask_key=self.keys[0])
        # 코멘트는 카탈로그가 적는 칸이다 (0036) — 완료가 그것을 덮으면 안 된다
        ForamObject.objects.filter(pk=row.foram_object_id).update(
            note="가장자리가 깨졌다")
        self.post(done=True, labels={self.keys[0]: "broken"})
        row.refresh_from_db()
        self.assertTrue(row.auto_confirmed, "분류를 붙인 행에 확인 표시가 안 붙었다")
        self.assertEqual(row.label, "broken", "분류를 덮었다")
        self.assertEqual(row.note, "가장자리가 깨졌다", "코멘트를 덮었다")

    def test_되살린_탈락분에도_붙는다(self):
        """되살린 것은 사람이 "이건 개체이다" 라고 콕 집어 말한 것이다 —
        `passed` 가 아니라는 이유로 빠지면 안 된다."""
        self.post(done=True, accepted=[self.reject])
        row = ObjectReview.objects.get(mask_key=self.reject)
        self.assertTrue(row.accepted)
        self.assertTrue(row.auto_confirmed, "되살린 탈락분에 확인 표시가 안 붙었다")

    def test_안_되살린_탈락분에는_안_붙는다(self):
        self.post(done=True)
        self.assertNotIn(self.reject, self.confirmed(),
                         "안 되살린 탈락분에 확인 표시를 달았다")

    def test_다음_저장의_청소가_안_지운다(self):
        """**화면은 `auto_confirmed` 를 모른다.** payload 에 안 실리므로 얹어 두지
        않으면 다음 저장이 "표시가 사라진 행" 으로 보고 지운다."""
        self.post(done=True)
        n = len(self.confirmed())
        self.assertEqual(n, len(self.keys))
        self.post()                      # 아무것도 안 담긴 평범한 저장
        self.assertEqual(len(self.confirmed()), n, "청소가 확인 표시를 지웠다")

    def test_두_번_눌러도_안_는다(self):
        self.post(done=True)
        n_rows = ObjectReview.objects.count()
        n_obj = ForamObject.objects.count()
        out = self.post(done=True)
        self.assertEqual(ObjectReview.objects.count(), n_rows)
        self.assertEqual(ForamObject.objects.count(), n_obj)
        self.assertIsNone(out.get("auto_confirmed"), "두 번째에도 새로 세웠다")

    def test_시야의_모든_판에_붙는다(self):
        """완료 표시는 `(시야, 묶음)` 단위라 그 주장도 시야 단위다 — 프레임
        넷 중 하나만 표시되면 안 된다."""
        fx.add_frame_detections(self.w.vp)
        self.post(done=True)
        imgs = set(ObjectReview.objects.filter(auto_confirmed=True)
                   .values_list("image_id", flat=True))
        self.assertGreater(len(imgs), 1, "한 판에만 붙었다")

    def test_개체가_함께_생긴다(self):
        """확인 표시가 붙은 마스크는 개체를 갖는다 — 그래야 프레임에 겹쳐 잡힌 것을
        하나로 세는 층이 통과분 위에도 얹힌다."""
        self.post(done=True)
        for row in ObjectReview.objects.filter(auto_confirmed=True):
            self.assertIsNotNone(row.foram_object_id)
            self.assertTrue(row.is_rep, "1:1 개체인데 대표가 아니다")
