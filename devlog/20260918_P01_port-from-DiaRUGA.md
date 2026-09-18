# [계획] DiaRUGA 에서 무엇을 얼마나 가져오나

**작성일** 2026-09-18
**상태** 확정 — 5절의 물음을 같은 날 다 정했다. **0단계는 같은 날 끝냈다**(HANDOFF)
**읽은 것** `~/projects/DiaRUGA` (v0.29.0, 2026-09-18) — `CLAUDE.md` · `README.md` ·
`HANDOFF.md` 1~2절 · `web/viewer/models.py` · `pipeline/judge.py` · `docs/…_pipeline-rationale.md` ·
`devlog/P01`·`P27`

---

## 0. 한 줄

**ForGIA 는 유공충(foraminifera) 동정 프로그램이다.** DiaRUGA 가 규조 사진을
받아 검출·교정하고 그것으로 검출기를 학습시키듯, ForGIA 는 픽킹 슬라이드 사진을
받아 개체를 검출·교정하고 **그 위에 종 동정 분류기를 얹는다.** 틀은 DiaRUGA 의
뷰어·파이프라인을 그대로 쓰되, 대상이 달라 갈리는 자리가 **다섯** 있다(2절).

전제(2026-09-18 확인):

| | |
|---|---|
| 사진 | **픽킹 슬라이드 전체 촬영** — 한 장에 개체 여럿. 검출 단계가 그대로 필요하다 |
| 대상 | **부유성·저서성 둘 다** — 종 수가 수십~수백. 평면 분류표(`ClassDef`)로는 안 된다 |
| 시료 | 남극 코어 — DiaRUGA 와 같은 지역·지점 |
| 운영 | **같은 틀** — 같은 서버 · NAS 폴링 · Docker 두 컨테이너 · `/srv` 배포. `/ForGIA/` 서브경로로 나란히 |

## 1. 방법 — fork 가 아니라 골라서 옮긴다

DiaRUGA 저장소를 fork 하지 않는다. 642 커밋의 이력이 전부 규조 어휘이고,
`atlas/`·`review/`·`coredata/` 는 자료라 가져올 것이 아니며, `data.py` 5,600줄
안에는 규조에만 있는 갈래(텍스처 관문 · 원형/봉상 · 파편)가 화면마다 박혀 있다.
**파일 단위로 골라 옮기고, 옮길 때 어휘를 바꾼다.**

이름 규칙은 DiaRUGA 의 048 을 그대로 따른다:

| DiaRUGA | ForGIA | 어디 |
|---|---|---|
| `DiaRUGA` | `ForGIA` | 저장소 · `/srv/ForGIA` · `/data3/ForGIA` · `~/venv/ForGIA` · URL `/ForGIA/` · `ForGIA.db` |
| `diaruga` | `forgia` | Docker Hub `koprifossillab/forgia` · 파이썬 패키지 `forgiaweb` · `localStorage` 키 |
| `DIARUGA_*` | `FORGIA_*` | 환경변수 |
| `diatom` (생물 이름 자리) | `foram` | `segment_forams.py` · YOLO 클래스 `foram` · NAS 폴더 |
| 포트 8090 / 9091 | **8092 / 8093** | nginx 뒤 컨테이너 · 시험 컨테이너 (충돌 안 나게) |
| `DiatomObject` | `ForamObject` | 모델 |

**공통 부품을 패키지로 빼지 않는다** — 아직. 두 번째 프로젝트에서 빼면 첫
프로젝트 하나에 맞춘 추상이 된다. 세 번째가 생기면 그때 `naming`·`backup`·
`deploy/host` 부터 뺀다. 그 대신 **옮긴 파일의 머리에 어느 판의 DiaRUGA 에서
왔는지 적는다**(`# from DiaRUGA v0.29.0 web/viewer/naming.py`) — 저쪽이 고친
것을 나중에 따라갈 때 대조할 자리다.

