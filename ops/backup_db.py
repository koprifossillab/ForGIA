#!/usr/bin/env python3
"""DiaRUGA v0.29.0 `ops/backup_db.py` 에서 왔다. DB 사본을 backup/ 에 타임스탬프를 붙여 뜬다.

    python backup_db.py
    python backup_db.py --note before-refilter    # 파일명에 꼬리말을 붙인다
    python backup_db.py --keep 20                 # 오래된 것부터 지운다

**`cp ForGIA.db` 로는 안 된다.** WAL 모드라 최근 쓰기가 `-wal` 파일에 있고, 그냥
복사하면 불완전한 사본이 나온다. SQLite 의 backup API 는 잠금을 잡고 일관된 사본을
만든다(쓰는 중에도 안전하다).

DB 는 gitignore 다. 사람의 교정(재생성 불가)이 DB 에만 있는 동안은 이것이 유일한
안전망이므로, 큰 작업 전에 한 번씩 돌린다. review/*.json 내보내기(P02 §5-5)가
갖춰지면 그쪽이 감사 기록을 맡는다.

## 실패했을 때 (.guides/web/data-safety.md §2)

세 가지를 **다** 한다. 하나라도 빠지면 안전망이 조용히 꺼진다.

1. **정리를 건너뛴다.** 깨진 사본을 채택하고 로테이션을 돌리면 지난 정상 사본이
   전부 밀려난다 — 형제 프로젝트가 그렇게 *N 시간 뒤 복구 가능한 사본 0개* 가 됐다
2. **증거를 남긴다.** 실패한 사본에 `.corrupt` 를 붙인다. 정리 glob(`ForGIA_*.db`)에
   안 걸리는 이름이라야 다음 성공 실행이 지우지 않는다
3. **깃발을 세운다.** `db_sentinel` 이 DB 옆에 `INTEGRITY_FAIL` 을 놓고
   `/healthz` 가 그것을 읽어 `degraded` 를 낸다. 로그에만 적으면 읽는 사람이
   없는 동안 아무도 모른다
"""
import argparse
import os
import sqlite3
import sys
import time
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

# 깃발에 적히는 주인. sync_backup_nas.py 와 갈라야 서로의 실패를 안 지운다.
SOURCE = "backup_db"

# 자동(시간별) 스냅샷의 이름. `?` 가 자리를 고정하므로 `--note` 꼬리말이 붙은 것은
# 여기 안 걸린다 — ForGIA_20260804_112852_before-refilter.db 는 글자가 더 많다.
#
# **로컬 정리는 이 구분을 쓰지 않는다.** 손으로 뜬 것은 작업 중에 되돌릴 지점이지
# 보관물이 아니라서, 일이 잘 끝나면 없어져도 되는 물건이다. 로테이션이 알아서
# 밀어내게 두는 편이 사람이 치우는 것을 기억할 필요가 없어 낫다.
#
# 구분이 필요한 곳은 둘이다.
#   - **오프사이트**: 하루 하나만 건너간다. 그 하나는 자동이어야 한다 — 마침 그때
#     사람이 뜬 것이 가장 새것이면 엉뚱한 것이 그날의 오프사이트 사본이 된다
#   - **/healthz 신선도**: "시간별이 멈췄는가" 를 묻는 것이므로 자동만 봐야 한다.
#     손으로 뜬 사본이 섞이면 죽은 cron 을 가린다
#
# 이름을 만드는 쪽에 둔 이유는, 쓰는 데마다 제 패턴을 들면 이름 규칙이 바뀔 때
# 조용히 어긋나기 때문이다.
AUTO_GLOB = "ForGIA_????????_??????.db"


def _env(key, default):
    """환경변수 → .env → 기본값.

    settings.py 에도 같은 것이 있다. 일부러 나눠 뒀다 — 이 스크립트는 **다른 것이
    전부 망가졌을 때 쓰는 마지막 안전망**이라, Django 를 임포트하지 않고 혼자
    돌아야 한다. 열 줄 중복이 그 값을 한다.
    """
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


# DB 는 배포 위치(/srv/ForGIA)로 나가 있을 수 있다 — .env 의 FORGIA_DB 를 따른다.
DB = Path(_env("FORGIA_DB", str(ROOT / "ForGIA.db")))
# 사본은 커지므로(3 GB 넘었다) 큰 디스크에 둘 수 있게 뺀다.
OUT = Path(_env("FORGIA_BACKUP_DIR", str(ROOT / "backup")))

