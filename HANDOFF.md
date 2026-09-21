# HANDOFF

**2026-09-21** · 3단계 코드 끝 · **운영 `v0.3.0`**(8092 · `/ForGIA/` · 첫 정식 판 · Docker Hub)과 시험(8093) 떠 있음 · **GPU 복구됨(재부팅)** · 백업 cron 들어감. 이어서 할 사람은 여기부터.

## 0. admin 이 할 것 (sudo · 2026-09-18 sclee) — **2026-09-21 기준 넷 다 끝났다.** 정식 판은 `v0.1.0` 이 아니라 **`v0.3.0`** 으로 찍었다(운영이 `v0.3.0-dev` 로 돌던 것과 번호를 맞췄다) → CI → Docker Hub → `deploy.sh v0.3.0` · smoke 7/7. 백업 cron 은 `deploy/host/crontab.ForGIA` 로 넣었다(paleoadmin)

`sclee` 계정으로는 못 하는 넷이다 — 전부 sudo 이거나 `koprifossillab` 의 것.
**위에서부터 차례로 하면 된다.** 셋째까지는 몇 분 일이다.

1. **paleolab 첫 화면에 "Foram Viewer" 카드** — Diatom Viewer 카드 바로 아래, `🐚`,
   `href="/foram/"`. 고친 사본이 이미 있다(`/srv/paleolab/index.html` 은 paleoadmin 644):

   ```bash
   sudo cp /srv/paleolab/index.html /srv/paleolab/index.html.bak-$(date +%y%m%d)
   sudo cp /srv/ForGIA/www/paleolab-index.html /srv/paleolab/index.html
   diff /srv/paleolab/index.html.bak-* /srv/paleolab/index.html    # 카드 하나만 더해졌어야 한다
   ```

2. **`/foram/` 짧은 주소** (Diatom 의 `/diatom/` 과 같은 꼴). 카드가 이것을 쓴다 —
   안 넣으면 카드가 404 다. 스니펫에 `(forgia|foram)` 한 자리만 늘었다:

   ```bash
   sudo cp ~sclee/projects/ForGIA/deploy/nginx/ForGIA-subpath.conf /etc/nginx/snippets/ForGIA-subpath.conf
   sudo cp ~sclee/projects/ForGIA/deploy/nginx/ForGIATest-subpath.conf /etc/nginx/snippets/ForGIATest-subpath.conf   # 시험(8093) 것 — 아직 안 들어갔다
   grep -q ForGIATest-subpath /etc/nginx/sites-available/phyloserver || echo "phyloserver 블록에 'include snippets/ForGIATest-subpath.conf;' 한 줄"
   sudo nginx -t && sudo systemctl reload nginx
   curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://paleolab/foram/     # 301 → /ForGIA/
   ```

3. **`work/20260918-sclee` 를 `main` 에 병합** — 0~3단계 커밋 전부. CI(`.github/workflows/test.yml`)는
   `main` 에서만 돈다. 정식 판은 `v0.1.0` 태그 → CI 가 `koprifossillab/forgia` 로 민다 —
   **Docker Hub 시크릿(`DOCKERHUB_USERNAME`·`DOCKERHUB_TOKEN`)이 저장소에 아직 없다.**
   그 뒤 `/srv/ForGIA/bin/deploy.sh v0.1.0` 으로 지금의 로컬 빌드(`v0.3.0-dev`)를 갈아 끼운다.

4. **호스트 재부팅** — NVIDIA 드라이버(커널 580.173)와 라이브러리(580.178)의 판이 어긋나
   GPU 컨테이너가 안 뜬다. DiaRUGA 폴러도 분마다 실패 중. 이것이 풀려야 씨앗 가중치
   재학습·파이프라인 이미지·폴러 한 바퀴(2단계의 남은 확인)가 된다.

