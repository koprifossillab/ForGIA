#!/usr/bin/env python3
"""DiaRUGA v0.29.0 `ops/sync_backup_nas.py` 에서 왔다. 검증된 DB 스냅샷과 사진을 NAS 로 밀어 둔다 (오프사이트 track).

    python sync_backup_nas.py --newest-only --prune --photos   # 일별 cron
    python sync_backup_nas.py --dry-run --prune --photos       # 무엇을 할지만
    python sync_backup_nas.py                                  # 밀린 DB 사본 전부

두 갈래가 한 스크립트에 있지만 **성격이 다르다.** DB 사본이 깨지면 깃발을 세워
`/healthz` 까지 알리고, 사진은 종료코드로만 알린다 — 사진은 NAS 에 원본이 따로
있다(애초에 거기서 받아 온 것이다). 사진 이야기는 `sync_photos` 머리말에 있다.

**하루에 하나만 건너간다** (`--newest-only`). 로컬은 시간별로 뜨지만 오프사이트는
일별 track 이다 — 시간 단위 복구는 로컬의 24시간 rolling 이 맡고, 이쪽은 가장 오래
남는 사본을 든다. 밀린 것을 따라잡지 않는 것이 요점이다: 하루치가 스물네 장이 되면
"하루에 하나" 를 전제로 한 계단식 보관이 무너진다.

**손으로 뜬 스냅샷은 여기 오지 않는다.** `backup_db.py` 가 그것을 `backup/manual/`
에 따로 쌓고, 이 스크립트는 윗단만 본다. 수동 스냅샷은 작업 중에 되돌릴 지점이지
보관물이 아니라서, 일이 잘 끝나면 지워도 되는 물건이다 — 오프사이트로 나를 이유가
없고, 섞이면 그날의 오프사이트 사본이 엉뚱한 것이 된다.

**왜 필요한가.** 이 장비는 개발·운영·백업을 겸한다. `backup_db.py` 의 사본은
전부 `/data3` 안이라 디스크 한 장이 죽으면 사람의 교정 2,400여 건이 같이 간다
(`export_review.py` 가 아직 없어 교정이 DB 에만 있다). NAS 는 물리적으로 별개
장비이므로, 거기 사본을 두면 기계 분리라는 핵심 이득을 얻는다.

**라이브 DB 를 복사하지 않는다** (.guides/web/data-safety.md §4). 도는 WAL DB 를
본체·`-wal`·`-shm` 따로 복사하면 체크포인트와 경합해 찢어진 사본이 나온다.
`backup_db.py` 가 온라인 백업 API 로 떠서 `integrity_check` 를 통과시킨 **단일 파일
스냅샷**만 소비한다. 신선도는 최대 한 주기만큼 뒤처지는데, 그 값이면 싸다.

**수신 후 다시 검증한다** (§2, §4). 가장 오래 남는 사본이 가장 덜 검증된 것이
되는 것 — 그게 정확히 피해야 할 형태다. 그래서 NAS 에 놓은 파일을 열어
`integrity_check` 와 행 수를 다시 본다.

**실패하면 지우지 않는다** (§2). 검증에 실패하면 그 사본에 돌아가지 않는 이름
(`.corrupt`)을 붙여 증거로 남기고, **정리를 건너뛴 채** 0 이 아닌 값으로 끝낸다.
지난 성공 사본이 살아 있어야 한다.

**NAS 가 안 붙었는데 붙은 줄 아는 것을 막는다.** NFS 가 빠지면 `/nfs/temp-share` 는
그냥 빈 로컬 디렉토리가 되고, 거기 쓰면 "오프사이트에 뒀다"고 믿으면서 실은 같은
디스크에 쓰게 된다. `/proc/mounts` 로 실제 마운트인지 확인한다.

**cron 에서는 `timeout` 으로 감쌀 것.** NAS 가 `hard` 마운트라 내려가면 접근하는
프로세스가 무한 대기한다.

    40 4 * * * timeout 1800 /home/paleoadmin/venv/ForGIA/bin/python \
        /home/paleoadmin/projects/ForGIA/sync_backup_nas.py \
        --newest-only --prune --photos \
        >> /data3/ForGIA/logs/nas-sync.log 2>&1
"""
import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import db_sentinel

