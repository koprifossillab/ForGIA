"""DiaRUGA v0.29.0 `tests/test_catalog_model.py` 에서 왔다.

개체 카탈로그가 쓰는 두 칸 — `ObjectReview.species` · `RunBatch.code` (0031).

여기서 지키는 것은 **DB 가 막아야 하는 것들**이다. 화면에서 막는 것은 막는
것이 아니고(063), 코드가 지키는 규칙은 자리가 흩어지면 전부 틀린다.

    묶음 코드가 겹친다        → 두 회차의 카탈로그 번호가 겹친다
    묶음 코드가 `M` 이다      → 손그림 개체와 한 번호 아래 섞인다
    `db_default` 가 없다     → 옛 파이프라인 이미지의 INSERT 가 죽는다
"""
from django.db import IntegrityError, connection, transaction

from ..models import ForamObject, ObjectReview, RunBatch
from .base import ForGIATestCase
from .factories import make_world
from . import factories


class BatchCodeConstraintTest(ForGIATestCase):
    """묶음 코드 — **번호의 꼬리라서 겹치면 안 된다.**"""

    def test_같은_코드를_두_묶음이_못_가진다(self):
        """`yolo-3차`·`yolo-4차` 가 같은 코드로 누우면 두 회차의 개체가 같은
        카탈로그 번호를 받는다 — 그때는 이미 번호가 논문·표에 적힌 뒤다."""
        RunBatch.objects.create(kind="detect", label="yolo-3차", code="Y3")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RunBatch.objects.create(kind="detect", label="yolo-4차",
                                        code="Y3")

    def test_빈_코드는_여럿이어도_된다(self):
        """아직 코드를 안 정한 묶음이다. 그때는 번호가 아예 안 나고, 화면이
        그것을 적는다 — 조용히 라벨로 대신하지 않는다."""
        RunBatch.objects.create(kind="detect", label="a", code="")
        RunBatch.objects.create(kind="detect", label="b", code="")
        self.assertEqual(RunBatch.objects.filter(code="").count(), 2)

    def test_M_은_손그림_자리라_못_쓴다(self):
        """`catalog.MANUAL_CODE` 다. 묶음이 가져가면 사람이 그린 개체와 그 묶음의
        개체가 한 번호 아래 섞인다."""
        for bad in ("M", "m"):
            with self.assertRaises(IntegrityError, msg=bad):
                with transaction.atomic():
                    RunBatch.objects.create(kind="detect", label=f"x{bad}",
                                            code=bad)

    def test_기본값은_비어_있다(self):
        """묶음을 새로 만들 때 코드가 저절로 생기면 안 된다 — 정하는 것은
        사람이다 (`RunBatch.code` 머리말)."""
        b = RunBatch.objects.create(kind="detect", label="새 회차")
        self.assertEqual(b.code, "")


class SpeciesFieldTest(ForGIATestCase):
    """종명 — **재생성 불가한 자료가 앉는 칸이다.**"""

    def test_기본값은_비어_있다(self):
        w = make_world()
        det = w.detection()
        o = factories.new_review(
            viewpoint=w.vp, image=det.image, mask_key=w.keys()[0])
        self.assertEqual(o.species, "")

    def test_적은_것이_그대로_읽힌다(self):
        w = make_world()
        det = w.detection()
        name = "Fragilariopsis kerguelensis (O'Meara) Hustedt"
        o = factories.new_review(
            viewpoint=w.vp, image=det.image, mask_key=w.keys()[0],
            species=name)
        self.assertEqual(ObjectReview.objects.get(pk=o.pk).species, name)

    def test_유형과_다른_축이다(self):
        """`label` 은 `ClassDef` 목록에서 고르는 것(원형·봉상)이고 종명은 자유
        입력이다. 한 칸에 섞으면 둘 중 하나를 못 적는다."""
        w = make_world()
        det = w.detection()
        o = factories.new_review(
            viewpoint=w.vp, image=det.image, mask_key=w.keys()[0],
            label="foram", species="Globigerina bulloides")
        o.refresh_from_db()
        self.assertEqual((o.label, o.species), ("foram", "Globigerina bulloides"))


class DbDefaultTest(ForGIATestCase):
    """**`db_default` 가 실제로 DB 에 걸려 있는가.**

    뷰어와 파이프라인은 **판이 따로 돈다** — 뷰어 판을 올려 칼럼이 생겨도 옛
    파이프라인 이미지는 그 칼럼을 모르고 INSERT 한다. Django 의 `default` 는
    파이썬 쪽이라 그 INSERT 에는 아무것도 안 실린다 (HANDOFF 3.7).

    그래서 **옛 판의 INSERT 를 흉내 내서** 확인한다 — 스키마 문자열을 읽는 것으로는
    "값이 들어 있다" 와 "그 값이 유효하다" 를 못 가른다.
    """

    def test_species_없이_넣어도_들어간다(self):
        """**개체 쪽 `db_default` 를 본다** (DiaRUGA P12 에서 칸이 옮겨 갔다).

        `ForamObject` 를 만드는 옛 판 코드가 `species` 를 안 실어도 들어가야
        한다 — Django 의 `default` 는 파이썬 쪽이라 판이 다른 이미지의 INSERT
        에는 칼럼이 아예 안 들어간다(HANDOFF 3.7).
        """
        w = make_world()
        with connection.cursor() as cur:
            cur.execute("""
                INSERT INTO viewer_foramobject
                    (viewpoint_id, batch_id, label, note,
                     created_at, updated_at)
                VALUES (%s, NULL, '', '',
                        '2026-08-10 00:00:00', '2026-08-10 00:00:00')
            """, [w.vp.pk])
        o = ForamObject.objects.get(viewpoint=w.vp)
        self.assertEqual(o.species, "")

    def test_code_없이_넣어도_들어간다(self):
        with connection.cursor() as cur:
            cur.execute("""
                INSERT INTO viewer_runbatch
                    (kind, label, note, for_review, recipe, started_at)
                VALUES ('detect', '옛 판이 만든 묶음', '', 0, '{}',
                        '2026-08-10 00:00:00')
            """)
        self.assertEqual(RunBatch.objects.get(label="옛 판이 만든 묶음").code, "")