TABLES = ("slide", "viewpoint", "frame", "detection", "candidate",
          "objectreview", "viewpointreview", "run")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--note", default="", help="파일명 꼬리말 (manual/ 로 간다)")
    ap.add_argument("--keep", type=int, default=0,
                    help="자동 스냅샷을 최근 N개만 남긴다 (0 이면 안 지운다)")
    ap.add_argument("--flat", action="store_true",
                    help="꼬리말이 있어도 manual/ 로 가르지 않는다 "
                         "(배포 전 스냅샷처럼 이미 전용 디렉토리에 쓸 때)")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()

    src_path = Path(args.db)
    if not src_path.exists():
        raise SystemExit(f"DB 가 없다: {src_path}")

    # **손으로 뜬 것과 시간별 자동을 디렉토리로 가른다.**
    #
    #   backup/            시간별 자동 — 24시간 rolling. 여기 것만 NAS 로 간다
    #   backup/manual/     사람이 --note 로 뜬 것 — 로테이션이 안 건드린다
    #
    # 이름 규칙(꼬리말 유무)으로도 가를 수 있지만 디렉토리가 낫다. 정리 glob 을
    # 한 번 잘못 쓰는 순간 섞이는데, 디렉토리는 glob 이 애초에 안 내려간다.
    #
    # 수동 스냅샷은 **작업 중에 되돌릴 지점**이지 보관물이 아니다. 그래서 NAS 로
    # 안 가고, 일이 잘 끝나면 사람이 지운다. 24시간 로테이션이 걷어 가게 두면
    # 하루 넘는 작업에서 정작 필요할 때 없다.
    dest = OUT / "manual" if (args.note and not args.flat) else OUT
    dest.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    tail = f"_{args.note}" if args.note else ""
    out = dest / f"ForGIA_{stamp}{tail}.db"
    # **제 이름은 검증을 통과한 뒤에 준다.** 뜨는 중에는 `.part` 다.
    #
    # 뜨다 실패하면 반쯤 쓴 파일이 남는데, 그것이 `ForGIA_*.db` 라는 이름을 달고
    # 있으면 두 가지가 어긋난다. 정리 glob 에 걸려 **가장 새 파일로 살아남아
    # 멀쩡한 사본을 하나 밀어내고**, 오프사이트 track 이 그것을 스냅샷으로 알고
    # 집어 간다. 실제로 그렇게 남는 것을 보고 고쳤다.
    #
    # `sync_backup_nas.py` 가 NAS 쪽에서 하는 것과 같다 — **이름이 붙어 있으면
    # 검증을 통과한 것**이라는 규칙을 양쪽에서 같이 지킨다.
    part = out.with_name(out.name + ".part")

    def fail(reason, evidence=None):
        """정리를 건너뛰고, 증거를 남기고, 깃발을 세운다 (머리말의 셋)."""
        if evidence is not None:
            print(f"  증거를 남겼다: {evidence.name}", file=sys.stderr)
        flag = db_sentinel.raise_fail(src_path, SOURCE, reason)
        print(f"  깃발을 세웠다: {flag} (/healthz 가 degraded 를 낸다)", file=sys.stderr)
        print("  정리는 건너뛴다 — 지난 사본을 남긴다", file=sys.stderr)
        return 1

    def sweep(base):
        """DELETE 모드로 내렸어도 남을 수 있는 부산물을 치운다."""
        for ext in ("-wal", "-shm"):
            stray = base.with_name(base.name + ext)
            if stray.exists():
                stray.unlink()

    # 뜨는 것 자체가 실패할 수도 있다 — 원본이 이미 깨졌거나 디스크가 찼거나.
    # 그때도 깃발은 서야 한다. 예외가 그냥 올라가면 cron 은 로그 한 줄만 남긴다.
    src = dst = None

    def close():
        """열린 것을 닫는다. sqlite3 는 두 번 닫아도 탈이 없다.

        **파일을 만지기 전에 먼저 닫아야 한다.** 마지막 연결이 빠져야 `-wal`·
        `-shm` 이 정리되므로, 연 채로 이름을 바꾸면 부산물이 옛 이름으로 남는다.
        """
        for con in (src, dst):
            if con is not None:
                con.close()

    try:
        src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
        dst = sqlite3.connect(part)
        with dst:
            src.backup(dst)

        # 사본은 파일 하나로 딱 떨어져야 한다. backup 은 journal 모드까지 복사하므로
        # 그대로 두면 WAL 로 남아, 읽을 때마다 -shm 이 생기고 옮길 때 딸려 다녀야
        # 하는 파일이 늘어난다. 보관용은 DELETE 모드가 맞다.
        dst.execute("PRAGMA journal_mode=DELETE")

        # 사본이 실제로 읽히는지 확인한다 — 뜨기만 하고 깨진 것을 모르면 뜻이 없다
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {}
        for t in TABLES:
            try:
                counts[t] = dst.execute(f"select count(*) from viewer_{t}").fetchone()[0]
            except sqlite3.Error:
                counts[t] = None
    except (sqlite3.Error, OSError) as e:
        close()
        print(f"사본을 뜨지 못했다: {e}", file=sys.stderr)
        evidence = None
        if part.exists():
            # 반쯤 쓴 것도 원인을 볼 값어치가 있다. 돌아가지 않는 이름으로 남긴다.
            #
            # **쓸고 나서 이름을 바꾼다.** 반대로 하면 `-wal`·`-shm` 형제가 옛
            # 이름(`.part-wal`)으로 남아 아무도 안 치우게 된다 — 실제로 그랬다.
            sweep(part)
            evidence = out.with_name(out.name + ".corrupt")
            part.replace(evidence)
        return fail(f"사본을 뜨지 못했다 — {e}", evidence)
    finally:
        close()

    sweep(part)

    mb = part.stat().st_size / 1e6
    # 사본 디렉토리는 저장소 밖일 수 있다 (FORGIA_BACKUP_DIR)
    try:
        shown = out.relative_to(ROOT)
    except ValueError:
        shown = out
    if dest != OUT:
        print("  (수동 스냅샷 — manual/ 에 둔다. NAS 로 안 가고 로테이션도 안 건드린다)")
    print(f"{shown}  {mb:.1f} MB  integrity={ok}")
    print("  " + " · ".join(f"{t} {n}" for t, n in counts.items() if n is not None))
    if ok != "ok":
        evidence = out.with_name(out.name + ".corrupt")
        part.replace(evidence)
        print("사본이 깨졌다 — 지우지 말고 원인을 확인하라", file=sys.stderr)
        return fail(f"스냅샷 integrity_check={ok} ({evidence.name})", evidence)

    # 통과했다. 이제서야 제 이름을 준다.
    part.replace(out)

    # 통과했으니 이 track 이 세워 둔 깃발을 내린다. **남의 줄은 안 건드린다** —
    # 오프사이트가 실패한 채로 있으면 그 깃발은 그대로 서 있어야 한다.
    if db_sentinel.clear(src_path, SOURCE):
        print("  지난 실패 깃발을 내렸다")

    if args.keep:
        # **자동만 굴린다** (AUTO_GLOB 주석). 손으로 뜬 것은 작업 중에 되돌릴
        # 지점이라, 24시간이 지났다고 로테이션이 걷어 가면 정작 필요할 때 없다.
        # 그것은 일이 끝나고 사람이 지운다.
        #
        # 다만 `--flat` 은 "이 디렉토리는 이 종류 전용이다" 는 뜻이다(배포 전
        # 스냅샷의 pre_deploy/). 거기서는 꼬리말이 붙은 것도 굴려야 한다 —
        # AUTO_GLOB 은 꼬리말 없는 이름만 잡아서 **--keep 을 줘도 아무것도 안
        # 걷혔고, 배포마다 한 장씩 무한히 쌓였다** (24장까지 갔다).
        # `.part`·`.corrupt` 는 `.db` 로 끝나지 않아 이 glob 에 안 걸린다.
        pat = "ForGIA_*.db" if args.flat else AUTO_GLOB
        old = sorted(OUT.glob(pat))[: -args.keep] if args.keep else []
        for p in old:
            p.unlink()
        if old:
            print(f"  오래된 사본 {len(old)}개 지웠다 (최근 {args.keep}개 유지)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
