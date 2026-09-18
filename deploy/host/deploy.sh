#!/usr/bin/env bash
# 판을 갈아 끼운다 (.guides/web/deployment.md §5).
#
#   /srv/ForGIA/bin/deploy.sh v0.1.1
#   /srv/ForGIA/bin/deploy.sh v0.1.1 --no-pull    # 이미 로컬에 있는 이미지로
#
# 순서가 곧 안전장치다:
#
#   1. 이미지를 받는다        받다 실패하면 지금 도는 것을 안 건드리고 끝난다
#   2. .env 의 IMAGE_TAG 만   통째로 다시 만들지 않는다 — 비밀키가 날아간다
#   3. 유지보수 플래그        내리는 동안 사람이 502 를 안 본다
#   4. 내린다                 쓰는 쪽이 빠져야 WAL 이 정리된다
#   5. 배포 전 스냅샷         새 판이 DB 를 건드렸을 때 돌아올 지점
#   6. 올리고 health 게이트   200 이 안 나오면 실패로 끝낸다
#   7. 플래그 해제            trap 이라 중간에 죽어도 풀린다
#   8. smoke                  판·자료·안전망까지 본다 — 200 은 "떴다" 일 뿐이다
#
# **이 머신은 개발·운영·백업을 겸한다.** 가이드는 빌드를 prod 밖에서 하라고
# 하지만 여기서는 성립하지 않는다(devlog 016). 그래서 이 스크립트는 빌드하지
# 않는다 — 레지스트리에 올라간 것을 받아 갈아 끼우기만 한다. 굽는 것은
# `deploy/docker-compose.yml` 의 몫이다.
set -euo pipefail

SRV="${FORGIA_SRV:-/srv/ForGIA}"
HEALTH="${FORGIA_HEALTH:-http://127.0.0.1:8092/healthz}"
SERVICE="${FORGIA_SERVICE:-web}"
FLAG="$SRV/maintenance.flag"
SNAP_DIR="${FORGIA_BACKUP_DIR:-/data3/ForGIA/backup}/pre_deploy"

VER="${1:-}"
[ -n "$VER" ] || { echo "쓰임새: $0 <판> [--no-pull]   예: $0 v0.1.1" >&2; exit 1; }
PULL=1; [ "${2:-}" = "--no-pull" ] && PULL=0

cd "$SRV"
say() { echo "[$(date '+%F %T')] $*"; }

# 지금 무엇이 도는지 먼저 적는다 — 되돌릴 때 이 줄을 본다
PREV=$(grep -oP '^IMAGE_TAG=\K.*' .env 2>/dev/null || echo "(없음)")
say "=== 배포 $PREV → $VER ==="

# 1) 받는다. 실패하면 여기서 끝 — 도는 것을 안 건드린다.
if [ "$PULL" = 1 ]; then
    say "이미지를 받는다"
    docker pull "koprifossillab/forgia:$VER"
fi

# 2) .env 의 판만 **제자리에서** 고친다. 파일을 다시 만들지 않는다.
if grep -q '^IMAGE_TAG=' .env; then
    sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=$VER|" .env
else
    printf '\nIMAGE_TAG=%s\n' "$VER" >> .env
fi
say ".env 의 IMAGE_TAG 를 $VER 로"

# 3) 유지보수 플래그. trap 이라 중간에 죽어도 풀린다.
touch "$FLAG"
trap 'rm -f "$FLAG"' EXIT
say "유지보수 모드"

# 4) 내린다. 쓰는 쪽이 빠져야 WAL 이 본체로 정리된다.
#
# **`down` 이 아니라 `stop web` 이다.** `down` 은 이 compose 프로젝트의 컨테이너와
# 네트워크를 통째로 걷어 가는데, 그 안에는 폴러가 띄운 일회성 파이프라인
# 컨테이너도 들어 있다 — 뷰어를 올리는 일이 몇십 분짜리 합성·검출을 죽인다.
# 실제로 새 슬라이드 3장이 들어오던 중에 그럴 뻔했다.
#
# 파이프라인은 DB 를 열어 두고 쓰지만, 그쪽이 WAL 을 정리하는 것을 막지는
# 않는다 — 스냅샷은 `backup_db.py` 가 sqlite 백업 API 로 뜨므로 다른 쓰는
# 쪽이 있어도 온전한 사본이 나온다.
docker compose stop web
docker compose rm -f web >/dev/null 2>&1 || true
RUNNING_PIPE=$(docker compose ps -q pipeline 2>/dev/null | wc -l)
if [ "$RUNNING_PIPE" -gt 0 ] || docker ps --format '{{.Names}}' | grep -q 'pipeline-run'; then
    say "파이프라인이 도는 중이다 — 건드리지 않는다"
fi

# 5) 배포 전 스냅샷. 새 판의 마이그레이션이 DB 를 건드렸을 때 돌아올 지점이다.
#    backup_db.py 를 쓴다 — cp 는 WAL 때문에 불완전한 사본이 된다.
#
#    **컨테이너 안에서 뜬다.** DB 를 만지는 일은 전부 한 문으로만 들어간다
#    (compose 의 dbtool 주석). 호스트 venv 로 부르던 것이 규칙의 마지막 구멍이었다.
#    돌아가는 것은 /srv/ForGIA/scripts 에 옮겨 둔 backup_db.py 다.
#
#    이 자리에서는 .env 의 IMAGE_TAG 가 이미 새 판이고 이미지도 받아 둔 뒤라
#    dbtool 이 새 이미지로 뜬다. backup_db.py 는 Django 를 안 쓰고 sqlite3 백업
#    API 만 쓰므로 어느 판의 이미지든 하는 일이 같다.
mkdir -p "$SNAP_DIR"
DBRUN="$(cd "$(dirname "$0")" && pwd)/dbrun.sh"
[ -x "$DBRUN" ] || DBRUN="${FORGIA_REPO:-$HOME/projects/ForGIA}/deploy/host/dbrun.sh"
SNAP_SCRIPT="${FORGIA_SCRIPTS_DIR:-$SRV/scripts}/backup_db.py"

