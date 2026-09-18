"""DiaRUGA v0.29.0 `tests/browser/__init__.py` 에서 왔다.

4겹 — 진짜 브라우저로 눌러 본다 (DiaRUGA P08 §2).

    python web/manage.py test viewer.tests.browser

**여기서 규약이 하나 바뀐다.** 지금까지 "브라우저는 반드시 사본 DB 에 붙인다"
였는데(HANDOFF 3.3 — 운영에 붙이면 클릭 한 번이 교정을 지운다), 이 겹은
`StaticLiveServerTestCase` 라 **테스트 DB 위에 서버가 뜬다.** 운영 DB 에 붙을
경로 자체가 없다. 손으로 `:8099` 를 띄우고 사본을 물리는 것보다 안전하다.

손으로 눌러 볼 때는 기존 규칙이 그대로 남는다 — 그쪽은 사람이 주소를 고른다.

## 이 겹만 볼 수 있는 것

테스트 클라이언트는 "200 이 뜨고 이런 HTML 이 나온다" 까지만 본다(HANDOFF 3.3).

- **이벤트 배선** — 키가 실제로 듣는가, 입력칸 안에서는 안 듣는가 (040)
- **콘솔 오류** — `?shot=last` 가 JS 를 죽이던 것을 `page.on("pageerror")` 한
  줄이 잡았다 (045)
- **되는 것처럼 보이는 것** — 저장은 잠갔는데 우클릭 메뉴가 살아 있던 읽기
  전용 화면 (051). 저장 경로만 보는 시험으로는 절대 안 걸린다

## 없으면 건너뛴다

`playwright` 나 크로미움이 없는 곳에서는 **실패가 아니라 skip** 이다. 이 겹은
`requirements-dev.txt` 와 `playwright install chromium` 을 함께 갖춘 데서만
돌고, 그것이 없다고 1~3겹까지 빨개지면 아무도 안 보게 된다.
"""
