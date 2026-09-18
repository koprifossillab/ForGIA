"""DiaRUGA v0.29.0 `test_schema_guard.py` 에서 왔다 — 걷는 칼럼·표를 이 저장소에 있는 것(`viewer_frame.sharpness` · `viewer_stack`)으로 바꿨다."""
"""**모델 코드와 DB 의 판이 다르면 파이프라인이 DB 를 만지기 전에 멈춘다** (198 ·
`pipeline/schema_guard.py`).

09-15 에 파이프라인 이미지 `v0.5.2`(0032 까지)가 `0036` 이 걷은
`ObjectReview.note` 를 SELECT 하다 검출 저장이 1분마다 4,200번 죽었고, 실패마다
첫 트랜잭션이 남긴 검출·후보가 쌓여 DB 가 여섯 배가 됐다. 사람이 "안 닿는다"
고 확인한 것(114)은 코드가 바뀌면 낡는다 — 기계가 모델의 칼럼을 DB 와 대조한다.

**되살려서 잡히는 것**을 본다: 대조를 떼면 아래가 무너져야 한다.
"""
import tempfile
from pathlib import Path
from unittest import mock

from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import override_settings

from .base import ForGIATestCase

import db_sentinel
import schema_guard


class CompareTest(ForGIATestCase):
    """이미지의 모델이 아는 칼럼이 DB 에 전부 있는가 — **마이그레이션 번호가
    아니라 칼럼**을 본다. 더한 칼럼은 옛 모델이 모르니 위험하지 않고, 걷힌
    칼럼만 위험하다."""

    def test_시험_DB_는_같은_판이다(self):
        self.assertEqual(schema_guard.compare(), ([], []))

    def test_칼럼이_걷히면_잡는다(self):
        """**이번 사고의 모양** — 모델은 아는데 DB 에는 없는 칼럼 (0036 의 `note`)."""
        with connection.cursor() as cur:
            cur.execute("ALTER TABLE viewer_frame DROP COLUMN sharpness")
        tables, cols = schema_guard.compare()
        self.assertEqual(tables, [])
        self.assertEqual(cols, ["viewer_frame.sharpness"])

    def test_테이블이_걷히면_잡는다(self):
        """모델 이름을 바꾼 경우(`Core` → `Locality` · 063)가 이 모양이다."""
        with connection.cursor() as cur:
            cur.execute("PRAGMA foreign_keys=OFF")
            cur.execute("DROP TABLE viewer_stack")
        tables, cols = schema_guard.compare()
        self.assertIn("viewer_stack", tables)

    def test_DB_에_칼럼이_더_있는_것은_안_잡는다(self):
        """뷰어가 칼럼을 **더한** 판은 옛 파이프라인이 몰라도 된다 — `db_default`
        가 받는다. 이것까지 잡으면 칼럼 하나에 7 GB 이미지를 다시 굽게 된다."""
        with connection.cursor() as cur:
            cur.execute("ALTER TABLE viewer_slide ADD COLUMN from_the_future "
                        "varchar(8) NOT NULL DEFAULT ''")
        self.assertEqual(schema_guard.compare(), ([], []))

    def test_마이그레이션_장부_차이는_참고로_센다(self):
        MigrationRecorder.Migration.objects.create(app="viewer",
                                                   name="9999_from_the_future")
        self.assertEqual(schema_guard.migration_gap(),
                         (["9999_from_the_future"], []))


class CheckOrExitTest(ForGIATestCase):
    """어긋나면 3 으로 끝내고 **깃발을 세운다** — `/healthz` 가 그것을 읽는다."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "ForGIA.db"
        self.db.touch()

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def test_같으면_지나가고_자기_깃발을_지운다(self):
        db_sentinel.raise_fail(self.db, schema_guard.SOURCE, "지난 번의 것")
        db_sentinel.raise_fail(self.db, "backup_db", "남의 것")
        with override_settings(FORGIA_DB=self.db):
            schema_guard.check_or_exit("시험")           # 예외가 없어야 한다
        left = db_sentinel.read(self.db)
        self.assertEqual([r["source"] for r in left], ["backup_db"])

    def test_어긋나면_3_으로_끝내고_깃발을_세운다(self):
        with connection.cursor() as cur:
            cur.execute("ALTER TABLE viewer_frame DROP COLUMN sharpness")
        MigrationRecorder.Migration.objects.create(app="viewer",
                                                   name="9999_from_the_future")
        with override_settings(FORGIA_DB=self.db), \
                mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit) as cm:
                schema_guard.check_or_exit("segment_forams")
        self.assertEqual(cm.exception.code, 3)
        flags = db_sentinel.read(self.db)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["source"], schema_guard.SOURCE)
        self.assertIn("segment_forams", flags[0]["reason"])
        self.assertIn("viewer_frame.sharpness", flags[0]["reason"])
        self.assertIn("9999_from_the_future", flags[0]["reason"])
        self.assertIn("PIPELINE_TAG", flags[0]["reason"])