지금 운영 상태: `http://paleolab/ForGIA/` — `forgia-web-1`(`:8092`), 이 머신에서 구운
`v0.3.0-dev`, DB 는 합성 시험 슬라이드 하나(실사진이 오면 지운다). smoke 7/7.

## 1. 한 줄 요약

반입 넷(`scan_nas → ingest_nas → group_focus_series → focus_stack`)이 합성 자료로
끝까지 돌고([001](devlog/20260918_001_stage1-ingest-viewer.md)), 그 위에 검출
(`segment_forams` YOLO → `judge` → DB)·문턱 재조정([002](devlog/20260918_002_stage2-detection.md)),
그 위에 **검토 화면(교정·묶기·손그림·번지기)·개체 카탈로그(동정)·학명 표(`Taxon` ·
WoRMS)** 가 있다([003](devlog/20260918_003_stage3-review-taxon.md)) — DiaRUGA 와
같은 것은 여기까지다. 시험 694개 + 브라우저. 시험 배포가 `:8093` 에 떠 있다.
**분류기(4단계)·계수표·도감(5단계)은 아직 없다.** **GPU 는 2026-09-21 재부팅으로 돌아왔다** — 씨앗 가중치는 아직 CPU 한 에포크짜리뿐이고, 제대로 된 것은 **jikhanserver(RTX 8000)에서 `11m` 으로 굽는다**(꾸러미 NAS `ForGIA/train/synth_v2`). **WoRMS 전체
반입은 스크립트만 있고 안 돌렸다.** 무엇을 옮길지는 [P01](devlog/20260918_P01_port-from-DiaRUGA.md).

| | 지금 |
|---|---|
| 뷰어 | **운영이 떠 있다** — `forgia-web-1`(`:8092` · `koprifossillab/forgia:v0.3.0`, CI 가 Docker Hub 로 민 첫 정식 판 · 2026-09-21) → `http://paleolab/ForGIA/`. 시험 `forgia-test-web-1`(`:8093` · v0.1.0-dev) |
| 파이프라인 | 반입 넷 + 검출(`segment_forams` · YOLO 하나)·`judge`·`refilter`·`batch_plan`. 폴러 4b 검출 고리 있음. **파이프라인 이미지는 아직 안 구웠다** |
| DB | 운영 `/srv/ForGIA/db/ForGIA.db` = 개발 DB 의 사본 — **합성 사진 슬라이드 하나**(`obs_label` "합성 시험자료" · 사진은 `/data3/ForGIA/photos/260918/`). 실사진이 오면 이 슬라이드는 지운다 |
| 자료 | NAS `Forams/Foram_YOLO_microscopy_scaled_v2` — **합성** 자료 660장 (P01 5.1) → `/data3/ForGIA/datasets/synth_v2`. 실사진은 장비가 아직 없다 |
| 가중치 | `/data3/ForGIA/runs/seed-cpu` — CPU 1 epoch(`yolo11m-seg` · 640). **모양 확인용**. GPU 가 돌아오면 다시 굽는다 |
| 학명 | `Taxon` 표 — 개발 DB 에 *Globigerina* 속 516행만(시험). 운영에서는 `migrate/import_worms.py harvest && load` 로 유공충 전체 |
| 단계 | **3 코드 끝 → 4(분류기)**. GPU 복구 뒤 씨앗 가중치 재학습·폴러 한 바퀴가 2단계의 남은 확인 |

## 2. 지금 돌아가는 것

### 뷰어 (Django 5.2)

