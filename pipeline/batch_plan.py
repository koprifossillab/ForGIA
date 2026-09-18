#!/usr/bin/env python3
"""DiaRUGA v0.29.0 `pipeline/batch_plan.py` 에서 왔다. 새 자료를 어느 묶음에 어떤 순서로 채울지 — **폴러가 읽는다** (079).

    python batch_plan.py            # 사람이 읽는 표
    python batch_plan.py --args     # 폴러가 먹는 줄 (탭으로 가른다)

`RunBatch.recipe` 가 `segment_forams.py` 의 인자를 담고 있고, 여기서는 그것을
**순서대로** 펴 준다. 순서는 `data.batches_to_run()` 이 정한다 — 검토 중인
묶음이 먼저, 나머지는 최근 것부터.

**셸에서 조리법을 해석하지 않는다.** 파이썬이 한 줄로 만들어 주고 셸은 그대로
넘긴다 — JSON 을 셸에서 뜯으면 따옴표와 공백에서 반드시 깨진다.

`dbrun.sh` 로 도는 다른 스크립트들과 같은 머리를 쓴다 (`check_db.py` 참고).
"""
import argparse
import os
import shlex
import sys
from pathlib import Path

import django

# **`check_db.py` 의 머리를 그대로 베낀다** (CLAUDE.md). 컨테이너 안에서는 코드가
# `/app` 이고 이 스크립트만 `/srv/ForGIA/scripts` 에서 마운트되므로, 자기 옆의
# `web/` 을 보게 짜면 `No module named 'forgiaweb'` 로 죽는다 — 실제로 그랬다.
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

import batch_scope                                                   # noqa: E402
from viewer import data                                              # noqa: E402

# 조리법 열쇠 → `segment_forams.py` 의 인자. 없는 열쇠는 그냥 건너뛴다 —
# 조리법에 새 항목이 생겨도 옛 파이프라인이 죽지 않아야 한다.
FLAGS = {
    "backend": "--backend", "scale": "--scale",
    "points_per_side": "--points-per-side",
    "min_um": "--min-um", "max_um": "--max-um",
    "weights": "--weights", "yolo_conf": "--yolo-conf",
    "yolo_imgsz": "--yolo-imgsz",
}
SWITCHES = {"all_images": "--all-images"}


def argv_for(recipe: dict) -> list[str]:
    out = []
    for k, flag in FLAGS.items():
        v = recipe.get(k)
        if v is not None:
            out += [flag, str(v)]
    for k, flag in SWITCHES.items():
        if recipe.get(k):
            out.append(flag)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--args", action="store_true",
                    help="폴러용: '<이름>\\t<인자들>' 을 줄마다")
    a = ap.parse_args()

    rows = data.batches_to_run()
    if a.args:
        for r in rows:
            if not r["ready"]:
                continue
            print(f"{r['batch'].label}\t{shlex.join(argv_for(r['recipe']))}")
        return 0

    if not rows:
        print("자동으로 채울 묶음이 없다 — 조리법(recipe)이 적힌 묶음이 없다.")
        return 0
    print(f"{'순서':<4} {'묶음':<16} {'상태':<6} 인자")
    for i, r in enumerate(rows, 1):
        mark = "◉" if r["batch"].for_review else " "
        state = "OK" if r["ready"] else "못 돌림"
        print(f"{i:<4} {mark} {r['batch'].label:<14} {state:<6} "
              f"{shlex.join(argv_for(r['recipe']))}")
        # **권역은 인자로 안 나간다** — `segment_forams.py` 가 슬라이드마다
        # 보는 규칙이라(`batch_scope.py`) 조리법 인자에 실을 것이 없다. 그래도
        # 사람이 읽는 표에는 적는다: 안 보이면 "왜 이 슬라이드만 비었나" 가 된다.
        scope = batch_scope.allowed_areas(r["recipe"])
        if scope is not None:
            print(f"       └─ {batch_scope.label_for_plan(r['recipe'])} 만 돈다")
        if not r["ready"]:
            print(f"       └─ {r['why']}")
        if r["recipe"].get("weights_guessed"):
            print("       └─ 가중치는 이행이 **추측한** 값이다 — 확인할 것")
    return 0


if __name__ == "__main__":
    sys.exit(main())
