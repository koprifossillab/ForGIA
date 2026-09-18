#!/usr/bin/env bash
# 배포한 것이 실제로 사는지 본다 (.guides/web/README.md 의 표준 동사 `smoke`).
#
#   /srv/ForGIA/bin/smoke.sh              # .env 의 IMAGE_TAG 를 기대값으로
#   /srv/ForGIA/bin/smoke.sh v0.1.20      # 이 판이 떠 있어야 한다
#
# `deploy.sh` 가 마지막에 이것을 부른다. 따로도 돌린다 — 배포와 무관하게 지금
# 상태를 묻는 데가 하나 있어야 하기 때문이다.
#
# ## 무엇을 보는가 (.guides/web/operations.md §4)
#
#   1. /healthz 가 200 인가          컨테이너가 떴는가
#   2. status 가 ok 인가             degraded 면 안전망이 깨져 있다
#   3. 판이 기대한 것인가            배포가 실제로 갈렸는가
#   4. 자료가 있는가 (행 수 > 0)     빈 DB 를 물고도 200 은 나온다
#   5. nginx 를 거쳐도 사는가        컨테이너만 보면 앞단이 끊긴 것을 못 본다
#   6. /srv 의 스크립트가 저장소와 같은가   **경고만 한다** (아래)
#
# **3번과 4번이 이 스크립트의 값어치다.** 1·2번은 `deploy.sh` 의 기동 게이트가
# 이미 본다. 판이 안 갈렸는데 갈린 줄 아는 것과, 빈 DB 를 물고도 멀쩡해 보이는
# 것 — 둘 다 실제로 형제 프로젝트가 당한 것이고 200 만으로는 안 걸린다.
#
# **degraded 는 여기서 막는다.** `/healthz` 는 degraded 에 200 을 낸다(그래야
# 배포의 기동 게이트가 안 멈춘다). 배포를 세우는 판단은 이쪽 몫이다.
set -uo pipefail        # -e 는 안 쓴다. 검사가 실패해도 나머지를 다 보여준 뒤 끝낸다

SRV="${FORGIA_SRV:-/srv/ForGIA}"
HEALTH="${FORGIA_HEALTH:-http://127.0.0.1:8092/healthz}"
# nginx 를 거치는 길. 사내 VPN 이 80 만 통과시켜 여기가 실제로 사람이 쓰는 주소다.
SITE="${FORGIA_SITE:-http://127.0.0.1/ForGIA/}"
# 그 블록의 server_name. Host 를 안 맞추면 다른 vhost 로 떨어진다.
SMOKE_HOST="${FORGIA_SMOKE_HOST:-172.16.116.98}"
PY="${FORGIA_PY:-$(command -v python3 || true)}"

WANT="${1:-}"
if [ -z "$WANT" ] && [ -f "$SRV/.env" ]; then
    WANT=$(grep -oP '^IMAGE_TAG=\K.*' "$SRV/.env" 2>/dev/null || true)
fi

fails=0
warns=0
ok()   { echo "  ✓ $*"; }
bad()  { echo "  ✗ $*" >&2; fails=$((fails + 1)); }
# **경고는 배포를 안 세운다.** `/healthz` 의 degraded 를 503 으로 만들면 배포가
# 스스로 멈춘다는 것과 같은 이야기다(034) — 뷰어가 성한지와 손으로 돌리는
# 스크립트가 최신인지는 다른 물음이고, 뒤엣것 때문에 판을 못 올리면 안 된다.
warn() { echo "  ! $*"; warns=$((warns + 1)); }

echo "=== smoke $(date '+%F %T') ==="

# 배포가 도는 중이면 502·503 이 정상이다. 그걸 고장으로 읽지 않게 먼저 알린다.
if [ -f "$SRV/maintenance.flag" ]; then
    echo "  ! 유지보수 깃발이 서 있다 — 배포가 도는 중일 수 있다"
fi

# --- 1) /healthz -----------------------------------------------------------
body=$(curl -s --max-time 10 -w '\n%{http_code}' "$HEALTH" 2>/dev/null || true)
code=$(printf '%s' "$body" | tail -n1)
json=$(printf '%s' "$body" | sed '$d')

if [ "$code" = "200" ]; then
    ok "/healthz 200"
