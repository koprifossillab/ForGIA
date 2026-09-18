#!/usr/bin/env bash
# from DiaRUGA v0.29.0 deploy/poll_nas.sh — 이름·경로만 바꿨다. **아직 손봐야 한다**
# (P01 §3.1 '손봄'): 검출 단계의 인자(`--backend sam2`·`--scale`·`--min-um`·`--max-um`)가
# 규조 것 그대로다. 2단계(`segment_forams`·새 `judge`)와 함께 고친다. 그 전에는 cron 에 걸지 말 것.
# NAS 를 주기적으로 보고, 복사가 끝난 새 슬라이드를 끝까지 돌린다 (P03 5단계).
#
#   * * * * * /srv/ForGIA/bin/poll_nas.sh
#
# **저장소 경로를 cron 에 적지 않는다** (100). 운영 서버에 저장소가 없을 수
# 있고, 있어도 편집 중인 작업 트리가 매분 도는 것은 "저장소는 만들고 /srv 는
# 돌린다" 를 어긴다. 저장소에서 고치고 `deploy/host/sync_to_srv.sh` 로 민다.
#
# ## 1분마다 돌아도 되는 이유
#
# 할 일이 없으면 컨테이너 하나를 띄워 NAS 를 훑고 끝난다(1~2초). 정찰은 torch 를
# 임포트하지 않으므로 **GPU 를 건드리지 않고 VRAM 은 0 그대로다.**
#
# 한 슬라이드 처리에 한 시간 반이 걸리므로 겹쳐 도는 것이 정상이다 — `flock` 으로
# 뒤 실행이 조용히 물러난다.
#
# ## 아무 일도 없으면 아무것도 적지 않는다
#
# 1분 주기면 하루 1,440번이다. 매번 "새것 없음" 을 적으면 정작 사고가 났을 때
# 그 줄을 못 찾는다. **할 일이 있거나 실패했을 때만 적는다.**
#
# ## 복사가 끝났다고 어떻게 판단하는가
#
# `scan_nas.py` 가 폴더의 (사진 수·XML 수·바이트) 지문을 기억해 두고, `--stable-min`
# 동안 한 번도 안 바뀌었을 때만 "새것" 으로 넘긴다. mtime 만 보면 `rsync -a` 가
# 원본 시각을 보존해서 **한창 들어오는 중인데도 조용해 보인다.**
#
# ## 왜 호스트 cron 인가
#
# 컨테이너에 docker.sock 을 물리는 방식(DooD)은 사실상 root 를 주는 것이라 피했다.
# 트리거만 호스트가 맡고 실제 작업은 전부 컨테이너 안에서 돈다.
set -euo pipefail

DEPLOY_DIR=/srv/ForGIA
# **저장소를 안 본다** (100 · 사용자 방침). 운영 서버에 저장소가 없을 수 있으므로
# 이 스크립트가 쓰는 것은 전부 `/srv/ForGIA` 안에 있다 — 파이프라인·운영
# 스크립트는 `scripts/`, 이 파일 자신은 `bin/` 이다. 저장소에서 고치고
# `deploy/host/sync_to_srv.sh` 로 밀어 넣는다.
SCRIPTS="$DEPLOY_DIR/scripts"
# 깃발을 세울 때만 쓴다. `db_sentinel.py` 는 Django 를 임포트하지 않고 DB 옆에
# 텍스트 파일 한 줄을 놓으므로 호스트에서 도는 것이 규약에 걸리지 않는다
# (`backup_db.py` 가 cron 으로 도는 것과 같은 이유).
HOST_PY="${FORGIA_PY:-$HOME/venv/ForGIA/bin/python}"
LOG_DIR=/data3/ForGIA/logs
LOCK=/tmp/ForGIA-poll.lock
STABLE_MIN="${STABLE_MIN:-5}"   # 이만큼(분) 폴더가 안 변해야 가져온다
PPB="${PPB:-16}"                # points-per-batch — 이 장비(3060 Ti 8 GB) 기준
# **어느 묶음에 넣을지는 DB 가 정한다** (079). 예전에는 여기 이름 하나를 박아
# 두었는데, 묶음이 여럿인 것이 기본이 되면서 그것이 문제가 됐다 — 새 슬라이드가
# 그 묶음에만 들어가고, 사람이 다른 묶음으로 갈아타면 **빈 화면**이 된다.
#
# 이제 `RunBatch.recipe` 가 묶음마다 "어떻게 돌리는가" 를 들고 있고
# `batch_plan.py` 가 **순서대로** 펴 준다: 검토 중인 묶음 먼저, 나머지는 최근
# 것부터. 조리법이 없는 묶음은 안 돈다(끝난 회차를 그대로 둔다).
#
# 되돌릴 길을 남긴다 — `DETECT_BATCH` 를 주면 그 묶음 하나만 돈다.
DETECT_BATCH="${DETECT_BATCH:-}"
# NAS 가 hard 마운트라 내려가면 무한 대기한다. 단계마다 상한을 건다.
T_SCAN=600                      # 10분
T_INGEST=3600                   # 1시간 — 슬라이드 하나가 1.4 GB 다
T_PIPE=21600                    # 6시간 — 74 시야 검출에 1시간 반쯤 걸렸다

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/poll.log"
say() { echo "[$(date '+%F %T')] $*" >>"$LOG"; }

