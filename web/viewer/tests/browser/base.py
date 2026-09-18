"""DiaRUGA v0.29.0 `tests/browser/base.py` 에서 왔다.

브라우저 시험의 바닥. `LiveServerTestCase` + playwright.

**`pageerror` 를 기본으로 건다.** 045 에서 `?shot=last` 가 JS 를 죽이고 있었는데
화면은 200 이었다 — 콘솔을 안 보면 "떴다" 와 "돈다" 를 구별할 수 없다. 그래서
모든 시험이 끝날 때 **JS 오류가 하나라도 있으면 실패**한다. 시험마다 기억해서
확인하게 두면 빠뜨리는 시험이 생긴다.
"""
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

# **playwright 의 동기 API 는 이벤트 루프 위에서 돈다.** 그래서 Django 의
# `async_unsafe` 검사가 ORM 호출을 `SynchronousOnlyOperation` 으로 막는다 —
# 시험 본체가 아니라 픽스처·flush 같은 뒤처리에서 터져서 원인이 잘 안 보인다.
# 여기서 푸는 것이 맞다: 진짜 async 서버가 아니라 **시험이 만든 루프**이고,
# 이 모듈은 브라우저 시험만 임포트한다.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db.utils import OperationalError
from django.test import tag

from ..base import assert_test_db, assert_sandboxed_root, _TMP_PREFIX

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                     # pragma: no cover
    sync_playwright = None


