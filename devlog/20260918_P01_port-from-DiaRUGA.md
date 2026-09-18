# [계획] DiaRUGA 에서 무엇을 얼마나 가져오나

**작성일** 2026-09-18
**상태** 초안 — 사람이 보고 고칠 것. 코드는 아직 한 줄도 없다
**읽은 것** `~/projects/DiaRUGA` (v0.29.0, 2026-09-18) — `CLAUDE.md` · `README.md` ·
`HANDOFF.md` 1~2절 · `web/viewer/models.py` · `pipeline/judge.py` · `docs/…_pipeline-rationale.md` ·
`devlog/P01`·`P27`

---

## 0. 한 줄

**Forgia 는 유공충(foraminifera) 동정 프로그램이다.** DiaRUGA 가 규조 사진을
받아 검출·교정하고 그것으로 검출기를 학습시키듯, Forgia 는 픽킹 슬라이드 사진을
받아 개체를 검출·교정하고 **그 위에 종 동정 분류기를 얹는다.** 틀은 DiaRUGA 의
뷰어·파이프라인을 그대로 쓰되, 대상이 달라 갈리는 자리가 **다섯** 있다(2절).

전제(2026-09-18 확인):

| | |
|---|---|
| 사진 | **픽킹 슬라이드 전체 촬영** — 한 장에 개체 여럿. 검출 단계가 그대로 필요하다 |
| 대상 | **부유성·저서성 둘 다** — 종 수가 수십~수백. 평면 분류표(`ClassDef`)로는 안 된다 |
| 시료 | 남극 코어(DiaRUGA 와 같은 시료로 시작한다고 본다 — 5절에서 묻는다) |
| 운영 | **같은 틀** — 같은 서버 · NAS 폴링 · Docker 두 컨테이너 · `/srv` 배포. `/Forgia/` 서브경로로 나란히 |

## 1. 방법 — fork 가 아니라 골라서 옮긴다

DiaRUGA 저장소를 fork 하지 않는다. 642 커밋의 이력이 전부 규조 어휘이고,
`atlas/`·`review/`·`coredata/` 는 자료라 가져올 것이 아니며, `data.py` 5,600줄
안에는 규조에만 있는 갈래(텍스처 관문 · 원형/봉상 · 파편)가 화면마다 박혀 있다.
**파일 단위로 골라 옮기고, 옮길 때 어휘를 바꾼다.**

이름 규칙은 DiaRUGA 의 048 을 그대로 따른다:

| DiaRUGA | Forgia | 어디 |
|---|---|---|
| `DiaRUGA` | `Forgia` | 저장소 · `/srv/Forgia` · `/data3/Forgia` · `~/venv/Forgia` · URL `/Forgia/` · `Forgia.db` |
| `diaruga` | `forgia` | Docker Hub `koprifossillab/forgia` · 파이썬 패키지 `forgiaweb` · `localStorage` 키 |
| `DIARUGA_*` | `FORGIA_*` | 환경변수 |
| `diatom` (생물 이름 자리) | `foram` | `segment_forams.py` · YOLO 클래스 `foram` · NAS 폴더 |
| 포트 8090 / 9091 | **8091 / 9092** | nginx 뒤 컨테이너 · 시험 컨테이너 (충돌 안 나게) |
| `DiatomObject` | `ForamObject` | 모델 |

**공통 부품을 패키지로 빼지 않는다** — 아직. 두 번째 프로젝트에서 빼면 첫
프로젝트 하나에 맞춘 추상이 된다. 세 번째가 생기면 그때 `naming`·`backup`·
`deploy/host` 부터 뺀다. 그 대신 **옮긴 파일의 머리에 어느 판의 DiaRUGA 에서
왔는지 적는다**(`# from DiaRUGA v0.29.0 web/viewer/naming.py`) — 저쪽이 고친
것을 나중에 따라갈 때 대조할 자리다.

## 2. 규조와 유공충 — 갈리는 자리 다섯

