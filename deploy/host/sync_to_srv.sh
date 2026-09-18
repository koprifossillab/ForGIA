#!/usr/bin/env bash
# from DiaRUGA v0.29.0 deploy/host/sync_to_srv.sh — 이름·경로·포트를 바꿨고, 아직
# `pipeline/`·`ops/` 가 없는 0단계에서도 돌도록 저장소 검사·글롭 두 줄만 손봤다 (P01 §3.1·§4)
# 배포 파일을 /srv/ForGIA 으로 옮긴다 (.guides/web/deployment.md §2).
#
#   ./deploy/host/sync_to_srv.sh                 # 저장소에서 (개발 중)
#   sync_to_srv.sh --from-image v0.9.1           # 이미지에서 (운영·저장소 없음)
#
# ## 근원이 둘인 이유 (100)
#
# **저장소가 있으면 저장소에서** — 스크립트만 고쳐 밀어 넣으면 7.2 GB 이미지를
# 다시 굽지 않아도 되는 것이 `/srv` 를 쓰는 이득의 절반이다.
#
# **`--from-image` 는 저장소를 안 본다.** 운영 서버에 저장소가 없을 수 있고
# (사용자 방침), 그때는 방금 받은 이미지가 곧 근원이다 — `COPY . .` 로 저장소가
# 통째로 `/app` 에 들어 있다. **이쪽이 판을 정의상 맞춘다**: 스크립트와 Django
# 코드가 같은 이미지에서 나오므로 어긋날 수가 없다. `deploy.sh` 가 배포 뒤에
# 이 갈래로 부른다.
#
# `git pull` 뒤에 돌린다. **매번 돌려도 해롭지 않다** — cp 뿐이다. 잊으면 배포
# 파일 변경이 한 판 미뤄질 뿐이다.
#
# ## 왜 스크립트인가
#
# 손으로 `cp` 하면 어느 파일을 옮겨야 하는지를 사람이 기억해야 한다. 파일이
# 늘면 하나를 빠뜨리고, 빠뜨린 것은 다음 배포에서야 드러난다.
#
# ## 무엇을 옮기지 않는가
#
# **`.env` 는 건드리지 않는다.** prod 전용 상태다 — 비밀키와 지금 도는 판이
# 거기 있다. 통째로 덮어쓰면 그것들이 날아간다. 형제 프로젝트가 그렇게 크롤러
# 자격증명을 잃고 3.5개월을 몰랐다 (data-safety.md §13).
#
# 없을 때만 견본에서 만들어 준다.
#
# ## 옮긴 파일은 그룹이 다시 쓸 수 있어야 한다
#
# 배포를 계정 둘이 나눠 하면(`docker` 그룹에 든 사람이면 `deploy.sh` 를 돌린다)
# 여기서 옮긴 파일을 다음 사람이 덮어써야 한다. 그런데 **`cp -p` 는 원본
# 모드를(644), `docker cp` 는 이미지 안의 모드를(755) 그대로 씌워 그룹 쓰기
# 비트를 지운다** — `chmod -R g+w /srv/ForGIA` 로 한 번 걸어 놔도 배포 한 번에
# 없어지고, 그 다음부터 다른 계정의 배포가 이 단계에서 막힌다. 게다가
# `deploy.sh` 는 이 실패를 치명적으로 안 봐서 **컨테이너만 새 판이고
# `/srv/scripts` 는 옛 판인 채로** 끝난다 — 조용히 어긋나는 쪽이다.
#
# 그래서 `put()` 하나로 모아 두 겹으로 막는다. **`-p` 를 안 붙인다** — plain
# `cp` 는 대상이 이미 있으면 그 모드를 안 건드리므로 한 번 열어 둔 것이 계속
# 열려 있다. 그리고 **새로 생긴 것만 `chmod g+w`** 로 연다(그 자리에서는 내가
# 소유자라 반드시 된다). 한 사람만 쓰는 서버에서는 그룹이 자기 그룹이라 아무
# 차이가 없다.
#
# `-p` 를 버려도 잃는 것이 없다 — 바뀌었는지는 `cmp -s` 로 보지 시각으로 보지
# 않고, 실행 비트는 `bin/*` 에 `chmod +x` 로 따로 준다.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd || echo /nonexistent)"
SRV="${FORGIA_SRV:-/srv/ForGIA}"
IMAGE=""
if [ "${1:-}" = "--from-image" ]; then
    IMAGE="${2:-}"
    [ -n "$IMAGE" ] || { echo "쓰임새: $0 --from-image <판>" >&2; exit 2; }
    case "$IMAGE" in */*) ;; *) IMAGE="koprifossillab/forgia:$IMAGE";; esac
fi

[ -d "$SRV" ] || { echo "배포 디렉토리가 없다: $SRV" >&2; exit 1; }

# **`chmod` 은 모드가 이미 맞아도 남의 파일이면 EPERM 이다** — 건너뛰지 않는다
# (`chmod +x /bin/ls` 로 확인). 계정 둘이 번갈아 배포하면 앞사람이 만든 파일이
# 여기 걸려 `set -e` 가 **배포 한복판에서** 끊는다. 그래서 모드를 손보는 자리는
# 전부 이 문으로 보낸다. 실패해도 좋은 이유가 있다 — 내가 만든 파일이면 내가
# 소유자라 반드시 되고, 남의 것이면 그 사람이 이미 같은 모드로 만들어 뒀다.
chmod_ok() { chmod "$@" 2>/dev/null || true; }

# 옮기는 자리는 전부 여기를 지난다 (머리말 "옮긴 파일은 그룹이…" 참고).
put() {
    cp "$1" "$2"
    chmod_ok g+w "$2"
}

# --- 이미지에서 (저장소를 안 본다) ---------------------------------------
if [ -n "$IMAGE" ]; then
    echo "$IMAGE → $SRV"
    docker image inspect "$IMAGE" >/dev/null 2>&1 || {
        echo "이미지가 없다: $IMAGE (docker pull 을 먼저)" >&2; exit 1; }
    # **컨테이너를 만들어 놓고 뽑는다.** `docker cp` 는 도는 컨테이너가 아니라
    # 만들어진 것에서도 되고, 그러면 entrypoint 를 안 건드린다.
    cid="$(docker create "$IMAGE" true)"
    trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
    tmp="$(mktemp -d)"
    for d in pipeline ops; do
        docker cp "$cid:/app/$d/." "$tmp/" 2>/dev/null || {
            echo "이미지에 /app/$d 가 없다 — 100 이전 판이다" >&2; exit 1; }
    done
    mkdir -p "$SRV/scripts" "$SRV/bin" "$SRV/www"
    n=0
    for f in "$tmp"/*.py; do
        b="$(basename "$f")"
        case "$b" in test_*) continue;; esac      # 시험은 운영에 안 간다
        if [ -f "$SRV/scripts/$b" ] && cmp -s "$f" "$SRV/scripts/$b"; then
            echo "  = scripts/$b"
        else
            put "$f" "$SRV/scripts/$b"; echo "  → scripts/$b"; n=$((n + 1))
        fi
    done
    # 배포 파일도 같은 이미지에서 — 저장소가 없어도 서는 것이 요점이다
    for pair in "deploy/srv/docker-compose.yml:docker-compose.yml" \
                "deploy/host/deploy.sh:bin/deploy.sh" \
                "deploy/host/smoke.sh:bin/smoke.sh" \
                "deploy/poll_nas.sh:bin/poll_nas.sh" \
                "deploy/warm_thumbs.sh:bin/warm_thumbs.sh" \
                "deploy/host/sync_to_srv.sh:bin/sync_to_srv.sh" \
                "deploy/nginx/maintenance.html:www/ForGIA-maintenance.html" \
                "deploy/nginx/unavailable.html:www/ForGIA-unavailable.html"; do
        src="${pair%%:*}"; dst="${pair##*:}"
        # **`docker cp` 로 곧장 꽂지 않는다** — 이미지 안의 모드를 그대로 씌워
        # 그룹 쓰기를 지운다. 받아 놓고 `put` 으로 옮긴다.
        docker cp "$cid:/app/$src" "$tmp/.staged" 2>/dev/null || {
            echo "  ! 이미지에 $src 가 없다 — 건너뛴다" >&2; continue; }
        put "$tmp/.staged" "$SRV/$dst"
        case "$dst" in bin/*) chmod_ok +x "$SRV/$dst"
                             [ -x "$SRV/$dst" ] || echo "  ! $dst 에 실행 비트가 없다" >&2;; esac
        echo "  → $dst"
    done
    rm -rf "$tmp"
    echo "완료 (스크립트 $n 개 갱신). .env 는 안 건드렸다."
    exit 0
fi

# `ops/` 가 아니라 배포 compose 로 저장소를 알아본다 — 0단계에는 `ops/` 가 아직 없다.
[ -f "$REPO/deploy/srv/docker-compose.yml" ] || {
    echo "저장소를 못 찾았다: $REPO — 저장소가 없으면 --from-image 를 쓸 것" >&2
    exit 1; }

# 배포용 compose 와 호스트 스크립트. 판(IMAGE_TAG)은 .env 에 있으므로 이 파일들은
# 저장소의 것과 글자 그대로 같다 — diff 가 나면 누가 손으로 고친 것이다.
copy() {
    local src="$REPO/$1" dst="$SRV/$2"
    if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
        echo "  = $2"
        return
    fi
    put "$src" "$dst"
    echo "  → $2"
}

echo "$REPO → $SRV"
copy deploy/srv/docker-compose.yml docker-compose.yml
mkdir -p "$SRV/bin"
# **폴러도 여기 산다** (100). cron 이 저장소를 부르면 저장소가 없는 서버에서
# 파이프라인이 통째로 안 돈다 — 운영에 필요한 것은 전부 /srv 안에 있어야 한다.
# **자기 자신도 옮긴다** — `deploy.sh` 가 `/srv/bin/sync_to_srv.sh` 를 부르고,
# 저장소 없는 서버에서는 그것이 유일한 사본이다.
for f in deploy.sh smoke.sh poll_nas.sh warm_thumbs.sh sync_to_srv.sh; do
    src="deploy/host/$f"
    # poll_nas.sh·warm_thumbs.sh 는 deploy/ 에 있다 (호스트 전용이 아니라
    # 이미지에도 들어가야 해서 — --from-image 갈래가 거기서 꺼낸다)
    [ -f "$REPO/$src" ] || src="deploy/$f"
    copy "$src" "bin/$f"
    chmod_ok +x "$SRV/bin/$f"
    [ -x "$SRV/bin/$f" ] || echo "  ! bin/$f 에 실행 비트가 없다" >&2
done

# **운영·파이프라인 스크립트** (100). 저장소에서는 `pipeline/`·`ops/` 로 갈려
# 있고 여기서는 **평평하게** 모인다 — 컨테이너가 이 디렉토리 하나만 물고,
# 스크립트끼리의 임포트(`judge`·`scale`·`runlog`)도 평평해야 돈다.
#
# **`migrate/`·`tools/` 는 안 옮긴다.** 이전기·일회성 도구라 운영이 스스로
# 부를 일이 없다 — 필요하면 그때 `dbsync.sh <이름>` 으로 하나만 옮긴다.
mkdir -p "$SRV/scripts"
shopt -s nullglob        # 0단계에는 두 디렉토리가 비어 있거나 없다
for f in "$REPO"/pipeline/*.py "$REPO"/ops/*.py; do
    n="$(basename "$f")"
    case "$n" in test_*) continue;; esac      # 시험은 운영에 안 간다
    copy "${f#$REPO/}" "scripts/$n"
done

# nginx 가 배포 중에 낼 안내 페이지. nginx(www-data)가 읽어야 하므로 권한을 연다.
mkdir -p "$SRV/www"
copy deploy/nginx/maintenance.html www/ForGIA-maintenance.html
copy deploy/nginx/unavailable.html www/ForGIA-unavailable.html
# 775 다 — nginx(www-data)는 읽기만 하면 되므로 그룹 쓰기를 열어도 잃는 것이
# 없고, 닫아 두면 **다음 판에서 페이지가 하나 늘 때** 만든 사람이 아닌 계정의
# 배포가 여기서 끊긴다(디렉토리에 못 만든다). put() 과 같은 이유다.
chmod_ok 775 "$SRV/www"; chmod_ok 664 "$SRV/www"/*.html

# .env 는 없을 때만 만든다. 있으면 손대지 않는다.
if [ ! -f "$SRV/.env" ]; then
    cp "$REPO/deploy/srv/env.template" "$SRV/.env"
    # 660 이다 — `deploy.sh` 가 `sed -i` 로 IMAGE_TAG 를 고치므로 배포하는 사람이
    # 읽고 써야 한다. 배포가 한 계정뿐이면 그룹이 자기 그룹이라 600 과 같다.
    chmod 660 "$SRV/.env"
    echo "  + .env (견본에서 만들었다 — FORGIA_SECRET_KEY 를 채울 것)"
else
    # 견본에 새 항목이 생겼는데 .env 에 없으면 알려만 준다. 채우는 것은 사람 몫이다.
    missing=$(comm -23 \
        <(grep -oE '^[A-Z_]+=' "$REPO/deploy/srv/env.template" | sort -u) \
        <(grep -oE '^[A-Z_]+=' "$SRV/.env" | sort -u) || true)
    if [ -n "$missing" ]; then
        echo "  ! .env 에 없는 항목: $(echo "$missing" | tr -d '=' | tr '\n' ' ')" >&2
    fi
fi

echo "완료."
