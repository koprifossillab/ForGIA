"""DiaRUGA v0.29.0 `tests/test_review_image.py` 에서 왔다.

시야 하나에 **현재 검출이 여럿**일 때 (DiaRUGA P09 1단계).

합성본에 하나 + 프레임마다 하나다. YOLO 는 합성본이 아니라 원본 프레임을 보므로
`yolo-3차` 를 current 로 올리면 그 모양이 된다 — 실측으로 시야 452개에 프레임
검출 1,310개, 합성본 검출 314개다(DiaRUGA P09 4.1).

**운영 DB 에는 아직 그 상태가 없다.** 그래서 "시야마다 이미지가 하나" 를 전제한
코드가 지금 전부 통과한다. 여기서 그 전제를 깨 놓고 본다 — 갈아타는 날 한꺼번에
드러나면 잃는 것이 재생성 불가한 교정이다.

세 가지를 못 박는다.

1. 화면이 **그 이미지의 검출**을 본다 (아무거나가 아니라)
2. **다른 이미지의 교정이 얹혀 보이지 않는다** — 합성본과 프레임은 같은 시야라
   좌표계가 같아서 `mask_key` 가 실제로 맞는다. 45%가 겹친다는 실측이 있다
3. 저장이 **그 이미지에만** 걸린다 — 마지막 줄이 범위를 갈아치우므로 여기가
   틀리면 017·027·053 과 같은 자리가 된다
"""
import json
from pathlib import Path

from django.test import Client
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from .. import data
from ..models import ObjectReview


class MultiImageViewpointTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=3)
        cls.extra = fx.add_frame_detections(cls.w.vp)

    def setUp(self):
        self.c = Client()
        self.vp = self.w.vp
        self.stack_det = self.w.detection()
        self.frame, self.frame_img, self.frame_det = self.extra[0]

    def post(self, payload, expect=200):
        r = self.c.post(reverse("save_review"), data=json.dumps(payload),
                        content_type="application/json")
        self.assertEqual(r.status_code, expect, r.content[:300])
        return r

    def full(self, **over):
        p = {"stem": self.w.stem(), "slug": self.w.slug, "gid": self.vp.idx,
             "done": False, "removed": [], "accepted": [],
             "labels": {}, "notes": {}}
        p.update(over)
        return p

    # --- 픽스처가 진짜 그 상태인가 -----------------------------------------

    def test_현재_검출이_여럿이다(self):
        dets = data.current_detections(self.vp)
        self.assertEqual(len(dets), 4, "합성본 1 + 프레임 3")
        kinds = sorted(d.image.kind for d in dets)
        self.assertEqual(kinds, ["frame", "frame", "frame", "stack"])

    # --- 대표 이미지 (집계가 세는 것) --------------------------------------

    def test_대표는_합성본이다(self):
        """**집계는 시야마다 하나에서 낸다** (DiaRUGA P09 5.3).

        다 세면 같은 개체이 4.6번 세어져 밀도가 그만큼 부푼다.
        """
        rep = data.representative_detection(self.vp)
        self.assertEqual(rep.image.kind, "stack")
        self.assertEqual(rep.pk, self.stack_det.pk)

    def test_합성본이_없으면_그_프레임이_대표다(self):
        w = fx.make_world(slug="single", with_stack=False, n_frames=1)
        rep = data.representative_detection(w.vp)
        self.assertEqual(rep.image.kind, "frame")

    # --- 어느 이미지의 검출을 그리는가 --------------------------------------

    def test_이미지를_주면_그_이미지의_검출을_낸다(self):
        d = data.detection_for_viewpoint(self.vp, image=self.frame_img)
        self.assertEqual(d["image"], self.frame_img.path)

    def test_안_주면_대표_이미지의_것이다(self):
        d = data.detection_for_viewpoint(self.vp)
        self.assertEqual(d["image"], self.stack_det.image.path)

    # --- 교정이 새어 들지 않는가 -------------------------------------------

    def test_합성본_교정이_프레임_화면에_안_얹힌다(self):
        """**같은 좌표계라 `mask_key` 가 실제로 맞는다** — 우연이 아니다.

        픽스처가 두 검출에 같은 bbox 를 쓰므로 키가 그대로 겹친다. 거르지 않으면
        합성본에서 "지웠다" 고 한 것이 프레임 화면에서도 지워져 보인다.
        """
        k = self.w.keys()[0]
        self.assertIn(k, [c.mask_key for c in self.frame_det.candidates.all()],
                      "픽스처 전제가 깨졌다 — 키가 안 겹치면 시험이 무의미하다")

        fx.add_review(self.vp, k, removed=True)      # 합성본 위의 판단

        on_stack = data.detection_for_viewpoint(self.vp)
        on_frame = data.detection_for_viewpoint(self.vp, image=self.frame_img)
        self.assertEqual(on_stack["n_removed"], 1,
                         "합성본에서는 지워져 보여야 한다")
        self.assertEqual(on_frame["n_removed"], 0,
                         "합성본 교정이 프레임 화면에 얹혔다")
        self.assertIn(k, [data.cand_key(c) for c in on_frame["candidates"]],
                      "프레임 화면에서는 살아 있는 개체로 보여야 한다")

    # --- 저장이 그 이미지에만 걸리는가 --------------------------------------

    def test_프레임에_저장하면_프레임_이미지에_앉는다(self):
        k = self.w.keys()[0]
        self.post(self.full(image=self.frame_img.pk, labels={k: "broken"}))
        obj = ObjectReview.objects.get(mask_key=k,
                                         foram_object__label="broken")
        self.assertEqual(obj.image_id, self.frame_img.pk)

    def test_프레임_저장이_합성본_교정을_안_지운다(self):
        """마지막 줄이 범위를 갈아치운다 — **여기가 017·027·053 의 자리다.**"""
        k = self.w.keys()[0]
        on_stack = fx.add_review(self.vp, k, removed=True)

        self.post(self.full(image=self.frame_img.pk))   # 프레임에 빈 목록

        self.assertTrue(ObjectReview.objects.filter(pk=on_stack.pk).exists(),
                        "프레임 화면의 저장이 합성본 교정을 지웠다")

    def test_합성본_저장이_프레임_교정을_안_지운다(self):
        k = self.w.keys()[0]
        self.post(self.full(image=self.frame_img.pk, labels={k: "broken"}))
        on_frame = ObjectReview.objects.get(image=self.frame_img)

        self.post(self.full(image=self.stack_det.image_id))   # 합성본에 빈 목록

        self.assertTrue(ObjectReview.objects.filter(pk=on_frame.pk).exists(),
                        "합성본 화면의 저장이 프레임 교정을 지웠다")

    def test_이미지를_안_주면_대표에_앉는다(self):
        """옛 화면(배포 중에 열려 있던 탭)이 그렇다 — 예전과 결과가 같다."""
        k = self.w.keys()[0]
        self.post(self.full(labels={k: "broken"}))
        self.assertEqual(ObjectReview.objects.get(mask_key=k,
                                         foram_object__label="broken")
                         .image_id, self.stack_det.image_id)

    # --- 짚은 것이 이 시야의 것인가 -----------------------------------------

    def test_남의_이미지를_짚으면_거절한다(self):
        """**조용히 대표에 앉히지 않는다.** 사람이 보고 있던 것과 다른 자리에
        판단이 쌓이면 무엇을 검토했는지 알 수 없게 된다.
        """
        other = fx.make_world(slug="남의것", n_frames=1)
        r = self.post(self.full(image=other.detection().image_id,
                                labels={self.w.keys()[0]: "broken"}), expect=409)
        self.assertIn("현재 검출이 없다", r.json()["error"])
        self.assertEqual(ObjectReview.objects.count(), 0)

    def test_다른_이미지의_키는_받지_않는다(self):
        """`mask_key` 는 프레임끼리 45% 겹친다 — 통과하면 엉뚱한 행이 남는다."""
        alien = "9999_9999_10_10"
        self.post(self.full(image=self.frame_img.pk, removed=[alien]),
                  expect=409)
        self.assertEqual(ObjectReview.objects.count(), 0)

    # --- 화면이 어느 판으로 렌더됐는가 (실사용 2026-08-10) -------------------
    #
    # `stem` 은 **서버가 그 화면을 어느 판으로 그렸는지**를 담아 돌아온다.
    # 합성본에 보일 것이 하나도 없으면 화면은 프레임 판으로 서고, 그때 화면이
    # 보내는 stem 은 프레임의 것이다. 그런데 `find_viewpoint` 가 시야의 현재
    # 검출 중 **아무거나 하나**와 비교하고 있어서 그 저장이 통째로 409 였다.
    #
    # 위의 시험들이 전부 `stem=합성본` 으로만 보내고 있어 이 갈래를 한 번도 안
    # 밟았다 — 086 과 같은 이야기다(URL 을 덮는 것과 갈래를 덮는 것은 다르다).

    def test_프레임_판을_보고_보낸_저장이_통한다(self):
        """실사용에서 오검출 둘을 지운 것이 이렇게 사라졌다."""
        frame_stem = Path(self.frame_img.path).stem
        k = self.w.keys()[0]
        self.post(self.full(stem=frame_stem, image=self.frame_img.pk,
                            removed=[k]))
        obj = ObjectReview.objects.get(mask_key=k)
        self.assertTrue(obj.removed)
        self.assertEqual(obj.image_id, self.frame_img.pk)

    def test_합성본_판의_stem_도_그대로_통한다(self):
        """넓히면서 원래 되던 쪽이 막히면 안 된다."""
        k = self.w.keys()[0]
        self.post(self.full(stem=self.w.stem(),
                            image=self.stack_det.image_id, removed=[k]))
        self.assertTrue(ObjectReview.objects.get(mask_key=k).removed)

    def test_이_시야에_없는_판이면_여전히_거절한다(self):
        """넓힌 것은 **이 시야의 판들**까지다 — 그 밖은 그대로 막는다.

        (같은 이름의 판이 다른 슬라이드에 있는 것은 이 검사가 못 가른다 —
        프레임 이름은 슬라이드끼리 겹친다. 시야를 짚는 것은 `(slug, gid)` 이고
        stem 은 그 위의 덧문이다.)
        """
        r = self.post(self.full(stem="Snap-99999", removed=[self.w.keys()[0]]),
                      expect=409)
        self.assertIn("어긋난다", r.json()["error"])
        self.assertEqual(ObjectReview.objects.count(), 0)