else
    bad "/healthz 가 200 이 아니다 (받은 것: ${code:-없음})"
    [ -n "$json" ] && echo "     $json" >&2
    # 여기서 끝낸다 — 아래 검사는 전부 이 응답을 읽는다
    echo "실패 $fails 건. 컨테이너를 본다: docker compose -f $SRV/docker-compose.yml logs --tail 50 web" >&2
    exit 1
fi

# --- 2~4) 응답을 읽는다 ----------------------------------------------------
if [ -z "$PY" ]; then
    bad "python3 을 못 찾았다 — 상태·판·행 수를 확인할 수 없다"
else
    # 한 번에 읽어서 쉘 변수로. jq 를 요구하지 않는다(표준 라이브러리로 충분하다).
    eval "$(printf '%s' "$json" | "$PY" -c '
import json, sys, shlex
d = json.load(sys.stdin)
db = d.get("db") or {}
out = {
    "S_OK": "1",
    "S_STATUS": d.get("status", ""),
    "S_VER": d.get("version", ""),
    "S_SLIDE": db.get("slide", -1),
    "S_REVIEW": db.get("objectreview", -1),
    "S_NOTES": " | ".join(d.get("notes") or []),
}
for k, v in out.items():
    print(f"{k}={shlex.quote(str(v))}")
' 2>/dev/null || echo 'S_OK=0')"
fi

# 읽지 못했으면 여기서 멈춘다. 아래 셋은 전부 이 응답을 읽으므로, 그냥 두면
# **한 가지 원인이 세 줄의 다른 진단으로 번진다** — 실제로 옛 판이 떠 있을 때
# "DB 마운트를 볼 것" 이라고 세 번 말했다. 엉뚱한 곳을 파게 만드는 문구다.
if [ -n "$PY" ] && [ "${S_OK:-0}" != "1" ]; then
    bad "/healthz 가 JSON 이 아니다 — 옛 이미지가 떠 있는가? (그 전에는 평문 ok 였다)"
    echo "     받은 것: ${json:0:120}" >&2
elif [ -n "$PY" ]; then
    case "${S_STATUS:-}" in
        ok)       ok "status=ok" ;;
        degraded) bad "status=degraded — 서비스는 되는데 손상이 감지됐다"
                  [ -n "${S_NOTES:-}" ] && echo "     ${S_NOTES}" >&2 ;;
        *)        bad "status=${S_STATUS:-읽지 못했다}"
                  [ -n "${S_NOTES:-}" ] && echo "     ${S_NOTES}" >&2 ;;
    esac

    if [ -z "$WANT" ]; then
        echo "  ! 기대하는 판을 모른다 (인자도 .env 도 없다) — 뜬 것: ${S_VER:-?}"
    elif [ "${S_VER:-}" = "$WANT" ]; then
        ok "판 $WANT"
    else
        bad "판이 다르다 — 기대 $WANT · 실제 ${S_VER:-없음}"
    fi

    # 도메인 불변식. **빈 DB 를 물어도 /healthz 는 200 을 낼 수 있다** —
    # 마운트가 어긋나 컨테이너가 새 DB 를 만든 경우가 그렇고, 행 수만이 그것을
    # 잡는다 (.guides/web/data-safety.md §8). objectreview 를 고른 이유는 그것이
    # 이 프로젝트에서 **다시 만들 수 없는 유일한 자료**이기 때문이다.
    if [ "${S_SLIDE:-0}" -gt 0 ] && [ "${S_REVIEW:-0}" -gt 0 ]; then
        ok "자료 있음 (슬라이드 ${S_SLIDE} · 교정 ${S_REVIEW})"
    else
        bad "자료가 비었다 (슬라이드 ${S_SLIDE:-?} · 교정 ${S_REVIEW:-?}) — DB 마운트를 볼 것"
    fi
fi

