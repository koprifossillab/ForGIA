#!/usr/bin/env bash
# `/ForGIATest/` 를 nginx 에 얹는다 (085). **root 로 돌린다.**
#
#   sudo bash deploy/host/install-nginx-test.sh
#   sudo bash deploy/host/install-nginx-test.sh --uninstall
#
# 운영 조각(`ForGIA-subpath.conf`)은 사람이 손으로 깔았고 그 절차가 그 파일
# 머리말에 적혀 있다. 테스트 쪽을 스크립트로 만든 이유는 **한 번 더 얹고 내릴 일이
# 반복되기 때문**이다 — 반복되는 절차를 사람이 기억하면 언젠가 한 줄을 빠뜨린다.
#
# 하는 일 셋:
#   1. 조각을 /etc/nginx/snippets/ 로 복사
#   2. phyloserver 의 server 블록에 include 한 줄 추가 (이미 있으면 건너뛴다)
#   3. `nginx -t` 로 검사한 뒤에만 reload
#
# **남의 설정(phyloserver)을 고친다.** 이 머신의 80 은 그 블록이 잡고 있어서
# 다른 길이 없다(018). 되돌릴 수 있게 사본을 먼저 뜨고, `--uninstall` 로 정확히
# 되돌린다.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO/deploy/nginx/ForGIATest-subpath.conf"
SNIP=/etc/nginx/snippets/ForGIATest-subpath.conf
SITE=/etc/nginx/sites-available/phyloserver

[ "$(id -u)" = 0 ] || { echo "root 로 돌릴 것: sudo bash $0" >&2; exit 1; }
[ -f "$SITE" ] || { echo "server 블록이 없다: $SITE" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
    echo "1) include 를 뺀다"
    sed -i '/ForGIATest-subpath/d' "$SITE"
    echo "2) 조각을 지운다"
    rm -f "$SNIP"
    echo "3) 검사 후 reload"
    nginx -t && systemctl reload nginx
    echo
    echo "걷었다. /ForGIATest/ 는 이제 404 다 (502 가 아니라)."
    echo "컨테이너도 내리려면: cd /srv/ForGIA/test && docker compose down"
    exit 0
fi

[ -f "$SRC" ] || { echo "조각이 없다: $SRC" >&2; exit 1; }

echo "1) 조각 설치"
install -m 644 -o root -g root "$SRC" "$SNIP"

echo "2) include 추가"
if grep -q 'ForGIATest-subpath' "$SITE"; then
    echo "   이미 있다 — 건너뛴다"
else
    bak="$SITE.bak-$(date +%Y%m%d_%H%M%S)"
    cp -a "$SITE" "$bak"
    echo "   사본: $bak"
    # 운영 조각 바로 아래에 넣는다 — 두 줄이 붙어 있어야 사람이 함께 읽는다
    sed -i 's|^\([[:space:]]*\)include snippets/ForGIA-subpath\.conf;|&\n\1include snippets/ForGIATest-subpath.conf;|' "$SITE"
    grep -q 'ForGIATest-subpath' "$SITE" || {
        echo "   넣지 못했다 — 운영 include 줄을 못 찾았다. 손으로 볼 것" >&2; exit 1; }
    echo "   넣었다"
fi

echo "3) 검사 후 reload"
nginx -t
systemctl reload nginx

echo
echo "됐다 →  http://172.16.116.98/ForGIATest/"
echo "판을 올리는 것은:  deploy/host/testdeploy.sh <판>"
echo "걷으려면:          sudo bash $0 --uninstall"