## 2. 규조와 유공충 — 갈리는 자리 다섯

| | 규조 (DiaRUGA) | 유공충 (ForGIA) | 그래서 |
|---|---|---|---|
| **① 크기·광학** | 10~150 µm · 40x 투과광 명시야 · 0.113 µm/px · 스케일은 ZEN XML 에서 | 63 µm~1 mm 이상 · Leica 실체현미경 반사광 · 배율이 몇 배 낮다 | `zen_meta.py` 는 못 쓴다. `scale.py` 가 Leica XML → EXIF → 폴더 `scale.toml` 순으로 본다(5절) |
| **② 투명 vs 불투명** | 투명해서 SAM2 가 조밀한 무리에서 약했다 → YOLO 로 감 · 텍스처(areolae)가 1차 관문 | 불투명 · 검은 바탕 · 픽킹해 놓아 서로 떨어져 있다 | 검출은 쉬운 쪽이다. **`judge.py` 의 텍스처·타원 관문은 뜻이 없다** — 크기 관문 + 분류기 확신도로 바꾼다 |
| **③ 동정 단위** | 형태 둘(원형·봉상) + 속 둘 · `ClassDef` 여섯 줄 · 종명은 자유 문자열(`species`) | **종** 수십~수백 · 부유성/저서성 · 과-속-종 계층 | 분류학은 `ClassDef` 가 아니라 **`Taxon` 테이블**(계층)로. `ClassDef` 는 형태·보존 상태만 남는다 |
| **④ 2단계 분류기** | 없다 — P27 에서 "지금 할 일이 아니다" 로 접었다 | **이것이 ForGIA 의 핵심이다** | `classify_crops.py` 를 새로 만든다. 검토 화면이 확신도·후보 셋을 낸다 |
| **⑤ 최종 산출** | 시야당 개체 수 · 비율 · 종명별 비교(202) | **군집 계수표**(시료 × 종) · 분획(>63/>125/>150 µm) · 분할(split 1/8 등)을 곱해 시료 전체로 환산 | `Slide` 에 `fraction_um`·`split_denom`, `Sample` 에 `dry_weight_g` · 산출표 화면은 `/compare/` 를 키운다 |

**그대로인 것도 많다.** 입체라 초점 시리즈가 여전히 필요하고(`focus_stack`),
층(권역-지역-지점-시료-관찰-시야)은 같고, 교정이 `mask_key` 에 붙는 설계도
그대로이며, `pose`(자세)는 유공충에서 오히려 더 맞는다 — umbilical · spiral ·
edge view 가 동정에 필수라 선택지만 갈아끼운다. `grade`(등급)도 보존 상태
(온전·파손·용해)로 그대로 쓴다.

## 3. 어디를 얼마나 옮기나

**판정 넷** — 그대로 · 손봄 · 새로 · 안 옴. 비율은 "옮긴 뒤 원본과 같은 줄이 얼마나
남나" 의 어림이다.

### 3.1 `deploy/` — **그대로 95%**

| 파일 | 판정 | 비고 |
|---|---|---|
| `docker-compose.yml` · `Dockerfile.base` · `Dockerfile.pipeline` · `entrypoint-web.sh` | 그대로 | 이름·포트만 |
| `host/{deploy,smoke,dbrun,dbsync,sync_to_srv,testdeploy}.sh` | 그대로 | `smoke.sh` 가 세는 판·행 수 이름만 |
| `nginx/*.conf` | 그대로 | `/ForGIA/` 서브경로 하나 더. DiaRUGA 의 80 포트 nginx 에 location 을 **더한다** — 별도 nginx 를 띄우지 않는다 |
| `poll_nas.sh` | 손봄 | 파이프라인 단계 이름과 인자(`--scale`·`--min-um`·`--max-um`) |
| `ca/` | 그대로 | 사내망 TLS |
| `srv/`·`test/` env.template | 그대로 | `FORGIA_*` |
| `warm_thumbs.sh` | 그대로 | |

