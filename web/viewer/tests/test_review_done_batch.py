"""DiaRUGA v0.29.0 `tests/test_review_done_batch.py` 에서 왔다.

검토 완료는 **묶음마다**, 시야 코멘트는 **시야마다** (DiaRUGA 073).

`ObjectReview` 와 같은 가름이다(DiaRUGA P09 5.2) — 무엇에 대한 판단인가로 나눈다.

| 칸 | 무엇에 대한 판단인가 | 속하는 곳 |
|---|---|---|
| `done` | 이 묶음이 낸 검출을 여기서 다 봤다 | **batch** |
| `note` | 이 시야가 이러이러하다 | 시야 — batch 를 갈아도 참이다 |

`done` 을 시야에 매달아 두었더니 `yolo-시험` 를 검토하고 붙인 완료 표시가
`yolo-3차` 화면에도 그대로 붙었다. **아직 아무도 안 본 검출이 "검토 완료" 로
보이고, "다음 미검토" 가 그 시야를 건너뛴다** — 그 시야는 다시 열리지 않는다.

코멘트를 함께 묶음에 매달지 않은 이유는 하나다: **사람이 쓴 글은 재생성
불가다.** 묶음을 갈 때마다 사라지면 안 된다.
"""
import json

from django.test import Client
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from .. import data, manage_data
from ..models import Detection, ObjectReview, RunBatch, ViewpointReview


class ReviewDoneBelongsToBatchTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_viewpoints=2, n_candidates=3)
        cls.vp = cls.w.vp
        cls.yolo = fx.add_other_engine(cls.vp, label="yolo-재학습")
        Detection.objects.filter(run=cls.yolo).update(is_current=True)

    def setUp(self):
        self.c = Client()

    def sam(self):
        return RunBatch.objects.get(label="yolo-시험")

    def switch(self, batch):
        ok, msg = manage_data.set_review_batch(batch.pk)
        self.assertTrue(ok, msg)

    def mark_done(self, note=""):
        """화면이 하는 그대로 — 저장이 완료 표시를 함께 나른다."""
        return data.save_review(self.vp, done=True, note=note,
                                removed=set(), accepted=set(),
                                labels={}, notes={},
                                image=None)

    # --- 완료는 묶음을 안 넘어간다 ------------------------------------------

    def test_한_묶음에서_완료해도_다른_묶음은_미검토다(self):
        """**여기가 고장 났던 자리다.**"""
        self.mark_done()
        self.assertTrue(data.review_state(self.vp)[0])

        self.switch(self.yolo.batch)
        self.assertFalse(data.review_state(self.vp)[0],
                         "안 본 묶음인데 검토 완료로 보인다")

    def test_돌아오면_완료가_그대로다(self):
        self.mark_done()
        self.switch(self.yolo.batch)
        self.switch(self.sam())
        self.assertTrue(data.review_state(self.vp)[0], "완료 표시가 사라졌다")

    def test_묶음마다_줄이_따로_생긴다(self):
        self.mark_done()
        self.switch(self.yolo.batch)
        self.mark_done()
        rows = ViewpointReview.objects.filter(viewpoint=self.vp, done=True)
        self.assertEqual(
            sorted(r.batch.label for r in rows),
            ["yolo-시험", "yolo-재학습"])

    # --- 코멘트는 묶음을 넘어간다 ------------------------------------------

    def test_코멘트는_묶음을_갈아도_남는다(self):
        """**사람이 쓴 글이라 재생성 불가다.**"""
        self.mark_done(note="이 시야는 초점이 흐리다")
        self.switch(self.yolo.batch)
        self.assertEqual(data.review_state(self.vp)[1], "이 시야는 초점이 흐리다")

    def test_코멘트는_묶음_없는_줄에_산다(self):
        self.mark_done(note="메모")
        row = ViewpointReview.objects.get(viewpoint=self.vp, batch__isnull=True)
        self.assertEqual(row.note, "메모")
        self.assertFalse(row.done, "코멘트 줄이 완료 표시를 들고 있다")

    def test_코멘트를_비우면_줄이_사라진다(self):
        self.mark_done(note="메모")
        self.mark_done(note="")
        self.assertFalse(ViewpointReview.objects
                         .filter(viewpoint=self.vp, batch__isnull=True).exists())

    # --- 화면이 그렇게 세는가 ----------------------------------------------

    def test_목록_배지가_묶음을_따라간다(self):
        self.mark_done()
        g = {x["id"]: x for x in data.dataset_detail("rs23")["groups"]}
        self.assertTrue(g[self.vp.idx]["reviewed"])

        self.switch(self.yolo.batch)
        g = {x["id"]: x for x in data.dataset_detail("rs23")["groups"]}
        self.assertFalse(g[self.vp.idx]["reviewed"], "배지가 묶음을 넘어갔다")

    def test_완료_수가_묶음을_따라간다(self):
        self.mark_done()
        self.assertEqual(data.dataset_detail("rs23")["reviewed_groups"], 1)
        self.switch(self.yolo.batch)
        self.assertEqual(data.dataset_detail("rs23")["reviewed_groups"], 0)

    def test_다음_미검토가_다시_그_시야를_준다(self):
        """**건너뛰면 그 시야는 다시 안 열린다.** 가장 나쁜 결과다."""
        self.mark_done()
        d = data.group_detail("rs23", self.w.viewpoints[1].idx)
        self.assertNotEqual(d.get("todo_id"), self.vp.idx,
                            "완료한 시야를 또 준다")

        self.switch(self.yolo.batch)
        d = data.group_detail("rs23", self.w.viewpoints[1].idx)
        self.assertEqual(d.get("todo_id"), self.vp.idx,
                         "안 본 묶음인데 미검토 목록에서 빠졌다")

    # --- 한꺼번에 표시하기 --------------------------------------------------

    def test_한꺼번에_표시도_묶음에_찍는다(self):
        n = data.mark_all_reviewed("rs23", done=True)
        self.assertEqual(n["changed"], 2)
        self.assertEqual(len(data.done_viewpoints()), 2)

        self.switch(self.yolo.batch)
        self.assertEqual(len(data.done_viewpoints()), 0,
                         "다른 묶음까지 완료로 찍혔다")

    def test_한꺼번에_되돌려도_코멘트는_남는다(self):
        self.mark_done(note="남아야 한다")
        data.mark_all_reviewed("rs23", done=False)
        self.assertFalse(data.review_state(self.vp)[0])
        self.assertEqual(data.review_state(self.vp)[1], "남아야 한다")


