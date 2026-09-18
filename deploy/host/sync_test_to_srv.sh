#!/usr/bin/env bash
# 저장소의 **테스트 배포** 파일을 /srv/ForGIA/test 로 옮긴다 (085).
# 운영 쪽은 `sync_to_srv.sh` 다 — 같은 갈래이고 같은 규칙이다.
#
#   ./deploy/host/sync_test_to_srv.sh
#
# **매번 돌려도 해롭지 않다** — cp 뿐이다.
#
# ## `.env` 는 건드리지 않는다
#
# 운영과 같은 이유다. 비밀키와 지금 미리 보는 판이 거기 있고, 통째로 덮어쓰면
# 날아간다. 없을 때만 견본에서 만들어 주고, 견본에 새 항목이 생기면 알리기만 한다.
#
# ## 자료(db/)는 여기서 안 만진다
#
# **사본을 갈아 끼우는 것은 `testdeploy.sh` 의 일이다.** 여기서 함께 하면
# "파일을 맞춘다" 와 "자료를 갈아 끼운다" 가 한 명령에 섞여, 설정만 고치려던
# 사람이 검토 중이던 사본을 잃는다.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_SRV="${FORGIA_TEST_SRV:-/srv/ForGIA/test}"

mkdir -p "$TEST_SRV"

copy() {
    local src="$REPO/$1" dst="$TEST_SRV/$2"
    if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
        echo "  = $2"
        return
    fi
    # `-p` 를 안 붙인다 — plain cp 는 대상이 있으면 그 모드를 안 건드리고,
    # 새로 생긴 것만 열면 된다. 계정 둘이 번갈아 배포해도 그대로 열려 있다
    # (까닭은 sync_to_srv.sh 머리말 "옮긴 파일은 그룹이…").
    cp "$src" "$dst"
    chmod g+w "$dst" 2>/dev/null || true
    echo "  → $2"
}

echo "$REPO → $TEST_SRV"
copy deploy/test/docker-compose.yml docker-compose.yml

# .env 는 없을 때만 만든다. 있으면 손대지 않는다.
if [ ! -f "$TEST_SRV/.env" ]; then
    cp "$REPO/deploy/test/env.template" "$TEST_SRV/.env"
    # 660 이다 — testdeploy.sh 가 sed -i 로 IMAGE_TAG 를 고치므로 배포하는
    # 사람이 읽고 써야 한다. 배포가 한 계정뿐이면 그룹이 자기 그룹이라 600 과 같다.
    chmod 660 "$TEST_SRV/.env"
    echo "  + .env (견본에서 만들었다 — FORGIA_SECRET_KEY 를 채울 것)"
else
    missing=$(comm -23 \
        <(grep -oE '^[A-Z_]+=' "$REPO/deploy/test/env.template" | sort -u) \
        <(grep -oE '^[A-Z_]+=' "$TEST_SRV/.env" | sort -u) || true)
    if [ -n "$missing" ]; then
        echo "  ! .env 에 없는 항목: $(echo "$missing" | tr -d '=' | tr '\n' ' ')" >&2
    fi
    if grep -q '^FORGIA_SECRET_KEY=여기를_채울_것' "$TEST_SRV/.env"; then
        echo "  ! .env 의 FORGIA_SECRET_KEY 가 아직 견본 값이다" >&2
    fi
fi

echo "완료.  다음: deploy/host/testdeploy.sh <판>"