**같은 서버에 나란히 뜬다.** DiaRUGA 의 `/srv/DiaRUGA` 옆에 `/srv/ForGIA`,
`/data3/DiaRUGA` 옆에 `/data3/ForGIA`. **GPU 잠금은 두 프로젝트가 같은 카드를
쓰므로 `flock` 파일을 공유해야 한다** — DiaRUGA 의 `segment_diatoms` 안 잠금이
프로젝트 안에 있어서 ForGIA 폴러가 그것을 모른다. 시스템 잠금(`/run/lock/paleo-gpu.lock`
같은 것)으로 둘 다 옮기는 것이 맞고, **DiaRUGA 쪽 수정이 하나 생긴다.**

### 3.2 `ops/` — **그대로 80%**

| 파일 | 판정 |
|---|---|
| `backup_db.py` · `db_sentinel.py` · `sync_backup_nas.py` · `export_review.py` | 그대로 |
| `check_db.py` | 손봄 — 검사 항목 중 분류(4번) 가 `Taxon` 을 보게, 층(7번) 그대로 |
| `export_yolo.py` | 그대로 (YOLO 클래스 이름만) |
| `batch_runs.py` · `batch_recipe.py` · `drop_batch.py` · `prune_detections.py` · `pending_slides.py` | 그대로 |
| `fetch_kpdc.py` | 그대로 — 같은 남극 코어면 그대로 값이 있다 |
| `import_atlas.py` · `import_occurrence.py` · `import_taxon_names.py` · `import_coredata.py` | 나중 — 유공충 자료 형식이 정해진 뒤 |
| **새로** `export_crops.py` | 종 라벨 크롭을 분류기 학습 자료로 뽑는다 (`export_yolo` 의 짝) |

### 3.3 `pipeline/` — **그대로 50%**

```
scan_nas → ingest_nas → group_focus_series → focus_stack → segment_forams → classify_crops → refilter
   그대로      그대로          그대로             그대로        손봄(=segment_diatoms)   새로       손봄
                                                                  ↑                       ↑
                                                        judge.py · scale.py (새로)   분류기 가중치 (models/)
```

| 파일 | 판정 | 비고 |
|---|---|---|
| `scan_nas.py` · `ingest_nas.py` · `runlog.py` · `schema_guard.py` · `batch_plan.py` · `batch_scope.py` | 그대로 | |
| `group_focus_series.py` | 그대로 | 사람이 초점을 바꿔 여러 장 찍는 것이 DiaRUGA 와 같다(5절). 묶은 순서가 곧 격자 칸 번호(`Viewpoint.cell`)가 된다 |
| `focus_stack.py` | 그대로 | 알고리즘이 대상을 안 가린다 |
| `segment_diatoms.py` → `segment_forams.py` | 손봄 | 1,404줄 중 YOLO 백엔드·DB 저장·배치는 그대로. **SAM2 백엔드는 아예 안 가져온다** — 첫 가중치를 합성 자료(5.1)로 굽는다. GPU 잠금은 시스템 공유 경로로(5절) |
| `judge.py` | **새로** | 텍스처·타원 관문을 걷는다. 남는 것은 크기 관문(`min_um`·`max_um`)과 분류기 확신도 문턱(`conf_min`) |
| `zen_meta.py` → `scale.py` | **새로** | Leica XML → EXIF → 폴더 `scale.toml` 순으로 스케일을 읽고 출처를 남긴다(5절). Leica 갈래는 실사진이 온 뒤 |
| `refilter.py` | 손봄 | 새 `judge` 의 문턱만 |
| **새로** `classify_crops.py` | | 검출된 개체의 크롭을 분류기에 넣어 **종 확률 상위 N** 을 `Candidate` 에 얹는다. ONNX 런타임(P27 의 miso-onnx 방식) — 학습 프레임워크와 런타임을 가른다 |

