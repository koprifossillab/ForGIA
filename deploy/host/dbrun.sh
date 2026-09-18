#!/usr/bin/env bash
# DB 를 만지는 스크립트를 **컨테이너 안에서** 돌린다.
#
#   deploy/host/dbrun.sh check_db.py
#   deploy/host/dbrun.sh check_db.py --slide rs23 -v
#   deploy/host/dbrun.sh prune_detections.py --apply
#   DBRUN_ENV="FORGIA_BACKUP_DIR=/data3/ForGIA/backup/pre_deploy" \
#     deploy/host/dbrun.sh backup_db.py --note before-refilter
#
# DBRUN_ENV 는 컨테이너로 넘길 환경변수다 (KEY=VAL, 공백으로 여럿). 스크립트가
# .env 에 없는 값을 봐야 할 때 쓴다 — 배포가 스냅샷 자리를 지정하는 것이 그것이다.
#
# 돌아가는 것은 /srv/ForGIA/scripts 에 옮겨 둔 것이다 (dbsync.sh 가 옮긴다).
# Django·모델 코드는 이미지 안의 /app — 뷰어 컨테이너가 쓰는 바로 그것이다.
# 왜 이 문 하나로만 들어가는지는 compose 의 dbtool 주석에 있다.
set -euo pipefail

DEPLOY_DIR="${FORGIA_DEPLOY_DIR:-/srv/ForGIA}"
SCRIPTS="${FORGIA_SCRIPTS_DIR:-$DEPLOY_DIR/scripts}"
# 저장소 — 옮겨 둔 것과 어긋났는지 보는 데만 쓴다. /srv/ForGIA/bin 에 복사해
# 두고 부를 수도 있어서 찾지 못하면 그 검사만 건너뛴다.
HERE="${FORGIA_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)}"

[ $# -gt 0 ] || { echo "쓰임: $(basename "$0") <스크립트> [인자...]" >&2; exit 2; }
[ -f "$DEPLOY_DIR/docker-compose.yml" ] || {
  echo "배포 구성이 없다: $DEPLOY_DIR/docker-compose.yml" >&2; exit 1; }

name="$(basename "$1")"; shift
[ -f "$SCRIPTS/$name" ] || {
  echo "$SCRIPTS 에 $name 이 없다 — deploy/host/dbsync.sh $name 로 옮길 것" >&2
  exit 1; }

# 옮겨 둔 것이 저장소와 어긋나면 알린다. 막지는 않는다 — 일부러 옛 판을 돌리는
# 일이 있고, 무엇이 도는지만 알면 된다.
if [ -f "$HERE/$name" ] && ! cmp -s "$SCRIPTS/$name" "$HERE/$name"; then
  echo "※ $name 이 저장소와 다르다 (/srv 쪽이 돈다. 맞추려면 dbsync.sh $name)" >&2
fi

ENVARGS=()
for kv in ${DBRUN_ENV:-}; do ENVARGS+=(-e "$kv"); done

cd "$DEPLOY_DIR"
exec docker compose run --rm "${ENVARGS[@]}" dbtool "$name" "$@"
