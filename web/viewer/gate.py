"""잠금 화면 — 접속 코드를 한 번 넣으면 그 브라우저는 다시 묻지 않는다 (2026-09-23).

DiaRUGA `web/viewer/gate.py`(work/20260923-sclee)에서 왔다 — 이름만 바꿨다.

**켜고 끄는 것은 `FORGIA_GATE_CODE` 하나다.** 비어 있으면 아무 일도 안 한다 —
시험·개발 서버·코드를 아직 안 정한 배포가 그 갈래다. 값이 있으면 코드를 모르는
요청은 전부 잠금 화면(`/gate/`)으로 간다.

## 한 번만 묻는 방법

맞는 코드를 넣으면 **서명한 쿠키**(`forgia_gate`)를 준다. 안에는 코드 자체가
아니라 코드의 지문과 발급 시각이 들어 있고, `FORGIA_SECRET_KEY` 로 서명한다.
그래서

- **코드를 바꾸면 이전 쿠키가 전부 무효가 된다** — 지문이 달라진다. 모두에게
  다시 묻고 싶을 때 코드를 바꾸면 된다.
- **`FORGIA_SECRET_KEY` 를 바꿔도 전부 무효가 된다** — 서명이 안 맞는다.
- 쿠키를 고쳐 쓰면 서명이 깨져 잠금 화면으로 간다.

`localStorage` 로 하지 않은 것은 **서버가 막지 못하기 때문이다** — 화면 앞에
가림막을 세울 뿐이라 `/img`·`/review` 같은 주소는 그대로 열린다.

**쿠키 수명은 400일이 상한이다.** Chrome 이 `Max-Age` 를 400일로 자른다. 그래서
발급한 지 30일이 지난 쿠키로 들어오면 새로 발급해 준다 — 한 해에 한 번이라도
들어오는 브라우저는 다시 묻지 않는다.

## 잠그지 않는 자리

`/healthz` — `deploy.sh` 의 기동 게이트와 `smoke.sh` 가 코드 없이 두드린다.
잠그면 **배포가 스스로 멈춘다**(DiaRUGA 034 와 같은 이야기다). 잠금 화면과 그 그림 둘도
당연히 열려 있다.

`smoke.sh` 5번(nginx 경유 목록)은 잠긴다 — 그쪽이 `.env` 의 코드로 한 번 들어가
쿠키를 받아 쓴다.

## 이것이 막는 것과 못 막는 것

사내망 안에서 주소를 아는 사람이 그냥 들어오는 것을 막는다. **사람마다 계정을
주는 것이 아니다** — 코드는 모두가 같고, 누가 무엇을 했는지는 여전히 안 남는다.
평문 HTTP 라 코드와 쿠키가 망 위에 그대로 지나간다.
"""
import hashlib
import hmac
import json
import time
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.http import (FileResponse, Http404, HttpResponseForbidden,
                         HttpResponseRedirect)
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme, urlencode

COOKIE = "forgia_gate"
_SALT = "viewer.gate"
_MAX_AGE_S = 400 * 86400        # Chrome 의 상한
_REFRESH_S = 30 * 86400         # 이보다 오래된 쿠키로 들어오면 새로 준다
_WRONG_DELAY_S = 0.8            # 틀린 코드에 늦게 답한다 — 하나씩 넣어 보는 속도를 늦춘다

ASSET_DIR = Path(__file__).resolve().parent / "assets" / "gate"
ASSETS = {"logo.gif": "image/gif", "logo.png": "image/png"}

# 잠그지 않는 주소 (path_info — 서브경로 `/ForGIA` 를 뗀 것)
_OPEN = {"/healthz", "/gate/"} | {f"/gate/{name}" for name in ASSETS}


def gate_code():
    return getattr(settings, "GATE_CODE", "") or ""


def _fingerprint(code):
    return hashlib.sha256(f"forgia-gate:{code}".encode()).hexdigest()[:24]


def _issue():
    return signing.dumps({"c": _fingerprint(gate_code()), "t": int(time.time())},
                         salt=_SALT)


def passed(request):
    """쿠키가 지금 코드로 발급된 것이면 그 내용을, 아니면 None."""
    raw = request.COOKIES.get(COOKIE)
    if not raw:
        return None
    try:
        data = signing.loads(raw, salt=_SALT)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    if not hmac.compare_digest(str(data.get("c", "")), _fingerprint(gate_code())):
        return None
    return data


def _give_cookie(resp):
    resp.set_cookie(
        COOKIE, _issue(), max_age=_MAX_AGE_S,
        # 서브경로마다 따로다 — 운영(/ForGIA/)과 시험(/ForGIATest/)이 한 오리진에
        # 떠 있어도 서로의 쿠키를 안 쓴다
        path=(settings.FORCE_SCRIPT_NAME or "") + "/",
        httponly=True, samesite="Lax")


class GateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not gate_code() or request.path_info in _OPEN:
            return self.get_response(request)
        data = passed(request)
        if data is None:
            if request.method in ("GET", "HEAD"):
                return HttpResponseRedirect(
                    reverse("gate") + "?" + urlencode({"next": request.get_full_path()}))
            # 저장·API 는 옮겨 줄 화면이 없다. 화면 쪽이 실패 띠로 알린다
            return HttpResponseForbidden("접속 코드가 필요합니다. 화면을 새로 고쳐 주세요.",
                                         content_type="text/plain; charset=utf-8")
        resp = self.get_response(request)
        if time.time() - data.get("t", 0) > _REFRESH_S:
            _give_cookie(resp)
        return resp


def _safe_next(request, nxt):
    if nxt and url_has_allowed_host_and_scheme(
            nxt, allowed_hosts={request.get_host()}, require_https=False):
        return nxt
    return reverse("index")


def _build_ms():
    try:
        return int(json.loads((ASSET_DIR / "logo.json").read_text())["build_ms"])
    except (OSError, ValueError, KeyError):
        return 0     # 모르면 바로 원본을 보인다


def gate(request):
    nxt = _safe_next(request, request.POST.get("next") or request.GET.get("next"))
    code = gate_code()
    if not code:
        return HttpResponseRedirect(nxt)

    wrong = False
    if request.method == "POST":
        given = (request.POST.get("code") or "").strip()
        if hmac.compare_digest(given.encode(), code.encode()):
            resp = HttpResponseRedirect(nxt)
            _give_cookie(resp)
            return resp
        time.sleep(_WRONG_DELAY_S)
        wrong = True
    elif passed(request) is not None:
        return HttpResponseRedirect(nxt)

    resp = render(request, "viewer/gate.html", {
        "next": nxt,
        "wrong": wrong,
        # 틀려서 다시 그린 화면에서는 그리는 장면을 또 보이지 않는다
        "still": wrong,
        "build_ms": _build_ms(),
    })
    resp["Cache-Control"] = "no-store"
    return resp


def gate_asset(request, name):
    ctype = ASSETS.get(name)
    if ctype is None:
        raise Http404
    resp = FileResponse((ASSET_DIR / name).open("rb"), content_type=ctype)
    resp["Cache-Control"] = "public, max-age=86400"
    return resp