# 겹쳐 돌면 같은 슬라이드를 둘이 검출한다. 물러나는 것이 정상이라 적지 않는다.
exec 9>"$LOCK"
flock -n 9 || exit 0

cd "$DEPLOY_DIR"
# **stdin 을 끊는다** (079). `docker compose run` 은 `-T` 를 줘도 물려받은
# stdin 을 읽어 버려서, `while read` 고리 안에서 부르면 **남은 줄을 먹는다** —
# 묶음이 둘인데 한 바퀴만 돌고 끝났다. 070 에서 YOLO 를 손으로 채울 때 파일
# 11개 중 1개만 처리되던 것과 같은 함정이다.
#
# 파이프라인 명령 중 stdin 을 읽는 것은 하나도 없으므로 여기서 한 번에 끊는다.
# **파이프라인 스크립트도 `/srv/ForGIA/scripts` 의 것이 돈다** (100). 이미지
# 안의 `/app` 사본이 아니다 — 그러면 스크립트 한 줄을 고칠 때마다 7.2 GB 를
# 다시 구워야 한다. `dbtool` 이 이미 쓰던 문을 파이프라인에도 연 것이고,
# Django 코드는 여전히 이미지 것(`FORGIA_APP=/app`)을 쓴다.
run() { timeout "$1" docker compose run --rm -T pipeline \
          python "$SCRIPTS/${2}" "${@:3}" </dev/null; }
# 인자를 그대로 넘기는 갈래 (python 이 아닌 것을 부를 때)
run_raw() { timeout "$1" docker compose run --rm -T pipeline "${@:2}" </dev/null; }
# DB 조회는 **dbtool 문**으로 (9.2절의 그 규약 — check_db.py 와 같은 자리).
# 뷰어 이미지라 torch 가 없고, 스크립트는 /srv/ForGIA/scripts 의 것이 돈다.
rundb() { timeout "$1" docker compose run --rm -T dbtool "${@:2}" </dev/null; }

# 1) 정찰. 조용히 돌리고, 실패하거나 할 일이 있을 때만 남긴다.
SCAN_JSON="$LOG_DIR/last_scan.json"
SCAN_OUT=$(mktemp); trap 'rm -f "$SCAN_OUT"' EXIT
if ! run "$T_SCAN" scan_nas.py --stable-min "$STABLE_MIN" \
        --json "$SCAN_JSON" >"$SCAN_OUT" 2>&1; then
    # **원인을 짐작해서 적지 않는다.** 예전에는 무조건 "NAS 가 내려갔는가" 라고
    # 적었는데, 실제로는 compose 가 없는 이미지 태그를 가리키고 있었다. 엉뚱한
    # 진단이 로그에 4시간 반 동안 524번 쌓였고 그동안 아무도 원인을 몰랐다.
    if grep -qi "manifest unknown\|not found: manifest\|pull access denied" "$SCAN_OUT"; then
        say "정찰 실패 — 파이프라인 이미지를 못 찾는다 (.env 의 PIPELINE_TAG 를 볼 것)"
    elif grep -qi "no such file\|Stale file handle\|Input/output error\|/nfs" "$SCAN_OUT"; then
        say "정찰 실패 — NAS 를 못 읽는다"
    else
        say "정찰 실패"
    fi
    sed 's/^/    /' "$SCAN_OUT" >>"$LOG"
    exit 1
fi