def _find_root():
    """`.env` 가 어디 있는지 스스로 찾는다.

    이 스크립트는 **두 자리에서 돈다** — 저장소의 `ops/` 와 배포의
    `/srv/ForGIA/scripts/`. 둘 다 `.env` 는 **한 칸 위**에 있다(저장소 루트 ·
    `/srv/ForGIA`). 옛날에는 스크립트가 저장소 루트에 있어 `parent` 가 맞았고,
    100 에서 옮기면서 그 전제가 깨졌다 — **시간별 백업이 두 시간 죽었다**.
    자리를 박아 두지 말고 찾는다.
    """
    here = Path(__file__).resolve().parent
    for d in (here, here.parent):
        if (d / ".env").exists():
            return d
    return here.parent


ROOT = _find_root()

# 계단식 보관의 경계 (plan_retention 참고). 값이 아니라 **정책**이라 여기 적는다.
DAILY_DAYS = 7        # 이 안쪽은 전부 남긴다
WEEKLY_DAYS = 30      # 여기까지는 주에 하나, 그 뒤로는 달에 하나

# 이 테이블이 있으면 스키마가 살아 있다고 본다 — backup_db.py 와 같은 기준
SMOKE_TABLE = "viewer_objectreview"

# 깃발에 적히는 주인. backup_db.py 와 갈라야 서로의 실패를 안 지운다.
SOURCE = "sync_backup_nas"


def _env(key, default):
    """환경변수 → .env → 기본값. backup_db.py 와 같은 이유로 Django 를 안 쓴다."""
    if key in os.environ:
        return os.environ[key]
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip()
    except OSError:
        pass
    return default


