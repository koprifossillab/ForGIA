"""DiaRUGA v0.29.0 `tests/test_manage_split.py` 에서 왔다.

관리 화면을 셋으로 갈랐다 (DiaRUGA 083) — **묻는 것이 다르기 때문**이다.

| 화면 | 무엇을 묻는가 | 얼마나 자주 |
|---|---|---|
| 자료 | 이 관찰이 어느 지점·시료의 것인가 | 새 슬라이드가 들어올 때 |
| 운영 | 지금 무엇을 보고 있고 새 자료를 어떻게 채우는가 | 회차를 돌릴 때 |
| 학습 자료 | 검토한 것에서 정답을 얼마나 뽑을 수 있는가 | 다음 회차를 준비할 때 |

한 화면에 다 있으면 **자주 쓰는 것이 드물게 쓰는 것에 묻힌다** — 층 편집표 셋
아래에 묶음 고르기가 있었다.
"""
from django.test import Client
from django.urls import reverse

from .base import ForGIATestCase, write_blob
from . import factories as fx
from .. import manage_data
from ..models import Detection, ObjectReview, RunBatch, ViewpointReview


class ManageSplitTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_viewpoints=2, n_candidates=3)
        cls.yolo = fx.add_other_engine(cls.w.vp, label="yolo-재학습")
        Detection.objects.filter(run=cls.yolo).update(is_current=True)

    def setUp(self):
        self.c = Client()

    def test_세_화면이_다_뜬다(self):
        for name in ("system_settings", "system_settings_ops", "system_settings_pipeline"):
            with self.subTest(name=name):
                self.assertEqual(self.c.get(reverse(name)).status_code, 200)

    def test_서로에게_가는_길이_있다(self):
        """**길이 없으면 없는 화면이다** — 주소를 직접 치게 두지 않는다."""
        for name in ("system_settings", "system_settings_ops", "system_settings_pipeline"):
            body = self.c.get(reverse(name)).content.decode()
            for other in ("system_settings", "system_settings_ops", "system_settings_pipeline"):
                self.assertIn(f'href="{reverse(other)}"', body,
                              f"{name} 에서 {other} 로 가는 길이 없다")

    def test_묶음_고르기는_운영에만_있다(self):
        data_body = self.c.get(reverse("system_settings")).content.decode()
        ops_body = self.c.get(reverse("system_settings_ops")).content.decode()
        self.assertNotIn("검토할 묶음", data_body, "자료 화면에 아직 남아 있다")
        self.assertIn("검토할 묶음", ops_body)

    def test_층_편집은_자료에만_있다(self):
        data_body = self.c.get(reverse("system_settings")).content.decode()
        ops_body = self.c.get(reverse("system_settings_ops")).content.decode()
        self.assertIn("지역", data_body)
        self.assertNotIn('name="act" value="move_slide"', ops_body)


class RecipeFormTest(ForGIATestCase):
    """조리법을 화면에서 적는다 (083 · 079 의 나머지 절반)."""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)

    def setUp(self):
        self.c = Client()
        self.b = RunBatch.objects.get(label="yolo-시험")

    def post(self, **kw):
        form = {"act": "recipe", "batch": self.b.pk}
        form.update(kw)
        return self.c.post(reverse("system_settings_ops"), form)

    def test_적으면_저장된다(self):
        # ForGIA 는 백엔드가 YOLO 하나라 가중치가 함께 가야 한다
        write_blob("models/w.pt")
        self.post(backend="yolo", weights="models/w.pt", scale="1.0",
                  min_um="63", max_um="2000")
        self.b.refresh_from_db()
        self.assertEqual(self.b.recipe["backend"], "yolo")
        self.assertEqual(self.b.recipe["scale"], 1.0)

    def test_엔진을_비우면_안_돈다(self):
        """**끝난 회차를 그대로 두는 것이 기본이다.**"""
        write_blob("models/w.pt")
        self.post(backend="yolo", weights="models/w.pt")
        self.post(backend="")
        self.b.refresh_from_db()
        self.assertEqual(self.b.recipe, {})

    def test_yolo_는_가중치가_있어야_한다(self):
        from urllib.parse import unquote
        r = self.post(backend="yolo")
        self.b.refresh_from_db()
        self.assertEqual(self.b.recipe, {})
        self.assertIn("가중치", unquote(r.url))   # 주소는 퍼센트 인코딩이다

    def test_가중치_파일이_없어도_적을_수는_있다(self):
        """아직 학습이 안 끝났는데 조리법을 먼저 적어 둘 수 있어야 한다 —
        대신 목록에서 '못 돌림' 으로 뜬다."""
        ok, m = manage_data.set_recipe(
            self.b.pk, {"backend": "yolo", "weights": "models/아직없다.pt"})
        self.assertTrue(ok)
        self.assertIn("아직 없습니다", m)
        self.b.refresh_from_db()
        self.assertEqual(self.b.recipe["weights"], "models/아직없다.pt")

    def test_숫자가_아니면_거절한다(self):
        write_blob("models/w.pt")
        ok, m = manage_data.set_recipe(self.b.pk, {"backend": "yolo",
                                                   "weights": "models/w.pt",
                                                   "scale": "빠르게"})
        self.assertFalse(ok)
        self.assertIn("숫자가 아닙니다", m)

    def test_화면이_실제로_돌_명령과_같은_것을_보인다(self):
        """**화면이 다른 것을 보여주면 사람이 화면을 믿고 틀린 명령을 만든다.**"""
        write_blob("models/w.pt")
        manage_data.set_recipe(self.b.pk, {"backend": "yolo",
                                           "weights": "models/w.pt",
                                           "all_images": "on"})
        body = self.c.get(reverse("system_settings_ops")).content.decode()
        self.assertIn("--backend yolo", body)
        self.assertIn("--weights models/w.pt", body)
        self.assertIn("--all-images", body)