# **실패를 "새것 없음" 으로 접지 않는다** (097 과 같은 무늬였다). 예전에는
# `|| echo 0` 이라 JSON 이 깨지면 반입이 조용히 영영 멈췄다. 순수 JSON 파싱이라
# 호스트 python3 로 충분하다 — 장고도 컨테이너도 안 쓴다.
if ! NEW=$(python3 -c "
import json
d=json.load(open('$SCAN_JSON'))
print(sum(1 for r in d['slides'] if r['state']=='new'))" 2>>"$LOG"); then
    say "정찰 JSON 을 읽지 못했다 ($SCAN_JSON) — 이 주기를 세운다"
    exit 1
fi

# 2) 새것이 있으면 가져온다
if [ "$NEW" -gt 0 ]; then
    say "새 슬라이드 $NEW 개 — 가져온다"
    sed 's/^/    /' "$SCAN_OUT" >>"$LOG"
    if ! run "$T_INGEST" ingest_nas.py --stable-min "$STABLE_MIN" >>"$LOG" 2>&1; then
        say "반입 실패"
        exit 1
    fi
fi

# 3) 밀린 슬라이드를 이어서 돌린다. 앞 실행이 끊겼거나 OOM 으로 죽었을 때
#    다음 주기가 마무리한다. failed 는 건드리지 않는다 — 사람이 봐야 한다.
# **셸 안의 인라인 파이썬이 아니라 스크립트 파일이다** (097 · 사용자 지적).
# 예전 heredoc 은 `run()` 의 `</dev/null` 에 굶겨져 **빈 목록으로 조용히
# 성공**했고 3단계가 사흘을 죽어 있었다. 파일이면 시험이 덮고,
# `dbsync.sh --list` 가 표류를 세고, stdin 에 기대지 않는다.
# 고칠 때는 저장소에서 고치고 `dbsync.sh pending_slides.py` 로 옮긴다.
TODO_OUT=$(mktemp); TODO_ERR=$(mktemp)
trap 'rm -f "$SCAN_OUT" "$TODO_OUT" "$TODO_ERR"' EXIT
# **조회 실패는 소리를 낸다.** 성공한 빈 목록만 "할 일 없음" 이다 — 실패를
# 같은 갈래로 접으면 이번처럼 며칠을 모른다.
if ! rundb "$T_SCAN" pending_slides.py >"$TODO_OUT" 2>"$TODO_ERR"; then
    say "밀린 일 조회 실패 — 3단계를 건너뛰지 않고 멈춘다"
    sed 's/^/    /' "$TODO_ERR" >>"$LOG"
    exit 1
fi
TODO=$(tr -d '\r' <"$TODO_OUT")

[ -n "$TODO" ] || exit 0        # 할 일 없음 — 조용히 끝낸다

say "=== 처리 시작 ==="
echo "$TODO" | while IFS=$'\t' read -r slug state image_dir; do
    [ -n "$slug" ] || continue
    say "--- $slug ($state) ---"

    # 그룹핑 — 시야가 없을 때만. 이미 있으면 재그룹핑이 그 아래를 어긋내므로
    # group_focus_series.py 가 스스로 거부한다.
    if [ "$state" = "pending" ]; then
        if ! run "$T_PIPE" group_focus_series.py \
                "/data3/ForGIA/$image_dir" >>"$LOG" 2>&1; then
            say "$slug: 그룹핑 실패"
            continue
        fi
        # 지점의 메타데이터를 KPDC 에서 긁는다 (197). 그룹핑이 폴더 이름으로
        # 지점을 만든 직후라 좌표·수심·채취일이 비어 있다 — 같은 코어의 공개
        # 항목에서 **빈 칸만** 채운다. 이미 긁은 지점이면 안에서 건너뛴다.
        #
        # **실패해도 폴러를 멈추지 않는다.** 바깥 서버라 사내망 사정에 매이고,
        # 항목이 아직 등록 전일 수도 있다 — 반입이 서는 조건이 아니다. 못 긁은
        # 것은 `dbrun.sh fetch_kpdc.py --missing` 으로 나중에 다시 돈다.
        if ! rundb "$T_SCAN" fetch_kpdc.py --slide "$slug" >>"$LOG" 2>&1; then
            say "$slug: KPDC 메타데이터를 못 긁었다 — fetch_kpdc.py --missing 으로 다시"
        fi
    fi

    # 합성·검출은 이미 끝난 시야를 스스로 건너뛴다
    # (Stack 행 / 현재 Detection 유무를 DB 에 묻는다)
    if ! run "$T_PIPE" focus_stack.py --slide "$slug" >>"$LOG" 2>&1; then
        say "$slug: 합성 실패"
        continue
    fi
    say "$slug: 그룹핑·합성 끝"

    # 4a) 축소본을 미리 굽는다.
    #
    # **검출을 기다리지 않는다.** 화면이 부르는 축소본은 합성본과 프레임에만
    # 달려 있고 그것은 지금 다 있다 — 검출은 그 위에 그리는 것이라 주소를
    # 바꾸지 않는다. 검출 한 바퀴가 슬라이드마다 한 시간 반이라, 여기서 굽지
    # 않으면 그 사이에 화면을 연 사람이 대신 기다린다.
    #
    # **실패해도 폴러를 멈추지 않는다.** 굽는 것은 빠르게 만드는 일이지 자료가
    # 서는 조건이 아니다 — 여기서 멈추면 검출이 통째로 안 돈다.
    if ! "$DEPLOY_DIR/bin/warm_thumbs.sh" "$slug" >>"$LOG" 2>&1; then
        say "$slug: 축소본 미리 굽기 실패 — 화면은 뜬다 (처음 여는 사람이 기다린다)"
    fi
done

# 4b) 검출 — **묶음마다 한 바퀴씩** (079).
#
# 묶음이 바깥 고리인 것이 요점이다. 검토 중인 묶음이 **모든 새 슬라이드에 대해**
# 먼저 채워지고, 그다음 옛 회차가 따라온다 — 사람이 지금 보고 있는 화면이 가장
# 빨리 메워진다. GPU 는 한 번에 하나만 도므로(잠금이 segment_forams 안에 있다)
# 이 순서가 곧 기다리는 순서다.
#
# 조리법은 셸에서 뜯지 않는다 — 파이썬이 인자 한 줄로 만들어 준다.
if [ -n "$DETECT_BATCH" ]; then
    PLAN=$(printf '%s\t--backend sam2 --scale 1.0 --points-per-side 48 --min-um 10 --max-um 150' "$DETECT_BATCH")
    say "DETECT_BATCH 가 주어졌다 — $DETECT_BATCH 하나만 돈다"
else
    PLAN=$(run "$T_SCAN" batch_plan.py --args 2>>"$LOG") || PLAN=""
fi
if [ -z "$PLAN" ]; then
    say "채울 묶음이 없다 — 조리법(recipe)이 적힌 묶음이 없다. batch_plan.py 로 볼 것"
fi

printf '%s\n' "$PLAN" | while IFS=$'\t' read -r batch bargs; do
    [ -n "$batch" ] || continue
    say "=== 묶음 $batch ==="
    echo "$TODO" | while IFS=$'\t' read -r slug state image_dir; do
        [ -n "$slug" ] || continue
        # `--points-per-batch` 는 이 장비의 VRAM 사정이라 조리법이 아니라
        # 여기서 준다. SAM2 만 본다 — YOLO 는 이 인자를 안 받는다.
        extra=""
        case "$bargs" in *"--backend sam2"*) extra="--points-per-batch $PPB";; esac
        # shellcheck disable=SC2086
        if ! run "$T_PIPE" segment_forams.py --slide "$slug" \
                --batch "$batch" $bargs $extra >>"$LOG" 2>&1; then
            say "$slug: 검출 실패 ($batch)"
            continue
        fi
        say "$slug: 검출 끝 ($batch)"
    done