### 3.4 `web/viewer/` — **그대로 70%**

**모델(`models.py` 1,694줄)**

| 모델 | 판정 | 비고 |
|---|---|---|
| `RunBatch` · `Run` | 그대로 | `RUN_KIND` 에 `classify` 추가 |
| `Site` · `Locality` · `Sample` · `Slide` | 그대로 + | `Slide.fraction_um`(분획) · `Slide.split_denom`(분할 1/n) · `Slide.cells`(격자 칸 수) · `Sample.dry_weight_g`(건시료 무게) — 전부 NULL 허용. **0단계에서 옮겼다** — 육상 갈래(`Locality.kind`·`Sample.sample_no`)는 안 가져왔다 |
| `Viewpoint` · `Frame` · `Stack` · `Image` | 그대로 + | **시야 = 격자 한 칸.** `Viewpoint.cell`(정수·NULL 허용) — 촬영 순서로 자동, 사람이 고친다. `Image.scale_source` 는 이미 있다 |
| `ThresholdSet` | 손봄 | 새 `judge.FIELDS` |
| `ClassDef` | 손봄 | **형태·보존 상태만** — 온전 · 파손 · 파편 · 기타(비유공충). `is_taxon=True` 줄은 안 만든다 |
| `Setting` | 그대로 | |
| `Detection` · `Candidate` | 그대로 + | `Candidate` 에 분류기 출력(`taxon_top` JSON — 상위 N 과 확률) |
| `ViewpointReview` · `ObjectReview` | 그대로 | 교정이 `mask_key` 에 붙는 설계 그대로. `ObjectReview.cls` 는 형태, 분류학은 개체 쪽 |
| `DiatomObject` → `ForamObject` | 손봄 | `species` 문자열 → `taxon` FK. `POSE` 선택지를 umbilical·spiral·edge·apertural 로. `grade` 그대로 |
| `Atlas` · `AtlasEntry` · `AtlasPlacement` · `Reference` · `Occurrence` | 그대로 | 자료만 새로 |
| `TaxonName` → **`Taxon`** | **새로** | 계층(rank · parent) · 부유성/저서성 · WoRMS AphiaID · 동의어 · **`active`**(자동완성에 낼 것만). WoRMS 유공충 전체를 반입한다(5절). DiaRUGA 의 P24 는 유효성 판정만이라 모자란다 |
| `CoreSeries` · `CorePoint` | 그대로 | 같은 코어 |

**순수 모듈**

| 파일 | 판정 |
|---|---|
| `naming.py` | 그대로 (남극 규칙) + 분획 접미사 토막 하나 (`>125um`, 5절) |
| `images.py` · `regroup.py` · `context.py` · `apps.py` · `urls.py` | 그대로 |
| `shape.py` | 손봄 — 타원 지표 대신 크기·둘레 정도만 |
| `catalog.py` | 그대로 (번호 규칙 같음) |
| `thresholds.py` | 손봄 — 새 문턱 |
| `atlas.py` · `kpdc.py` · `offline.py` | 그대로 — 도감은 사내망 안에서만 (5절) |
| `manage_data.py` | 그대로 + 분획·분할·무게 칸 · 칸 번호 고치기 |
| `antarctica.py` · `ross.py` | 그대로 |
| `korea.py` · `outcrop.py` | **안 옴** — 육상 노두 시료가 생기면 그때. `naming.py` 의 육상 규칙도 같이 빼 둔다 |

**화면(`views.py` 3,120줄 · `data.py` 5,632줄 · 템플릿 15,201줄)** — 화면 단위로