snapshot_failed() {
    # 스냅샷은 되돌아올 지점이다. 없이 진행하는 것은 사람이 정할 일이지
    # 스크립트가 조용히 넘길 일이 아니다.
    say "스냅샷을 뜨지 못했다 — $1" >&2
    say "  그래도 진행하려면 FORGIA_SKIP_SNAPSHOT=1 을 주고 다시 돌린다" >&2
    [ "${FORGIA_SKIP_SNAPSHOT:-}" = "1" ] || exit 1
}

# -f 로 본다. -x 로 보면 실행 권한이 없는 것만으로 건너뛴다 — 실제로 그래서
# 첫 배포에서 스냅샷 없이 지나갔다. 어차피 python 으로 부르므로 권한은 상관없다.
if [ ! -f "$SNAP_SCRIPT" ]; then
    snapshot_failed "$SNAP_SCRIPT 이 없다 (deploy/host/dbsync.sh backup_db.py)"
elif [ ! -x "$DBRUN" ]; then
    snapshot_failed "dbrun.sh 를 못 찾았다 ($DBRUN)"
else
    # --flat 을 준다. 꼬리말이 붙었지만 이건 사람이 뜬 것이 아니라 **배포가 뜨는
    # 것**이고, 이미 전용 디렉토리(pre_deploy/)에 쓴다. 없으면 manual/ 로 새서
    # --keep 20 이 아무것도 못 지우고 배포마다 한 장씩 무한히 쌓인다.
    DBRUN_ENV="FORGIA_BACKUP_DIR=$SNAP_DIR" "$DBRUN" backup_db.py \
        --note "pre-deploy-$VER" --keep 20 --flat \
        || snapshot_failed "backup_db.py 가 실패했다"
fi

# 6) 올리고 health 게이트. 200 이 안 나오면 실패다.
docker compose up -d "$SERVICE"
say "기동 확인 중 ($HEALTH)"
for i in $(seq 1 30); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH" || true)
    if [ "$code" = "200" ]; then
        say "정상 ($((i * 2))초)"
        # 점검 안내를 여기서 내린다. smoke 가 nginx 를 거치는 길도 보는데, 깃발이
        # 서 있으면 그 길이 503 이라 정작 확인할 것을 못 본다. trap 은 그대로 둔다
        # — 아래에서 죽어도 풀리게.
        rm -f "$FLAG"
        docker compose ps --format '  {{.Name}}  {{.Image}}  {{.Status}}'
        break
    fi
    sleep 2
done

if [ "${code:-}" != "200" ]; then
    say "실패 — $HEALTH 가 200 을 안 낸다. 되돌리려면:" >&2
    say "  $0 $PREV --no-pull" >&2
    docker compose logs --tail 30 "$SERVICE" >&2
    exit 1
fi

# 7) smoke (.guides/web/README.md 의 표준 동사).
#
# **기동 게이트만으로는 모자란다.** 200 은 "떴다" 는 뜻일 뿐이다. 판이 실제로
# 갈렸는지, DB 를 제대로 물었는지(빈 DB 를 물어도 200 은 나온다), 백업이 무결성
# 실패를 물고 있는지는 200 에 안 담긴다. `/healthz` 가 degraded 에 **200** 을 내는
# **8) `/srv` 의 스크립트를 이 판에 맞춘다** (100).
#
# 스크립트는 이미지 안(`/app/pipeline`·`/app/ops`)에도 같은 것이 있다 —
# 거기서 뽑으면 **판이 정의상 맞는다.** 예전에는 사람이 `dbsync.sh` 로 옮겼고,
# 잊으면 옛 `check_db.py` 가 없는 고장을 외쳤다(080 에서 실제로 났다).
#
# **막지는 않는다.** 스크립트가 낡은 것은 방금 올린 판이 잘못됐다는 뜻이 아니다
# — smoke 의 표류 검사가 경고만 하는 것과 같은 이유다.
SYNC="${FORGIA_SYNC:-$SRV/bin/sync_to_srv.sh}"
if [ -x "$SYNC" ]; then
    say "/srv 스크립트를 $VER 에 맞춘다"
    "$SYNC" --from-image "$VER" 2>&1 | sed 's/^/    /' | tee -a /dev/stderr \
        >/dev/null || say "스크립트 갱신에 실패했다 — 손으로 볼 것"
else
    say "sync_to_srv.sh 가 없다 ($SYNC) — /srv 스크립트는 그대로다"
fi

# 것도 그래서다 — 배포를 세우는 판단은 여기서 한다.
SMOKE="${FORGIA_SMOKE:-$SRV/bin/smoke.sh}"
if [ ! -x "$SMOKE" ]; then
    say "smoke.sh 가 없다: $SMOKE — sync_to_srv.sh 를 돌렸는가?" >&2
    say "=== 끝 (smoke 없이) ==="
    exit 0
fi

if "$SMOKE" "$VER"; then
    say "=== 끝 ==="
    exit 0
fi

# **되돌리지 않는다.** 새 판은 떠 있고, 무엇이 걸렸는지는 사람이 봐야 한다
# (.guides/web/deployment.md §9 — 손상에 반사적으로 롤백하지 말 것).
say "smoke 가 실패했다. 새 판($VER)은 떠 있다 — 위 항목을 볼 것." >&2
say "되돌리려면:  $0 $PREV --no-pull" >&2
exit 1