done

# 5) 자료를 바꿨으면 **화면이 그것을 그릴 수 있는지** 본다.
#
# **반입만으로 뷰어가 통째로 500 이 된 적이 있다** (057). 슬러그 하나가 URL 규칙
# (`urls.py` 의 `<slug:slug>`)을 어기자 목록 템플릿이 링크를 만들다 죽었다 —
# 그 슬라이드 한 장이 아니라 **모든 화면**이 안 떴고, 11분 36초 동안 아무도 몰랐다.
#
# `smoke.sh` 가 그 검사를 하고 있었지만 **배포할 때만 돈다.** 이 고장은 배포와
# 무관하게 났다 — 자료가 들어오면서 났다. **자료를 바꾸는 자리가 여기다.**
#
# `/healthz` 로는 안 걸린다. 링크를 안 만드는 경로라 그때도 `ok` 였다.
SITE="${FORGIA_SITE:-http://127.0.0.1/ForGIA/}"
SMOKE_HOST="${FORGIA_SMOKE_HOST:-172.16.116.98}"
page=$(curl -s -o /dev/null --max-time 20 -w '%{http_code}' \
    -H "Host: $SMOKE_HOST" "$SITE" 2>/dev/null || true)
if [ "$page" = "200" ]; then
    say "뷰어 목록 200"
else
    say "!! 뷰어가 목록을 못 낸다 (HTTP ${page:-없음}) — 방금 반입·처리한 것을 볼 것"
    # 로그에만 적으면 읽는 사람이 없는 동안 꺼진 안전망이다. `/healthz` 까지
    # 나른다 — 뷰어의 상태와 `smoke.sh` 가 그 깃발을 본다 (034 · db_sentinel).
    "$HOST_PY" "$SCRIPTS/db_sentinel.py" raise poll_nas \
        "목록 페이지가 ${page:-무응답} 이다 (자동 처리 뒤). 새 슬라이드의 슬러그를 볼 것" \
        >>"$LOG" 2>&1 || say "깃발을 세우지 못했다"
fi

say "=== 처리 끝 ==="
