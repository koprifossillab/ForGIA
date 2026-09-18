"""자리 이름 워터마크 — 켜지는가, 그리고 **꺼져 있는가** (115).

`FORGIA_ENV_LABEL` 이 있으면 화면 전체에 빨간 워터마크가 대각선으로 뜬다.
운영과 테스트가 **같은 오리진에 같은 모양으로** 떠 있어 주소창 말고는 구분할
것이 없어서다 (`viewer/context.py` 머리말).

## 무엇을 잡으려는 시험인가

**꺼져 있는 쪽이 더 중요하다.** 이 값이 실수로 서면 **운영 화면 전체에 빨간
글자가 얹힌다** — 그 고장은 예외를 안 내고 화면만 이상해진다. 그래서 "없으면
안 나온다" 를 먼저 세운다.

그리고 **글자를 코드가 정하지 않는다는 것**을 지킨다. `"Test Server"` 를
템플릿에 박으면 개발 서버·시연용 자리가 자기 이름을 못 갖는다.

## 되살려서 잡히는가

넷 다 되살려 확인했다 — 템플릿의 `{% if env_label %}` 를 지우면 1번이,
`{{ env_label }}` 를 글자로 박으면 2·3번이, `pointer-events: none` 을 빼면
4번이 실패한다. 마지막은 눈에 안 보이는 고장이라 특히 그렇다: 워터마크가
글 위에 얹혀 있어(`z-index: 9000`) 그 줄이 없으면 **검토 화면의 클릭·드래그를
통째로 먹는다.**
"""
import os
import re
from unittest import mock

from django.test import Client

from .base import ForGIATestCase


def _body(label=None):
    """`FORGIA_ENV_LABEL` 을 그 값으로 두고 첫 화면을 렌더한다.

    환경변수는 **컨텍스트 프로세서가 요청마다 읽는다** — 그래서 설정처럼
    `override_settings` 가 아니라 `mock.patch.dict` 다.
    """
    env = dict(os.environ)
    env.pop("FORGIA_ENV_LABEL", None)
    if label is not None:
        env["FORGIA_ENV_LABEL"] = label
    with mock.patch.dict(os.environ, env, clear=True):
        return Client().get("/").content.decode()


class EnvLabelTests(ForGIATestCase):

    def test_없으면_안_나온다(self):
        """**운영이 이 갈래다.** 여기가 깨지면 운영 화면이 가려진다."""
        html = _body(None)
        self.assertNotIn('class="envwm"', html)

    def test_있으면_그_글자로_나온다(self):
        html = _body("Test Server")
        self.assertIn('class="envwm"', html)
        self.assertIn("Test Server", html)

    def test_글자를_코드가_정하지_않는다(self):
        """다른 이름을 주면 **그 이름**이 나오고 `Test Server` 는 안 나온다."""
        html = _body("개발 서버")
        self.assertIn("개발 서버", html)
        self.assertNotIn("Test Server", html)

    def test_공백만_주면_안_나온다(self):
        """`.strip()` 을 지나므로 빈 줄을 적어 둔 `.env` 가 워터마크를 세우지 않는다."""
        self.assertNotIn('class="envwm"', _body("   "))

    def test_클릭을_통과시킨다(self):
        """**글 위에 얹히는 층이라 이것이 없으면 화면이 죽는다.**

        `.envwm` 은 `z-index: 9000` 으로 본문 위에 있다. 검토 화면은 캔버스
        전체에 클릭·드래그를 걸므로 `pointer-events: none` 이 빠지면 마스크를
        하나도 못 누른다 — **예외도 경고도 안 난다.**
        """
        html = _body("Test Server")
        rule = re.search(r"\.envwm\s*\{(.*?)\}", html, re.S)
        self.assertIsNotNone(rule, ".envwm 규칙이 base.html 에서 사라졌다")
        self.assertIn("pointer-events: none", rule.group(1))

    def test_DEBUG_면_안_적어도_뜬다(self):
        """개발 서버는 사람이 손으로 세우는 자리라 **빠뜨리는 것이 기본 고장**이다.

        운영·테스트 컨테이너는 `.env` 에 `FORGIA_DEBUG=0` 이라 안 걸린다.
        """
        with self.settings(DEBUG=True):
            self.assertIn('class="envwm"', _body(None))

    def test_적은_이름이_DEBUG_보다_이긴다(self):
        with self.settings(DEBUG=True):
            html = _body("개발 서버")
        self.assertIn("개발 서버", html)
        self.assertNotIn("Test Server", html)

    def test_DEBUG_가_아니면_안_뜬다(self):
        """**운영이 이 갈래다.** 위 갈래가 운영으로 새면 여기서 잡힌다."""
        with self.settings(DEBUG=False):
            self.assertNotIn('class="envwm"', _body(None))

    def test_테마마다_진하기가_따로다(self):
        """색을 박으면 한쪽 테마에서 묻힌다 (CLAUDE.md · 107).

        `--envwm-op` 가 어두운 쪽과 밝은 쪽에 각각 있어야 한다.
        """
        html = _body("Test Server")
        self.assertEqual(len(re.findall(r"--envwm-op\s*:", html)), 2)
