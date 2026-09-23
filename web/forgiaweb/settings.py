"""
ForGIA 설정. DiaRUGA v0.29.0 `web/diarugaweb/settings.py` 에서 왔다 (P01).

데이터와 설정은 SQLite(`ForGIA.db`)에 모은다 — DiaRUGA 가 JSON 에서 DB 로
옮긴 이유(DiaRUGA P02)를 그대로 물려받는다: NAS 로 사진이 계속 들어오면 파일
읽기가 선형으로 늘고, 실행 이력·처리 상태·문턱 이력을 둘 곳이 없다.

`review/*.json` 은 사람이 읽고 diff 할 감사 기록이라 git 에 둔다(DB 파일은
gitignore).
"""
import os
import tempfile
from pathlib import Path

# web/forgiaweb/settings.py -> web/ -> 프로젝트 루트
BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent


def _load_dotenv(path):
    """프로젝트 루트의 .env 를 환경변수로 올린다 (이미 있는 값은 덮지 않는다).

    사진이 저장소 밖(/data3/ForGIA)으로 나가면서 호스트에서 스크립트를 그냥
    돌렸을 때도 데이터 위치를 알아야 해서 둔다 (DiaRUGA P03). 컨테이너는 compose 가
    환경변수를 직접 주므로 이 파일 없이도 돈다 — 그래서 없어도 조용히 넘어간다.

    라이브러리를 하나 더 들이지 않으려고 최소한만 읽는다. KEY=VALUE 와 주석뿐,
    따옴표·여러 줄·치환은 다루지 않는다.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(PROJECT_ROOT / ".env")

# 사내망 분석용 뷰어. 인증이 없으므로 공개망에 그대로 올릴 물건이 아니다.
SECRET_KEY = os.environ.get("FORGIA_SECRET_KEY", "dev-only-not-a-secret")
DEBUG = os.environ.get("FORGIA_DEBUG", "1") == "1"

# 이 서버가 실제로 응답할 이름만 명시한다. "*" 로 두면 DEBUG=True 와 겹칠 때
# Host 헤더 스푸핑을 그대로 받아들이게 된다.
# 다른 호스트명으로 붙어야 하면 FORGIA_HOSTS 에 콤마로 구분해 넣는다.
ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "[::1]",
    "172.16.116.98",    # 이 서버의 사내망 주소
    "paleo-server",     # 이 머신의 hostname
    # 형제 서비스들이 쓰는 이름. nginx 의 phyloserver 블록이
    # `server_name 172.16.116.98 paleolab` 이고 /ForGIA/ 이 그 안에 얹혀 있어,
    # 여기 없으면 이름으로 들어온 요청만 400 이 된다.
    "paleolab",
]
ALLOWED_HOSTS += [h.strip() for h in os.environ.get("FORGIA_HOSTS", "").split(",") if h.strip()]

# 서브경로 아래에 얹을 때 쓴다 (예: "/ForGIA"). 빈 값이면 뿌리(/)에 붙는다.
#
# 왜 필요한가: 사내 VPN 이 80 만 통과시킨다. 이 머신의 80 은 phyloserver 블록이
# server_name 172.16.116.98 로 이미 잡고 있어서 뷰어를 /ForGIA/ 아래로 넣는 것
# 말고는 방법이 없다(호스트명 기반 vhost 는 IP 로 들어오는 VPN 사용자에게 안
# 걸린다). DiaRUGA 가 `/DiaRUGA/` 로 같은 자리에 얹혀 있다.
#
# 이 값을 주면 Django 의 reverse()·{% url %} 가 앞에 뿌리를 붙인다. 템플릿의
# JS 는 base.html 이 내보내는 window.ROOT 를 쓴다 — 절대경로를 박아 두면 여기만
# 바꿔서는 안 돌아간다.
FORCE_SCRIPT_NAME = os.environ.get("FORGIA_SCRIPT_NAME", "").rstrip("/") or None

# 폼이 없어 CSRF 표면은 없지만, DEBUG 를 끄고 쓸 때를 위해 맞춰 둔다.
# 컨테이너 포트(직접)와 80(nginx 서브경로) 둘 다 살아 있다.
CSRF_TRUSTED_ORIGINS = [
    f"http://{h}:9090" for h in ALLOWED_HOSTS if not h.startswith("[")
] + [
    f"http://{h}" for h in ALLOWED_HOSTS if not h.startswith("[")
]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "viewer",
]

# 인증/세션이 없으므로 미들웨어도 최소한만.
# 잠금 화면(viewer/gate.py)은 아래 GATE_CODE 가 비어 있으면 아무 일도 안 한다.
MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    "viewer.gate.GateMiddleware",
]

# 접속 코드 (viewer/gate.py 머리말). **비어 있으면 잠그지 않는다** — 시험·개발
# 서버는 이 갈래다. 운영은 /srv/ForGIA/.env 에 넣는다. 바꾸면 이미 들어온
# 브라우저도 모두 다시 묻는다.
GATE_CODE = os.environ.get("FORGIA_GATE_CODE", "").strip()

ROOT_URLCONF = "forgiaweb.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": [
            # 판 번호를 머리글에 띄운다 (viewer/context.py 머리말 참고)
            "viewer.context.version",
        ]},
    }
]

WSGI_APPLICATION = "forgiaweb.wsgi.application"

# GPU 배치가 쓰는 동안 뷰어가 읽어야 하므로 WAL 이 필수다. 기본 journal 모드면
# 쓰기 트랜잭션이 읽기를 막는다.
FORGIA_DB = Path(os.environ.get("FORGIA_DB", PROJECT_ROOT / "ForGIA.db"))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": FORGIA_DB,
        "OPTIONS": {
            "timeout": 20,
            "init_command": (
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA foreign_keys=ON;"
            ),
        },
        # **시험 DB 는 파일로 만든다** (DiaRUGA 183). Django 의 sqlite 시험 DB 기본값은
        # **공유 캐시 인메모리**(`file:memorydb_default?mode=memory&cache=shared`)
        # 인데, 그 모드의 잠금은 **테이블 단위**라(`SQLITE_LOCKED`) busy 핸들러가
        # 물러섰다 다시 잡는 길이 없다 — **기다려도 안 풀린다.**
        #
        # 브라우저 겹이 CI 에서 그 자리에 걸렸다. `LiveServerTestCase` 는 서버
        # 스레드가 따로 있어 뒷정리(`flush`)와 요청이 겹치는데, 시험이 늘자
        # `database table is locked: viewer_objectreview` 로 죽기 시작했고
        # 재시도를 15.7초로 늘려도 그대로였다(로컬 파일 DB 에서는 안 났다).
        # 파일이면 잠금이 DB 단위이고 `timeout` 이 실제로 듣는다.
        #
        # 이름에 `test` 와 pid 가 들어간다 — 앞엣것은 `tests/base.assert_test_db`
        # 가 운영 DB 와 가르는 표식이고, 뒤엣것은 두 벌이 동시에 돌 때 서로
        # 안 밟게 한다. Django 가 끝나면 지운다.
        "TEST": {
            "NAME": os.environ.get(
                "FORGIA_TEST_DB",
                str(Path(tempfile.gettempdir())
                    / f"ForGIA_test_{os.getpid()}.db")),
        },
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 슬래시로 시작하지 않는 것이 중요하다 — 상대경로면 Django 가 SCRIPT_NAME 을
# 앞에 붙여 준다. "/static/" 로 박으면 서브경로 배포에서 어긋난다.
STATIC_URL = "static/"

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

# --- 뷰어 전용 설정 -------------------------------------------------------

# 원본 이미지와 산출물이 놓인 곳. 이미지 서빙은 이 목록 안으로만 허용한다.
#
# 뿌리를 환경변수로 뺀 것은 컨테이너 때문이다 (DiaRUGA P03). 컨테이너 안에서는 /data 로,
# 호스트에서는 /data3/ForGIA 으로 같은 디렉토리를 가리킨다. 경로는 전부 이 뿌리
# 기준 상대경로로 다루므로 뿌리만 바꾸면 나머지는 그대로 돈다.
DATA_ROOT = Path(os.environ.get("FORGIA_DATA_ROOT", PROJECT_ROOT))
# `atlas` 는 도감 도판이다 (DiaRUGA P15 §6) — 사진이 아니라 스캔한 책이지만
# **여는 길이 같다**: `/img?p=…&w=` 가 축소본을 만들어 캐시한다. 따로 문을
# 내면 그 캐시·경로 검사(`safe_image_path`)를 한 벌 더 짜게 된다.
IMAGE_DIRS = ["photos", "stacked", "out", "out_hi", "atlas"]

# 검출 결과(<stem>_candidates.json)를 찾을 순서.
DETECT_DIRS = ["out_hi", "out"]

# focus_stack.py 기본 출력 위치.
STACK_DIR = "stacked"

# 사람이 교정한 결과(오검출 제거 / 누락 복구). 산출물이 아니라 사람의 판단이므로
# out/ 과 달리 재생성되지 않는다 — 지우면 복구할 수 없다.
#
# 그래서 이것만은 DATA_ROOT 를 따라가지 않는다. 사진·산출물은 용량 때문에
# /data3 로 나갔지만 review/ 는 git 이 추적하는 감사 기록이라 저장소에 남는다
# (DiaRUGA P03).
REVIEW_ROOT = PROJECT_ROOT
REVIEW_DIR = "review"


# 썸네일 캐시. 원본을 그리드에 그대로 물리면 못 쓴다.
#
# 컨테이너에서는 이미지 안(/app)이 읽기 전용이나 마찬가지라 여기 못 쓴다.
# 데이터 쪽으로 빼면 이미지를 다시 구워도 캐시가 살아남는다 (DiaRUGA P03).
THUMB_CACHE = Path(os.environ.get("FORGIA_THUMB_CACHE", BASE_DIR / ".thumbcache"))

# --- 안전망 상태 (/healthz 가 읽는다) -------------------------------------

# 백업 사본이 놓인 곳. backup_db.py 와 같은 환경변수를 본다 — 값이 갈리면
# /healthz 가 아무도 안 쓰는 디렉토리를 보며 "최신" 이라고 말하게 된다.
BACKUP_DIR = Path(os.environ.get("FORGIA_BACKUP_DIR", PROJECT_ROOT / "backup"))

# 가장 새 스냅샷이 이보다 오래면 degraded (data-safety.md §4 의 신선도 게이트).
#
# **배포에서는 3 으로 켜져 있다** (cron 이 매시 :20 에 뜬다 — 034). 두 번 연달아
# 걸러야 울린다. 잡는 것은 둘이다 — 시간별 백업이 멈췄거나, 무결성 게이트가
# 채택을 막고 있거나. **배포와 무관하게 도는 신호**라 "배포가 뜸하면 탐지도
# 뜸해지는" 구멍을 메운다.
#
# **기본은 꺼 둔다.** cron 이 없는 장비(개발 체크아웃)에서 켜면 늘 울리고,
# 늘 울리는 경보는 안 보게 된다. 그때 /healthz 는 나이를 **알려만** 준다.
#
# 위의 BACKUP_DIR 은 자동 스냅샷만 있는 윗단이다 — 손으로 뜬 것은 manual/ 로
# 갈라져 있어 여기 안 걸린다. 사람이 뜬 사본이 섞이면 죽은 cron 을 가린다.
try:
    BACKUP_MAX_AGE_H = float(os.environ.get("FORGIA_BACKUP_MAX_AGE_H", "") or 0) or None
except ValueError:
    BACKUP_MAX_AGE_H = None
