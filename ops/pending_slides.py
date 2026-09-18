"""DiaRUGA v0.29.0 `ops/pending_slides.py` 에서 왔다. 폴러 3단계의 "밀린 일 목록" — pending·processing 슬라이드를 한 줄씩 낸다.

    슬러그<TAB>상태<TAB>image_dir

`deploy/poll_nas.sh` 가 매분 부른다. 예전에는 이 조회가 폴러 안의 heredoc
파이썬이었는데 두 번 데었다 (097):

- `run()` 의 `</dev/null` 이 heredoc(stdin)을 굶겨 **빈 목록으로 조용히
  성공**했다 — 3단계가 사흘을 죽어 있었다
- 셸 문자열 안의 파이썬은 시험이 못 덮고, `dbsync.sh --list` 의 표류 검사도
  못 본다

그래서 파일이다. **`dbtool` 문으로 돈다** — DB 를 만지는 스크립트의 규약
그대로(`check_db.py` 와 같은 자리): 저장소는 만들고, `/srv/ForGIA/scripts`
로 옮긴 것이 돈다. 뷰어 이미지라 torch 가 없고, 파이프라인 이미지를 다시
구울 필요도 없다.

읽기 전용이다 — 아무것도 안 쓴다.
"""
import os
import sys
from pathlib import Path

import django

# /srv/ForGIA/scripts 에 복사해 컨테이너 안에서 돌 때 Django 코드가 어디
# 있는지는 FORGIA_APP 이 알려 준다 (check_db.py 와 같은 규약).
# **저장소에서는 한 단계 위가 뿌리다** (스크립트가 pipeline/·ops/·migrate/
# 안에 있다). `/srv/ForGIA/scripts` 처럼 저장소 밖에서 돌 때는 그 짐작이
# 안 맞으므로 `FORGIA_APP` 이 알려 준다 — 컨테이너에서는 이미지 안의 /app 이다.
APP = Path(os.environ.get("FORGIA_APP")
          or Path(__file__).resolve().parent.parent)
# **`APP` 은 Django 코드를 찾는 자리일 뿐이다** (100). `sys.path` 앞에 통째로
# 밀어 넣으면 **이미지 안의 옛 `judge.py`·`scale.py` 가 자기 옆의 것을 가린다**
# — `/srv/ForGIA/scripts` 로 밀어 넣은 새 규칙이 안 먹는 채로 돌았다(실측).
# 그래서 **뒤에 붙인다**: 스크립트 자신의 디렉토리(파이썬이 `sys.path[0]` 에
# 놓는다)가 먼저이고, Django 는 그 뒤에서 찾힌다.
sys.path.insert(0, str(APP / "web"))
sys.path.append(str(APP))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "forgiaweb.settings")
django.setup()

from viewer.models import Slide                                     # noqa: E402


def main():
    # `failed` 는 안 낸다 — 사람이 봐야 하는 상태라 폴러가 건드리면 안 된다.
    for s in (Slide.objects.filter(state__in=("pending", "processing"))
              .order_by("pk")):
        print(f"{s.slug}\t{s.state}\t{s.image_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