class SummaryCountsOneImageTest(ForGIATestCase):
    """집계는 **시야마다 대표 이미지 하나**에서 낸다 (DiaRUGA P09 5.3).

    검토는 모든 이미지에서 하지만, 세는 것은 하나여야 한다. 다 세면 같은 개체이
    합성본 1 + 프레임 3.6 장에서 거듭 세어져 **밀도가 4.6배**가 된다 — 학습
    자료로는 맞고 계측 통계로는 틀리다. 그리고 그 숫자가 보고서에 실린다.

    **이 고장은 예외를 안 낸다.** 화면이 그럴듯한 숫자를 내고, 옳은 값을 아는
    사람만 이상하다고 느낀다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=3)

    def summary(self):
        """목록 화면이 쓰는 값 그대로 (`_summary_by_sql`)."""
        return data._summary_by_sql(self.w.slide)

    def test_프레임_검출이_붙어도_개체_수가_안_는다(self):
        before = self.summary()
        fx.add_frame_detections(self.w.vp)          # 현재 검출 1 → 4
        after = self.summary()

        self.assertEqual(len(data.current_detections(self.w.vp)), 4,
                         "픽스처 전제가 깨졌다")
        self.assertEqual(after["n_detected"], before["n_detected"],
                         "프레임 검출이 붙자 개체 수가 늘었다 — 밀도가 부푼다")
        self.assertEqual(after["n_auto"], before["n_auto"])
        self.assertEqual(after["per_cls"], before["per_cls"])

    def test_평균의_분모는_시야_수다(self):
        """**검출 수가 아니다.** 분자는 대표 하나만 세므로 둘이 어긋난다."""
        fx.add_frame_detections(self.w.vp)
        self.assertEqual(self.summary()["detected_groups"], 1)

    def test_표지_마스크도_대표에서만_온다(self):
        fx.add_frame_detections(self.w.vp)
        masks, n_kept = data._kept_masks(self.w.slide)
        self.assertEqual(n_kept[self.w.vp.pk], 3,
                         "표지의 개체 수가 프레임만큼 부풀었다")

    def test_옛_묶음의_교정이_새_후보에_안_붙는다(self):
        """묶음을 갈아탄 뒤 같은 이미지에 남아 있는 옛 회차의 교정.

        키가 겹치면 `LEFT JOIN` 이 행을 불려 **개수가 조용히 는다.**
        """
        from ..models import ObjectReview, RunBatch
        det = self.w.detection()
        old, _ = RunBatch.objects.get_or_create(kind="detect", label="옛회차")
        for c in det.candidates.all():              # 같은 키, 다른 묶음
            fx.new_review(
                viewpoint=self.w.vp, image=det.image, batch=old,
                mask_key=c.mask_key, label="broken",
                geom={"bbox": c.bbox_xywh, "polygon": list(c.polygon)})

        s = self.summary()
        self.assertEqual(s["n_detected"], 3, "옛 묶음의 교정이 행을 불렸다")
        self.assertEqual(s["n_labeled"], 0,
                         "옛 묶음의 분류가 이번 회차 집계에 섞였다")


class OrphanReviewSurvivesTest(ForGIATestCase):
    """**후보가 없는 교정은 화면에 안 나오고, 그래서 다음 저장에 지워진다.**

    `_apply_review` 가 `det.candidates.all()` 만 도므로 고아 교정(`orphan`)과
    사람이 그린 개체는 **그려지지 않는다.** 화면은 자기가 아는 키만 보내고,
    `save_review` 의 마지막 줄은 payload 에 없는 키를 지운다 —
    **재생성 불가한 판단이 사람이 아무것도 안 해도 사라진다.**

    017·027·053 과 같은 자리이고, 다른 점은 **엉뚱한 화면이 아니라 제 화면에서
    난다**는 것이다. 지금은 고아가 0이라 안 밟히지만, 재검출 한 번이면 생긴다
    (실측: 재검출에서 exact 26 · iou 40 · 고아 1).
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)

    def setUp(self):
        self.c = Client()

    def orphan(self):
        """현재 검출에 대응 후보가 없는 교정 — 사람의 판단만 남은 것."""
        det = self.w.detection()
        return fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=det.batch,
            mask_key="900_900_50_50", bind_method="orphan", label="other",
            note="엔진이 놓친 것", geom={"bbox": [900, 900, 50, 50],
                                      "polygon": [900, 900, 950, 900,
                                                  950, 950, 900, 950]})

    def test_고아_교정이_화면에_나온다(self):
        o = self.orphan()
        d = data.detection_for_viewpoint(self.w.vp)
        keys = [data.cand_key(c) for c in d["candidates"]]
        self.assertIn(o.mask_key, keys,
                      "고아 교정이 화면에 안 나온다 — 다음 저장에 지워진다")

    def test_화면이_보낸_그대로_저장해도_고아가_남는다(self):
        """**사람이 아무것도 안 해도 사라지는가.**

        화면이 그리는 것을 그대로 되돌려 보낸다 — 검토 화면이 여는 즉시 하는
        일이고, "검토 완료" 만 눌러도 같은 payload 가 나간다.
        """
        o = self.orphan()
        d = data.detection_for_viewpoint(self.w.vp)
        keys = [data.cand_key(c) for c in d["candidates"]]
        r = self.c.post(
            reverse("save_review"),
            data=json.dumps({"stem": self.w.stem(), "slug": self.w.slug,
                             "gid": self.w.vp.idx, "done": True,
                             "removed": [], "accepted": [],
                             "labels": {k: "broken" for k in keys if k == o.mask_key},
                             "notes": {}}),
            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertTrue(ObjectReview.objects.filter(pk=o.pk).exists(),
                        "검토 완료를 누른 것만으로 고아 교정이 사라졌다")
