# HANDOFF

**2026-09-18** · 2단계(검출·판정·문턱) 코드 끝 · 시험 배포(8093) 떠 있음. 이어서 할 사람은 여기부터.

## 1. 한 줄 요약

반입 넷(`scan_nas → ingest_nas → group_focus_series → focus_stack`)이 합성 자료로
끝까지 돌고([001](devlog/20260918_001_stage1-ingest-viewer.md)), 그 위에 검출
(`segment_forams` YOLO → `judge` → DB)·문턱 재조정(`refilter`·문턱 화면)·검출 표·
크롭·운영 탭이 있다([002](devlog/20260918_002_stage2-detection.md)). 시험 118개.
시험 배포가 `:8093` 에 떠 있다. **교정·동정은 아직 없다**(3단계). **GPU 는 호스트
드라이버 판 어긋남으로 지금 못 쓴다** — 씨앗 가중치는 CPU 한 에포크짜리뿐.
무엇을 옮길지는 [P01](devlog/20260918_P01_port-from-DiaRUGA.md).

| | 지금 |
|---|---|
| 뷰어 | 시험 배포 `forgia-test-web-1`(`:8093` · `koprifossillab/forgia:v0.1.0-dev`). 운영(8092)은 아직 안 띄웠다 — 판 `v0.1.0` 을 admin 이 정한다 |
| 파이프라인 | 반입 넷 + 검출(`segment_forams` · YOLO 하나)·`judge`·`refilter`·`batch_plan`. 폴러 4b 검출 고리 있음. **파이프라인 이미지는 아직 안 구웠다** |
| DB | 운영 DB 없다 — `ForGIA.db` 는 개발 장비의 사본(합성 사진 슬라이드 하나) · 시험 배포는 그 스냅샷(`/data3/ForGIA/backup`) |
| 자료 | NAS `Forams/Foram_YOLO_microscopy_scaled_v2` — **합성** 자료 660장 (P01 5.1) → `/data3/ForGIA/datasets/synth_v2`. 실사진은 장비가 아직 없다 |
| 가중치 | `/data3/ForGIA/runs/seed-cpu` — CPU 1 epoch(`yolo11m-seg` · 640). **모양 확인용**. GPU 가 돌아오면 다시 굽는다 |
| 단계 | **2 코드 끝 → 3(교정·동정)**. GPU 복구 뒤 씨앗 가중치 재학습·폴러 한 바퀴가 2단계의 남은 확인 |

## 2. 지금 돌아가는 것

### 뷰어 (Django 5.2)

