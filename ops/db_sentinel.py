#!/usr/bin/env python3
"""DiaRUGA v0.29.0 `ops/db_sentinel.py` 에서 왔다. 백업이 무결성 실패를 겪었다는 것을 `/healthz` 까지 나르는 깃발.

    import db_sentinel
    db_sentinel.raise_fail(DB, "backup_db", "스냅샷 integrity_check=... ")
    db_sentinel.clear(DB, "backup_db")
    db_sentinel.read(DB)        # [{"time":..., "source":..., "reason":...}, ...]

## 왜 파일인가

**안전망이 스스로를 알려야 한다** (`.guides/web/data-safety.md` §2). 백업이 깨진
사본을 잡아내고 로그에만 적으면, 그 로그를 읽는 사람이 없는 동안 안전망은
꺼진 채로 있다. 사람이 이미 보고 있는 통로에 실어야 한다
(`.guides/web/operations.md` §5) — 여기서는 `/healthz` 다.

DB 를 통로로 쓰지 않는 이유가 중요하다. **이 깃발이 서는 상황이 곧 DB 를 믿을 수
없는 상황이다.** 깨진 DB 에 "DB 가 깨졌다" 고 적을 수는 없다. 그래서 옆에 놓인
평범한 텍스트 파일이다.

## 어디에 놓이나

DB 파일과 같은 디렉토리다 — 배포에서는 `/srv/ForGIA/db/INTEGRITY_FAIL`.
그 디렉토리가 컨테이너에 **통째로** 마운트되므로(data-safety §8), 호스트의
`backup_db.py` 가 세운 깃발을 컨테이너 안의 뷰어가 그대로 읽는다. 마운트가
디렉토리인 덕을 여기서 한 번 더 본다.

## 왜 track 마다 한 줄인가

백업 track 이 둘이다 — 시간별 `backup_db.py` 와 오프사이트 `sync_backup_nas.py`.
둘은 서로 다른 것을 본다. 앞의 것은 **원본 DB** 가 성한지를, 뒤의 것은 **NAS 로
건너간 사본** 이 성한지를 본다.

하나의 깃발을 공유하면 나중에 성공한 track 이 남의 실패를 지운다 — NAS 사본이
깨졌다는 깃발을 다음 시간별 백업이 조용히 내리는 식이다. 그래서 줄마다 세운
주인을 적고, **자기 줄만 지운다.**

    2026-08-04T10:35:12	backup_db	스냅샷 integrity_check=... (ForGIA_....db)
    2026-08-04T04:00:03	sync_backup_nas	NAS 사본 검증 실패 — file is not a database

한 줄이라도 남아 있으면 `/healthz` 는 `degraded` 다.

## Django 를 부르지 않는다

`backup_db.py`·`sync_backup_nas.py` 와 같은 이유다 — 이 코드가 도는 시점은
**다른 것이 다 망가졌을 수 있는 시점**이라 혼자 돌아야 한다. 뷰어(Django) 쪽은
읽기만 하며, `settings.py` 가 저장소 뿌리를 `sys.path` 에 더해 이 모듈을 본다.

## 유의

읽고-고쳐-쓰기라 두 track 이 같은 순간에 쓰면 한쪽 줄이 날아갈 수 있다. 지금은
서로 다른 시각의 cron 이라 겹치지 않는다. 겹치게 되면 잠금을 붙일 것.
"""
import os
import time
from pathlib import Path

NAME = "INTEGRITY_FAIL"


def sentinel_path(db_path) -> Path:
    """DB 옆의 깃발 자리. DB 가 어디로 나가 있든 따라간다."""
    return Path(db_path).resolve().parent / NAME


def _one_line(text) -> str:
    """줄바꿈·탭이 섞여도 한 줄로 만든다 — 형식이 TSV 라 깨지면 못 읽는다."""
    return " ".join(str(text).split())


def read(db_path):
    """세워진 깃발을 [{time, source, reason}, ...] 로. 없으면 빈 목록.

    **읽기는 절대 실패하지 않는다.** 이 함수를 부르는 곳이 `/healthz` 라,
    여기서 예외가 나면 상태를 알리려다 상태 화면을 죽이는 꼴이 된다.
    """
    try:
        text = sentinel_path(db_path).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        parts += [""] * (3 - len(parts))
        out.append({"time": parts[0], "source": parts[1], "reason": parts[2]})
    return out


def _write(path: Path, rows):
    """비면 지운다. 남으면 임시 파일로 쓰고 갈아 끼운다 (반쯤 쓴 깃발을 안 남긴다)."""
    if not rows:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    body = "".join(f"{r['time']}\t{r['source']}\t{r['reason']}\n" for r in rows)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    # 컨테이너(1000:1000)가 읽어야 한다. umask 가 022 여도 0644 는 나온다.
    os.chmod(tmp, 0o664)
    os.replace(tmp, path)


def raise_fail(db_path, source: str, reason: str) -> Path:
    """`source` 의 깃발을 세운다(이미 있으면 갱신). 세운 자리를 돌려준다."""
    path = sentinel_path(db_path)
    rows = [r for r in read(db_path) if r["source"] != source]
    rows.append({
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": _one_line(source),
        "reason": _one_line(reason),
    })
    _write(path, rows)
    return path


def clear(db_path, source: str) -> bool:
    """`source` 가 세운 줄만 지운다. 실제로 지운 것이 있으면 True.

    **남의 줄은 건드리지 않는다.** 다른 track 의 실패를 이 track 의 성공으로
    덮으면 안 된다 — 머리말 참고.
    """
    rows = read(db_path)
    left = [r for r in rows if r["source"] != source]
    if len(left) == len(rows):
        return False
    _write(sentinel_path(db_path), left)
    return True


if __name__ == "__main__":
    # 손으로 확인할 때. 게이트를 일부러 터뜨려 보는 데도 쓴다.
    #   python db_sentinel.py raise <source> <이유>
    #   python db_sentinel.py clear <source>
    #   python db_sentinel.py show
    import sys

    def _env_db():
        if "FORGIA_DB" in os.environ:
            return os.environ["FORGIA_DB"]
        # `.env` 는 한 칸 위다 (저장소 루트 · /srv/ForGIA).
        # 100 에서 스크립트를 옮기며 이 전제가 깨진 적이 있다.
        here = Path(__file__).resolve().parent
        root = here if (here / ".env").exists() else here.parent
        try:
            for line in (root / ".env").read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("FORGIA_DB="):
                    return line.partition("=")[2].strip()
        except OSError:
            pass
        return str(root / "ForGIA.db")

    db = _env_db()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "raise":
        print(raise_fail(db, sys.argv[2], " ".join(sys.argv[3:])))
    elif cmd == "clear":
        print("지웠다" if clear(db, sys.argv[2]) else "그 주인의 줄이 없다")
    else:
        rows = read(db)
        print(sentinel_path(db))
        for r in rows:
            print(f"  {r['time']}  {r['source']}  {r['reason']}")
        if not rows:
            print("  (없음)")
    sys.exit(0)