| 화면 | 판정 | 비고 |
|---|---|---|
| 목록 `/` · 시야 목록 `/d/<slug>/` · 지점 `/loc/…` | 그대로 | 권역 탭은 남극 하나 (한국 탭은 코드는 두고 안 켠다) |
| **시야 화면 `/d/<slug>/g/<n>/`** | 손봄 | 교정·마스크·묶기·단축키 그대로. **분류 지정 메뉴가 `ClassDef` 여섯 줄이 아니라 `Taxon` 자동완성**이 된다 — 이것이 가장 큰 화면 수정. 분류기의 상위 N 을 추천으로 낸다 |
| 크롭 갤러리 · 계측 표 | 그대로 | 계측 칼럼만 |
| 카탈로그 `/d/<slug>/catalog/` · 검토 화면의 카탈로그 칸(183) | 손봄 | `species` → `taxon`. 동정하는 자리라 ForGIA 에서 가장 많이 쓸 화면 |
| 문턱 `/thresholds/` | 손봄 | 문턱 목록이 다름 |
| 속성 편집 `/d/<slug>/edit/` | 그대로 + | 분획·분할 |
| 시스템 설정 넷 | 그대로 | 학습 자료 탭에 **분류기 자료**(종별 크롭 수) 한 칸 더 |
| 도감 `/atlas/…` | 그대로 | 자료만 새로 (3.6) |
| 산출 비교 `/compare/` | **키움** | 시료 × 종 계수표 · 분획·분할 환산 · CSV — ForGIA 의 최종 산출물 |
| 오프라인 검토기 `/offline/` | 그대로 · **나중** | 4단계 뒤 |
| `/healthz` · `/img` · `/crop` · `api/*` | 그대로 | |

**시험(`tests/` 14,849줄)** — 옮기는 화면의 시험을 **같이 옮긴다.** 옮겨서 실패하는
것이 곧 "여기가 규조에 묶여 있다" 는 표시라, 시험이 이식의 검사 도구가 된다.
`browser/` 시험도 포함. 규조 전용 시험(`test_fold_object_cls` 같은 것)은 판단해서 뺀다.

### 3.5 `tools/` — **그대로 30%**

| 파일 | 판정 |
|---|---|
| `render_atlas_pages.py` · `parse_atlas.py` · `parse_paper_atlas.py` · `crop_plates.py` · `ocr_pdf.py` | 그대로 — 도감 PDF 를 굽는 틀. `SOURCES` 만 |
| `build_offline_atlas.py` | **안 옴** — 도감은 사내망 안에서만 본다(5절). 오프라인 검토기(`offline.py`)와는 다른 것이다 |
| `build_map.py` · `build_map_ross.py` · `proj.py` | 그대로 |
| `harvest_worms.py` · `worms_db.py` · `triage_worms.py` | 그대로 — WoRMS 에 유공충이 있다 |
| `md2docx.py` · `bench*.py` · `dump_schema.py` · `schema_diff.py` · `review_progress.py` | 그대로 |
| `algaebase*` · `genus_*` · `build_map_kr.py` · `proj_kr.py` · `shrink_outcrop.py` · `parse_dsdp87_captions.py` | 안 옴 |

### 3.6 자료 — **안 옴 (0%)**

`atlas/` · `review/` · `coredata/` · `taxon_names.json` 은 규조 자료다. `coredata/mapping.toml` 은
같은 코어면 다시 쓸 수 있으나 그것도 자료라 필요할 때 복사한다.

유공충 쪽 자료원(후보 — 정하는 것은 사람):

- **분류 체계**: **WoRMS 로 정했다**(5절) — `harvest_worms.py` 가 그대로 돈다. mikrotax 는 화면에서 링크만
- **도감**: 부유성은 Kennett & Srinivasan (1983), 저서성은 Holbourn·Henderson·MacLeod (2013), 총론은 Loeblich & Tappan (1987). 상업 출판이라 **사내망 안에서만 본다 — 오프라인 꾸러미·외부 배포는 안 만든다**(5절)
- **학습 자료**: **Endless Forams 를 쓴다**(Hsiang et al. 2019 — 현생 부유성 35종 · 34,000여 장 · CC BY). 저서성은 공개 자료가 드물다 — 우리 검토에서 나온다. 검출 씨앗은 NAS 의 합성 자료(5.1)