| | 규조 (DiaRUGA) | 유공충 (Forgia) | 그래서 |
|---|---|---|---|
| **① 크기·광학** | 10~150 µm · 40x 투과광 명시야 · 0.113 µm/px · 스케일은 ZEN XML 에서 | 63 µm~1 mm 이상 · 실체현미경 반사광(추정) · 배율이 몇 배 낮다 | `zen_meta.py` 는 못 쓴다. **스케일을 어디서 읽는지가 첫 질문**(5절) |
| **② 투명 vs 불투명** | 투명해서 SAM2 가 조밀한 무리에서 약했다 → YOLO 로 감 · 텍스처(areolae)가 1차 관문 | 불투명 · 검은 바탕 · 픽킹해 놓아 서로 떨어져 있다 | 검출은 쉬운 쪽이다. **`judge.py` 의 텍스처·타원 관문은 뜻이 없다** — 크기 관문 + 분류기 확신도로 바꾼다 |
| **③ 동정 단위** | 형태 둘(원형·봉상) + 속 둘 · `ClassDef` 여섯 줄 · 종명은 자유 문자열(`species`) | **종** 수십~수백 · 부유성/저서성 · 과-속-종 계층 | 분류학은 `ClassDef` 가 아니라 **`Taxon` 테이블**(계층)로. `ClassDef` 는 형태·보존 상태만 남는다 |
| **④ 2단계 분류기** | 없다 — P27 에서 "지금 할 일이 아니다" 로 접었다 | **이것이 Forgia 의 핵심이다** | `classify_crops.py` 를 새로 만든다. 검토 화면이 확신도·후보 셋을 낸다 |
| **⑤ 최종 산출** | 시야당 개체 수 · 비율 · 종명별 비교(202) | **군집 계수표**(시료 × 종) · 분획(>63/>125/>150 µm) · 분할(split 1/8 등)을 곱해 시료 전체로 환산 | `Sample` 에 `fraction_um`·`split` · 산출표 화면은 `/compare/` 를 키운다 |

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
| `nginx/*.conf` | 그대로 | `/Forgia/` 서브경로 하나 더. DiaRUGA 의 80 포트 nginx 에 location 을 **더한다** — 별도 nginx 를 띄우지 않는다 |
| `poll_nas.sh` | 손봄 | 파이프라인 단계 이름과 인자(`--scale`·`--min-um`·`--max-um`) |
| `ca/` | 그대로 | 사내망 TLS |
| `srv/`·`test/` env.template | 그대로 | `FORGIA_*` |
| `warm_thumbs.sh` | 그대로 | |

