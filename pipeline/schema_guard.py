#!/usr/bin/env python3
"""DiaRUGA v0.29.0 `pipeline/schema_guard.py` 에서 왔다. 이미지 안의 모델 코드와 DB 의 스키마가 같은 판인지, **DB 를 만지기 전에** 본다 (198).

    import schema_guard
    schema_guard.check_or_exit("segment_forams")     # 어긋나면 SystemExit(3)

## 왜

파이프라인 컨테이너는 스크립트만 `/srv/ForGIA/scripts` 것을 쓰고 **Django
모델은 이미지 안의 `/app`** 을 쓴다(100). 뷰어와 파이프라인은 판이 따로라
(`IMAGE_TAG`/`PIPELINE_TAG`) 뷰어 마이그레이션이 칼럼을 걷으면 파이프라인
이미지의 모델은 그 칼럼을 계속 안다 — 그 행을 SELECT 하는 순간 `no such
column` 으로 죽는다.

114 에서 "파이프라인은 `ObjectReview` 를 안 읽는다" 고 실측하고 `0035`·`0036`
을 뷰어만 올렸는데, `rebind.rebind_viewpoint` 가 검출을 저장할 때마다 그 행을
읽고 있었다. 08-09 이후 새 슬라이드가 없어 한 달을 숨어 있다가 09-15 에
슬라이드 6개가 들어오자 **검출 저장이 1분마다 4,200번 실패했고**, 저장의
첫 트랜잭션은 이미 커밋된 뒤라 실패마다 `Detection`·`Candidate` 가 남아 DB 가
104 → 634 MB 로 불었다. `/healthz` 는 그동안 `ok` 였다.

사람이 "안 닿는다" 를 코드 기준으로 확인하는 것은 코드가 바뀌면 낡는다. 그래서
**기계가 판을 대조한다** — GPU 를 올리기 전, `Run` 행을 만들기 전에.

## 무엇을 보나

**이미지의 모델이 아는 칼럼이 DB 에 전부 있는가.** 마이그레이션 번호를 맞춰
보는 것이 아니다 — 그러면 뷰어가 칼럼 하나를 **더할** 때마다 파이프라인
이미지(7 GB)를 다시 구워야 한다. 더한 칼럼은 옛 모델이 모르니 SELECT 도
안 하고, INSERT 는 `db_default` 가 받는다(CLAUDE.md 가 그래서 `db_default` 를
요구한다). 위험한 것은 **옛 모델이 아는 칼럼이 걷힌 경우**뿐이고, 이번 사고가
그것이다. 그래서 모델의 실제 칼럼(`db_column`)을 DB 의 테이블 정의와 맞춰 본다.

- **테이블이 없다** — 모델 이름을 바꿨거나(`Core` → `Locality` · 063) 표를 걷었다
- **칼럼이 없다** — 칼럼을 걷었거나 옮겼다(`ObjectReview.note` → 개체 · 0036)

이미지가 DB 보다 새 판이라 **새 칼럼을 DB 가 아직 모르는** 경우도 같은 검사에
걸린다(뷰어를 안 올린 채 파이프라인만 구웠을 때).

어느 쪽이든 **돌지 않는다.** 판을 맞추는 것은 사람의 일이고(`PIPELINE_TAG`
올리기 또는 뷰어 먼저 올리기) 여기서는 무엇이 없는지만 정확히 말한다.
마이그레이션 장부의 차이는 참고로 함께 적는다.

## 소리를 낸다

폴러는 0 이 아닌 종료를 "검출 실패" 로 로그에 적을 뿐이라, 로그를 안 보는
동안은 아무도 모른다. `db_sentinel` 깃발을 세워 `/healthz` 를 `degraded` 로
만든다 — 백업 무결성 실패가 가는 그 통로다(034). 맞으면 자기 줄만 지운다.

Django 가 세워진 뒤에 부른다 (`django.setup()` 다음).
"""
from __future__ import annotations

import sys
from pathlib import Path

SOURCE = "schema_guard"


def compare(app_label: str = "viewer") -> tuple[list[str], list[str]]:
    """(DB 에 없는 테이블, DB 에 없는 칼럼 `테이블.칼럼`). 둘 다 비면 안전하다."""
    from django.apps import apps
    from django.db import connection

    with connection.cursor() as cur:
        tables = set(connection.introspection.table_names(cur))
        missing_tables, missing_cols = [], []
        for model in apps.get_app_config(app_label).get_models(include_auto_created=True):
            table = model._meta.db_table
            if table not in tables:
                missing_tables.append(table)
                continue
            have = {c.name for c in connection.introspection.get_table_description(cur, table)}
            for f in model._meta.local_concrete_fields:
                if f.column not in have:
                    missing_cols.append(f"{table}.{f.column}")
    return sorted(missing_tables), sorted(missing_cols)


def migration_gap(app_label: str = "viewer") -> tuple[list[str], list[str]]:
    """(DB 에만 있는 마이그레이션, 이미지에만 있는 마이그레이션) — 참고용."""
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(connection)
    applied = {name for app, name in loader.applied_migrations if app == app_label}
    known = {name for app, name in loader.graph.nodes if app == app_label}
    return sorted(applied - known), sorted(known - applied)


def check_or_exit(caller: str, app_label: str = "viewer") -> None:
    """어긋나면 깃발을 세우고 3 으로 끝낸다. 맞으면 자기 깃발을 지운다."""
    from django.conf import settings

    try:
        import db_sentinel                       # /srv/ForGIA/scripts 는 평평하다
    except ImportError:                          # 저장소에서는 ops/ 에 있다
        sys.path.append(str(Path(__file__).resolve().parent.parent / "ops"))
        import db_sentinel

    db_path = settings.FORGIA_DB
    tables, cols = compare(app_label)
    if not tables and not cols:
        db_sentinel.clear(db_path, SOURCE)
        return

    parts = []
    if tables:
        parts.append(f"DB 에 없는 테이블 {len(tables)}개 ({', '.join(tables[:3])})")
    if cols:
        parts.append(f"DB 에 없는 칼럼 {len(cols)}개 ({', '.join(cols[:3])})")
    db_only, image_only = migration_gap(app_label)
    if db_only:
        parts.append(f"DB 에만 있는 마이그레이션 {len(db_only)}개 "
                     f"({db_only[0]} ~ {db_only[-1]}) — 파이프라인 이미지가 낡았다. "
                     f"PIPELINE_TAG 를 올릴 것")
    if image_only:
        parts.append(f"이미지에만 있는 마이그레이션 {len(image_only)}개 "
                     f"({image_only[0]} ~ {image_only[-1]}) — DB 가 낡았다. "
                     f"뷰어를 먼저 올릴 것(migrate)")
    reason = f"{caller}: 모델 코드와 DB 스키마가 어긋난다 · " + " · ".join(parts)
    print(f"!! {reason}", file=sys.stderr)
    print("   DB 를 만지지 않고 멈춘다 — 판을 맞추기 전에는 파이프라인이 돌지 않는다.",
          file=sys.stderr)
    try:
        db_sentinel.raise_fail(db_path, SOURCE, reason)
    except OSError as e:                       # 깃발을 못 세워도 멈추는 것은 같다
        print(f"   (깃발을 세우지 못했다: {e})", file=sys.stderr)
    raise SystemExit(3)
