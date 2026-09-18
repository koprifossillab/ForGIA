"""DiaRUGA v0.29.0 `tests/test_batch_plan.py` 에서 왔다.

새 자료를 **어느 묶음에 어떤 순서로** 채우는가 (DiaRUGA 079).

묶음이 여럿인 것이 기본이 됐다. 폴러가 아는 묶음 하나에만 넣으면, 사람이 다른
묶음으로 갈아탄 순간 **그 사이에 들어온 슬라이드가 빈 화면**이다.

순서는 사람이 정했다(2026-08-07): **검토 중인 묶음이 먼저, 나머지는 최근
것부터.** GPU 는 한 번에 하나만 도므로 이 순서가 곧 기다리는 순서다.
"""
from datetime import timedelta

from django.utils import timezone

from .base import ForGIATestCase, write_blob
from . import factories as fx
from .. import data
from ..models import RunBatch


class BatchesToRunTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)

    def batch(self, label, *, recipe=None, review=False, age_days=0):
        b = RunBatch.objects.create(kind="detect", label=label,
                                    recipe=recipe or {})
        # `auto_now_add` 를 우회한다 — 순서를 시험하려면 시각을 손으로 정해야 한다
        RunBatch.objects.filter(pk=b.pk).update(
            started_at=timezone.now() - timedelta(days=age_days),
            for_review=review)
        return RunBatch.objects.get(pk=b.pk)

    def labels(self):
        return [r["batch"].label for r in data.batches_to_run()]

    # --- 무엇이 목록에 오르는가 --------------------------------------------

    def test_조리법이_없으면_안_돈다(self):
        """**끝난 회차를 그대로 두는 것이 기본이다** — 묶음이 늘 때마다 GPU
        시간이 곱으로 는다."""
        self.batch("옛회차")
        self.assertNotIn("옛회차", self.labels())

    def test_조리법이_있으면_오른다(self):
        self.batch("새회차", recipe={"backend": "sam2", "scale": 1.0})
        self.assertIn("새회차", self.labels())

    # --- 순서 --------------------------------------------------------------

    def test_검토_중인_묶음이_먼저다(self):
        """**이름순으로도, 최근순으로도 1등이 아닌 것**을 검토 중으로 둔다 —
        그래야 "검토 중이라서 먼저" 를 실제로 시험한다. 처음에는 이름이 앞서는
        것을 골라, 정렬을 이름순으로 되돌려도 시험이 안 깨졌다."""
        self.batch("aaa-어제", recipe={"backend": "sam2"}, age_days=1)
        b = RunBatch.objects.get(label="yolo-시험")
        RunBatch.objects.filter(pk=b.pk).update(recipe={"backend": "sam2"})
        RunBatch.objects.filter(pk=b.pk).update(
            started_at=timezone.now() - timedelta(days=100))
        got = self.labels()
        self.assertEqual(got[0], "yolo-시험", f"검토 중인 것이 먼저가 아니다: {got}")
        self.assertIn("aaa-어제", got)

    def test_나머지는_최근_것부터다(self):
        self.batch("작년", recipe={"backend": "sam2"}, age_days=300)
        self.batch("어제", recipe={"backend": "sam2"}, age_days=1)
        self.batch("지난달", recipe={"backend": "sam2"}, age_days=30)
        got = [x for x in self.labels() if x in ("작년", "어제", "지난달")]
        self.assertEqual(got, ["어제", "지난달", "작년"])

    # --- 가중치 -------------------------------------------------------------

    def test_가중치가_없으면_못_돌린다고_적는다(self):
        """**조용히 빼지 않는다** — 그러면 "왜 이 묶음만 비어 있나" 를
        나중에 묻게 된다."""
        self.batch("yolo-4차", recipe={"backend": "yolo",
                                       "weights": "models/없는것.pt"})
        row = next(r for r in data.batches_to_run()
                   if r["batch"].label == "yolo-4차")
        self.assertFalse(row["ready"])
        self.assertIn("가중치 파일이 없다", row["why"])

    def test_가중치가_있으면_돌린다(self):
        p = write_blob("models/있는것.pt")
        self.assertTrue(p.exists())
        self.batch("yolo-5차", recipe={"backend": "yolo",
                                       "weights": "models/있는것.pt"})
        row = next(r for r in data.batches_to_run()
                   if r["batch"].label == "yolo-5차")
        self.assertTrue(row["ready"], row["why"])

    def test_sam2_는_가중치를_안_본다(self):
        """SAM2 가중치는 HF 캐시에서 온다 — 조리법에 없는 것이 정상이다."""
        self.batch("sam2-6차", recipe={"backend": "sam2"})
        row = next(r for r in data.batches_to_run()
                   if r["batch"].label == "sam2-6차")
        self.assertTrue(row["ready"], row["why"])
