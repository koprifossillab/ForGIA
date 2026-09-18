# 003 — 3단계: 교정·동정·카탈로그·학명 (2026-09-18)

**계획** [P01](20260918_P01_port-from-DiaRUGA.md) 4절의 3단계 — "사람이 종을 붙일 수
있다. 여기까지가 DiaRUGA 와 같은 것".
**상태** 코드 끝 — 시험 694개(+브라우저) · `check_db` 열 번 · 검토 화면·카탈로그·
학명 표. WoRMS 전체 반입은 **스크립트만** 있고 아직 안 돌렸다(아래).

## 무엇을 했나

3단계는 옮기는 양이 가장 많은 단계다(P01 이 그렇게 적었다). 2단계까지의 방식
(DiaRUGA 함수를 골라 짧게 다시 쓰기)으로는 안 됐다 — 검토 화면의 JS 가 4,117줄이고
`data.py` 의 교정·묶기·손그림·번지기가 서로 물려 있어 **한 조각만 옮기면 그 조각을
검사하던 시험이 안 돈다.** 그래서 방향을 바꿨다: **DiaRUGA 의 파일을 통째로 가져와
ForGIA 의 것으로 바꾼다.** 이름 바꾸기(`DiatomObject`→`ForamObject` · 규조각→개체)는
스크립트로, 뜻이 다른 자리(종명·분류 색·엔진 라디오·노두)는 손으로.

| 자리 | 판정 | 무엇 |
|---|---|---|
| `models.py` | 그대로 + 새로 | `ViewpointReview`·`ObjectReview` 그대로 · `ForamObject`(`species` 문자열 → **`taxon` FK** · `POSE` 넷) · **`Taxon`**(WoRMS 계층 · `active` · `habit`). 0005 |
| `data.py` | **통째로** | 5,632줄 중 도감·코어 곡선·한국 지도·비교 화면을 뺀 4,400줄. 1·2단계에서 짧게 썼던 함수(`_cand_dict`·`candidate_rows`·`_SUMMARY_SQL`…)는 DiaRUGA 원본으로 되돌리고 격자 칸·분획·배율 출처만 다시 얹었다 |
| `views.py` | 옮김 | `group`(검토 화면)·`split_group`·`mark_all`·`catalog`·`save_catalog`·`save_object_link`·`spread_detection`·`save_review`·`api_dataset`. 새로 `taxon_suggest`·`system_settings_taxa` |
| `catalog.py`·`shape.py`·`regroup.py` | 그대로 | |
| 템플릿 | 옮김 | `group`·`_detection`·`_detview_js`·`_shots`·`catalog`·`regroup_confirm`·`dataset`(검토 완료 숨기기·전체 표시). `base.html` 에 검토 화면 CSS 800줄. 새로 `system_settings_taxa` |
| `pipeline/rebind.py` | 옮김 | `migrate/` 에서 `pipeline/` 로 — `segment_forams` 가 저장마다 부르니 /srv 로 가야 한다 |
| `ops/export_review.py`·`export_yolo.py` | 옮김 | 종명은 `Taxon` 조인 · YOLO 클래스 `foram` |
| `ops/check_db.py` | 손봄 | 3(교정)·8(묶음)·9(카탈로그)·10(등급·자세) + 2·4의 교정 갈래 |
| `migrate/import_worms.py` | **새로** | 아래 |
| 시험 | **옮김 38 + 브라우저 31** | 자동 이름 바꾸기 + 손질(아래). `factories.py` 도 DiaRUGA 원본 위에 분획·칸·`make_taxon` |

## 정한 것 · 알게 된 것

**종은 `Taxon` 행이고, 없는 이름은 거절한다.** DiaRUGA 의 `species` 는 자유 문자열
이었고 그쪽 devlog 가 같은 종을 두 가지로 적는 일을 걱정했다. ForGIA 는 종이 수백이고
계층이 있어 계수표(5단계)가 문자열로는 안 선다. `ForamObject.taxon` FK 로 두고
`species` 는 **읽기·쓰기 통로**로 남겼다(`Taxon.resolve` 가 이름을 찾는다 · 없으면
`ValueError`) — DiaRUGA 의 읽는 자리 70여 곳과 시험이 그대로 돈다. **동정에 한 번
쓰인 학명은 저절로 켜진다**(`active`) — "쓰는 것만 켠다" 는 P01 5절의 규칙이
사람이 목록을 관리하는 일 없이 지켜진다. 화면(카탈로그·검토 칸)은 켠 것을
`<datalist>` 로 갖고 시작하고, 세 글자를 치면 `/api/taxon/suggest` 가 반입 전체에서
찾아 목록을 갈아 끼운다. **종명은 치는 도중에 저장하지 않는다** — 거절이 글자마다
뜨기 때문에 칸을 떠나거나 목록에서 고른 값만 보낸다(코멘트는 DiaRUGA 대로 흐른다).