# `TrainingOverviewTest`(학습 자료 탭 · `manage_data.training_overview`)는 4단계에서 온다

class CreateBatchTest(ForGIATestCase):
    """묶음을 화면에서 만든다 (DiaRUGA 084).

    지금까지 묶음은 **파이프라인이 `--batch` 로 처음 쓸 때** 생겼다. 그래서
    "다음 회차를 이렇게 돌리겠다" 를 미리 적어 둘 수가 없었다 — 조리법을 적으려면
    묶음이 먼저 있어야 하는데, 묶음을 만들려면 검출을 한 번 돌려야 했다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)

    def setUp(self):
        self.c = Client()
        self.base = RunBatch.objects.get(label="yolo-시험")

    def post(self, **kw):
        form = {"act": "new_batch"}
        form.update(kw)
        return self.c.post(reverse("system_settings_ops"), form)

    def test_만들어진다(self):
        self.post(label="yolo-4차")
        b = RunBatch.objects.filter(kind="detect", label="yolo-4차").first()
        self.assertIsNotNone(b)
        self.assertEqual(b.recipe, {}, "새 묶음이 조리법을 갖고 태어났다")
        self.assertFalse(b.for_review, "만들자마자 검토 대상이 됐다")

    def test_이름이_없으면_거절한다(self):
        ok, m = manage_data.create_batch({"label": "  "})
        self.assertFalse(ok)
        self.assertIn("이름", m)

    def test_같은_이름은_거절한다(self):
        ok, m = manage_data.create_batch({"label": "yolo-시험"})
        self.assertFalse(ok)
        self.assertIn("이미 있는", m)

    def test_조리법을_베껴_온다(self):
        """새 회차는 대개 지난 회차에서 가중치만 바뀐 것이다."""
        write_blob("models/w.pt")
        manage_data.set_recipe(self.base.pk, {"backend": "yolo", "weights": "models/w.pt",
                                              "scale": "1.0", "min_um": "63"})
        ok, m = manage_data.create_batch({"label": "yolo-5차",
                                          "copy_from": str(self.base.pk)})
        self.assertTrue(ok, m)
        b = RunBatch.objects.get(label="yolo-5차")
        self.assertEqual(b.recipe["backend"], "yolo")
        self.assertEqual(b.recipe["min_um"], 63)

    def test_추측_표시는_안_물려준다(self):
        """물려주면 경고가 영영 따라다닌다 — 새 묶음의 가중치는 사람이 다시 본다."""
        self.base.recipe = {"backend": "yolo", "weights": "models/w.pt",
                            "weights_guessed": True}
        self.base.save(update_fields=["recipe"])
        manage_data.create_batch({"label": "yolo-9차",
                                  "copy_from": str(self.base.pk)})
        self.assertNotIn("weights_guessed",
                         RunBatch.objects.get(label="yolo-9차").recipe)

    def test_만들었다고_자료가_생기지는_않는다(self):
        """**이미 있는 슬라이드는 사람이 한 번 돌려야 한다** — 몇 시간짜리
        GPU 작업이라 화면이 조용히 시작하면 안 된다. 그 말을 응답에 담는다."""
        ok, m = manage_data.create_batch({"label": "yolo-6차"})
        self.assertTrue(ok)
        self.assertIn("이미 있는 슬라이드", m)
        self.assertEqual(
            Detection.objects.filter(run__batch__label="yolo-6차").count(), 0)

    def test_조리법을_적으면_실행_계획에_오른다(self):
        write_blob("models/w.pt")
        manage_data.create_batch({"label": "yolo-7차"})
        b = RunBatch.objects.get(label="yolo-7차")
        manage_data.set_recipe(b.pk, {"backend": "yolo",
                                      "weights": "models/w.pt"})
        body = self.c.get(reverse("system_settings_ops")).content.decode()
        self.assertIn("yolo-7차", body)
        from .. import data
        self.assertIn("yolo-7차", [r["batch"].label for r in data.batches_to_run()])
