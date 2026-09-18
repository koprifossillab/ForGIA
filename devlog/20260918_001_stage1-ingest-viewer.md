# 001 — 1단계: 층·반입·뷰어 (2026-09-18)

**계획** [P01](20260918_P01_port-from-DiaRUGA.md) 4절의 1단계.
**상태** 끝 — 시험 101개 · 합성 자료로 반입 넷이 끝까지 돈다 · 화면 일곱이 뜬다.
시험 배포(`testdeploy.sh`)는 `/srv/ForGIA` 가 아직 없어(sudo) 못 띄웠다.

## 무엇을 했나

| 자리 | 판정 | 무엇 |
|---|---|---|
| `models.py` | 그대로 + | `RunBatch`·`Run`·`Viewpoint`·`Frame`·`Stack`·`Image` (DiaRUGA 그대로) · **`Viewpoint.cell`**(격자 칸) · `Slide.corr_thresh` 되살림 · `Frame.SOURCE` 를 `scale.py` 의 출처 목록으로 · `RUN_KIND` 에 `classify` |
| `pipeline/` | 그대로·손봄 | `scan_nas`·`ingest_nas`(사진 확장자 넷 · 메타 `.xml/.json/.toml` · "스케일 근거" 셈) · `group_focus_series`(칸 번호 매김 · 분획 defaults · 검출 테이블 없어도 `--force` 갈래가 죽지 않게) · `focus_stack`(`um_per_pixel_override` 대신 `scale.toml`) · `runlog`·`schema_guard` 그대로 |
| `pipeline/scale.py` | **새로** | 아래 |
| `ops/` | 그대로·손봄 | `backup_db`·`db_sentinel`·`sync_backup_nas`·`pending_slides` 그대로 · `fetch_kpdc`(코어 거름 없이) · **`check_db` 는 5·7번만**(뼈대·층 + 격자 칸·분획·집계 제외 검사를 더함). `export_review` 는 3단계 |
| `web/viewer/` | 손봄 | `data.py`(1단계 함수만 · 검출 값은 `0` 자리) · `manage_data.py` · `views.py` · `urls.py`(DiaRUGA 와 같은 주소) · `antarctica`·`ross`·`kpdc`·`images`·`templatetags` 그대로 |
| 템플릿 | 손봄·새로 | `index`(분획·분할·합성 열) · `dataset`(칸 번호 · 검출 배지 없음) · **`group`(1단계용 사진 화면 — 3단계가 갈아 끼운다)** · **`core`(정보 카드 + 깊이순 표 — 코어 자료 곡선은 5단계)** · `dataset_edit`(분획·분할·칸 수·건시료) · `system_settings`·`_system_settings_nav`(자료·파이프라인 탭) · `_map*`(한국 갈래 없이) · `base.html` 에 DiaRUGA CSS 이식 + 톱니 |
| 시험 | 옮김·새로 | DiaRUGA 에서 `env_label`·`theme`·`schema_guard`·`kpdc`·`map_ross`·`map_filter`·`pipeline_tab`·`topnav`(없는 화면 뺌) · 새로 `scale`·`pipeline_ingest`(합성 사진으로 그룹핑→합성 끝까지)·`edit_settings` |
| `deploy/` | 손봄 | `poll_nas.sh`(검출 고리는 2단계로 미룸) · `Dockerfile.pipeline`(SAM2 없이). web 이미지는 로컬에서 구워 `check` 가 도는 것을 봤다 |

## 정한 것 · 알게 된 것

**`scale.py` 의 차례** — `scale.toml(override=true)` → Leica XML(아직 못 읽음 · 파일이
있으면 경고만) → EXIF `ImageDescription` JSON → `scale.toml` → 사이드카 → 기본값.
DiaRUGA 의 `Slide.um_per_pixel_override` 칸은 안 가져왔다 — 사람이 못 박는 값은
DB 가 아니라 **사진 폴더의 `scale.toml`** 에 둔다. 자료 곁에 있어야 폴더를 옮겨도
따라가고, 파이프라인이 DB 를 안 보고도 읽는다. 합성 자료의 EXIF(`microscope.um_per_pixel`)
가 ⑵ 갈래의 시험 자료다.

**그룹핑 임계값은 실사진으로 다시 잡아야 한다.** DiaRUGA 기본 0.55 로 합성 사진을
돌리니 **서로 다른 시야 둘이 0.61 로 묶였다** — 빈 바탕에 개체 하나라 저주파
지문이 서로 닮는다. 실사진(격자 선·바탕 무늬)이 오기 전에는 판단할 수 없어
0.8 로 돌렸고, 임계값은 `Slide.corr_thresh` 에 남는다. 지문에서 조명 기울기를
빼는(큰 시그마 블러를 한 번 더 빼는) 손질이 후보다 — TODOs.

**분획·분할은 `Slide` 의 것이다.** 0단계에서 정한 그대로 옮겼다(`check_db` 7번이
"같은 시료·분획의 관찰 여럿에 집계 제외가 있는가" 를 센다 — DiaRUGA 056 의 두 배
함정을 분획까지 넓힌 것).

**DiaRUGA 에 잠복한 버그 하나.** `views.dataset_edit` 이 `attached = … slide.sample_id
!= sample.pk` 를 **저장 전에** 계산한다 — 코드로 시료를 새로 만들어 붙이는 길에서
`sample.pk` 가 아직 `None` 이라 `None != None` 이 거짓이 되고, "새로 만들어
붙였습니다" 만 뜬 채 관찰은 소속 없이 남는다. 새로 쓴 `test_edit_settings.
test_create_layers_from_codes` 가 잡았다. ForGIA 는 고쳤고(저장 뒤에 비교),
**DiaRUGA `web/viewer/views.py:741` 도 같다** — 저쪽에 알릴 것.

**옮겨 온 시험이 이식의 검사 도구가 됐다** (P01 3.4 가 말한 그대로). `test_topnav`
가 머리줄 톱니가 빠진 것을, `test_kpdc` 가 지점 페이지의 KPDC 줄이 값 없이도
찍히는 것을 잡았다.

## 버린 것

- **`Slide.um_per_pixel_override`** — 위. `scale.toml` 이 그 자리다
- **`data.py` 통째로 옮기기** — 5,632줄 중 1단계가 쓰는 것은 400줄이다. 검출
  집계(`_summary_by_sql`)는 그 테이블과 함께 2단계에 온다 — 열쇠 이름은 그대로
  두어 화면이 안 바뀌게 했다
- **DiaRUGA `core.html`(637줄)** — 코어 자료 곡선·깊이 로그·노두 사진이 대부분이라
  새로 짧게 썼다. 곡선은 5단계 `CoreSeries` 와 함께
- **`test_urls_render`·`test_depth_hidden`** — 검출·엔진 화면을 전제한다. 그 단계에서
- **한국 지도·노두** — P01 대로

## 다음

2단계(검출). 합성 자료로 YOLO 씨앗 가중치를 굽고 `segment_forams.py`·새 `judge.py`
를 만든다. 그 전에 `/srv/ForGIA` 를 만들어(sudo) `testdeploy.sh` 로 시험 배포를
한 번 띄워 본다 — `deploy/` 는 아직 한 번도 안 돌았다.
