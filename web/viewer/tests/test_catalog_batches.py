"""DiaRUGA v0.29.0 `tests/test_catalog_batches.py` 에서 왔다.

엔진마다 카탈로그가 **완전히 별개다** (사용자 방침 2026-08-10).

한국 권역은 SAM, 남극 권역은 YOLO 로 동정을 채울 계획이다. 그런데 **고르는
장치는 두지 않는다** — 카탈로그는 검토 대상 묶음 하나만 따라가고, 엔진을 갈려면
관리 화면에서 검토 대상을 간다. 그러면 **검토·크롭·카탈로그가 함께** 옮겨 가서
화면마다 다른 판을 보는 상태가 아예 안 생긴다. 051 이 그 상태에서 났다.

그래서 여기서 지키는 것은 **갈아탔을 때 판이 통째로 바뀌는가** 다.

1. 개체가 바뀐다 — 섞이면 SAM 판에 YOLO 개체가 앉는다
2. 번호의 꼬리가 바뀐다 — 번호만 보고 어느 검출인지 알 수 있어야 한다
3. **동정이 안 따라간다** — `ObjectReview` 의 열쇠에 `batch` 가 있어 그렇고,
   없이 두면 SAM 시절 판단이 YOLO 검출에 얹힌다 (실측 1,076건, P09 4.4)
4. 앞쪽 `관찰-시야-위치` 는 두 판이 같은 모양이다 — 나란히 놓고 읽으라고
"""
from . import factories as fx
from .base import ForGIATestCase
from .. import data
from ..models import ObjectReview, RunBatch


class SwitchReviewBatchTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # 두 번째 묶음 — **그 묶음 안에서 현재 검출**이다. 운영이 그 모양이다
        # (`yolo-시험`·`yolo-3차` 둘 다 `is_current` 가 켜져 있다).
        fx.add_other_engine(cls.w.vp, label="yolo-3차", current=True, code="Y3")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # **묶음마다 따로 세운다**: 개체의 열쇠에 묶음이 있어 카탈로그가 엔진마다
        # 완전히 별개다(`ForamObject.batch`). 한쪽만 세우면 갈아 낀 뒤 카드가
        # 없어 이 시험이 아무것도 안 본다.
        for _b in RunBatch.objects.all():
            for _vp in cls.w.viewpoints:
                fx.review_done(_vp, _b)

    def use(self, code):
        """관리 화면이 하는 일 — 검토 대상을 간다."""
        RunBatch.objects.update(for_review=False)
        RunBatch.objects.filter(code=code).update(for_review=True)

    # --- 머리에 어느 엔진인지 적힌다 ----------------------------------------

    def test_검토_대상의_이름과_코드를_낸다(self):
        self.use("S1")
        self.assertEqual(data.review_batch_info()["code"], "S1")
        self.assertEqual(data.review_batch_info()["label"], "yolo-시험")
        self.use("Y3")
        self.assertEqual(data.review_batch_info()["code"], "Y3")

    def test_검토_대상이_없으면_None_이다(self):
        RunBatch.objects.update(for_review=False)
        self.assertIsNone(data.review_batch_info())

    # --- 갈면 판이 통째로 바뀐다 --------------------------------------------

    def test_개체가_바뀐다(self):
        self.use("S1")
        a = {r["key"] for r in data.catalog_rows("rs23")}
        self.use("Y3")
        b = {r["key"] for r in data.catalog_rows("rs23")}
        self.assertTrue(a and b)
        self.assertFalse(a & b, "두 묶음의 개체가 섞였다")

    def test_번호_꼬리가_바뀐다(self):
        for code in ("S1", "Y3"):
            self.use(code)
            rows = data.catalog_rows("rs23")
            self.assertTrue(rows)
            for r in rows:
                self.assertTrue(r["catalog_no"].endswith(f"-{code}"),
                                r["catalog_no"])

    def test_앞쪽은_두_판이_같은_모양이다(self):
        for code in ("S1", "Y3"):
            self.use(code)
            for r in data.catalog_rows("rs23"):
                self.assertTrue(r["catalog_no"].startswith("RS23-GC03-071-g00-"),
                                r["catalog_no"])

    def test_동정이_판을_안_넘는다(self):
        """**이것이 "완전히 별개" 의 뜻이다.**"""
        self.use("S1")
        row = data.catalog_rows("rs23")[0]
        fx.new_review(
            viewpoint=self.w.vp, image_id=row["image_id"],
            batch_id=row["batch_id"], mask_key=row["key"],
            bind_method="exact", species="Globigerina bulloides")

        self.assertEqual(
            next(r["species"] for r in data.catalog_rows("rs23")
                 if r["key"] == row["key"]), "Globigerina bulloides")
        self.use("Y3")
        self.assertFalse(any(r["species"] for r in data.catalog_rows("rs23")))

    def test_되돌아오면_동정이_그대로_있다(self):
        """갈아탄 동안 사라지는 것이 아니라 **다른 판에 그대로 있다.**"""
        self.use("S1")
        row = data.catalog_rows("rs23")[0]
        fx.new_review(
            viewpoint=self.w.vp, image_id=row["image_id"],
            batch_id=row["batch_id"], mask_key=row["key"],
            bind_method="exact", species="Globigerina bulloides")
        self.use("Y3")
        data.catalog_rows("rs23")
        self.use("S1")
        self.assertEqual(
            next(r["species"] for r in data.catalog_rows("rs23")
                 if r["key"] == row["key"]), "Globigerina bulloides")


class NoReviewBatchTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def test_검토_대상이_없으면_빈_목록이다(self):
        """조용히 아무 묶음이나 보여주지 않는다 (DiaRUGA P10 3.6)."""
        RunBatch.objects.update(for_review=False)
        self.assertEqual(data.catalog_rows("rs23"), [])

    def test_없는_관찰은_빈_목록이다(self):
        self.assertEqual(data.catalog_rows("없는슬러그"), [])


class QueryCountTest(ForGIATestCase):
    """**어느 묶음인지는 한 번만 묻는다.**

    예전에는 `current_detections` 가 시야마다 `review_batch_id()` 를 다시 물었다 —
    실측으로 한 화면에 390~560번이고(`bp09-0901` 시야 86개), 값은 매번 같다.
    크롭 화면·계측 표도 같은 값을 치르고 있었다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_viewpoints=6, n_candidates=2)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def test_시야가_늘어도_묶음_조회는_한_번이다(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            rows = data.catalog_rows("rs23")
        self.assertTrue(rows)
        n = sum(1 for q in ctx.captured_queries if "for_review" in q["sql"])
        self.assertLessEqual(n, 1, f"검토 대상 묶음을 {n}번 물었다")
