#!/usr/bin/env python3
"""DiaRUGA v0.29.0 `pipeline/ingest_nas.py` 에서 왔다. NAS 의 새 슬라이드를 로컬로 가져오고 `Slide` 행을 만든다.

    python ingest_nas.py --dry-run
    python ingest_nas.py
    python ingest_nas.py --only "260731/RS23-GC03 369cm"

무엇이 새것인지는 `scan_nas.py` 가 정한다. 이 스크립트는 **가져오기만** 한다 —
그룹핑·합성·검출은 그다음이다.

## 왜 복사하는가

NAS 를 직접 읽고 처리하지 않는다. 슬라이드 하나가 1 GB 대인데 합성 단계에서 반복해
읽으면 NFS 왕복이 비싸고, `hard` 마운트라 NAS 가 흔들리면 처리 중인 작업이 통째로
멈춘다. 원본은 NAS 에 그대로 두고(**읽기만 한다**) 로컬 사본으로 처리한다.

## 받다 만 것이 정상처럼 보이면 안 된다

`<대상>.part/` 로 받아서 **개수와 바이트를 대조한 뒤에** 제 이름을 준다.
이름이 붙어 있으면 온전히 받은 것이다. 중간에 죽으면 `.part` 가 남고, 다음 실행이
그것을 지우고 다시 받는다.

## 사진과 메타를 같이 가져온다

`scale.py` 가 사진 곁의 메타(Leica XML · EXIF · `scale.toml`)에서 µm/px 를 읽는다.
근거가 없으면 배율이 기본값으로 **조용히** 떨어진다(DiaRUGA 004 에서 당했다).
그래서 근거가 안 보이면 반입하되 `state_note` 에 남긴다.
"""
import argparse
import os
import shutil
import sys
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
from django.utils import timezone                                   # noqa: E402

import runlog                                                       # noqa: E402
import scan_nas                                                     # noqa: E402
from group_focus_series import (sample_fields, parse_obs_no,        # noqa: E402
                                slide_slug, git_version, rel)
from viewer.models import Run, Slide                                # noqa: E402

COPY_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".xml", ".json", ".toml")


def copy_slide(src: Path, dst: Path) -> tuple[int, int]:
    """폴더 하나를 옮긴다. 온전히 받은 뒤에야 제 이름을 준다.

    돌려주는 값: (파일 수, 바이트).
    """
    part = dst.with_name(dst.name + ".part")
    if part.exists():
        shutil.rmtree(part)                 # 지난번에 죽은 자리
    part.mkdir(parents=True)

    n = total = 0
    with os.scandir(src) as it:
        for e in it:
            if not e.is_file():
                continue
            low = e.name.lower()
            if not low.endswith(COPY_EXT):
                continue
            shutil.copy2(e.path, part / e.name)
            n += 1
            total += e.stat().st_size

    # 대조: 받은 것이 원본과 같은가
    got = sum(1 for _ in part.iterdir())
    got_bytes = sum(p.stat().st_size for p in part.iterdir())
    if got != n or got_bytes != total:
        shutil.rmtree(part)
        raise IOError(f"사본이 원본과 다르다: {got}/{n}개 {got_bytes}/{total}바이트")

    part.rename(dst)
    return n, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stable-min", type=float, default=5.0,
                    help="폴더 내용이 이만큼(분) 안 변해야 가져온다")
    ap.add_argument("--only", help="이 상대경로 하나만 (예: '260731/RS23-GC03 369cm')")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    res = scan_nas.scan(args.stable_min)
    todo = [r for r in res["slides"] if r["state"] == "new"]
    if args.only:
        todo = [r for r in todo if r["rel"] == args.only]
        if not todo:
            raise SystemExit(f"'{args.only}' 은 새 슬라이드 목록에 없다. "
                             f"scan_nas.py 로 확인할 것.")

    if not todo:
        print("가져올 새 슬라이드가 없다.")
        return 0

    data_root = Path(settings.DATA_ROOT)
    print(f"가져올 슬라이드 {len(todo)}개")
    for r in todo:
        print(f"  {r['rel']:<40} 사진 {r['jpgs']:>4}  {r['bytes'] / 1e6:>7.1f} MB")
    if args.dry_run:
        print("\ndry-run — 아무것도 가져오지 않았다.")
        return 0

    run = runlog.start(
        "ingest",
        params={"nas_root": res["nas_root"], "stable_min": args.stable_min,
                "slides": [r["rel"] for r in todo]},
        host=os.uname().nodename, code_version=git_version())

    n_ok = 0
    try:
        for r in todo:
            src = Path(r["nas_path"])
            dst = data_root / r["local"]
            folder = dst.name
            note = ""
            if not r["scale_ok"]:
                note = "메타도 scale.toml 도 없다 — EXIF 에도 없으면 배율이 기본값으로 떨어진다"

            slide, _ = Slide.objects.update_or_create(
                slug=slide_slug(dst),
                defaults=dict(name=folder, image_dir=r["local"],
                              state="copying", state_note=note,
                              # 시료 소속. **빈 dict 면 아무것도 안 쓴다** —
                              # 사람이 채운 코어를 자동값이 지우지 않는다
                              **sample_fields(folder),
                              # 자동값만. `obs_label` 은 사람 것이라 안 건드린다
                              obs_no=parse_obs_no(folder),
                              discovered_at=timezone.now()))

            n, total = copy_slide(src, dst)
            # 가져왔을 뿐 아직 아무 처리도 안 됐다. 뷰어는 done 이 아니면 검토를 막는다.
            slide.state = "pending"
            slide.copied_at = timezone.now()
            slide.state_note = (note + " · " if note else "") + "그룹핑 대기"
            slide.save(update_fields=["state", "copied_at", "state_note"])
            n_ok += 1
            print(f"  {r['rel']}: {n}개 {total / 1e6:.1f} MB -> {rel(dst)}"
                  + (f"  ** {note}" if note else ""))
    except Exception as e:
        run.status = "failed"
        run.error = f"{type(e).__name__}: {e}"
        run.finished_at = timezone.now()
        run.counts = {"slides": n_ok}
        run.save()
        raise

    run.status = "done"
    run.finished_at = timezone.now()
    run.counts = {"slides": n_ok}
    run.save()
    print(f"\n슬라이드 {n_ok}개를 가져왔다 · Run #{run.pk}")
    print("  다음: group_focus_series.py 로 시야를 묶는다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
