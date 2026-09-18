#!/usr/bin/env bash
# 새로 들어온 슬라이드의 축소본을 미리 굽는다 (P03 5단계 뒤 · poll_nas.sh 가 부른다).
#
#   warm_thumbs.sh <slug> [<slug>…]
#
# ## 왜 미리 굽는가
#
# `views._thumbnail` 은 `경로|mtime|폭` 으로 캐시하고 **지우지 않는다.** 있으면
# 2ms, 없으면 w=220 이 97ms · w=1600 이 146ms 다(실측 2026-08-18). 검토 화면
# 한 장이 축소본을 열 개 남짓 부르므로, 한 번도 안 연 시야는 **처음 여는 사람이
# 1초를 대신 기다린다.** 그 사람이 곧 검토자다.
#
# ## 폭 목록을 여기 적지 않는다
#
# 화면을 열어 **화면이 부르는 축소본 주소를 그대로 다시 부른다.** 템플릿의
# `{% thumb %}` 이 폭을 바꾸거나 판이 하나 늘면 굽는 것도 따라간다. 목록을 두 벌
# 두면 어긋난 날 **조용히 안 구워지고**, 사람은 그것을 "아직 안 찬 캐시" 로 읽어
# 원인을 못 찾는다 (057 이 같은 줄이다 — 규칙이 두 곳에 있으면 갈린다).
#
# ## 만드는 일은 뷰어가 한다
#
# 호스트에서 PIL 로 직접 구우면 **캐시 열쇠가 두 벌이 되고**(`_thumbnail` 과
# 여기), 파일 소유자도 갈린다(`.thumbcache` 는 컨테이너의 1000:1000 것이다).
# 열쇠는 그 함수 하나에만 있어야 한다.
#
# ## 몇 번 돌려도 같다
#
# 이미 있는 것은 뷰어가 2ms 에 돌려준다. 실측으로 슬라이드 하나(시야 86개 ·
# 축소본 692자리)가 찬 상태에서 25초, 다 구워진 뒤로는 5초다.
set -euo pipefail

HOST="${FORGIA_WARM_HOST:-http://127.0.0.1}"
ROOT="${FORGIA_ROOT_PATH:-/ForGIA}"
# nginx 가 이름으로 가른다 — poll_nas.sh 의 목록 검사와 같은 값을 쓴다.
SMOKE_HOST="${FORGIA_SMOKE_HOST:-172.16.116.98}"
# **뷰어 워커가 셋이다. 하나는 지금 검토 중인 사람에게 남긴다** — 미리 굽는 일이
# 사람을 기다리게 하면 굽는 뜻이 없다.
JOBS="${WARM_JOBS:-2}"

# 본문을 파일로 받고 **HTTP 코드를 돌려준다.** 코드를 안 보면 404·500 이
# 본문 없는 성공으로 읽혀 "구울 것이 없다" 로 조용히 지나간다 — 굽는 일이
# 안 됐는데 됐다고 말하는 자리다.
get() {
    local out
    # `|| echo 000` 를 뒤에 붙이면 curl 이 이미 낸 `000` 뒤에 하나가 더 붙어
    # `000000` 이 된다 — 받아 놓고 비었을 때만 채운다.
    out=$(curl -s -o "$2" -w '%{http_code}' --max-time 60 \
              -H "Host: $SMOKE_HOST" "$HOST$1" 2>/dev/null) || true
    printf '%s' "${out:-000}"
}

# 화면이 부르는 축소본 주소만 뽑는다. `&amp;` 를 되돌리고 **폭이 붙은 것만**
# 고른다 — 폭 없는 `/img?p=` 는 원본이라 캐시할 것이 없다.
urls_of() {
    grep -oE "[^\"']*$ROOT/img\?[^\"']*" "$1" \
        | sed 's/&amp;/\&/g' | grep -- 'w=[0-9]' | sort -u
}

warm_one() {
    local slug=$1 tmp code gids urls n bad=0 g
    tmp=$(mktemp -d); trap 'rm -rf "$tmp"' RETURN

    code=$(get "$ROOT/d/$slug/" "$tmp/list.html")
    if [ "$code" != "200" ]; then
        echo "!! $slug: 목록 화면이 HTTP $code 다 — 축소본을 굽지 못했다" >&2
        return 1
    fi

    # 시야 화면 주소는 목록 화면의 링크가 알려 준다. 여기서도 시야 번호를
    # 따로 세지 않는다 — 세는 자리가 둘이 되면 갈린다.
    gids=$(grep -oE "$ROOT/d/$slug/g/[0-9]+/" "$tmp/list.html" | sort -u)

    urls_of "$tmp/list.html" > "$tmp/urls"          # 목록 화면의 시야 표지
    for g in $gids; do                              # 시야마다의 판들
        code=$(get "$g" "$tmp/page.html")
        if [ "$code" != "200" ]; then
            # **여기서 멈추지 않는다** — 나머지 시야는 구울 수 있다. 다만
            # 조용히 넘기지도 않는다: 굽지 못한 시야가 있다는 것을 세어 말한다.
            echo "!! $slug: $g 가 HTTP $code 다" >&2
            bad=$((bad + 1))
            continue
        fi
        urls_of "$tmp/page.html" >> "$tmp/urls"
    done

    sort -u "$tmp/urls" | grep . > "$tmp/warm" || true
    n=$(grep -c . "$tmp/warm" || true)
    if [ "$n" = "0" ]; then
        echo "$slug: 구울 축소본이 없다 (시야 $(printf '%s\n' "$gids" | grep -c .)개)"
        [ "$bad" = "0" ] || return 1
        return 0
    fi
    xargs -a "$tmp/warm" -P"$JOBS" -I{} \
        curl -s -o /dev/null --max-time 120 -H "Host: $SMOKE_HOST" "$HOST{}"
    echo "$slug: 축소본 $n 자리 · 시야 $(printf '%s\n' "$gids" | grep -c .)개$(
        [ "$bad" = "0" ] || printf ' · 못 연 시야 %s개' "$bad")"
    [ "$bad" = "0" ] || return 1
}

rc=0
for slug in "$@"; do warm_one "$slug" || rc=1; done
exit $rc