class ReviewDoneScreenTest(ForGIATestCase):
    """화면이 실제로 그 값을 그리는가."""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)
        cls.yolo = fx.add_other_engine(cls.w.vp, label="yolo-재학습")
        Detection.objects.filter(run=cls.yolo).update(is_current=True)

    def test_완료_배지가_묶음을_따라간다(self):
        c = Client()
        data.save_review(self.w.vp, done=True, note="", removed=set(),
                         accepted=set(), labels={}, notes={})
        # **배지의 마크업을 짚는다.** 글자만 찾으면 `base.html` 의 CSS 주석에
        # 있는 같은 글자가 걸린다 — 실제로 그렇게 짰다가 헛통과할 뻔했다.
        badge = '<span class="badge done">✓ 검토</span>'
        body = c.get(reverse("dataset", args=["rs23"])).content.decode()
        self.assertIn(badge, body)

        ok, msg = manage_data.set_review_batch(self.yolo.batch_id)
        self.assertTrue(ok, msg)
        body = c.get(reverse("dataset", args=["rs23"])).content.decode()
        self.assertNotIn(badge, body, "배지가 묶음을 넘어갔다")


class ReviewDoneOnlyTest(ForGIATestCase):
    """**완료만 보내는 요청** — 교정을 안 싣고 안 지운다 (116 덧).

    완료는 `(시야, 묶음)` 한 줄인데 판 단위 payload 를 타고 갔다. 그래서 표시
    하나를 켜는 일이 **그 판의 교정을 갈아치우는 일**이기도 했고, 화면이 어느
    판을 고르고 있느냐가 완료에까지 걸렸다 — 검출이 없는 판(깊이 맵)을 고르면
    `image` 가 비어 저장이 **대표 이미지**로 가서 그 판의 교정이 지워졌다.

    여기서 보는 것은 서버 쪽이다. 화면 쪽은 `tests/browser/test_done_plate.py`.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)

    def setUp(self):
        self.c = Client()
        self.image = self.w.detection().image
        self.keys = self.w.keys()[:2]
        # **묶음을 끄기 전에 잡아 둔다** — `w.stem()` 은 현재 검출을 물어서
        # 만든다(검토 대상이 없으면 그 자리에서 죽는다).
        self.stem = self.w.stem()
        # 사람의 교정을 심는다 — 이것이 지워지면 안 되는 것이다.
        data.save_review(self.w.vp, done=False, note="", removed=set(self.keys),
                         accepted=set(), labels={}, image=self.image.pk)
        self.assertEqual(self.marks(), 2, "심은 교정이 없다")

    def marks(self):
        return ObjectReview.objects.filter(image=self.image,
                                           removed=True).count()

    def post_done(self, on=True, expect=200, **over):
        p = {"stem": self.stem, "slug": self.w.slug, "gid": self.w.vp.idx,
             "only": "done", "done": on}
        p.update(over)
        r = self.c.post(reverse("save_review"), data=json.dumps(p),
                        content_type="application/json")
        self.assertEqual(r.status_code, expect, r.content[:300])
        return json.loads(r.content)

    def row(self):
        return ViewpointReview.objects.filter(
            viewpoint=self.w.vp, batch__isnull=False).first()

    # --- 켜고 끈다 ----------------------------------------------------------

    def test_완료가_켜진다(self):
        self.post_done(True)
        self.assertTrue(self.row() and self.row().done)

    def test_완료가_꺼진다(self):
        self.post_done(True)
        self.post_done(False)
        self.assertFalse(self.row().done)

    def test_묶음에_찍힌다(self):
        """시야 줄(`batch=None`)이 아니라 묶음 줄이어야 한다 (DiaRUGA 073)."""
        self.post_done(True)
        self.assertIsNotNone(self.row())
        self.assertFalse(ViewpointReview.objects.filter(
            viewpoint=self.w.vp, batch__isnull=True, done=True).exists())

    # --- 교정을 안 건드린다 (여기가 고장 났던 자리다) ------------------------

    def test_교정이_안_지워진다(self):
        self.post_done(True)
        self.assertEqual(self.marks(), 2,
                         "완료만 보냈는데 그 판의 교정이 지워졌다")

    def test_해제해도_교정이_안_지워진다(self):
        self.post_done(True)
        self.post_done(False)
        self.assertEqual(self.marks(), 2)

    def test_시야_코멘트는_안_건드린다(self):
        """코멘트는 사람이 쓴 글이라 재생성 불가다 — 완료가 지울 것이 아니다."""
        data.save_review(self.w.vp, done=False, note="가장자리가 깨졌다",
                         removed=set(self.keys), accepted=set(), labels={},
                         image=self.image.pk)
        self.post_done(True)
        note = ViewpointReview.objects.get(viewpoint=self.w.vp,
                                           batch__isnull=True).note
        self.assertEqual(note, "가장자리가 깨졌다")

    # --- 대조군 · 거절 -------------------------------------------------------

    def test_옛_화면의_전체_payload_는_그대로_돈다(self):
        """`only` 를 모르는 탭이 보내면 예전 길로 간다 — 그쪽은 갈아치운다."""
        r = self.c.post(reverse("save_review"), data=json.dumps(
            {"stem": self.stem, "slug": self.w.slug, "gid": self.w.vp.idx,
             "done": True, "removed": [], "accepted": [], "labels": {},
             "image": self.image.pk}), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertTrue(self.row().done)
        self.assertEqual(self.marks(), 0, "전체 payload 인데 안 갈아치웠다")

    def test_검토_대상_묶음이_없으면_거절한다(self):
        """조용히 아무 데나 찍지 않는다 — 무엇을 다 봤다는 말인지가 없다."""
        RunBatch.objects.update(for_review=False)
        out = self.post_done(True, expect=409)
        self.assertIn("검출이 없다", out.get("error", ""))
        self.assertFalse(self.row().done, "거절해 놓고 찍었다")

    def test_남의_시야를_짚으면_거절한다(self):
        """`stem` 검증은 이 길에서도 지난다 (DiaRUGA 053)."""
        self.post_done(True, expect=409, stem="Snap-99999")
        self.assertFalse(self.row().done, "거절해 놓고 찍었다")

    # --- 나머지 절반: 판 저장이 이 둘을 안 나른다 (180 B2) --------------------
    #
    # 116 이 완료를 **자기 문으로 보내게** 했지만, 판 payload 에는 그대로 실려
    # 다녔다. 그래서 같은 시야를 두 탭으로 열면 **한쪽에서 켠 완료와 적은 글을
    # 다른 탭의 마스크 저장 한 번이 되돌린다** — 어느 화면도 아무 말을 안 한다.

    def post_plate(self, expect=200, **over):
        """지금 화면이 보내는 판 payload — `done`·`note` 가 없다."""
        p = {"stem": self.stem, "slug": self.w.slug, "gid": self.w.vp.idx,
             "removed": list(self.keys), "accepted": [], "labels": {},
             "image": self.image.pk}
        p.update(over)
        r = self.c.post(reverse("save_review"), data=json.dumps(p),
                        content_type="application/json")
        self.assertEqual(r.status_code, expect, r.content[:300])
        return json.loads(r.content)

    def test_판_저장이_완료를_안_끈다(self):
        self.post_done(True)
        self.post_plate()
        self.assertTrue(self.row().done,
                        "다른 탭의 마스크 저장이 검토 완료를 껐다")

    def test_판_저장이_시야_코멘트를_안_지운다(self):
        self.c.post(reverse("save_review"), data=json.dumps(
            {"stem": self.stem, "slug": self.w.slug, "gid": self.w.vp.idx,
             "only": "note", "note": "가장자리가 깨졌다"}),
            content_type="application/json")
        self.post_plate()
        self.assertEqual(
            ViewpointReview.objects.get(viewpoint=self.w.vp,
                                        batch__isnull=True).note,
            "가장자리가 깨졌다", "다른 탭의 마스크 저장이 코멘트를 지웠다")

    def test_코멘트만_보내면_교정을_안_건드린다(self):
        """완료와 같은 규칙이다 — 층이 다른 것을 한 요청에 안 싣는다."""
        self.c.post(reverse("save_review"), data=json.dumps(
            {"stem": self.stem, "slug": self.w.slug, "gid": self.w.vp.idx,
             "only": "note", "note": "여기 적는다"}),
            content_type="application/json")
        self.assertEqual(self.marks(), 2,
                         "코멘트만 보냈는데 그 판의 교정이 지워졌다")
        self.assertEqual(
            ViewpointReview.objects.get(viewpoint=self.w.vp,
                                        batch__isnull=True).note, "여기 적는다")

    def test_코멘트를_비우면_줄이_사라진다_문이_달라져도(self):
        for note in ("적었다", ""):
            self.c.post(reverse("save_review"), data=json.dumps(
                {"stem": self.stem, "slug": self.w.slug, "gid": self.w.vp.idx,
                 "only": "note", "note": note}),
                content_type="application/json")
        self.assertFalse(
            ViewpointReview.objects.filter(viewpoint=self.w.vp,
                                           batch__isnull=True).exists())

    def test_옛_탭이_보낸_완료_코멘트는_그대로_쓴다(self):
        """**배포 중에 열려 있던 탭**을 무시하면 그 탭에서 한 일이 사라진다."""
        self.post_plate(done=True, note="옛 탭이 적었다")
        self.assertTrue(self.row().done)
        self.assertEqual(
            ViewpointReview.objects.get(viewpoint=self.w.vp,
                                        batch__isnull=True).note,
            "옛 탭이 적었다")

    def test_완료를_켜면_확인_표시가_붙는다(self):
        """**`confirm_kept` 가 `save_done` 으로 옮겨 왔다** (180 B2).

        예전에는 판 payload 의 `done` 이 그 자리를 맡았는데, 완료를 payload 에서
        떼면 그 표시를 달 자리가 없어진다 — **완료를 누르는 순간이 곧 "남는 것을
        확인했다" 는 순간**이라 거기가 제자리다.
        """
        self.assertFalse(ObjectReview.objects.filter(auto_confirmed=True)
                         .exists())
        self.post_done(True)
        self.assertTrue(
            ObjectReview.objects.filter(auto_confirmed=True).exists(),
            "완료를 켰는데 확인 표시가 안 붙었다")