```
데이터셋 목록  /                  [남극 | 전체] 권역 탭 · 표·카드·지도(남극 전체·로스해 확대) 전환 · 숨김 토글
시야 목록      /d/<slug>/         격자 칸마다 타일 — 대표 그림(합성본)·장수·합성 여부
시야 사진      /d/<slug>/g/<n>/   합성본 크게 + 마스크 폴리곤 덮개 + 프레임 줄. 3단계 검토 화면이 이 주소를 물려받는다
검출 표        /d/<slug>/detections/  시야마다 검출·탈락 사유
크롭           /d/<slug>/crops/   개체 크롭 모음 (분류·탈락 필터)
문턱           /thresholds/ · /d/<slug>/thresholds/   미리보기 → 적용 → 이력 (`api/threshold/*`)
지점           /loc/<지역>/<지점>/ 정보 카드(좌표·수심·KPDC) + 깊이순 시료·관찰 표
정보 편집      /d/<slug>/edit/    관찰(분획·분할·칸 수)·시료(깊이·건시료)·지점·지역을 한 폼으로
시스템 설정    /system-settings/          자료 — 지역·지점·시료 만들기·옮기기·지우기 · 소속 없는 관찰 붙이기
               /system-settings/ops/      운영 — 묶음 만들기 · 검토 대상 고르기 · 조리법
               /system-settings/pipeline/ 파이프라인 — 정찰 나이 · 최근 실행 · 밀린 슬라이드
/crop?…                      개체 크롭 (bbox 로 잘라 낸 것)
/img?p=&w=                   축소본 (DATA_ROOT 안만)
/healthz                     판 · 테이블 행 수 · 무결성 깃발 · 백업 나이. 슬라이드 0 이면 unhealthy
```

**주소 모양은 DiaRUGA 와 같다** — 없는 화면(`/review`·`/catalog/`·`/atlas/`)은
그 단계에서 같은 이름으로 더한다.

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

`ops/check_db.py` 는 1(판정 캐시)·2(현재 검출)·4(분류)·5(뼈대)·6(문턱)·7(층) 번 —
3·8~12 는 교정·카탈로그·도감 테이블과 함께 온다.

`base.html` 에 테마(연보라 강조색 · `data-theme` · `localStorage` `forgia.theme`) ·
머리줄 · 워터마크(로고 원본 구성 그대로) · 자리 이름 워터마크(`FORGIA_ENV_LABEL`,
`DEBUG` 면 `Test Server`)가 있다.

### 시험

`python web/manage.py test viewer` — 118개 · 2초. DiaRUGA 시험 여덟을 옮겨 왔고
(`test_topnav`·`test_kpdc` 가 이식의 빈틈을 실제로 잡았다) `test_scale`·
`test_pipeline_ingest`·`test_edit_settings`·`test_judge`·`test_detections` 를 새로 썼다. `test_pipeline_ingest` 는
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
- **GPU 가 지금 안 된다.** 호스트 NVIDIA 드라이버(커널 580.173)와 라이브러리
  (580.178)의 판이 어긋나 GPU 컨테이너가 못 뜬다 — DiaRUGA 폴러도 분마다 실패
  중. admin 의 재부팅이 답이다. 그때까지 검출은 CPU(`--yolo-imgsz 640`)로만
- **씨앗 가중치는 모양 확인용이다.** `runs/seed-cpu` 는 CPU 한 에포크 — 이것으로
  낸 검출 수를 믿지 말 것. GPU 가 돌아오면 P01 5.1 대로 다시 굽는다
- **판정 기본값(63~2000 µm · conf 0.25)은 합성 자료 기준이다** — 실사진으로 다시
  잡는다. `judge.DEFAULTS` 하나만 고치면 `segment_forams`·`ThresholdSet` 이 따라온다
- **분획·분할이 P01 5절 표와 다른 자리에 있다** — 표는 `Sample` 이라 적었는데
  옮기면서 `Slide` 로 갔다(`models.py` 머리말). P01 은 고쳐 두었다
- **그룹핑 임계값 0.55 는 합성 사진에서 안 맞았다** — 다른 시야 둘이 0.61 로 묶였다.
  실사진이 오면 다시 잡는다 (001). 지금 개발 DB 의 슬라이드는 0.8 로 묶은 것
- **DiaRUGA `views.py:741` 에 같은 버그가 있다** — 정보 편집에서 코드로 시료를
  새로 만들어 붙이면 안 붙는다(`attached` 를 저장 전에 계산). ForGIA 는 고쳤다.
  저쪽에 알릴 것 (001)
- **파이프라인 이미지는 아직 안 구웠다.** 폴러 4b 는 코드만 있고 컨테이너로 한
  바퀴 돈 적이 없다 — GPU 가 돌아온 뒤 시험 배포에서 먼저 돌린다

## 4. 어디까지 왔나

| 단계 | 상태 |
|---|---|
| 0 뼈대 | **끝** (2026-09-18) |
| 1 층·반입·뷰어 | **끝** (2026-09-18 · [001](devlog/20260918_001_stage1-ingest-viewer.md)). 시험 배포 떠 있음 |
| 2 검출 | **코드 끝** (2026-09-18 · [002](devlog/20260918_002_stage2-detection.md)). 남은 것: GPU 복구 뒤 씨앗 가중치 재학습 · 파이프라인 이미지 · 폴러 한 바퀴 |
| 3 교정·동정 | 다음. 시야 화면(`/review`) · `ViewpointReview`·`ObjectReview`·`ForamObject` · `Taxon`(WoRMS) · 카탈로그 · `rebind`·`export_review`·`export_yolo` |
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
