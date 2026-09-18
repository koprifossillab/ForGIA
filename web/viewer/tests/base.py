"""모든 시험이 딛는 바닥. DiaRUGA v0.29.0 `tests/base.py` 에서 왔다. **운영 자료에 닿지 않는다는 것을 여기서 확인한다.**

Django 가 sqlite 에는 알아서 인메모리 테스트 DB 를 준다. 그래도 assert 를 넣는
이유는 이 저장소가 그 가정이 한 번 깨졌을 때 잃는 것이 **다시 만들 수 없는
자료**이기 때문이다. DiaRUGA HANDOFF 3.3 이 "시험 코드에 assert 로 사본인지 확인하는
줄을 넣는다" 고 적어 둔 것을, 사람이 기억하는 대신 베이스 클래스가 하게 한다.

## 문이 둘이다 — 처음에 하나만 잠갔다

**DB 만 확인하고 파일 쪽을 안 봤더니 시험이 `/data3/ForGIA` 에 그대로 썼다**
(DiaRUGA 2026-08-06, P08 1단계). 원인은 순서였다.

    TestCase.setUpClass()        ← 이 안에서 setUpTestData() 가 돈다
      └ setUpTestData()          ← 픽스처가 여기서 파일을 쓴다

`super().setUpClass()` 를 **먼저** 부르고 그 다음에 `DATA_ROOT` 를 갈았더니,
픽스처가 도는 동안에는 아직 진짜 뿌리였다. `photos/260801/` 과
`stacked/rs23/` 이 실제로 만들어졌다. 덮어쓴 것은 없었지만(진짜 `stacked/` 는
평면이고 `260801` 은 없던 날짜였다) **그건 운이었다.**

그래서 둘을 고쳤다.

1. **순서** — 뿌리를 먼저 갈고 `super().setUpClass()` 를 부른다
2. **쓰는 자리에서 다시 본다** — `write_image()` 가 임시 뿌리 밖이면 거절한다.
   순서에 기대는 안전은 다음에 순서가 바뀌면 조용히 풀린다

**"막는 것으로 끝나지 않는다"** 는 DiaRUGA 051 의 교훈이 시험 장치에도 그대로 든다.
문 하나만 잠근 안전 장치는 잠갔다는 착각을 준다는 점에서 안 잠근 것보다 나쁘다.
"""
from pathlib import Path
import shutil
import tempfile

from django.conf import settings
from django.db import connection
from django.test import TestCase, TransactionTestCase

# 이 시험 판이 만든 임시 뿌리들. `write_image()` 가 여기 안인지 본다.
_TMP_PREFIX = "forgia-test-"


def assert_test_db():
    """지금 열려 있는 DB 가 운영 DB 가 아님을 확인한다.

    Django 의 sqlite 테스트 DB 는 `:memory:` 이거나 `file:memorydb_...` 꼴이다.
    파일이면 이름에 `test` 가 들어간다(`--keepdb` 를 쓸 때). 그 셋 중 하나가
    아니면 **무엇이든 돌리기 전에 멈춘다.**
    """
    name = str(connection.settings_dict.get("NAME") or "")
    ok = (name == ":memory:"
          or name.startswith("file:memorydb")
          or "test" in Path(name).name.lower())
    if not ok:
        raise AssertionError(
            f"시험이 운영 DB 를 열었다: {name}\n"
            f"이 저장소의 교정은 재생성 불가다 — 여기서 멈춘다. "
            f"`manage.py test` 로 돌렸는지, settings 의 DATABASES 를 누가 "
            f"덮어쓰지 않았는지 볼 것.")


