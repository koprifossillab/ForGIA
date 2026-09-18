"""렌더한 HTML 에서 인라인 스크립트를 뽑아 `node --check` 로 파싱하는 도구. DiaRUGA v0.29.0
`test_detection_js.py` 의 앞부분이다 — 그 시험 자체(검토 화면 JS)는 3단계에서 온다.
"""
import re

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