### 3.7 문서 — `CLAUDE.md` 는 가져와서 **손봄 60%**

DiaRUGA `CLAUDE.md` 의 "자주 빠지는 함정" 은 대부분 Django·SQLite·WAL·Docker·
템플릿 함정이라 그대로 유효하다. **번호(063·116 같은 devlog 참조)는 DiaRUGA 의
것이라 `DiaRUGA 063` 처럼 출처를 붙인다.** 말을 고르는 규칙, 커밋 규칙(하루치
브랜치 · 파일 지정 add), 새 작업자 붙이기도 그대로. `HANDOFF.md`·`TODOs.md`·
`CHANGELOG.md` 는 빈 뼈대로 새로 만든다. `docs/`·`devlog/` 는 안 온다.

## 4. 단계

각 단계 끝에 **뷰어가 뜨고 시험이 도는 상태**를 유지한다. 한 번에 다 옮기고
고치기 시작하면 어디가 규조 때문에 깨졌는지 못 가른다.

| 단계 | 무엇 | 끝나면 |
|---|---|---|
| **0 뼈대** | 저장소 구조 · `README`·`CLAUDE`·`LICENSE`(AGPL)·`.env.template` · requirements 넷(SAM2 없이) · `deploy/` 이름 바꿔서 · `forgiaweb` 설정 · 빈 `models.py`(층 넷만) · `naming.py` · `tests/base.py` · CI · **연보라 테마** | 빈 DB 로 뷰어 목록이 뜬다. `manage.py test` 가 0개로 통과 |
| **1 층·반입** | `Site`~`Slide`·`Viewpoint`~`Image` · `scan_nas`·`ingest_nas`·`group_focus_series`·`focus_stack` · 목록·시야 목록·지점·지도·시스템 설정(자료) · `backup_db`·`check_db` · 시험 | NAS 폴더가 시야·합성본까지 간다. 사진이 화면에 뜬다 |
| **2 검출** | `Detection`·`Candidate`·`ThresholdSet`·`ClassDef` · `segment_forams`(YOLO) · 새 `judge`·`scale` · `refilter` · 크롭·계측·문턱 화면 · `RunBatch` 운영 화면 · GPU 시스템 잠금 | 첫 가중치는 합성 자료(5.1)로 굽는다. 실사진이 오면 DiaRUGA P04 의 길: 검토 → `export_yolo` → 재학습 |
| **3 교정·동정** | `ViewpointReview`·`ObjectReview`·`ForamObject`·**`Taxon`** · 시야 화면(교정·묶기·마스크 그리기) · 카탈로그 · `export_review` · 시험 대부분 | 사람이 종을 붙일 수 있다. 여기까지가 "DiaRUGA 와 같은 것" |
| **4 분류기** | `classify_crops` · `export_crops` · `Candidate.taxon_top` · 시야·카탈로그 화면의 추천·확신도 · 학습 자료 탭 | **ForGIA 가 DiaRUGA 와 달라지는 자리.** 첫 가중치는 Endless Forams 로 사전학습한 것 |
| **5 산출·도감·오프라인** | `/compare/` 계수표 · 분획·분할 환산 · CSV · `Atlas*`+`render_atlas_pages` · 오프라인 검토기 | 보고서에 실을 표가 나온다 |

0~1 단계는 옮기는 일이 대부분이라 빠르다. 2단계는 합성 자료로 검출기까지는 가지만
**판정 문턱은 실사진이 있어야** 세운다 — 장비가 아직 없어(5절) 그 부분은 뒤다. 3단계가 옮기는 양이 가장 많다(시야
화면 · `data.py` 의 절반). 4단계는 새로 만드는 것이라 DiaRUGA 를 안 본다.

