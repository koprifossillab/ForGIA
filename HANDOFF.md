# HANDOFF

**2026-09-18** · 1단계(층·반입·뷰어) 끝. 이어서 할 사람은 여기부터.

## 1. 한 줄 요약

반입 넷(`scan_nas → ingest_nas → group_focus_series → focus_stack`)이 합성 자료로
끝까지 돌고, 뷰어 화면 일곱(목록·지도·시야 목록·시야 사진·지점·정보 편집·시스템
설정 자료/파이프라인)이 뜨며, 시험 101개가 돈다([001](devlog/20260918_001_stage1-ingest-viewer.md)).
**검출·교정·배포는 아직 없다.** 무엇을 옮길지는 [P01](devlog/20260918_P01_port-from-DiaRUGA.md).

| | 지금 |
|---|---|
| 뷰어 | 판 없음 — 개발 서버로만 뜬다 (`CLAUDE.md` "저장소 소스를 그대로 사내망에 띄운다"). web 이미지는 로컬에서 구워 봤다(`koprifossillab/forgia:dev`) |
| 파이프라인 | 반입 넷이 있다. 검출(`segment_forams`)·`judge`·`refilter` 는 2단계 |
| DB | 운영 DB 없다 — `ForGIA.db` 는 개발 장비의 사본뿐(합성 사진으로 만든 슬라이드 하나) |
| 자료 | NAS `Forams/Foram_YOLO_microscopy_scaled_v2` — **합성** 자료 660장 (P01 5.1). 실사진은 장비가 아직 없다 |
| 단계 | **1 끝 → 2(검출) 시작 전**. 그 사이에 `/srv/ForGIA` 를 만들어 시험 배포를 띄운다 |

## 2. 지금 돌아가는 것

### 뷰어 (Django 5.2)

```
데이터셋 목록  /                  [남극 | 전체] 권역 탭 · 표·카드·지도(남극 전체·로스해 확대) 전환 · 숨김 토글
시야 목록      /d/<slug>/         격자 칸마다 타일 — 대표 그림(합성본)·장수·합성 여부
시야 사진      /d/<slug>/g/<n>/   합성본 크게 + 프레임 줄. **1단계 화면** — 3단계 검토 화면이 이 주소를 물려받는다
지점           /loc/<지역>/<지점>/ 정보 카드(좌표·수심·KPDC) + 깊이순 시료·관찰 표
정보 편집      /d/<slug>/edit/    관찰(분획·분할·칸 수)·시료(깊이·건시료)·지점·지역을 한 폼으로
시스템 설정    /system-settings/          자료 — 지역·지점·시료 만들기·옮기기·지우기 · 소속 없는 관찰 붙이기
               /system-settings/pipeline/ 파이프라인 — 정찰 나이 · 최근 실행 · 밀린 슬라이드
/img?p=&w=                   축소본 (DATA_ROOT 안만)
/healthz                     판 · 테이블 행 수 · 무결성 깃발 · 백업 나이. 슬라이드 0 이면 unhealthy
```

**주소 모양은 DiaRUGA 와 같다** — 없는 화면(`/d/<slug>/crops/`·`/atlas/`·운영 탭)은
그 단계에서 같은 이름으로 더한다.

### 파이프라인 (전부 DB)

```
scan_nas → ingest_nas → group_focus_series → (fetch_kpdc --slide) → focus_stack --slide
```

`deploy/poll_nas.sh` 가 이 흐름을 돌린다 — **검출 고리(4b)는 2단계로 비워 두었다.**
스케일은 `pipeline/scale.py` 가 읽는다(`scale.toml` override → Leica(아직 못 읽음) →
EXIF → `scale.toml` → 사이드카 → 기본값). **NAS 폴더에 `scale.toml` 을 두는 것이
지금의 기본이다.** 합성 자료로 돌린 기록은 [001](devlog/20260918_001_stage1-ingest-viewer.md).

`ops/check_db.py` 는 5번(뼈대)·7번(층·격자 칸·분획) 만 있다 — 나머지 번호는 그
테이블과 함께 온다.