**같은 서버에 나란히 뜬다.** DiaRUGA 의 `/srv/DiaRUGA` 옆에 `/srv/Forgia`,
`/data3/DiaRUGA` 옆에 `/data3/Forgia`. **GPU 잠금은 두 프로젝트가 같은 카드를
쓰므로 `flock` 파일을 공유해야 한다** — DiaRUGA 의 `segment_diatoms` 안 잠금이
프로젝트 안에 있어서 Forgia 폴러가 그것을 모른다. 시스템 잠금(`/tmp/gpu.lock`
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
   그대로      그대로          손봄               그대로        손봄(=segment_diatoms)   새로       손봄
                                                                  ↑                       ↑
                                                             judge.py (새로)      분류기 가중치 (models/)
```

| 파일 | 판정 | 비고 |
|---|---|---|
| `scan_nas.py` · `ingest_nas.py` · `runlog.py` · `schema_guard.py` · `batch_plan.py` · `batch_scope.py` | 그대로 | |
| `group_focus_series.py` | 손봄 | 초점 시리즈를 이미지 내용으로 묶는 것은 같다. 다만 실체현미경이 z-stack 을 파일 이름·메타로 남기면 그것을 먼저 쓴다 — **촬영 방식을 보고 정한다**(5절) |
| `focus_stack.py` | 그대로 | 알고리즘이 대상을 안 가린다 |
| `segment_diatoms.py` → `segment_forams.py` | 손봄 | 1,404줄 중 YOLO 백엔드·DB 저장·잠금·배치는 그대로. **SAM2 백엔드는 뺀다**(불투명 개체는 YOLO 가 첫 판부터 낫다 — 첫 가중치가 없을 때만 SAM2 나 단순 임계로 씨앗 후보를 만든다) |
| `judge.py` | **새로** | 텍스처·타원 관문을 걷는다. 남는 것은 크기 관문(`min_um`·`max_um`)과 분류기 확신도 문턱(`conf_min`) |
| `zen_meta.py` | **새로** | 촬영 장비에 맞춰 스케일·메타를 읽는다. 이름도 장비를 따라 |
| `refilter.py` | 손봄 | 새 `judge` 의 문턱만 |
| **새로** `classify_crops.py` | | 검출된 개체의 크롭을 분류기에 넣어 **종 확률 상위 N** 을 `Candidate` 에 얹는다. ONNX 런타임(P27 의 miso-onnx 방식) — 학습 프레임워크와 런타임을 가른다 |

### 3.4 `web/viewer/` — **그대로 70%**

**모델(`models.py` 1,694줄)**

| 모델 | 판정 | 비고 |
|---|---|---|
| `RunBatch` · `Run` | 그대로 | `RUN_KIND` 에 `classify` 추가 |
| `Site` · `Locality` · `Sample` · `Slide` | 그대로 + | `Sample.fraction_um`(분획) · `Sample.split`(분할 비율) · `Slide` 에 픽킹 슬라이드 격자 정보(칸 수) |
| `Viewpoint` · `Frame` · `Stack` · `Image` | 그대로 | 시야 = 촬영 한 자리. 격자 칸 좌표(`cell_row`·`cell_col`)를 `Viewpoint` 에 둘지는 촬영 방식을 보고 |
| `ThresholdSet` | 손봄 | 새 `judge.FIELDS` |
| `ClassDef` | 손봄 | **형태·보존 상태만** — 온전 · 파손 · 파편 · 기타(비유공충). `is_taxon=True` 줄은 안 만든다 |
| `Setting` | 그대로 | |
| `Detection` · `Candidate` | 그대로 + | `Candidate` 에 분류기 출력(`taxon_top` JSON — 상위 N 과 확률) |
| `ViewpointReview` · `ObjectReview` | 그대로 | 교정이 `mask_key` 에 붙는 설계 그대로. `ObjectReview.cls` 는 형태, 분류학은 개체 쪽 |
| `DiatomObject` → `ForamObject` | 손봄 | `species` 문자열 → `taxon` FK. `POSE` 선택지를 umbilical·spiral·edge·apertural 로. `grade` 그대로 |
| `Atlas` · `AtlasEntry` · `AtlasPlacement` · `Reference` · `Occurrence` | 그대로 | 자료만 새로 |
| `TaxonName` → **`Taxon`** | **새로** | 계층(rank · parent) · 부유성/저서성 · WoRMS AphiaID · 동의어. DiaRUGA 의 P24 는 유효성 판정만이라 모자란다 |
| `CoreSeries` · `CorePoint` | 그대로 | 같은 코어 |

**순수 모듈**

| 파일 | 판정 |
|---|---|
| `naming.py` | 그대로 (남극 규칙) + 분획 접미사 규칙 하나 — **NAS 폴더 이름을 보고**(5절) |
| `images.py` · `regroup.py` · `context.py` · `apps.py` · `urls.py` | 그대로 |
| `shape.py` | 손봄 — 타원 지표 대신 크기·둘레 정도만 |
| `catalog.py` | 그대로 (번호 규칙 같음) |
| `thresholds.py` | 손봄 — 새 문턱 |
| `atlas.py` · `kpdc.py` · `offline.py` | 그대로 |
| `manage_data.py` | 그대로 + 분획·분할 칸 |
| `antarctica.py` · `ross.py` | 그대로 |
| `korea.py` · `outcrop.py` | **안 옴** — 육상 노두 시료가 생기면 그때. `naming.py` 의 육상 규칙도 같이 빼 둔다 |

**화면(`views.py` 3,120줄 · `data.py` 5,632줄 · 템플릿 15,201줄)** — 화면 단위로

| 화면 | 판정 | 비고 |
|---|---|---|
| 목록 `/` · 시야 목록 `/d/<slug>/` · 지점 `/loc/…` | 그대로 | 권역 탭은 남극 하나 (한국 탭은 코드는 두고 안 켠다) |
| **시야 화면 `/d/<slug>/g/<n>/`** | 손봄 | 교정·마스크·묶기·단축키 그대로. **분류 지정 메뉴가 `ClassDef` 여섯 줄이 아니라 `Taxon` 자동완성**이 된다 — 이것이 가장 큰 화면 수정. 분류기의 상위 N 을 추천으로 낸다 |
| 크롭 갤러리 · 계측 표 | 그대로 | 계측 칼럼만 |
| 카탈로그 `/d/<slug>/catalog/` · 검토 화면의 카탈로그 칸(183) | 손봄 | `species` → `taxon`. 동정하는 자리라 Forgia 에서 가장 많이 쓸 화면 |
| 문턱 `/thresholds/` | 손봄 | 문턱 목록이 다름 |
| 속성 편집 `/d/<slug>/edit/` | 그대로 + | 분획·분할 |
| 시스템 설정 넷 | 그대로 | 학습 자료 탭에 **분류기 자료**(종별 크롭 수) 한 칸 더 |
| 도감 `/atlas/…` | 그대로 | 자료만 새로 (3.6) |
| 산출 비교 `/compare/` | **키움** | 시료 × 종 계수표 · 분획·분할 환산 · CSV — Forgia 의 최종 산출물 |
| 오프라인 검토기 `/offline/` | 그대로 · **나중** | 4단계 뒤 |
| `/healthz` · `/img` · `/crop` · `api/*` | 그대로 | |

**시험(`tests/` 14,849줄)** — 옮기는 화면의 시험을 **같이 옮긴다.** 옮겨서 실패하는
것이 곧 "여기가 규조에 묶여 있다" 는 표시라, 시험이 이식의 검사 도구가 된다.
`browser/` 시험도 포함. 규조 전용 시험(`test_fold_object_cls` 같은 것)은 판단해서 뺀다.

### 3.5 `tools/` — **그대로 30%**

| 파일 | 판정 |
|---|---|
| `render_atlas_pages.py` · `parse_atlas.py` · `parse_paper_atlas.py` · `build_offline_atlas.py` · `crop_plates.py` · `ocr_pdf.py` | 그대로 — 도감 PDF 를 굽는 틀. `SOURCES` 만 |
| `build_map.py` · `build_map_ross.py` · `proj.py` | 그대로 |
| `harvest_worms.py` · `worms_db.py` · `triage_worms.py` | 그대로 — WoRMS 에 유공충이 있다 |
| `md2docx.py` · `bench*.py` · `dump_schema.py` · `schema_diff.py` · `review_progress.py` | 그대로 |
| `algaebase*` · `genus_*` · `build_map_kr.py` · `proj_kr.py` · `shrink_outcrop.py` · `parse_dsdp87_captions.py` | 안 옴 |

### 3.6 자료 — **안 옴 (0%)**

`atlas/` · `review/` · `coredata/` · `taxon_names.json` 은 규조 자료다. `coredata/mapping.toml` 은
같은 코어면 다시 쓸 수 있으나 그것도 자료라 필요할 때 복사한다.

유공충 쪽 자료원(후보 — 정하는 것은 사람):

- **분류 체계**: WoRMS(AphiaID 가 있다 — `harvest_worms.py` 가 그대로 돈다) · mikrotax.org(pforams · bforams)
- **도감**: 부유성은 Kennett & Srinivasan (1983), 저서성은 Holbourn·Henderson·MacLeod (2013), 총론은 Loeblich & Tappan (1987). **PDF 를 굽는 것은 저작권을 본다** — DiaRUGA 는 국내 도감·논문이라 됐지만 이쪽은 상업 출판이 섞인다. 사내망 안에서만 보는 것으로 한정하거나 mikrotax 링크로 대신한다
- **학습 자료**: Endless Forams(Hsiang et al. 2019 — 현생 부유성 35종 · 34,000여 장 · CC 라이선스)가 분류기 사전학습감이다. 저서성은 공개 자료가 드물다 — 우리 검토에서 나온다

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
| **0 뼈대** | 저장소 구조 · `README`·`CLAUDE`·`.env.template` · requirements 넷 · `deploy/` 이름 바꿔서 · `forgiaweb` 설정 · 빈 `models.py`(층 넷만) · `naming.py` · `tests/base.py` · CI | 빈 DB 로 뷰어 목록이 뜬다. `manage.py test` 가 0개로 통과 |
| **1 층·반입** | `Site`~`Slide`·`Viewpoint`~`Image` · `scan_nas`·`ingest_nas`·`group_focus_series`·`focus_stack` · 목록·시야 목록·지점·지도·시스템 설정(자료) · `backup_db`·`check_db` · 시험 | NAS 폴더가 시야·합성본까지 간다. 사진이 화면에 뜬다 |
| **2 검출** | `Detection`·`Candidate`·`ThresholdSet`·`ClassDef` · `segment_forams`(YOLO) · 새 `judge` · `refilter` · 크롭·계측·문턱 화면 · `RunBatch` 운영 화면 | 첫 가중치가 없으니 씨앗 후보(SAM2 한 번 또는 단순 임계)로 시작 — DiaRUGA P04 가 걸은 길 그대로: 검토 → `export_yolo` → 학습 → 갈아탄다 |
| **3 교정·동정** | `ViewpointReview`·`ObjectReview`·`ForamObject`·**`Taxon`** · 시야 화면(교정·묶기·마스크 그리기) · 카탈로그 · `export_review` · 시험 대부분 | 사람이 종을 붙일 수 있다. 여기까지가 "DiaRUGA 와 같은 것" |
| **4 분류기** | `classify_crops` · `export_crops` · `Candidate.taxon_top` · 시야·카탈로그 화면의 추천·확신도 · 학습 자료 탭 | **Forgia 가 DiaRUGA 와 달라지는 자리.** 첫 가중치는 Endless Forams 로 사전학습한 것 |
| **5 산출·도감·오프라인** | `/compare/` 계수표 · 분획·분할 환산 · CSV · `Atlas*`+`render_atlas_pages` · 오프라인 검토기 | 보고서에 실을 표가 나온다 |

0~1 단계는 옮기는 일이 대부분이라 빠르다. 2단계는 **촬영 자료가 있어야** 판정
기준을 세울 수 있어 사진이 들어온 뒤다. 3단계가 옮기는 양이 가장 많다(시야
화면 · `data.py` 의 절반). 4단계는 새로 만드는 것이라 DiaRUGA 를 안 본다.

## 5. 먼저 정할 것 — 코드보다 앞선다

| 물음 | 무엇이 걸려 있나 |
|---|---|
| **촬영 장비·카메라·소프트웨어는?** 스케일(µm/px)을 어디서 읽나 — 메타 파일 · 스케일바 · 배율 상수? | ①. `zen_meta` 의 대체. 상수로 박으면 DiaRUGA 가 피한 사고("배율이 바뀌었는데 예외 없이 전부 어긋난다")를 그대로 맞는다 |
| **z-stack 을 장비가 남기나, 사람이 초점을 바꿔 여러 장 찍나?** | `group_focus_series` 를 손보는 정도 |
| **픽킹 슬라이드 격자를 쓰나(60칸 등)? 한 시야가 격자 한 칸인가, 슬라이드 전체 한 장인가, 타일인가?** | `Viewpoint` 의 뜻 · 검출 크기 관문 |
| **NAS 폴더 이름 규칙** — DiaRUGA 와 같은 `<지역>-<지점> <깊이>cm` 인가, 분획(`>125`)이 어디 붙나 | `naming.py` |
| **분획·분할을 어디까지 기록하나** (>63 / >125 / >150 µm · split 1/n · 픽킹한 개체 수 vs 전체) | `Sample` 칸 · 산출표 환산 |
| **분류 체계의 기준** — WoRMS 를 정본으로 하나, mikrotax 를 하나 | `Taxon` 반입 |
| **동정할 종 목록의 첫 판** — 남극 부유성은 사실상 *N. pachyderma* 하나에 가깝고 저서성이 많다. 처음 몇 종으로 시작하나 | 4단계 분류기의 클래스 수 · 학습 자료 |
| **Endless Forams 를 쓰나** | 사전학습 여부 |
| **도감 PDF 의 저작권** | 3.6 |
| **라이선스** — ultralytics 를 쓰면 AGPL-3.0 그대로 | `LICENSE` |
| **GPU 잠금을 DiaRUGA 와 공유** — DiaRUGA 쪽 수정 하나 | 3.1 |

## 6. 버린 것

- **fork** — 1절
- **공통 패키지로 먼저 빼기** — 1절. 세 번째 프로젝트 때
- **`ClassDef` 를 종 목록으로 늘리기** — 여섯 줄에 맞춰진 UI(단축키 순환 · 색 · CSS 배지)가 수백 종에서 안 선다. 형태와 분류학을 처음부터 가른다
- **SAM2 백엔드 유지** — 불투명 개체에 SAM2 가 나을 이유가 없고 VRAM·의존성만 든다. 씨앗 후보 한 번에만 쓴다
- **한국·노두 갈래** — 시료가 남극 코어라 코드를 끌고 오지 않는다. 생기면 DiaRUGA 에서 그때 가져온다(063 이 다 적어 놨다)