## 5. 정한 것 (2026-09-18, 같은 날)

처음 적을 때는 물음이었고 **같은 날 하나씩 정했다.** 물음 그대로 두고 답을 붙인다.

| 물음 | 정한 것 | 어디에 걸리나 |
|---|---|---|
| **촬영 장비** | **Leica 실체현미경 + LAS X**, JPG/PNG + 별도 메타 파일. **장비는 아직 없다** — 실사진은 한참 뒤 | ① · `scale.py` |
| **스케일을 어디서 읽나** | **`scale.py` 가 세 곳을 차례로 본다** — ⑴ 사진 옆 Leica XML → ⑵ EXIF `ImageDescription` → ⑶ 슬라이드 폴더의 **`scale.toml`**(사람이 적은 배율). 어디서 읽었는지를 `Image.scale_source` 에 남긴다(DiaRUGA `backfill_scale_source` 가 둔 칸). **⑴은 실제 파일을 본 뒤에 짠다** — 그때까지는 ⑶이 기본이다. 상수를 코드에 박지 않는다 | `pipeline/scale.py` (=`zen_meta` 자리) |
| **z-stack** | **사람이 초점을 바꿔 여러 장 찍는다** — DiaRUGA 와 같다 | `group_focus_series` **그대로** (손봄 → 그대로) |
| **시야의 뜻** | **격자 슬라이드, 한 시야 = 격자 한 칸.** 칸 번호는 **촬영 순서 = 칸 순서**로 자동 매기고 건너뛴 칸은 사람이 화면에서 고친다 | `Viewpoint.cell`(정수, NULL 허용) · 시야 목록에 칸 번호 |
| **NAS 폴더 이름** | DiaRUGA 규칙 + **분획 접미사** — `<촬영일>/<지역>-<지점> <깊이>cm >125um (관찰)`. 분획 토막이 없으면 `fraction_um` 은 빈다 | `naming.py` 에 토막 하나 |
| **시료에 기록할 것** | **분획(`fraction_um`) · 분할(`split_denom`, 1/n 의 n) · 건시료 무게(`dry_weight_g`)** — 셋 다 NULL 허용. 계수표가 개체/g 로 환산한다. **0단계에서 자리를 고쳤다**: 분획·분할은 관찰(`Slide`)의 것이고 무게만 `Sample` 이다 — 같은 시료를 분획 둘로 픽킹하면 슬라이드가 둘이다 | `Slide` · `Sample` · `/compare/` |
| **분류 체계 정본** | **WoRMS** — AphiaID · 계층 · 동의어를 API 로. `harvest_worms.py` 그대로 | `Taxon` 반입 |
| **첫 종 목록** | **WoRMS 의 유공충 전체를 반입하고 쓰는 것만 `active` 로 켠다.** 자동완성은 켠 것만 낸다 — 목록을 사람이 따로 만들지 않는다 | `Taxon.active` · 시스템 설정 |
| **사전학습 자료** | **Endless Forams 를 쓴다**(현생 부유성 35종 · CC BY) — 부유성 분류기의 첫 가중치. 저서성은 우리 검토에서 모은다 | 4단계 |
| **검출 씨앗** | **NAS `Forams/Foram_YOLO_microscopy_scaled_v2` 로 YOLO 를 먼저 굽는다**(3.6). 그래서 **SAM2 는 아예 안 가져온다** — DiaRUGA 가 SAM2 로 시작한 것은 첫 가중치가 없어서였다 | `segment_forams` · `requirements-pipeline` 에서 SAM2 뺌 |
| **도감** | **사내망 안에서만 보는 PDF 도판** — DiaRUGA 틀 그대로. **오프라인 꾸러미·외부 배포는 안 만든다**(상업 출판 도감이라) | 3.6 · `build_offline_atlas` 는 도감에 안 쓴다 |
| **라이선스** | **AGPL-3.0**, DiaRUGA 와 같게. 분류기도 같은 저장소 | `LICENSE` |
| **GPU 잠금** | **시스템 잠금으로 둘 다 옮긴다** — `/run/lock/paleo-gpu.lock` 같은 공유 경로를 `flock`. **DiaRUGA `segment_diatoms.py` 수정 하나가 생긴다** — 저쪽에 따로 알리고 한다 | 3.1 · 2단계 |
| **뷰어 테마** | **연보라.** `base.html` 색 토큰(`--accent` 계열)만 갈고 밝음/어둠 두 벌 다 채운다 — DiaRUGA 201 의 `data-theme` 틀 그대로. 강조색은 로고 원본의 `#c4b5fd` | 0단계 (했다) |
| **이름 표기** | **`ForGIA`** — 로고 워드마크의 대문자 그대로(사용자 2026-09-18). DiaRUGA 규칙대로 대문자 자리가 약자다. 저장소·경로·DB 전부 `ForGIA`, 소문자가 강제되는 자리만 `forgia`. GitHub 저장소 이름은 admin 이 바꾼다 | 1절 |
| **로고** | 사용자 제공 `forgia_logo.svg`(부유성 마크 + 워드마크 + 저서성 사슬 + 밑줄). `docs/assets/logo/` 에 원본, `_logo.html`·`_logo_benthic.html` 로 갈라 심었다 — DiaRUGA `_logo.html` 과 같은 방식(글자는 HTML, 색은 `currentColor`) | 0단계 (했다) |

