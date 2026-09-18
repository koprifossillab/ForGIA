"""뷰어 자동 시험. DiaRUGA v0.29.0 `tests/__init__.py` 에서 왔다 (그쪽 P08).

**커버리지를 목표로 하지 않는다.** 여기 있는 시험은 전부 실제로 한 번씩 당한
사고에 대응한다 — DiaRUGA 027(빈 목록 POST) · 051(되는 것처럼 보이는 읽기 전용) ·
053(다른 슬라이드의 시야) · 057(링크를 못 만들어 전체 500) · 038~040(분류의
여덟 칸). 공통점은 **예외도 경고도 안 난다** 는 것이고, 그래서 사람 눈으로는
안 걸린다.

    ~/venv/ForGIA/bin/python web/manage.py test viewer

**컨테이너 밖에서 돈다.** DB 규약(`dbrun.sh`)이 막으려는 것은 "같은 파일을 두
벌의 환경이 만진다" 인데, 시험은 자기 DB 를 새로 만들고 끝나면 버린다 —
`backup_db.py`·`export_review.py` 와 같은 자리다. 그것을 사람이 기억하는 대신
`base.ForGIATestCase` 가 확인한다.
"""
import sys
from pathlib import Path

# `judge.py` 는 저장소 뿌리에 있다 — `web/` 안이 아니다. 앱에서는
# `viewer/thresholds.py` 가 임포트 직전에 뿌리를 `sys.path` 에 넣어서 되는데,
# **시험은 그 모듈을 안 거치고 `judge` 를 바로 부를 수 있다**(1겹은 Django 를
# 안 쓰는 것이 요점이다). 그래서 여기서 같은 일을 한 번 해 둔다 — 패키지
# `__init__` 이라 어느 시험 모듈보다 먼저 돈다.
_ROOT = Path(__file__).resolve().parents[3]
# **스크립트가 갈려 있다** (DiaRUGA 100) — 판정 규칙은 `pipeline/judge.py`, 무결성
# 깃발은 `ops/db_sentinel.py`, 재바인딩은 `pipeline/rebind.py` 다. 뿌리만
# 넣으면 `import judge` 가 안 돈다.
for _d in (_ROOT, _ROOT / "pipeline", _ROOT / "ops", _ROOT / "migrate"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))