**WoRMS 반입은 긁기와 넣기를 갈랐다** (`import_worms.py harvest` / `load`).
유공충 문(AphiaID 1410) 아래를 자식 API 로 내려가며 훑는데 한 쪽이 50행이라 요청이
수천 번이다 — 원시 레코드를 `records.jsonl` 에 쌓아 끊겨도 이어 간다. 넣기는 몇 초고
멱등이다(`aphia_id`). 시험 삼아 *Globigerina* 속 하나(516행)를 긁어 넣어 봤다 —
`status` 가 열 가지가 넘어(`junior subjective synonym` …) 선택지로 가두지 않고 그대로
적는다. **전체 반입은 안 돌렸다** — 몇 분짜리 일이지만 `/data3/ForGIA/worms/` 에
남을 파일이라 시험 배포에서 한 번에 한다(TODOs). 부유성/저서성은 WoRMS 에 없다 —
과·목에 붙이면 아래로 물려주는 길만 두었다(`habit` · 화면에서도).

**엔진 라디오는 묶음마다다.** DiaRUGA 는 SAM/YOLO 를 비교하는 화면이라 **엔진
단위**로 접었다(`yolo-1차`·`yolo-3차` 가 한 칸). ForGIA 는 백엔드가 하나라 그렇게
접으면 라디오가 하나만 남는다 — 비교하는 것이 씨앗 가중치와 재학습 가중치의
**회차**이므로 묶음마다 한 칸이다(`engines_from_batches`).

**되살린 개체의 짐작 분류는 `foram` 하나다.** DiaRUGA 의 `_guess_cls` 는 신장비로
원형/봉상을 갈랐고 그 규칙이 `_SUMMARY_SQL`·`_COVER_SQL` 의 CASE 에도 있었다.
셋을 함께 바꿨다 — 규칙이 세 자리라는 것을 그쪽 주석이 미리 적어 두었다.

**시험을 옮기는 규칙.** 분류 이름은 무늬로 옮겼다 — DiaRUGA 픽스처가 후보를
`원형(센다)·원형조각(안 센다)·봉상(센다)·봉상조각(안 센다)` 로 돌리고 시험이 "몇 번째
후보가 세어지나" 를 그 무늬로 짚는다. 그래서 표의 차례(온전·파손·파편·비유공충)와
따로 **후보가 도는 차례**를 `온전·파편·파손·비유공충` 으로 두었다(`CAND_CLASSES`).
묶음 이름은 `sam2-시험`→`yolo-시험`(검토 대상) · `yolo-시험`→`yolo-재학습`(다른 회차)
— 처음에 둘을 다 `yolo-시험` 으로 눌러 열다섯 개가 "이미 검토 대상" 으로 죽었다.
종명 문자열(`몰래`·`x`)은 라틴 학명으로 바꾸고 `make_world` 가 그 여덟을 심는다.
노두(`kind="outcrop"`)·한국·도감·오프라인 시험은 뺐다.

**옮겨 온 시험이 잡은 것.** `Taxon` 의 `related_name="objects"` 가 `Taxon.objects`
매니저를 가렸다(`test_group_page` 가 잡았다) · `.badge.<분류>` 색을 `rgb()` 로 적어
`test_classdef_css` 가 `ClassDef.color` 와 대조하지 못했다 · 묶기 API 가 종명을
`update_fields=["species"]` 로 저장하려 했다(`test_object_link`) · 시야 사진의 폴리곤
CSS 선택자가 `.detview` 만 봐서 검은 덩어리로 그려졌다(2단계 화면에서 눈으로).

## 버린 것

- **도감(`atlas`)·비교(`compare`)·코어 곡선·오프라인 검토기** — 5단계. 카탈로그의
  `도판` 단추와 오프라인 꺼내기 자리는 비워 두었다(`atlas_hit=None` · `off=None`)
- **학습 자료 탭(`training_overview`)** — 4단계(`export_yolo` 는 옮겼다)
- **`Taxon.status` 선택지** — WoRMS 가 내는 열 가지를 가둘 이유가 없다

## 다음

- 시험 배포에서 `import_worms.py harvest && load` 를 한 번 돌린다(수만 행)
- 실사진이 오면 판정 기본값·그룹핑 임계값(001·002)과 함께 **자세 넷·등급 기준**을
  사람과 다시 본다 — DiaRUGA 의 valve/girdle 을 umbilical/spiral/edge/apertural 로
  바꾼 것은 이름뿐이고 매기는 규칙은 아직 없다
- 4단계(분류기): `classify_crops`·`Candidate.taxon_top`·학습 자료 탭·Endless Forams