### 5.1 NAS 에 있는 것 — 합성 자료 (2026-09-17 올라옴)

`/nfs/temp-share/Forams/Foram_YOLO_microscopy_scaled_v2` (210 MB · README_ko.md 있음):

- 3D 렌더한 *N. pachyderma* 개체 **300** 을 다섯 가상 배율(실체 20/40/80× · 광학 40/100×)로
  놓은 **1024×1024 시야 660장**(양성 600 · 빈 시야 60) — 시야당 개체 0~3
- YOLO 분할 라벨(`0: foraminifer`) · 16-bit 마스크 · 스케일바 · 시야마다 JSON(배율·µm/px·스케일바 좌표·개체 크기)
- **실물 장비 사진이 아니다** — 스테이지 마이크로미터로 교정한 것도 아니다. µm/px 는 설계값
- 학습 528 / 검증 66 / 시험 66. **같은 개체가 갈래에 걸쳐 있지 않게 나눠 놓았다** — 다시 섞지 말 것

쓸 자리: **2단계 검출기의 첫 가중치.** 실사진이 오면 그것으로 검토 → `export_yolo` → 재학습이
DiaRUGA P04 와 같은 길이다. 스케일바·EXIF 가 있어 **`scale.py` ⑵ 갈래의 시험 자료**로도 쓴다.
분류기(4단계)에는 못 쓴다 — 종이 하나다.

## 6. 버린 것

- **fork** — 1절
- **공통 패키지로 먼저 빼기** — 1절. 세 번째 프로젝트 때
- **`ClassDef` 를 종 목록으로 늘리기** — 여섯 줄에 맞춰진 UI(단축키 순환 · 색 · CSS 배지)가 수백 종에서 안 선다. 형태와 분류학을 처음부터 가른다
- **SAM2 백엔드** — 불투명 개체에 SAM2 가 나을 이유가 없고 VRAM·의존성만 든다. 씨앗 가중치는 합성 자료로 굽는다
- **mikrotax 를 분류 정본으로** — API 가 없어 긁어야 한다. WoRMS 정본 + mikrotax 링크
- **오프라인 도감 꾸러미** — 상업 출판 도감이라 사내망 밖으로 안 낸다
- **한국·노두 갈래** — 시료가 남극 코어라 코드를 끌고 오지 않는다. 생기면 DiaRUGA 에서 그때 가져온다(063 이 다 적어 놨다)