def is_real_mount(path: Path) -> bool:
    """path 가 실제 마운트 아래에 있는가.

    NFS 가 빠진 자리에 남은 빈 디렉토리에 쓰는 것을 막는다. 이것이 없으면
    "오프사이트에 뒀다"고 믿으면서 원본과 같은 디스크에 쓰게 된다.
    """
    try:
        mounts = Path("/proc/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    target = str(path.resolve())
    for line in mounts:
        parts = line.split()
        if len(parts) < 3:
            continue
        mp, fstype = parts[1], parts[2]
        if fstype.startswith("nfs") and (target == mp or target.startswith(mp + "/")):
            return True
    return False


def stamp_of(name: str):
    """`ForGIA_20260804_114433.db` → datetime. 못 읽으면 None.

    **이름을 믿는다.** mtime 은 복사·이동으로 바뀌지만 이름은 뜬 시각 그대로다.
    보관 정책이 나이로 판단하므로 이 차이가 실제로 갈린다 — NAS 로 건너간 사본은
    전부 "옮긴 시각" 의 mtime 을 갖는다.
    """
    m = re.match(r"^ForGIA_(\d{8})_(\d{6})", name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def plan_retention(paths, now):
    """계단식 보관 — 나이에 따라 촘촘함을 달리한다. (남길 것, 지울 것).

    | 나이 | 남기는 것 |
    |---|---|
    | 1주일 이내 | **전부** (하루 하나씩 들어오므로 곧 일 단위) |
    | 1주일 ~ 한 달 | **주에 하나** |
    | 한 달 이후 | **달에 하나** |

    개수(`--keep N`)가 아니라 나이로 판단하는 이유: 개수는 주기가 바뀌면 뜻이
    바뀐다. 실제로 이 스크립트의 `--keep 30` 은 "한 달" 이라고 정한 값이었는데,
    로컬이 시간별이 되자 같은 30 이 30시간을 뜻하게 됐다. 나이로 적으면 주기와
    무관하게 정책이 그대로 읽힌다.

    구간마다 **가장 새 것**을 남긴다. 오래된 사본을 하나만 들 것이라면 그 구간의
    끝 상태를 드는 편이 쓸모 있다.

    이름에서 시각을 못 읽는 파일은 **남긴다.** 정리 코드가 모르는 파일을 지우게
    두지 않는다 — 여기서 지워지는 것은 되돌릴 수 없다.
    """
    keep, drop = [], []
    seen_week, seen_month = set(), set()
    # 새것부터 본다. 그래야 각 구간에서 처음 만나는 것이 가장 새 것이다.
    for p in sorted(paths, key=lambda q: q.name, reverse=True):
        ts = stamp_of(p.name)
        if ts is None:
            keep.append(p)
            continue
        age_days = (now - ts).days
        week, month = ts.isocalendar()[:2], (ts.year, ts.month)
        if age_days <= DAILY_DAYS:
            keeping = True
        elif age_days <= WEEKLY_DAYS:
            keeping = week not in seen_week
        else:
            keeping = month not in seen_month
        if keeping:
            keep.append(p)
            # 남긴 것은 제 주·달을 채운다. 1주일 경계를 걸친 주에서 하루 단위로
            # 이미 남긴 것이 있으면, 그 주의 더 옛것을 또 남기지 않는다.
            seen_week.add(week)
            seen_month.add(month)
        else:
            drop.append(p)
    return keep, drop


def sync_photos(src: Path, nas_dir: Path, keep: int, dry_run: bool) -> bool:
    """사진 트리를 하루 한 덩어리(`photos_YYYYMMDD.tar`)로 묶어 NAS 에 둔다.

    ## 왜 압축하지 않는가

    3.65 GB 가 JPEG 이고 177 MB 만 XML 이다. 재 보니 슬라이드 하나가 440 MB →
    419 MB, **5%** 다. 그 5% 를 얻자고 매일 3.8 GB 를 통째로 갈아 낸다. 묶는
    목적은 "파일 하나로" 이지 용량이 아니므로 `tar` 만 쓴다.

    ## 왜 tar 인가 (하드링크 스냅샷이 아니라)

    가이드(§9)는 media 디렉토리에 rsnapshot 식 하드링크 트리를 권한다 —
    바뀐 것만 나르고 공간도 안 먹는다. 여기서 tar 를 쓰는 이유는 하나다:
    **덩어리 하나로 들고 다니고 싶다.** NAS 가 18 T 남아 있어 7 × 3.8 GB 가
    문제되지 않는 동안은 그 편의를 사는 것이 낫다.

    바뀔 조건을 적어 둔다 — **사진이 커지면 이 판단이 뒤집힌다.** 매일 드는
    비용이 전량에 비례하므로, 사진이 수십 GB 가 되면 하드링크 트리로 옮길 것.
    NAS 가 하드링크를 받는 것은 확인해 뒀다(rsync 3.2.7, `--link-dest` 로 같은
    inode 가 나온다).

    ## 이름은 검증을 통과한 뒤에 준다

    `.part` 로 쓰고 `tar -tf` 로 열어 본 뒤 제 이름을 준다. DB 스냅샷과 같은
    규칙이다 — **이름이 붙어 있으면 검증을 통과한 것.** 받다 만 3.8 GB 가
    정상 묶음처럼 보이면 그것을 믿고 원본을 지우는 날이 온다.

    파일 수도 견준다. `tar` 가 성공했는데 내용이 빈 경우를 크기만으로는 못 잡는다.
    """
    if not src.is_dir():
        print(f"사진 디렉토리가 없다: {src}", file=sys.stderr)
        return False

    stamp = time.strftime("%Y%m%d")
    final = nas_dir / f"photos_{stamp}.tar"
    part = nas_dir / f"photos_{stamp}.tar.part"

    n_src = sum(1 for _ in src.rglob("*") if _.is_file())
    print(f"\n사진 — {src} ({n_src}개)")

    if final.exists():
        print(f"  오늘 것이 이미 있다: {final.name}")
    elif dry_run:
        print(f"  묶을 것: {final.name}")
    else:
        nas_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        # -C 로 뿌리를 바꿔 담아야 묶음 안이 photos/... 로 깔끔하다.
        # 절대경로 그대로 담으면 풀 때 어디에 쏟아질지 알기 어렵다.
        r = subprocess.run(["tar", "cf", str(part), "-C", str(src.parent), src.name],
                           capture_output=True, text=True)
        if r.returncode != 0:
            part.unlink(missing_ok=True)
            print(f"  묶기 실패 — {r.stderr.strip()[:200]}", file=sys.stderr)
            return False

        # 열어 본다. 묶기만 하고 깨진 것을 모르면 뜻이 없다 (§2 의 tar 판).
        r = subprocess.run(["tar", "tf", str(part)], capture_output=True, text=True)
        n_tar = sum(1 for line in r.stdout.splitlines() if not line.endswith("/"))
        if r.returncode != 0 or n_tar < n_src:
            bad = nas_dir / f"photos_{stamp}.tar.corrupt"   # 정리 glob 밖
            part.replace(bad)
            print(f"  검증 실패 — 원본 {n_src}개 · 묶음 {n_tar}개"
                  f"{' · ' + r.stderr.strip()[:120] if r.stderr.strip() else ''}",
                  file=sys.stderr)
            print(f"    증거를 남겼다: {bad.name}", file=sys.stderr)
            return False

        part.replace(final)
        mb = final.stat().st_size / 1e6
        print(f"  {final.name}  {mb:.0f} MB  {n_tar}개  {time.time() - t0:.0f}초")

    # 일주일 rolling. 이름이 날짜라 사전순 = 시간순이다.
    olds = sorted(nas_dir.glob("photos_*.tar"))[:-keep] if keep else []
    for old in olds:
        if dry_run:
            print(f"  정리할 것: {old.name}")
        else:
            old.unlink()
            print(f"  정리: {old.name}")
    if not olds:
        print(f"  보관 {len(sorted(nas_dir.glob('photos_*.tar')))}개 (최근 {keep}개 유지)")
    return True


def sync_models(src: Path, nas_dir: Path, dry_run: bool) -> bool:
    """가중치를 `models_YYYYMMDD.tar` 로 묶어 NAS 에 둔다.

    **재생성 불가에 가까운 자료다.** 학습은 확률적이라 같은 자료로 다시 돌려도
    같은 가중치가 안 나온다. 그리고 `Run.params.weights` 가 묶음마다 **어느
    가중치가 냈는지**를 sha256 으로 적어 두므로, 그 파일이 사라지면 지난 회차의
    근거를 되짚을 수 없다.

    ## 사진과 다른 점 둘

    - **정리하지 않는다.** 사진은 NAS 에 원본이 따로 있어 일주일 rolling 이지만,
      가중치는 여기 있는 것이 원본이다. 회차마다 44 MB 씩 늘 뿐이라 값도 싸다
    - **안 바뀌었으면 안 보낸다.** 학습할 때만 바뀌므로 매일 126 MB 를 새로
      나를 이유가 없다. 가장 새 묶음과 **이름·크기**를 견줘 같으면 건너뛴다

    그 견줌이 못 잡는 것을 적어 둔다: **같은 이름으로 다시 학습해 크기까지 같은**
    경우다. 그래서 이름에 날짜를 붙인다(`11m-v1seg-1280-260815.pt`) — 덮어쓰면
    옛 묶음의 근거가 사라진다는 것이 규칙의 이유다.
    """
    if not src.is_dir():
        print(f"\n가중치 디렉토리가 없다: {src}", file=sys.stderr)
        return True                     # 없는 것은 실패가 아니다

    files = sorted(f for f in src.rglob("*") if f.is_file())
    if not files:
        print(f"\n가중치 — {src} (없음)")
        return True
    mb = sum(f.stat().st_size for f in files) / 1e6
    print(f"\n가중치 — {src} ({len(files)}개 · {mb:.0f} MB)")

    # 지금 무엇이 있는가 — 이름과 크기로 적는다
    now = sorted((str(f.relative_to(src.parent)), f.stat().st_size)
                 for f in files)
    olds = sorted(nas_dir.glob("models_*.tar"))
    if olds:
        r = subprocess.run(["tar", "tvf", str(olds[-1])],
                           capture_output=True, text=True)
        was = []
        for ln in r.stdout.splitlines():
            parts = ln.split()
            # `tar tvf` 한 줄: 권한 소유자 크기 날짜 시각 이름
            if len(parts) >= 6 and not ln.endswith("/"):
                was.append((parts[-1], int(parts[2])))
        was = sorted(was)
        if r.returncode == 0 and was == now:
            print(f"  안 바뀌었다 — {olds[-1].name} 그대로 둔다")
            return True

    # **여기 왔다는 것은 바뀌었다는 뜻이다.** 오늘 묶음이 이미 있으면 뒤에 번호를
    # 붙인다 — 같은 날 새로 학습한 가중치가 내일까지 안 넘어가면 안 된다.
    #
    # 번호를 `_2` 로 붙이는 것은 정렬 때문이다. `-2` 면 `models_260807-2.tar` 가
    # `models_260807.tar` 보다 **앞에** 서서(`-` < `.`) "가장 새 묶음" 을 잘못
    # 짚는다. `_` 는 `.` 보다 뒤라 사전순 = 시간순이 유지된다.
    stamp = time.strftime("%Y%m%d")
    final = nas_dir / f"models_{stamp}.tar"
    n = 1
    while final.exists():
        n += 1
        final = nas_dir / f"models_{stamp}_{n}.tar"
    part = final.with_suffix(".tar.part")
    if dry_run:
        print(f"  묶을 것: {final.name}")
        return True

    nas_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["tar", "cf", str(part), "-C", str(src.parent), src.name],
                       capture_output=True, text=True)
    if r.returncode != 0:
        part.unlink(missing_ok=True)
        print(f"  묶기 실패 — {r.stderr.strip()[:200]}", file=sys.stderr)
        return False

    # 이름은 검증을 통과한 뒤에 준다 (사진·DB 사본과 같은 규칙)
    r = subprocess.run(["tar", "tf", str(part)], capture_output=True, text=True)
    n_tar = sum(1 for line in r.stdout.splitlines() if not line.endswith("/"))
    if r.returncode != 0 or n_tar < len(files):
        bad = nas_dir / f"models_{stamp}.tar.corrupt"
        part.replace(bad)
        print(f"  검증 실패 — 원본 {len(files)}개 · 묶음 {n_tar}개", file=sys.stderr)
        print(f"    증거를 남겼다: {bad.name}", file=sys.stderr)
        return False

    part.replace(final)
    print(f"  {final.name}  {final.stat().st_size / 1e6:.0f} MB  {n_tar}개")
    print(f"  보관 {len(sorted(nas_dir.glob('models_*.tar')))}개 (정리하지 않는다)")
    return True


def verify(db_path: Path):
    """사본을 열어 무결성과 행 수를 본다. (ok, rows, error) 를 돌려준다."""
    try:
        # 읽기 전용으로 연다 — WAL 이 아닌 단일 파일이라 -shm 이 생기지 않는다
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        rows = con.execute(f"select count(*) from {SMOKE_TABLE}").fetchone()[0]
        con.close()
        return ok == "ok", rows, "" if ok == "ok" else ok
    except sqlite3.Error as e:
        return False, None, str(e)


def copy_verified(src: Path, dst_dir: Path):
    """임시 이름으로 옮긴 뒤 검증하고, 통과해야 제 이름을 준다.

    받다 만 파일이 정상 사본처럼 보이는 것을 막는다 — 이름이 붙었으면 검증을
    통과한 것이다.
    """
    tmp = dst_dir / (src.name + ".part")
    final = dst_dir / src.name
    shutil.copyfile(src, tmp)

    ok, rows, err = verify(tmp)
    if not ok or not rows:
        bad = dst_dir / (src.name + ".corrupt")   # 돌아가지 않는 이름 — 정리 대상 밖
        tmp.replace(bad)
        return False, rows, (err or f"{SMOKE_TABLE} 가 비었다"), bad

    tmp.replace(final)
    return True, rows, "", final


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", default=_env("FORGIA_BACKUP_DIR",
                                            str(ROOT / "backup")))
    ap.add_argument("--nas", default=_env("FORGIA_NAS_BACKUP_DIR",
                                          "/nfs/temp-share/ForGIA/backup"))
    ap.add_argument("--prune", action="store_true",
                    help=f"계단식 보관을 적용한다 ({DAILY_DAYS}일 이내 전부 · "
                         f"{WEEKLY_DAYS}일까지 주 1개 · 그 뒤 달 1개)")
    ap.add_argument("--newest-only", action="store_true",
                    help="가장 새 스냅샷 하나만 보낸다 (일별 cron 이 쓴다)")
    ap.add_argument("--photos", action="store_true",
                    help="사진 디렉토리도 하루 한 덩어리로 묶어 NAS 에 둔다")
    ap.add_argument("--photos-src", default=None,
                    help="사진 뿌리 (기본: $FORGIA_DATA_ROOT/photos)")
    ap.add_argument("--photos-nas", default=None,
                    help="사진 묶음을 둘 곳 (기본: NAS 백업 옆의 photos/)")
    ap.add_argument("--photos-keep", type=int, default=7,
                    help="사진 묶음을 최근 N개로 (일주일 rolling)")
    # **기본으로 켠다.** cron 줄(`--newest-only --prune --photos`)을 안 고쳐도
    # 다음 실행부터 함께 간다 — 남의 설정을 고쳤다가 되돌리기를 잊는 쪽이 위험하다.
    ap.add_argument("--no-models", action="store_true",
                    help="가중치를 NAS 로 안 보낸다 (기본은 보낸다)")
    ap.add_argument("--models-src", default=None,
                    help="가중치 뿌리 (기본: $FORGIA_DATA_ROOT/models)")
    ap.add_argument("--models-nas", default=None,
                    help="가중치 묶음을 둘 곳 (기본: NAS 백업 옆의 models/)")
    ap.add_argument("--stale-hours", type=float, default=26.0,
                    help="가장 새 로컬 스냅샷이 이보다 오래면 경고 (0 이면 끔)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    local = Path(args.local)
    nas = Path(args.nas)

    snaps = sorted(local.glob("ForGIA_*.db"))
    if not snaps:
        print(f"로컬 스냅샷이 없다: {local}", file=sys.stderr)
        print("  backup_db.py 를 먼저 돌렸는가?", file=sys.stderr)
        return 1

    # 신선도 — 로컬 백업이 멈췄거나 무결성 게이트에 막혀 있으면 여기서 드러난다.
    # 오프사이트가 조용히 낡아 가는 것을 배포와 무관하게 잡는 신호다 (§4).
    newest_age_h = (time.time() - snaps[-1].stat().st_mtime) / 3600
    if args.stale_hours and newest_age_h > args.stale_hours:
        print(f"경고: 가장 새 로컬 스냅샷이 {newest_age_h:.1f} 시간 전이다 "
              f"({snaps[-1].name}). backup_db.py 가 멈췄는가?", file=sys.stderr)

    if not is_real_mount(nas.parent if not nas.exists() else nas):
        print(f"NAS 가 마운트되어 있지 않다: {nas}", file=sys.stderr)
        print("  빈 디렉토리에 쓰면 원본과 같은 디스크에 쌓인다 — 멈춘다.",
              file=sys.stderr)
        return 1

    if not args.dry_run:
        nas.mkdir(parents=True, exist_ok=True)

    # 윗단만 본다. 손으로 뜬 스냅샷은 애초에 로컬 `manual/` 에 있어 `snaps` 에
    # 안 들어오므로(backup_db.py), 여기로 올라올 일이 없다.
    have = {p.name for p in nas.glob("ForGIA_*.db")} if nas.is_dir() else set()

    if args.newest_only:
        # **하루에 하나만 건너간다.** 로컬은 시간별로 뜨지만 오프사이트는 일별
        # track 이다 — 시간 단위 복구는 로컬이 맡고, 이쪽은 가장 오래 남는 사본을
        # 든다(data-safety.md §1 의 세 갈래).
        #
        # 밀린 것을 따라잡지 않는 것이 요점이다. 다 보내면 하루치가 스물네 장이
        # 되어 "하루에 하나" 라는 계단식 보관의 전제가 무너진다.
        #
        # 가장 새 것이 이미 가 있으면 할 일이 없다. **더 옛것으로 물러서지
        # 않는다** — 그러면 매번 하나씩 옛 사본을 실어 나르게 된다.
        todo = [] if snaps[-1].name in have else [snaps[-1]]
    else:
        todo = [p for p in snaps if p.name not in have]

    if not todo:
        print(f"NAS 가 최신이다 — 사본 {len(have)}개 ({nas})")
    elif args.dry_run:
        for p in todo:
            print(f"  보낼 것: {p.name}  {p.stat().st_size / 1e6:.1f} MB")
        print(f"dry-run — {len(todo)}개. 보내지 않았다.")
        # 여기서 끝내지 않는다. 정리가 무엇을 지울지도 **보내기 전에** 보여야
        # 한다 — dry-run 으로 확인하는 것 중 더 무서운 쪽이 그것이다.
        todo = []

    sent = failed = 0
    for p in todo:
        ok, rows, err, dst = copy_verified(p, nas)
        if ok:
            sent += 1
            print(f"  {p.name}  {p.stat().st_size / 1e6:.1f} MB  "
                  f"integrity=ok  {SMOKE_TABLE}={rows}")
        else:
            failed += 1
            print(f"  {p.name}  검증 실패 — {err}", file=sys.stderr)
            print(f"    증거를 남겼다: {dst.name}", file=sys.stderr)

    # 깃발은 **사본을 믿을 수 없을 때만** 세운다 (db_sentinel 머리말).
    #
    # NAS 가 안 붙은 것은 여기 넣지 않았다. 그건 자료가 상한 것이 아니라 lane 이
    # 잠깐 없는 것인데, 그것까지 세우면 NAS 점검 한 번에 뷰어가 degraded 가 되고
    # 배포 smoke 가 막힌다. 가장 센 신호(자료가 상했다)가 잡음에 묻히면 안 된다.
    # 오프사이트 가용성은 별도 신호가 맡을 몫이다.
    db = Path(_env("FORGIA_DB", str(ROOT / "ForGIA.db")))
    if failed:
        # 실패했으면 정리하지 않는다 — 지난 성공 사본이 유일한 안전망일 수 있다
        print(f"\n{failed}개가 검증에 실패했다. 정리를 건너뛴다.", file=sys.stderr)
        if not args.dry_run:
            flag = db_sentinel.raise_fail(
                db, SOURCE, f"NAS 사본 {failed}개가 검증에 실패했다 ({nas})")
            print(f"  깃발을 세웠다: {flag} (/healthz 가 degraded 를 낸다)",
                  file=sys.stderr)
        return 1

    if not args.dry_run and db_sentinel.clear(db, SOURCE):
        print("  지난 실패 깃발을 내렸다")

    if args.prune:
        # 정리는 이 스크립트만 한다 (§10). `.corrupt` 는 glob 에 안 걸려 남는다.
        # glob 은 하위로 안 내려가므로 사람이 따로 둔 디렉토리도 안 건드린다.
        _keep, drop = plan_retention(list(nas.glob("ForGIA_*.db")),
                                     datetime.now())
        for old in sorted(drop, key=lambda p: p.name):
            age = (datetime.now() - (stamp_of(old.name) or datetime.now())).days
            if args.dry_run:
                print(f"  정리할 것: {old.name} ({age}일)")
            else:
                old.unlink()
                print(f"  정리: {old.name} ({age}일)")
        if not drop:
            print("  정리할 것 없음")

    total = len(list(nas.glob("ForGIA_*.db")))
    print(f"\n보냄 {sent}개 · NAS 사본 {total}개 · {nas}")

    # 사진은 **DB 와 갈라 둔다.** 여기서 실패해도 깃발을 세우지 않는다 — 깃발은
    # "DB 사본을 믿을 수 없다" 는 뜻이고, 사진은 NAS 에 원본이 따로 있다(거기서
    # 받아 온 것이다). 종료코드로만 알린다.
    if args.photos:
        src = Path(args.photos_src or
                   (Path(_env("FORGIA_DATA_ROOT", "/data3/ForGIA")) / "photos"))
        pnas = Path(args.photos_nas or (nas.parent / "photos"))
        if not sync_photos(src, pnas, args.photos_keep, args.dry_run):
            return 1

    # 가중치도 사진과 같은 자리에 둔다 — 깃발은 안 세우고 종료코드로만 알린다.
    if not args.no_models:
        msrc = Path(args.models_src or
                    (Path(_env("FORGIA_DATA_ROOT", "/data3/ForGIA")) / "models"))
        mnas = Path(args.models_nas or (nas.parent / "models"))
        if not sync_models(msrc, mnas, args.dry_run):
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
