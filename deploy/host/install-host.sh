#!/usr/bin/env bash
# 호스트에 ForGIA 의 자리를 만든다 — /srv/ForGIA · /data3/ForGIA · nginx 의 /ForGIA/.
# **root 로 돌린다.** 나머지(compose·bin·www·.env)는 root 가 필요 없어
# `sync_to_srv.sh` 가 한다.
#
#   sudo bash deploy/host/install-host.sh
#   sudo bash deploy/host/install-host.sh --uninstall     # nginx 조각만 걷는다
#
# DiaRUGA 에는 이 스크립트가 없다 — 운영 조각은 사람이 손으로 깔았고(그 절차가
# `DiaRUGA-subpath.conf` 머리말에 있다), 테스트 쪽만 `install-nginx-test.sh` 로
# 만들었다. ForGIA 는 **처음부터 스크립트로** 한다 — 같은 서버에 두 번째로 얹는
# 것이라 손 절차가 한 번 더 반복되고, 반복되는 절차를 사람이 기억하면 언젠가
# 한 줄을 빠뜨린다. `install-nginx-test.sh` 와 같은 꼴이다.
#
# 하는 일:
#   0. /srv/ForGIA · /data3/ForGIA 를 만들고 배포 계정 소유로 (이미 있으면 그대로)
#   1. 조각을 /etc/nginx/snippets/ 로 복사
#   2. phyloserver 의 server 블록에 include 한 줄 추가 (이미 있으면 건너뛴다)
#   3. `nginx -t` 로 검사한 뒤에만 reload
#
# **남의 설정(phyloserver)을 고친다.** 이 머신의 80 은 그 블록이 잡고 있어서
# 다른 길이 없다(DiaRUGA 018). 되돌릴 수 있게 사본을 먼저 뜨고, `--uninstall`
# 로 정확히 되돌린다. 디렉토리는 `--uninstall` 이 지우지 않는다 — 자료가 있다.
#
# include 는 **DiaRUGA 조각 아래**에 넣는다. 두 뷰어가 같은 server 블록에
# 나란히 있어야 사람이 함께 읽고, 조각의 순서는 nginx 에 뜻이 없다(location
# 매칭은 파일 순서가 아니라 규칙으로 정해진다).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO/deploy/nginx/ForGIA-subpath.conf"
SNIP=/etc/nginx/snippets/ForGIA-subpath.conf
SITE=/etc/nginx/sites-available/phyloserver
SRV=/srv/ForGIA
DATA=/data3/ForGIA
# 배포 계정 — sudo 로 부른 사람. DiaRUGA 와 같은 uid 1000 이어야 compose 의
# `user: "1000:1000"` 과 맞는다.
OWNER="${SUDO_USER:-paleoadmin}"

[ "$(id -u)" = 0 ] || { echo "root 로 돌릴 것: sudo bash $0" >&2; exit 1; }
[ -f "$SITE" ] || { echo "server 블록이 없다: $SITE" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
    echo "1) include 를 뺀다"
    sed -i '/snippets\/ForGIA-subpath/d' "$SITE"
    echo "2) 조각을 지운다"
    rm -f "$SNIP"
    echo "3) 검사 후 reload"
    nginx -t && systemctl reload nginx
    echo
    echo "걷었다. /ForGIA/ 는 이제 404 다 (502 가 아니라)."
    echo "$SRV · $DATA 는 그대로 두었다 — 지우는 것은 사람이 한다."
    echo "컨테이너도 내리려면: cd $SRV && docker compose down"
    exit 0
fi

[ -f "$SRC" ] || { echo "조각이 없다: $SRC" >&2; exit 1; }

echo "0) 디렉토리"
for d in "$SRV" "$DATA"; do
    if [ -d "$d" ]; then
        echo "   = $d ($(stat -c '%U:%G %a' "$d"))"
    else
        # /srv/DiaRUGA 와 같은 2775 — setgid 라 안에 생기는 것이 그룹을 물려받고,
        # 배포를 계정 둘이 나눠 해도 서로 덮어쓸 수 있다.
        install -d -m 2775 -o "$OWNER" -g "$OWNER" "$d"
        echo "   + $d ($OWNER:$OWNER 2775)"
    fi
done

echo "1) 조각 설치"
install -m 644 -o root -g root "$SRC" "$SNIP"

echo "2) include 추가"
if grep -q 'snippets/ForGIA-subpath' "$SITE"; then
    echo "   이미 있다 — 건너뛴다"
else
    bak="$SITE.bak-$(date +%Y%m%d_%H%M%S)"
    cp -a "$SITE" "$bak"
    echo "   사본: $bak"
    # DiaRUGA 조각들의 **마지막** include 아래에 넣는다 (DiaRUGATest 가 있으면 그
    # 아래, 없으면 DiaRUGA 아래). 주석 한 줄을 같이 넣어 어느 저장소 것인지 남긴다.
    anchor=$(grep -n '^[[:space:]]*include snippets/DiaRUGA[A-Za-z]*-subpath\.conf;' "$SITE" | tail -n1 | cut -d: -f1)
    [ -n "$anchor" ] || {
        echo "   넣지 못했다 — DiaRUGA include 줄을 못 찾았다. 손으로 볼 것" >&2; exit 1; }
    indent=$(sed -n "${anchor}p" "$SITE" | sed 's/include.*//')
    sed -i "${anchor}a\\
\\
${indent}# ForGIA 뷰어 (/ForGIA/). DiaRUGA 옆에 같은 꼴로 얹는다.\\
${indent}# 실체는 ForGIA 저장소의 deploy/nginx/ForGIA-subpath.conf 다.\\
${indent}include snippets/ForGIA-subpath.conf;" "$SITE"
    grep -q 'snippets/ForGIA-subpath' "$SITE" || {
        echo "   넣지 못했다. 손으로 볼 것" >&2; exit 1; }
    echo "   넣었다 (${anchor}행 아래)"
fi

echo "3) 검사 후 reload"
nginx -t
systemctl reload nginx

echo
echo "됐다. 다음은 root 없이:"
echo "  deploy/host/sync_to_srv.sh          # compose·bin·www·.env 를 $SRV 로"
echo "  $SRV/.env 의 FORGIA_SECRET_KEY 를 채운다"
echo "  deploy/host/deploy.sh <판>          # 이미지가 생긴 뒤"
echo "테스트 자리(/ForGIATest/)는 따로:  sudo bash deploy/host/install-nginx-test.sh"
echo "걷으려면:                          sudo bash $0 --uninstall"