@tag("browser")
class BrowserTestCase(StaticLiveServerTestCase):
    """진짜 크로미움으로 이 앱을 연다.

    **`browser` 로 표를 달아 둔다.** 1~3겹만 돌릴 때
    `--exclude-tag browser` 로 뺀다 — 이 겹은 크로미움이 있어야 하고 30배
    느리다. 표가 없으면 `manage.py test viewer` 가 늘 함께 끌고 간다.

    `self.uniq` 는 시험마다 다른 정수다 — 픽스처의 슬러그를 갈라 놓는 데 쓴다
    (`setUp` 의 주석에 이유가 있다). **슬러그에 쓸 수 있는 문자여야 하므로
    시험 이름을 못 쓴다** — 이 저장소의 시험 이름은 한글이고 `<slug:slug>` 는
    그것을 못 받는다.

    `_SafeRootsMixin` 을 상속하지 않고 뿌리 갈이를 다시 적는다 —
    `StaticLiveServerTestCase` 는 `setUpClass` 에서 서버까지 띄우므로 순서가
    한 겹 더 있고, 그것을 섞으면 어느 쪽이 먼저인지가 안 보인다. **순서를 잘못
    잡아 시험이 `/data3` 에 쓴 적이 있다** (`..base` 머리말).
    """

    _seq = 0        # setUp 이 올린다. 시험마다 다른 슬러그를 만드는 데 쓴다

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest("playwright 가 없다 — requirements-dev.txt")

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
            super().setUpClass()            # 여기서 서버가 뜨고 픽스처가 돈다
            cls._pw = sync_playwright().start()
            try:
                cls._browser = cls._pw.chromium.launch()
            except Exception as e:          # 크로미움을 안 깔았다
                cls._pw.stop()
                raise unittest.SkipTest(
                    f"크로미움을 못 띄웠다 ({e}) — `playwright install chromium`")
        except Exception:
            cls._restore()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            if getattr(cls, "_browser", None):
                cls._browser.close()
            if getattr(cls, "_pw", None):
                cls._pw.stop()
            super().tearDownClass()
        finally:
            cls._restore()

    @classmethod
    def _restore(cls):
        for k, v in getattr(cls, "_saved", {}).items():
            setattr(settings, k, v)
        shutil.rmtree(getattr(cls, "_tmp", ""), ignore_errors=True)

    # --- 각 시험 ----------------------------------------------------------

    def setUp(self):
        # **`setUpTestData` 를 쓸 수 없다.** `LiveServerTestCase` 는
        # `TransactionTestCase` 라서 표를 시험마다 비운다 — 클래스 한 번만
        # 만든 자료는 두 번째 시험에서 사라진다. 픽스처는 여기서 세운다.
        #
        # **그래서 시험마다 다른 슬러그를 쓴다** (`self.uniq`). 표를 비우는
        # 것은 `TransactionTestCase` 의 뒤처리(flush)인데, 그것이 **살아 있는
        # 서버 스레드와 경합한다** — 앞 시험의 페이지가 아직 이미지를 받는
        # 중이면 그 연결이 트랜잭션을 물고 있어 flush 가 늦고, 다음 시험의
        # `Slide.objects.create(slug="rs23")` 가
        # `UNIQUE constraint failed: viewer_slide.slug` 로 죽는다.
        # CI 에서 6개 중 2개가 그렇게 났다(로컬에서는 빨라서 잘 안 난다).
        #
        # 고칠 자리는 여기다 — **픽스처가 제 유효성을 남의 뒤처리에 기대면
        # 안 된다.** 청소를 기다리는 대신 애초에 안 부딪히게 한다.
        type(self)._seq += 1
        self.uniq = type(self)._seq
        # 분류표 캐시 — `tests/base.py` 의 `setUp` 과 같은 이유
        from ... import data
        data.invalidate_classes()
        self.make_data()
        self.errors = []
        self.ctx = self._browser.new_context(viewport={"width": 1400,
                                                       "height": 900})
        self.page = self.ctx.new_page()
        # **이 두 줄이 이 겹의 값어치다.** 화면이 200 이어도 JS 가 죽어 있으면
        # 아무 버튼도 안 듣는다 (045).
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("console", lambda m: (
            self.errors.append(f"console.error: {m.text}")
            if m.type == "error" else None))

    # 시험이 **일부러 실패 응답을 부를 때** 그 자국을 지운다. 4xx 를 받으면
    # 크로미움이 스스로 `Failed to load resource` 를 콘솔에 적는데, 그것은 JS
    # 고장이 아니라 **서버가 제대로 거절했다는 증거**다. 문구를 못 박아 두어
    # 진짜 오류까지 함께 눈감는 일이 없게 한다.
    def expect_http_error(self, status):
        self._expect_http = getattr(self, "_expect_http", set()) | {int(status)}

    def tearDown(self):
        expected = getattr(self, "_expect_http", set())
        errors = [e for e in self.errors
                  if not any(f"status of {s} " in e for s in expected)]
        # **떠나기 전에 요청을 비운다.** 페이지가 아직 썸네일을 받는 중이면
        # 그 요청을 처리하는 서버 스레드가 **읽기 트랜잭션을 쥔 채** 남고,
        # 곧바로 도는 `flush` 가 그것과 부딪힌다 (아래 `_fixture_teardown`).
        # 창을 닫는 것만으로는 이미 서버에 들어간 요청이 안 끊긴다 —
        # **끊는 것이 아니라 끝나기를 기다리는 것이 맞다**(183): 핸들러는 이미
        # DB 를 잡고 있고, 브라우저가 손을 떼도 서버는 그것을 끝까지 돈다.
        #
        # 그래서 순서가 셋이다 — **조용해질 때까지 기다리고**, 빈 쪽으로 옮기고,
        # 닫는다. 기다리는 것을 안 하면 뒷정리가 잠기는데, 그 빨간불은 시험이
        # 아니라 뒷정리가 낸 것이라 **없는 고장을 쫓게 만든다**(CI 에서 실제로
        # `test_catalog_atlas` 셋이 그렇게 섰다).
        try:
            self.page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:                       # 5초 안에 안 조용해지면 그냥 간다
            pass
        try:
            self.page.goto("about:blank")
            self.page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:                       # 이미 닫혔으면 그만이다
            pass
        self.ctx.close()
        self.assertEqual(errors, [], f"JS 오류가 났다:\n" + "\n".join(errors))

    def _fixture_teardown(self):
        """표를 비우는 뒷정리. **경합하면 몇 번 다시 시도한다.**

        `TransactionTestCase` 는 시험마다 `flush` 로 표를 비우는데, 그것이
        **살아 있는 서버 스레드와 경합한다.** CI 에서 `database table is locked:
        viewer_candidate` 로 실제로 깨졌다 — 로컬은 빨라서 잘 안 난다.

        고칠 자리가 둘이고 둘 다 한다: 위에서 **진행 중인 요청을 끊고**,
        여기서 **져 주고 다시 시도한다.** 상대는 곧 끝나는 읽기라 기다리면 풀린다.

        시험이 실패하는 것과 뒷정리가 실패하는 것은 다른 일인데, 뒤엣것도
        빨간불을 내므로 **없는 고장을 쫓게 만든다.** 실제로 이번 배포에서
        v0.8.0 CI 를 그것으로 한 번 멈췄다.
        """
        last = None
        # **기다리는 시간을 늘렸다** (183). 시험이 늘면서 `test_catalog_atlas`
        # 의 뒷정리가 CI 에서 세 번 연달아 여기서 졌다 — 3.7초로는 모자랐다.
        # 상대는 곧 끝나는 읽기라 **기다리면 반드시 풀린다**: 늘려서 잃는 것은
        # 진짜로 막혔을 때의 12초뿐이고, 안 늘리면 **없는 고장을 쫓게 된다.**
        for wait in (0, 0.2, 0.5, 1.0, 2.0, 4.0, 8.0):
            if wait:
                time.sleep(wait)
            try:
                return super()._fixture_teardown()
            except OperationalError as e:       # locked / busy
                last = e
        raise last

    def make_data(self):
        """시험마다 세울 자료. 하위 클래스가 채운다."""

    def open(self, path):
        """앱의 주소 하나를 연다. `path` 는 `reverse()` 가 낸 것."""
        self.page.goto(f"{self.live_server_url}{path}", wait_until="load")
        return self.page

    # --- 이미지 좌표로 짚기 ------------------------------------------------

    def masks_svg(self, uid="stack"):
        """마스크 `<svg>`. 이것이 이미지 화소 좌표계를 그대로 들고 있다."""
        return self.page.wait_for_selector(f"#masks-{uid}", state="attached",
                                           timeout=10_000)

    def image_point(self, img_x, img_y, uid="stack"):
        """**이미지 화소 좌표**를 화면 좌표로 옮긴다.

        `<svg>` 가 `viewBox="0 0 W H"` 에 `preserveAspectRatio="none"` 이라
        상자에 정확히 늘어난다 — 그래서 변환이 선형이다. 여백을 짐작하지
        않는다.

        **화면 한가운데를 찍어 보는 식으로 하면 안 된다.** 개체 위에 떨어질지
        아닐지가 운이라, 못 맞히면 시험이 `skip` 으로 조용히 넘어가고 **덮은
        줄 알게 된다** — 실제로 탈락 펼침판이 그렇게 한 번 건너뛰었다.
        """
        svg = self.masks_svg(uid)
        box = svg.bounding_box()
        vb = [float(v) for v in svg.get_attribute("viewBox").split()]
        vx, vy, vw, vh = vb
        return (box["x"] + (img_x - vx) / vw * box["width"],
                box["y"] + (img_y - vy) / vh * box["height"])

    def click_image(self, img_x, img_y, uid="stack", button="left"):
        """이미지 화소 좌표 하나를 누른다."""
        x, y = self.image_point(img_x, img_y, uid)
        self.page.mouse.click(x, y, button=button)
        self.page.wait_for_timeout(150)

    def context_menu_at(self, img_x, img_y, uid="stack"):
        """그 자리에서 우클릭하고 메뉴를 돌려준다 (없으면 `None`)."""
        self.click_image(img_x, img_y, uid, button="right")
        self.page.wait_for_timeout(200)
        return self.page.query_selector(".ctxmenu")

    def menu_click(self, text, exact=False):
        """우클릭 메뉴의 **그 항목**을 누른다 — 화면 전체에서 찾지 않는다.

        `page.get_by_text("봉상").first` 로 찾던 자리들이 183 에서 깨졌다.
        오른쪽 칸의 카탈로그에 **유형 셀렉트**가 생기면서 `<option>봉상</option>`
        이 문서 앞쪽에 놓였고, `.first` 가 그것을 집어 클릭이 타임아웃 났다.

        **메뉴 안으로 범위를 좁히는 것이 맞다** — 시험이 누르려던 것은 늘
        메뉴 항목이었고, 같은 낱말이 화면 어딘가에 또 생기는 일은 앞으로도
        있다(분류 이름은 화면 여러 곳에 나온다).
        """
        self.page.locator(".ctxmenu").get_by_text(
            text, exact=exact).first.click()