# --- 5) nginx 를 거쳐서도 사는가 · 링크를 만들 수 있는가 -------------------
# 컨테이너만 보면 앞단이 끊긴 것을 못 본다. 사람이 실제로 쓰는 길로 한 번 더.
#
# **여기서 목록을 한 번 그려 보는 것이 값을 한다** (057). 슬러그 하나가 URL 규칙
# (`urls.py` 의 `<slug:slug>`)을 어기면 목록 템플릿이 링크를 만들다 죽어
# **모든 화면이 500** 이 된다. `/healthz` 는 링크를 안 만들어 그때도 `ok` 였다.
#
# 그리고 **200 은 "떴다" 일 뿐이다.** 목록이 멀쩡해도 상세가 죽을 수 있으므로
# 목록에서 링크를 하나 뽑아 따라가 본다 — 링크를 만들 수 있는가와 그 화면이
# 그려지는가는 다른 물음이다.
resp=$(curl -s --max-time 10 -w '\n%{http_code}' \
    -H "Host: $SMOKE_HOST" "$SITE" 2>/dev/null || true)
site_code=$(printf '%s' "$resp" | tail -n1)
site_html=$(printf '%s' "$resp" | sed '$d')
case "$site_code" in
    200)
        ok "nginx 경유 $SITE 200"
        link=$(printf '%s' "$site_html" \
               | grep -oE 'href="[^"]*/d/[^"/]+/"' | head -n1 \
               | sed 's/^href="//; s/"$//')
        if [ -z "$link" ]; then
            # 자료가 있는데 링크가 없으면 목록이 반만 그려진 것이다.
            # 자료 자체가 없는 경우는 4번이 이미 잡았다.
            if [ "${S_SLIDE:-0}" -gt 0 ]; then
                bad "목록이 200 인데 슬라이드 링크가 하나도 없다 (슬라이드 ${S_SLIDE}장)"
            fi
        else
            origin=$(printf '%s' "$SITE" | grep -oE '^https?://[^/]+')
            d_code=$(curl -s -o /dev/null --max-time 10 -w '%{http_code}' \
                -H "Host: $SMOKE_HOST" "$origin$link" 2>/dev/null || true)
            if [ "$d_code" = "200" ]; then
                ok "시야 목록 $link 200"
            else
                bad "시야 목록이 200 이 아니다 (받은 것: ${d_code:-없음}) — $link"
            fi
        fi
        ;;
    503) echo "  ! nginx 가 503 — 유지보수 안내 중이다 (깃발을 볼 것)" ;;
    *)   bad "nginx 경유가 200 이 아니다 (받은 것: ${site_code:-없음}) — $SITE" ;;
esac

# 6) **`/srv` 의 스크립트가 저장소와 어긋났는가** (080).
#
# 배포는 뷰어 이미지를 갈지만 `/srv/ForGIA/scripts` 는 아무도 안 갈아 준다.
# v0.8.0 배포 뒤 옛 `check_db.py` 가 **"is_current 가 둘 이상이다 508건"** 이라는
# 없는 고장을 냈다 — P10 이 그 검사의 뜻을 바꿨는데 사본이 옛 판이었다.
# 그 문구는 "자료가 상했다" 로 읽히므로 다음엔 시간을 버린다.
#
# **경고만 한다.** 스크립트가 낡은 것은 방금 올린 판이 잘못됐다는 뜻이 아니다.
REPO="${FORGIA_REPO:-$HOME/projects/ForGIA}"
SYNC="$REPO/deploy/host/dbsync.sh"
if [ -x "$SYNC" ]; then
    drift=$(FORGIA_REPO="$REPO" "$SYNC" --list 2>/dev/null | grep -c "다르다" || true)
    if [ "${drift:-0}" -gt 0 ]; then
        warn "/srv 의 스크립트 ${drift}개가 저장소와 다르다 — dbsync.sh 로 맞출 것"
        FORGIA_REPO="$REPO" "$SYNC" --list 2>/dev/null | grep "다르다" | sed 's/^/      /'
    else
        ok "/srv 의 스크립트가 저장소와 같다"
    fi
else
    warn "dbsync.sh 를 못 찾았다 ($SYNC) — 스크립트 표류를 확인하지 못했다"
fi

echo
if [ "$fails" -eq 0 ]; then
    if [ "$warns" -gt 0 ]; then
        echo "smoke 통과 (경고 $warns 건 — 위를 볼 것)."
    else
        echo "smoke 통과."
    fi
    exit 0
fi
echo "smoke 실패 — $fails 건." >&2
exit 1