```
데이터셋 목록  /                  [남극 | 전체] 권역 탭 · 표·카드·지도(남극 전체·로스해 확대) 전환 · 숨김 토글
시야 목록      /d/<slug>/         격자 칸마다 타일 — 대표 그림(합성본)·장수·합성 여부
검토 화면      /d/<slug>/g/<n>/   **DiaRUGA 의 검토 화면 그대로** — 지우기·되살리기·분류 단축키(q/w/e/r)·
                                  마스크 그리기·기하 고치기·같은 개체 묶기(`/link`)·다른 판에 앉히기(`/spread`)·
                                  시야 가르기(`/split`)·검토 완료·개체 카탈로그 칸(종명·등급·자세·코멘트)·
                                  묶음 라디오(`?batch=` 읽기 전용)·짚은 개체(`?obj=&img=`)
개체 카탈로그  /d/<slug>/catalog/ 카드마다 번호(`RS23-GC03-071-g03-…-YS`)·종명(학명 자동완성)·유형·등급·자세·코멘트 · 일괄
검출 표        /d/<slug>/detections/  시야마다 검출·탈락 사유
크롭           /d/<slug>/crops/   개체 크롭 모음 (분류·복구·사람지정·코멘트·지운 것·탈락분)
문턱           /thresholds/ · /d/<slug>/thresholds/   미리보기 → 적용 → 이력 (`api/threshold/*`)
교정 저장      POST /review       그 (이미지, 묶음)의 교정 전체를 갈아치운다 (`{"only": "done"|"note"}` 는 따로)
학명 찾기      /api/taxon/suggest?q=   켠 것 먼저 · WoRMS 반입 전체
지점           /loc/<지역>/<지점>/ 정보 카드(좌표·수심·KPDC) + 깊이순 시료·관찰 표
정보 편집      /d/<slug>/edit/    관찰(분획·분할·칸 수)·시료(깊이·건시료)·지점·지역을 한 폼으로
시스템 설정    /system-settings/          자료 — 지역·지점·시료 만들기·옮기기·지우기 · 소속 없는 관찰 붙이기
               /system-settings/ops/      운영 — 묶음 만들기 · 검토 대상 고르기 · 조리법 · **카탈로그 코드**
               /system-settings/taxa/     학명 — 켠 학명 목록 · 이름으로 켜기 · 생활형(부유성/저서성) 물려주기 · 반입 상태
               /system-settings/pipeline/ 파이프라인 — 정찰 나이 · 최근 실행 · 밀린 슬라이드
/crop?…                      개체 크롭 (bbox 로 잘라 낸 것)
/img?p=&w=                   축소본 (DATA_ROOT 안만)
/healthz                     판 · 테이블 행 수 · 무결성 깃발 · 백업 나이. 슬라이드 0 이면 unhealthy
```

**주소 모양은 DiaRUGA 와 같다** — 없는 화면(`/compare/`·`/atlas/`·오프라인)은
그 단계에서 같은 이름으로 더한다. `/api/taxon/suggest`·`/system-settings/taxa/` 는 ForGIA 의 것.

### 파이프라인 (전부 DB)

```
scan_nas → ingest_nas → group_focus_series → (fetch_kpdc --slide) → focus_stack --slide
        → [묶음마다] segment_forams --slide --batch <조리법 인자>      ← batch_plan.py --args 가 준다
```

`deploy/poll_nas.sh` 가 이 흐름을 돌린다. 판정 규칙은 `pipeline/judge.py` 하나
(장축 µm 범위 · 확신도) — `segment_forams`·`refilter`·뷰어 문턱 화면·`check_db` 1번이
같은 함수를 부른다. GPU 잠금은 DiaRUGA 의 `/data3/DiaRUGA/locks/gpu.lock` 을 그대로
가리킨다(`FORGIA_GPU_LOCK`).
스케일은 `pipeline/scale.py` 가 읽는다(`scale.toml` override → Leica(아직 못 읽음) →
EXIF → `scale.toml` → 사이드카 → 기본값). **NAS 폴더에 `scale.toml` 을 두는 것이
지금의 기본이다.** 합성 자료로 돌린 기록은 [001](devlog/20260918_001_stage1-ingest-viewer.md).

`ops/check_db.py` 는 1(판정 캐시)·2(현재 검출·완료 줄)·3(교정)·4(분류)·5(뼈대)·
6(문턱)·7(층)·8(묶음)·9(카탈로그 코드·번호)·10(등급·자세) — 11·12(도감·출현)는 5단계.
`ops/export_review.py` 가 교정을 `review/<슬라이드>/g<n>.json` 으로 내보낸다
(`--check` 로 대조). `pipeline/rebind.py` 가 재검출 뒤 교정을 새 후보에 다시 맺는다.

`base.html` 에 테마(연보라 강조색 · `data-theme` · `localStorage` `forgia.theme`) ·
머리줄 · 워터마크(로고 원본 구성 그대로) · 자리 이름 워터마크(`FORGIA_ENV_LABEL`,
`DEBUG` 면 `Test Server`)가 있다.

### 시험

`python web/manage.py test viewer --exclude-tag browser` — 694개 · 15초.
`python web/manage.py test viewer` 는 브라우저(playwright · `tests/browser/`) 포함.
3단계에서 DiaRUGA 시험 38 + 브라우저 31 모듈을 이름 바꾸기로 옮겨 왔다(003 에 규칙).
옮겨 온 시험이 `Taxon.objects` 가림·배지 색·묶기 저장 칸을 실제로 잡았다. `test_pipeline_ingest` 는
**cv2 가 있어야 돈다**(호스트 venv) — CI 의 web 러너에서는 건너뛴다.
브라우저 겹은 아직 없다 (화면 확인은 playwright 로 손으로 했다).

### 배포 틀

`deploy/` 는 DiaRUGA v0.29.0 것을 이름·포트만 바꿔 옮겼다 — 8092/8093, `/srv/ForGIA`,
`/data3/ForGIA`. **시험 배포(`testdeploy.sh`)는 돌아간다** — `forgia-test-web-1`
이 `:8093` 에 떠 있고 `smoke.sh` 5/6(nginx 시험 조각만 sudo 라 못 넣었다). 운영
(`deploy.sh`)·파이프라인 이미지는 아직이다.

CI(`.github/workflows/test.yml`)는 push 마다 시험을 돌리고 `v*` 태그에 Docker Hub
`koprifossillab/forgia` 로 민다 — **Docker Hub 시크릿이 이 저장소에 아직 없다.**

## 3. 지금 조심할 것

- **저장소 이름이 `Forgia` → `ForGIA` 로 바뀌는 중이다** (2026-09-18 결정). GitHub
  에서 바꾸는 것은 admin(`koprifossillab`)이고, 바뀌면 옛 이름은 자동으로
  넘어간다. 로컬 원격은 바뀐 뒤 `git remote set-url` 로 맞춘다
- **원격은 HTTPS 다.** 이 머신의 SSH 키가 `jikhanjung` 계정이라 `wetherilli` 로
  받은 초대에는 안 맞는다 — `gh` 인증(wetherilli)으로 민다
- **GPU 는 2026-09-21 재부팅으로 돌아왔다** (580.178.04 · 컨테이너에서 3060 Ti 8 GB 잡힘).
  다시 `nvidia-smi` 가 "Driver/library version mismatch" 를 내면 `apt upgrade` 가
  드라이버를 올린 것이다 — 재부팅뿐. 학습은 여기서 안 한다(jikhanserver RTX 8000)
- **씨앗 가중치는 모양 확인용이다.** `runs/seed-cpu` 는 CPU 한 에포크 — 이것으로
  낸 검출 수를 믿지 말 것. GPU 가 돌아오면 P01 5.1 대로 다시 굽는다
- **판정 기본값(63~2000 µm · conf 0.25)은 합성 자료 기준이다** — 실사진으로 다시
  잡는다. `judge.DEFAULTS` 하나만 고치면 `segment_forams`·`ThresholdSet` 이 따라온다
- **종명은 `Taxon` 표에 있는 이름만 받는다** (`Taxon.resolve`). 반입 전에는 카탈로그에
  아무 종도 못 적는다 — 시험 배포에서 `import_worms.py` 를 먼저 돌릴 것. 개발 DB 는
  *Globigerina* 속만 들어 있다
- **자세 넷(umbilical·spiral·edge·apertural)과 등급 A/B/C 는 이름만 바꿨다** — 매기는
  규칙은 사람과 정한다 (003)
- **묶음의 카탈로그 코드가 비면 그 묶음의 개체는 번호가 없다.** 운영 탭의 코드 칸 ·
  `check_db` 9번. `yolo-seed` 는 `YS` 로 적어 두었다
- **분획·분할이 P01 5절 표와 다른 자리에 있다** — 표는 `Sample` 이라 적었는데
  옮기면서 `Slide` 로 갔다(`models.py` 머리말). P01 은 고쳐 두었다
- **그룹핑 임계값 0.55 는 합성 사진에서 안 맞았다** — 다른 시야 둘이 0.61 로 묶였다.
  실사진이 오면 다시 잡는다 (001). 지금 개발 DB 의 슬라이드는 0.8 로 묶은 것
- **DiaRUGA `views.py:741` 에 같은 버그가 있다** — 정보 편집에서 코드로 시료를
  새로 만들어 붙이면 안 붙는다(`attached` 를 저장 전에 계산). ForGIA 는 고쳤다.
  저쪽에 알릴 것 (001)
- **파이프라인 이미지는 아직 안 구웠다.** 폴러 4b 는 코드만 있고 컨테이너로 한
  바퀴 돈 적이 없다 — GPU 가 돌아온 뒤 시험 배포에서 먼저 돌린다. `/srv/ForGIA/.env`
  의 `PIPELINE_TAG=unbuilt` 는 **자리만** 채운 것이다(비면 compose 가 web 까지
  못 띄운다) — 이미지를 구우면 그 판으로 고친다
- **paleolab 첫 화면의 "Foram Viewer" 카드는 admin 이 넣는다** (`/srv/paleolab/index.html`
  은 paleoadmin 소유 644). 고친 사본이 `/srv/ForGIA/www/paleolab-index.html` 에 있고,
  카드가 쓰는 `/foram/` 은 `deploy/nginx/ForGIA-subpath.conf` 에 넣었다 — 설치된
  스니펫(`/etc/nginx/snippets/`)에도 옮겨야 산다(sudo)

## 4. 어디까지 왔나

| 단계 | 상태 |
|---|---|
| 0 뼈대 | **끝** (2026-09-18) |
| 1 층·반입·뷰어 | **끝** (2026-09-18 · [001](devlog/20260918_001_stage1-ingest-viewer.md)). 시험 배포 떠 있음 |
| 2 검출 | **코드 끝** (2026-09-18 · [002](devlog/20260918_002_stage2-detection.md)). 남은 것: GPU 복구 뒤 씨앗 가중치 재학습 · 파이프라인 이미지 · 폴러 한 바퀴 |
| 3 교정·동정 | **코드 끝** (2026-09-18 · [003](devlog/20260918_003_stage3-review-taxon.md)). 남은 것: WoRMS 전체 반입 · 자세·등급 기준 |
| 4 분류기 | `classify_crops` · Endless Forams 사전학습 |
| 5 산출·도감·오프라인 | 계수표 · 도감(사내망) · 오프라인 검토기 |

## 5. 주요 파일

`CLAUDE.md` 의 "구조" 절.

## 6. 자주 빠지는 함정 → `CLAUDE.md`

## 7. 이 머신

DiaRUGA 와 같은 `paleo-server`(172.16.116.98). venv `~/venv/ForGIA`(Python 3.12.3 ·
Django·pillow·gunicorn·playwright). 개발 서버 포트 대역 `8051~8059` 를 DiaRUGA 와
나눠 쓴다. NAS 는 `/nfs/temp-share` 에 있고 `Forams/` 폴더가 거기 있다.

## 8. 다음에 할 일 → `TODOs.md`