def assert_sandboxed_root():
    """`DATA_ROOT` 가 이 시험이 만든 임시 디렉토리인지 확인한다.

    **`ForGIA.db` 확인과 짝이다.** 이쪽을 빼먹어서 시험이 `/data3/ForGIA` 에
    사진을 썼다 — 파일은 DB 와 달리 Django 가 대신 격리해 주지 않는다.
    """
    root = Path(settings.DATA_ROOT).resolve()
    tmp = Path(tempfile.gettempdir()).resolve()
    if not (root.is_relative_to(tmp) and _TMP_PREFIX in str(root)):
        raise AssertionError(
            f"시험이 운영 자료 뿌리를 가리키고 있다: {root}\n"
            f"`ForGIA.db` 만 격리하고 파일 뿌리를 안 격리하면 사진·산출물 쪽에 "
            f"그대로 쓴다. `ForGIATestCase` 를 상속했는지, `setUpClass` 에서 "
            f"뿌리를 갈기 전에 픽스처가 돌지 않는지 볼 것.")


def write_image(rel, size=(64, 48)) -> Path:
    """`DATA_ROOT` 아래에 진짜 PNG 를 하나 심는다. **쓰기 전에 뿌리를 확인한다.**

    뷰가 디스크에서 사진을 읽으므로(`/img`·`/crop`) 0바이트 파일로는 모자란다 —
    PIL 이 열 수 있어야 한다. 확장자가 `.jpg` 여도 PIL 이 알아서 맞춘다.
    """
    from PIL import Image as PILImage

    assert_sandboxed_root()
    p = Path(settings.DATA_ROOT) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", size, (128, 128, 128)).save(p)
    return p


def write_blob(rel, data=b"\x00" * 16) -> Path:
    """`DATA_ROOT` 아래에 아무 파일이나 하나 심는다 (DiaRUGA 079).

    가중치처럼 **PIL 이 열 필요가 없는** 것에 쓴다. `write_image` 와 같은 자리에
    두는 이유는 하나다 — **쓰기 전에 뿌리를 확인하는 문을 하나로 모으기 위해서**다
    (`assert_sandboxed_root` 머리말).
    """
    assert_sandboxed_root()
    p = Path(settings.DATA_ROOT) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


class _SafeRootsMixin:
    """`DATA_ROOT`·`THUMB_CACHE` 를 임시 디렉토리로.

    **`super().setUpClass()` 보다 먼저 간다.** 그 안에서 `setUpTestData()` 가
    돌고, 픽스처가 파일을 쓰는 자리가 거기다 (머리말).

    `override_settings` 데코레이터를 안 쓰는 것도 같은 이유다 — 그쪽은
    `setUpClass` 안의 어느 시점에 발동하는지가 상속 순서에 달려 있어서, 여기서
    필요한 "픽스처보다 먼저" 를 보장하지 못한다.
    """

    @classmethod
    def setUpClass(cls):
        assert_test_db()
        cls._tmp = Path(tempfile.mkdtemp(prefix=_TMP_PREFIX))
        cls._saved = {k: getattr(settings, k)
                      for k in ("DATA_ROOT", "THUMB_CACHE")}
        settings.DATA_ROOT = cls._tmp / "data"
        settings.THUMB_CACHE = cls._tmp / "thumbs"
        for d in settings.DATA_ROOT, settings.THUMB_CACHE:
            d.mkdir(parents=True, exist_ok=True)
        assert_sandboxed_root()
        try:
            super().setUpClass()        # 여기서 setUpTestData() 가 돈다
        except Exception:
            cls._restore()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            cls._restore()

    @classmethod
    def _restore(cls):
        for k, v in getattr(cls, "_saved", {}).items():
            setattr(settings, k, v)
        shutil.rmtree(getattr(cls, "_tmp", ""), ignore_errors=True)

    # 시험에서 바로 부를 수 있게. 실제 구현은 모듈 함수다 — 픽스처도 같은
    # 문을 지나야 하는데 그쪽은 클래스를 안 들고 있다.
    write_image = staticmethod(write_image)


class ForGIATestCase(_SafeRootsMixin, TestCase):
    """보통의 시험. 트랜잭션으로 감싸고 되돌린다."""


class ForGIATransactionTestCase(_SafeRootsMixin, TransactionTestCase):
    """트랜잭션 자체를 보는 시험용 (`save_review` 가 나눠 커밋한다)."""
