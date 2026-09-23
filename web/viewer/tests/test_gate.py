"""잠금 화면 (viewer/gate.py) — 꺼져 있는가, 켜면 막는가, 한 번 넣으면 다시 안 묻는가.

## 무엇을 잡으려는 시험인가

**꺼져 있는 쪽이 먼저다.** 코드가 비어 있는데 잠그면 시험 700여 개와 개발
서버가 전부 잠금 화면으로 간다 — `test_env_label` 과 같은 순서다.

**`/healthz` 는 코드 없이 열린다.** 잠기면 `deploy.sh` 의 기동 게이트가 200 을
기다리다 **배포가 스스로 멈춘다**(DiaRUGA 034).

**코드를 바꾸면 이전 쿠키가 무효가 된다.** "모두에게 다시 묻기" 의 방법이 이것
하나라 깨지면 되돌릴 길이 없다.

**`next` 로 바깥에 보내지 않는다.** 잠금 화면 주소에 남의 주소를 실어 보내면
코드를 넣은 사람이 그리로 넘어간다.

## 되살려서 잡히는가

`_OPEN` 에서 `/healthz` 를 빼면 `test_healthz_는_안_잠근다` 가, `passed()` 의 지문
대조를 빼면 `test_코드를_바꾸면_다시_묻는다` 가, `_safe_next` 를 거치지 않게 하면
`test_바깥_주소로_안_보낸다` 가, 미들웨어 첫 줄의 `not gate_code()` 를 빼면
`test_코드가_없으면_안_잠근다` 가 실패한다 — 넷 다 되살려 확인했다.
"""
import time
from unittest import mock

from django.core import signing
from django.test import Client, override_settings
from django.urls import clear_script_prefix, set_script_prefix

from viewer import gate

from .base import ForGIATestCase

CODE = "for-2026"


def _no_wait():
    return mock.patch.object(gate, "_WRONG_DELAY_S", 0)


class GateOffTests(ForGIATestCase):

    def test_코드가_없으면_안_잠근다(self):
        with override_settings(GATE_CODE=""):
            r = Client().get("/")
        self.assertEqual(r.status_code, 200)

    def test_코드가_없으면_잠금_화면은_목록으로_보낸다(self):
        with override_settings(GATE_CODE=""):
            r = Client().get("/gate/")
        self.assertRedirects(r, "/", fetch_redirect_response=False)


@override_settings(GATE_CODE=CODE)
class GateOnTests(ForGIATestCase):

    def test_코드_없이_들어오면_잠금_화면으로_간다(self):
        r = Client().get("/slides/?x=1")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/gate/?next=%2Fslides%2F%3Fx%3D1")

    def test_잠금_화면이_그려진다(self):
        r = Client().get("/gate/")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('name="code"', html)
        self.assertIn("/gate/logo.gif", html)
        self.assertIn("/gate/logo.png", html)
        self.assertEqual(r["Cache-Control"], "no-store")

    def test_그림은_코드_없이_받는다(self):
        for name, ctype in (("logo.gif", "image/gif"), ("logo.png", "image/png")):
            r = Client().get(f"/gate/{name}")
            self.assertEqual(r.status_code, 200, name)
            self.assertEqual(r["Content-Type"], ctype)
            self.assertGreater(len(b"".join(r.streaming_content)), 1000)

    def test_없는_그림은_404(self):
        """목록에 없는 이름은 파일을 찾아 나서지 않는다 (들어온 브라우저 기준)."""
        c = Client()
        c.post("/gate/", {"code": CODE})
        self.assertEqual(c.get("/gate/settings.py").status_code, 404)
        self.assertEqual(c.get("/gate/..%2Fgate.py").status_code, 404)

    def test_healthz_는_안_잠근다(self):
        r = Client().get("/healthz")
        self.assertNotEqual(r.status_code, 302)

    def test_쓰기는_옮기지_않고_403(self):
        r = Client().post("/review", data="{}", content_type="application/json")
        self.assertEqual(r.status_code, 403)

    def test_틀린_코드는_다시_묻는다(self):
        c = Client()
        with _no_wait():
            r = c.post("/gate/", {"code": "nope", "next": "/"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("코드가 맞지 않습니다", r.content.decode())
        self.assertNotIn(gate.COOKIE, r.cookies)
        self.assertEqual(c.get("/").status_code, 302)

    def test_맞는_코드를_넣으면_다음부터_안_묻는다(self):
        c = Client()
        r = c.post("/gate/", {"code": CODE, "next": "/?q=1"})
        self.assertRedirects(r, "/?q=1", fetch_redirect_response=False)
        morsel = r.cookies[gate.COOKIE]
        self.assertTrue(morsel["httponly"])
        self.assertEqual(morsel["path"], "/")
        self.assertGreater(int(morsel["max-age"]), 300 * 86400)
        self.assertEqual(c.get("/").status_code, 200)
        # 들어온 브라우저가 잠금 화면을 열면 바로 넘겨 준다
        self.assertEqual(c.get("/gate/").status_code, 302)

    def test_코드를_바꾸면_다시_묻는다(self):
        c = Client()
        c.post("/gate/", {"code": CODE})
        with override_settings(GATE_CODE="new-code"):
            self.assertEqual(c.get("/").status_code, 302)

    def test_고쳐_쓴_쿠키는_안_받는다(self):
        c = Client()
        c.cookies[gate.COOKIE] = signing.dumps({"c": "x", "t": 0}, salt="다른것")
        self.assertEqual(c.get("/").status_code, 302)

    def test_오래된_쿠키는_새로_준다(self):
        """Chrome 이 수명을 400일로 자르므로 드나드는 브라우저는 갈아 준다."""
        c = Client()
        old = signing.dumps({"c": gate._fingerprint(CODE), "t": int(time.time()) - 40 * 86400},
                            salt=gate._SALT)
        c.cookies[gate.COOKIE] = old
        r = c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(gate.COOKIE, r.cookies)
        # 갓 받은 것은 안 갈아 준다
        self.assertNotIn(gate.COOKIE, c.get("/").cookies)

    def test_바깥_주소로_안_보낸다(self):
        for bad in ("http://evil.example/", "//evil.example/", "https:evil"):
            r = Client().post("/gate/", {"code": CODE, "next": bad})
            self.assertEqual(r["Location"], "/", bad)


@override_settings(GATE_CODE=CODE, FORCE_SCRIPT_NAME="/ForGIA")
class GateSubpathTests(ForGIATestCase):
    """운영은 `/ForGIA/` 아래에 있다 — 쿠키 경로와 되돌아갈 주소가 그 안이어야 한다."""

    def setUp(self):
        # 운영의 WSGIHandler 는 요청마다 접두사를 잡는데 시험 클라이언트는 안 잡는다
        set_script_prefix("/ForGIA/")
        self.addCleanup(clear_script_prefix)

    def test_서브경로(self):
        c = Client()
        r = c.get("/", SCRIPT_NAME="/ForGIA")
        self.assertTrue(r["Location"].startswith("/ForGIA/gate/?next=%2FForGIA%2F"),
                        r["Location"])
        r = c.post("/gate/", {"code": CODE, "next": "/ForGIA/"}, SCRIPT_NAME="/ForGIA")
        self.assertEqual(r.cookies[gate.COOKIE]["path"], "/ForGIA/")
        self.assertEqual(r["Location"], "/ForGIA/")
