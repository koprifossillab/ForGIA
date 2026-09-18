# 002 — 2단계: 검출·판정·문턱 (2026-09-18)

**계획** [P01](20260918_P01_port-from-DiaRUGA.md) 4절의 2단계.
**상태** 코드는 끝 — 시험 118개 · `check_db` 1·2·4·5·6·7번 · 화면 넷(검출 표·크롭·
문턱·운영 탭) · 폴러 4b 검출 고리. **GPU 는 못 썼다** — 호스트의 NVIDIA 드라이버가
라이브러리와 판이 어긋나(커널 580.173 / 라이브러리 580.178) 컨테이너가 카드를 못 잡는다.
씨앗 가중치는 CPU 로 한 에포크만 구웠다(아래).

## 무엇을 했나

| 자리 | 판정 | 무엇 |
|---|---|---|
| `models.py` | 그대로·손봄 | `ThresholdSet`(**`min_um`·`max_um`·`conf_min` 셋**) · `ClassDef`(0004 가 `foram`·`broken`·`fragment`·`other` 를 심는다 · 단축키 q/w/e/r) · `Setting` · `Detection`(`reviewing()` = 검토 대상 묶음의 현재 검출) · `Candidate`(**`major_um`·`long_side_um`·`predicted_iou`** — 텍스처 칸 없음) |
| `pipeline/judge.py` | **새로** | 아래 |
| `pipeline/segment_forams.py` | 손봄 | `segment_diatoms.py` 1,404줄에서 **SAM2·텍스처를 뺐다** → 1,313줄. YOLO 하나 · DB 저장 · `Run`·묶음 · OOM 재시도 · GPU 잠금 그대로. `rebind` 는 3단계라 `is_current` 만 옮긴다 |
| `pipeline/refilter.py` · `batch_plan.py` · `batch_scope.py` | 그대로·손봄 | 문턱 이름만 셋으로. `batch_scope` 는 권역이 남극 하나뿐이라 지금은 통과만 한다 |
| `web/viewer/thresholds.py` | 그대로·손봄 | 미리보기·적용·이력. 지표 셋만 읽는다 |
| `web/viewer/data.py` | 손봄 | 1단계에 `0` 으로 두었던 자리를 채웠다 — `_summary_by_sql`(검출·계수 집계) · `current_detections` · `candidate_rows` · 마스크 폴리곤·크롭 기하·스케일바. **교정 조인은 아직 없다**(3단계) |
| `views.py` · `urls.py` | 손봄 | `/d/<slug>/detections/` · `/d/<slug>/crops/` · `/thresholds/`·`/d/<slug>/thresholds/` + `api/threshold/*` · `/crop` · 시스템 설정 **운영 탭**(묶음 만들기·검토 대상·조리법) · `/healthz` 가 검출 테이블도 센다 |
| 템플릿 | 손봄·새로 | `dataset`(마스크 미리보기·검출 배지·크롭 링크) · `group`(SVG 폴리곤 덮개 + 토글) · `index`(검출·계수 열) · 새로 `crops`·`detections`·`thresholds`·`system_settings_ops`·`_batchtag` |
| `ops/check_db.py` | 손봄 | **1·2·4·6번**을 더했다 — 판정 캐시(`judge.apply` 다시 돌려 저장값과 비교) · 이미지마다 현재 검출 하나·검토 대상 묶음 하나 · 분류가 `ClassDef` 에 있고 단축키·색이 있다 · 슬라이드 안 문턱 하나. 교정 갈래(`ViewpointReview`)는 뺐다 |
| `deploy/poll_nas.sh` | 손봄 | 4b 검출 고리 되살림 — `batch_plan.py --args` 가 조리법을 주고 묶음마다 `segment_forams.py` 를 돈다. `--points-per-batch` 갈래 없음 |
| `deploy/srv/docker-compose.yml` | 손봄 | 파이프라인 서비스에 `FORGIA_GPU_LOCK=/data3/DiaRUGA/locks/gpu.lock` + 그 디렉토리 마운트 |
| 시험 | 옮김·새로 | `test_judge`(관문·`collapse_boxes`·`dedupe`) · `test_detections`(검출 표·크롭·문턱 미리보기/적용·운영 탭) · `factories.make_world` 가 검출·후보를 심는다 |

## 정한 것 · 알게 된 것

