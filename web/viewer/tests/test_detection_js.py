"""DiaRUGA v0.29.0 `tests/test_detection_js.py` 에서 왔다.

검토 화면의 **렌더한** 인라인 스크립트가 파싱되는가.

**템플릿 원본이 아니라 렌더한 것을 본다** (CLAUDE.md). Django 태그가 JS 주석
안에서도 실행되고, `{% block %}` 바깥에 적은 것은 아예 렌더되지 않는다 —
원본만 봐서는 둘 다 안 보인다.

**예외도 경고도 없는 종류의 고장이다.** 인라인 스크립트가 깨지면 그 아래 배선이
통째로 안 돌고 화면은 멀쩡해 보인다 — 063 에서 `<script>` 가 `{% block title %}`
안에 들어가 끌어다 놓기가 죽어 있던 자리가 그것이다.

`node` 가 없으면 건너뛴다 — 이 시험이 없는 것보다 나쁜 것은 **없는데 있다고
믿는 것**이라, 건너뛴 것은 건너뛴다고 말한다.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from django.test import Client

from .base import ForGIATestCase
from . import factories as fx

SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script>", re.S)
TYPE = re.compile(r'\btype\s*=\s*["\']([^"\']+)["\']', re.I)
# **자료 블록은 JS 가 아니다.** 화면이 `application/json` 으로 상태를 실어
# 보내는데(`__stack__` 등), 그것까지 `node --check` 에 넣으면 늘 실패한다 —
# 그러면 이 시험이 진짜 고장을 못 가린다.
JS_TYPES = {"", "text/javascript", "application/javascript", "module"}


LINE_COMMENT = re.compile(r"(^|[^:])//[^\n]*")
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def strip_comments(js):
    """주석을 걷는다 — **문구가 주석에만 있어도 통과하는 것**을 막는다.

    문자열 안의 `//`(URL 등)까지 정확히 가르지는 않는다. 이 시험이 쓰는
    거친 도구이고, 지나치게 걷히면 **시험이 더 엄해질 뿐** 느슨해지지 않는다.
    """
    return LINE_COMMENT.sub(r"\1", BLOCK_COMMENT.sub("", js))


def js_blocks(html):
    out = []
    for attrs, body in SCRIPT.findall(html):
        m = TYPE.search(attrs)
        if (m.group(1).strip().lower() if m else "") not in JS_TYPES:
            continue
        if body.strip():
            out.append(body)
    return out


@unittest.skipUnless(shutil.which("node"), "node 가 없다")
class DetectionInlineJsTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)
        fx.add_frame_detections(cls.w.vp)

    def html(self):
        r = Client().get(f"/d/{self.w.slug}/g/{self.w.vp.idx}/")
        self.assertEqual(r.status_code, 200)
        return r.content.decode("utf-8")

    def test_인라인_스크립트가_파싱된다(self):
        blocks = js_blocks(self.html())
        self.assertTrue(blocks, "인라인 스크립트가 하나도 없다")
        for i, body in enumerate(blocks):
            with tempfile.NamedTemporaryFile("w", suffix=".js",
                                             delete=False) as f:
                f.write(body)
                path = f.name
            try:
                r = subprocess.run(["node", "--check", path],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0,
                                 f"덩이 {i} 가 안 파싱된다:\n{r.stderr[:600]}")
            finally:
                os.unlink(path)

    def test_앉히기_배선이_렌더된다(self):
        """**주석에 있는 글자로 세지 않는다.**

        149·150·151·152·154 가 `<style>` 의 글자로 세다 이빨 없는 시험을
        만들었다. 여기서는 같은 일이 **주석**으로 났다 — 항목 문구를 지워도
        바로 위 주석이 같은 말을 들고 있어 통과했다. 그래서 **주석을 걷고
        코드만 본다.**
        """
        code = strip_comments("\n".join(js_blocks(self.html())))
        for name in ("function spreadOne", "function spreadTargets",
                     "다른 판에도 앉히기", "spreadOne(one)"):
            self.assertIn(name, code, f"{name} 이 스크립트 코드에 없다")
