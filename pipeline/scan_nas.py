#!/usr/bin/env python3
"""DiaRUGA v0.29.0 `pipeline/scan_nas.py` 에서 왔다. NAS 를 훑어 어떤 슬라이드가 있고 무엇이 새것인지 보고한다. **읽기 전용이다.**

    python scan_nas.py
    python scan_nas.py --json /tmp/scan.json

DB 도 파일도 건드리지 않는다. 반입은 `ingest_nas.py` 가 한다.

**왜 나누는가.** NAS 디렉토리는 사람이 만든 것이라 예외가 많다 — 촬영 중인 폴더,
이름 규칙에서 벗어난 폴더, XML 이 빠진 사진. 먼저 읽기 전용으로 훑어 "무엇이 있고
무엇이 안 맞는가" 를 보고 나서 반입한다. ForGIA 는 사람의 교정이 재생성 불가라
이 순서가 더 중요하다(DiaRUGA 와 같다). (형제 프로젝트의 EPMA 반입도 같은 갈래다.)

## NAS 구조

    /nfs/temp-share/ForamPhotos/<촬영일>/<슬라이드>/<이름>.jpg|.png
                                                   scale.toml            ← 사람이 적은 배율 (pipeline/scale.py)
                                                   <이름>_Properties.xml ← Leica 가 내보내면 (아직 안 읽는다)

로컬은 이 구조를 그대로 비춘다: `<DATA_ROOT>/photos/<촬영일>/<슬라이드>/`.
그래서 상대경로 하나가 곧 키다.

## 복사가 끝났는지 어떻게 아는가

**아직 들어오는 중인 폴더를 건드리면 안 된다.** 절반만 가져가면 그룹핑이 시야를
잘못 묶고, 그 위에 검출과 교정이 쌓인 뒤에 되돌리는 것은 훨씬 비싸다.

NFS 에서 inotify 는 믿을 수 없으므로(P01 §1) 폴링으로 본다. 그런데 **mtime 만으로는
안 된다** — `rsync -a` 나 `cp -p` 는 원본 시각을 보존하므로, 지금 한창 들어오는
중인데도 "몇 시간째 조용함" 으로 보인다.

그래서 **본 것을 기억한다.** 폴더마다 (사진 수, 메타 수, 총 바이트)를 지문으로 삼아
파일에 적어 두고, 다음 폴링에서 그대로면 "그때부터 안 변했다" 로 센다. 지문이
달라지면 시계를 0 으로 되돌린다. `--stable-min` 동안 지문이 한 번도 안 바뀌어야
가져온다.

여기에 mtime 을 보조로 쓴다. 최근 1분 안에 쓰인 파일이 있으면 지문이 우연히 같아도
(같은 크기로 덮어쓰는 중) 아직 들어오는 중으로 본다.

상태 파일은 `FORGIA_NAS_STATE` (기본 `<로그디렉토리>/nas_state.json`). 지워도
안전하다 — 시계가 처음부터 다시 갈 뿐이다.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import django

# 이 스크립트는 저장소 밖(/srv/ForGIA/scripts)에 복사해 두고 컨테이너 안에서
# 돌릴 수도 있다. 그때 Django 코드가 어디 있는지는 FORGIA_APP 이 알려 준다 —
# 이미지 안의 /app 이고, 뷰어 컨테이너가 쓰는 바로 그 코드다. 저장소에서 그냥
# 돌리면 예전처럼 자기 옆의 web/ 을 본다.
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

from django.conf import settings                                    # noqa: E402
from viewer.models import Slide                                     # noqa: E402

# 로컬에서 사진이 놓이는 자리. NAS 의 <촬영일>/<슬라이드> 를 이 아래에 그대로 편다.
PHOTOS = "photos"


def nas_root() -> Path:
    return Path(os.environ.get("FORGIA_NAS_PHOTOS",
                               "/nfs/temp-share/ForamPhotos"))


def is_mounted(path: Path) -> bool:
    """NFS 가 실제로 붙어 있는가.

    빠지면 그 자리는 그냥 빈 로컬 디렉토리가 된다. 그것을 "새 슬라이드가 하나도
    없다" 로 읽으면 조용히 아무것도 안 하게 된다 — 고장이 정상처럼 보인다.
    """
    try:
        mounts = Path("/proc/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    target = str(path.resolve())
    for line in mounts:
        p = line.split()
        if len(p) >= 3 and p[2].startswith("nfs") and (
                target == p[1] or target.startswith(p[1] + "/")):
            return True
    return False


IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
META_EXT = (".xml", ".json")


def survey(slide_dir: Path) -> dict:
    """폴더 하나를 재 본다. NAS 왕복을 한 번만 하도록 한 번에 모은다.

    `jpgs` 라는 이름은 DiaRUGA 것을 그대로 둔다(폴러·기록이 그 이름을 본다) —
    뜻은 "사진 수" 다. `xmls` 는 곁의 메타 파일 수, `toml` 은 폴더에 `scale.toml`
    이 있는가다. **스케일 근거가 하나도 없으면** 배율이 기본값으로 조용히
    떨어진다(`pipeline/scale.py`) — 그것을 아래 `scale_ok` 로 센다.
    """
    jpgs, xmls, toml, newest, total = 0, 0, False, 0.0, 0
    with os.scandir(slide_dir) as it:
        for e in it:
            if not e.is_file():
                continue
            st = e.stat()
            newest = max(newest, st.st_mtime)
            total += st.st_size
            low = e.name.lower()
            if low.endswith(IMAGE_EXT):
                jpgs += 1
            elif low.endswith(META_EXT):
                xmls += 1
            elif low == "scale.toml":
                toml = True
    return {"jpgs": jpgs, "xmls": xmls, "toml": toml, "bytes": total,
            # EXIF 는 여기서 안 연다(NAS 왕복). 메타나 toml 이 있으면 근거가 있다고 본다
            "scale_ok": toml or xmls > 0,
            "newest_mtime": newest,
            "quiet_min": round((time.time() - newest) / 60, 1) if newest else None}


def state_path() -> Path:
    return Path(os.environ.get(
        "FORGIA_NAS_STATE",
        str(Path(settings.DATA_ROOT) / "logs" / "nas_state.json")))


def load_state() -> dict:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}          # 없으면 시계를 처음부터 — 안전한 쪽으로 틀린다


def save_state(state: dict) -> None:
    p = state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        # 못 적어도 스캔 자체는 유효하다. 다만 안정성 시계가 매번 0 이 되어
        # 아무것도 안 가져오게 되므로 조용히 넘기지 않는다.
        print(f"경고: 상태를 적지 못했다 ({e}) — 안정성 판단이 진행되지 않는다",
              file=sys.stderr)


def scan(stable_min: float = 5.0, remember: bool = True) -> dict:
    """NAS 의 슬라이드 폴더를 전부 훑고 DB·직전 관측과 대조한다."""
    root = nas_root()
    if not is_mounted(root):
        raise SystemExit(f"NAS 가 마운트되어 있지 않다: {root}\n"
                         f"  빈 디렉토리를 '새것 없음' 으로 읽으면 안 된다 — 멈춘다.")

    known = set(Slide.objects.values_list("image_dir", flat=True))
    prev = load_state()
    now = time.time()
    state_out = {}

    rows = []
    for date_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for slide_dir in sorted(p for p in date_dir.iterdir() if p.is_dir()):
            rel = f"{date_dir.name}/{slide_dir.name}"
            local = f"{PHOTOS}/{rel}"
            s = survey(slide_dir)

            # 지문이 그대로면 그때부터 안 변한 것이다. 달라지면 시계를 되돌린다.
            sig = f"{s['jpgs']}:{s['xmls']}:{int(s['toml'])}:{s['bytes']}"
            was = prev.get(rel)
            since = (was["since"] if was and was.get("sig") == sig else now)
            state_out[rel] = {"sig": sig, "since": since}
            stable_min_seen = (now - since) / 60.0

            if s["jpgs"] == 0:
                st = "empty"
            elif local in known:
                st = "known"
            elif stable_min_seen < stable_min:
                st = "copying"
            elif s["quiet_min"] is not None and s["quiet_min"] < 1.0:
                # 지문이 같아도 방금 쓰인 파일이 있으면 아직 들어오는 중이다
                # (같은 크기로 덮어쓰는 경우)
                st = "copying"
            else:
                st = "new"

            rows.append({"rel": rel, "local": local, "state": st,
                         "nas_path": str(slide_dir),
                         "stable_min": round(stable_min_seen, 1), **s})

    if remember:
        save_state(state_out)
    return {"nas_root": str(root), "stable_min_required": stable_min,
            "slides": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stable-min", type=float, default=5.0,
                    help="폴더 내용(파일 수·바이트)이 이만큼(분) 안 변해야 "
                         "복사가 끝난 것으로 본다")
    ap.add_argument("--no-remember", action="store_true",
                    help="관측을 기록하지 않는다 (사람이 들여다볼 때)")
    ap.add_argument("--json", dest="json_file", help="결과를 JSON 으로 저장")
    args = ap.parse_args()

    res = scan(args.stable_min, remember=not args.no_remember)
    rows = res["slides"]
    by = {}
    for r in rows:
        by.setdefault(r["state"], []).append(r)

    label = {"new": "새 슬라이드", "known": "이미 있음",
             "copying": "들어오는 중(안정되길 기다림)", "empty": "사진 없음"}
    print(f"NAS {res['nas_root']} · 슬라이드 폴더 {len(rows)}개 "
          f"· 안정 기준 {args.stable_min}분")
    for state in ("new", "copying", "known", "empty"):
        got = by.get(state) or []
        if not got:
            continue
        print(f"\n{label[state]} — {len(got)}개")
        for r in got:
            xml = "" if r["scale_ok"] else "  ** 스케일 근거 없음"
            print(f"  {r['rel']:<40} 사진 {r['jpgs']:>4}  "
                  f"{r['bytes'] / 1e6:>7.1f} MB  안정 {r['stable_min']}분{xml}")

    # 스케일 근거가 없으면 배율이 기본값으로 조용히 떨어진다 (DiaRUGA 004 에서 당했다).
    # EXIF 에 들어 있을 수도 있어 단정은 못 한다 — 반입 뒤 `scale.py` 가 다시 본다
    short = [r for r in rows if r["state"] in ("new", "known") and not r["scale_ok"]]
    if short:
        print(f"\n** 메타도 scale.toml 도 없는 폴더 {len(short)}개 — EXIF 에도 없으면 "
              f"배율(µm/px)이 기본값으로 조용히 떨어진다", file=sys.stderr)

    if args.json_file:
        Path(args.json_file).write_text(json.dumps(res, indent=2, ensure_ascii=False),
                                        encoding="utf-8")
        print(f"\nJSON: {args.json_file}")

    print(f"\n새로 반입할 것 {len(by.get('new') or [])}개"
          f" (반입은 ingest_nas.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