`base.html` 에 테마(연보라 강조색 · `data-theme` · `localStorage` `forgia.theme`) ·
머리줄 · 워터마크(로고 원본 구성 그대로) · 자리 이름 워터마크(`FORGIA_ENV_LABEL`,
`DEBUG` 면 `Test Server`)가 있다.

### 시험

`python web/manage.py test viewer` — 101개 · 1.2초. DiaRUGA 시험 여덟을 옮겨 왔고
(`test_topnav`·`test_kpdc` 가 이식의 빈틈을 실제로 잡았다) `test_scale`·
`test_pipeline_ingest`·`test_edit_settings` 를 새로 썼다. `test_pipeline_ingest` 는
**cv2 가 있어야 돈다**(호스트 venv) — CI 의 web 러너에서는 건너뛴다.
브라우저 겹은 아직 없다 (화면 확인은 playwright 로 손으로 했다).

### 배포 틀 (아직 안 돌렸다)

`deploy/` 는 DiaRUGA v0.29.0 것을 이름·포트만 바꿔 옮겼다 — 8091/9092, `/srv/ForGIA`,
`/data3/ForGIA`. **한 번도 돌려 보지 않았다.** 1단계 끝에 `testdeploy.sh` 로
시험 배포를 먼저 띄워 본다. nginx 조각은 DiaRUGA 의 phyloserver 블록에 `include`
한 줄을 더하는 것이라 sudo 가 필요하다.

CI(`.github/workflows/test.yml`)는 push 마다 시험을 돌리고 `v*` 태그에 Docker Hub
`koprifossillab/forgia` 로 민다 — **Docker Hub 시크릿이 이 저장소에 아직 없다.**

## 3. 지금 조심할 것

- **저장소 이름이 `Forgia` → `ForGIA` 로 바뀌는 중이다** (2026-09-18 결정). GitHub
  에서 바꾸는 것은 admin(`koprifossillab`)이고, 바뀌면 옛 이름은 자동으로
  넘어간다. 로컬 원격은 바뀐 뒤 `git remote set-url` 로 맞춘다
- **원격은 HTTPS 다.** 이 머신의 SSH 키가 `jikhanjung` 계정이라 `wetherilli` 로
  받은 초대에는 안 맞는다 — `gh` 인증(wetherilli)으로 민다
- **GPU 잠금을 DiaRUGA 와 공유해야 한다** (P01 5절) — 2단계에서 `segment_forams`
  를 만들 때 DiaRUGA `segment_diatoms.py` 도 함께 고친다. 저쪽에 따로 알린다
- **분획·분할이 P01 5절 표와 다른 자리에 있다** — 표는 `Sample` 이라 적었는데
  옮기면서 `Slide` 로 갔다(`models.py` 머리말). P01 은 고쳐 두었다
- **그룹핑 임계값 0.55 는 합성 사진에서 안 맞았다** — 다른 시야 둘이 0.61 로 묶였다.
  실사진이 오면 다시 잡는다 (001). 지금 개발 DB 의 슬라이드는 0.8 로 묶은 것
- **DiaRUGA `views.py:741` 에 같은 버그가 있다** — 정보 편집에서 코드로 시료를
  새로 만들어 붙이면 안 붙는다(`attached` 를 저장 전에 계산). ForGIA 는 고쳤다.
  저쪽에 알릴 것 (001)
- **`deploy/` 는 아직 한 번도 안 돌았다.** web 이미지만 로컬에서 구워 `check` 를
  봤다. `/srv/ForGIA` 가 없어(sudo) `testdeploy.sh` 를 못 띄웠다

## 4. 어디까지 왔나

| 단계 | 상태 |
|---|---|
| 0 뼈대 | **끝** (2026-09-18) |
| 1 층·반입·뷰어 | **끝** (2026-09-18 · [001](devlog/20260918_001_stage1-ingest-viewer.md)). 시험 배포만 남았다 |
| 2 검출 | 다음. 합성 자료로 YOLO 씨앗 가중치. `segment_forams` · 새 `judge` · `refilter` · GPU 시스템 잠금 · `Detection`·`Candidate`·`ClassDef`·`ThresholdSet` · 크롭·계측·문턱 화면 · 운영 탭 |
| 3 교정·동정 | 시야 화면 · `Taxon`(WoRMS) · 카탈로그 |
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