**판정 관문은 둘이다** (P01 2절 ②). DiaRUGA 는 크기 → 텍스처 → 형태 셋이었다 —
투명한 규조각을 쇄설물에서 가르는 데 다 필요했다. 유공충은 불투명하고 픽킹해서
서로 떨어져 있어 검출기가 낸 것이 곧 후보다. 남는 것은 **장축 µm 범위**와
**확신도**(`predicted_iou` 자리에 YOLO conf 가 든다 — DiaRUGA 와 칸 이름을 같게
두어 `thresholds.py`·`check_db` 를 그대로 옮겼다)뿐이다. 형태 지표(`elongation`·
`solidity`·`ellipse_iou`)는 관문이 아니라 **재서 남기는 값**이다 — 나중에 파편/온전
분류의 근거로 볼 수 있다. `classify` 가 내는 분류는 `foram` 하나다 — "무엇인가·
온전한가" 는 분류기(4단계)와 사람(3단계)의 몫이라 `ClassDef` 의 나머지 셋(`broken`·
`fragment`·`other`)은 지금은 사람이 붙일 자리만 있다.

**`ThresholdSet` 은 셋이다.** DiaRUGA 의 열 남짓(`tex_min`·`tex_max`·`ellipse_iou_min`…)
에서 `min_um`·`max_um`·`conf_min` 만 남겼다. 문턱 화면·`refilter`·`batch_plan` 의
`RECIPE_NUM` 이 전부 이 셋을 본다. 기본값(63~2000 µm · conf 0.25)은 **실사진으로
다시 잡을 값이다** — 지금 것은 픽킹 분획 >63 µm 와 합성 자료의 크기 범위를 보고
넉넉히 둔 것.

**GPU 잠금은 DiaRUGA 쪽을 안 고쳐도 된다.** `segment_diatoms.py` 의 잠금 파일이
`<DATA_ROOT>/locks/gpu.lock` 이라 ForGIA 가 **그 파일을 그대로 가리키면** 같은
inode 에 `flock` 이 걸린다. `FORGIA_GPU_LOCK` 환경변수 + 디렉토리 마운트로 끝 —
P01 5절과 HANDOFF 가 "DiaRUGA 도 같이 고친다" 고 적었던 것을 고쳤다. 잠금 파일
소유자가 `paleoadmin`(1000) 이고 컨테이너도 `1000:1000` 이라 쓰기 열기가 된다.

**씨앗 가중치는 CPU 로 한 에포크뿐이다.** 호스트 드라이버 판 어긋남으로 GPU
컨테이너가 안 뜬다(DiaRUGA 폴러도 같은 이유로 분마다 실패하고 있다 — admin 이
재부팅해야 한다). `yolo11m-seg` · imgsz 640 · `fraction 0.25` · 1 epoch 로
`/data3/ForGIA/runs/seed-cpu` 에 구웠다. **모양을 확인하는 가중치이지 쓸 가중치가
아니다** — 검출 파이프라인이 DB 까지 끝까지 도는지, 화면이 그것을 그리는지를
보는 용도. GPU 가 돌아오면 P01 5.1 대로 다시 굽는다(TODOs).

**`judge.py` 는 torch·cv2 에 기대지 않는다.** DiaRUGA 와 같은 이유 — 문턱 재조정은
GPU 없는 일이고 뷰어 컨테이너(torch 없음)가 `thresholds.py` 로 같은 함수를 부른다.

## 버린 것

- **SAM2 백엔드·`--points-per-side`·`--points-per-batch`** — P01 대로. 폴러의 PPB
  변수도 뺐다
- **텍스처 지표(`tex_period`·`tex_strength`)와 그 문턱** — 규조의 areolae 를 보는
  것이라 유공충에 없다
- **`export_yolo.py`** — 교정 테이블(`ObjectReview`)을 읽으므로 3단계로. TODOs
- **`check_db` 1·2번의 교정 갈래** — 같은 이유
- **DiaRUGA `views.py` 의 `detections` 화면 중 엔진 비교 열** — 백엔드가 하나다

## 다음

- GPU 가 돌아오면: 씨앗 가중치를 제대로 굽고(`11n-synth-v2-1024.pt`) 시험 배포에서
  `poll_nas.sh` 한 바퀴 → `check_db` → 화면
- 3단계(교정·동정). `group.html` 을 검토 화면으로 갈아 끼우고 `ViewpointReview`·
  `ObjectReview`·`ForamObject`·`Taxon`(WoRMS) 을 들인다. `rebind`·`export_review`·
  `export_yolo`·`check_db` 3·8번이 그때 온다
