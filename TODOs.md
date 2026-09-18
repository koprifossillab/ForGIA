# TODOs

## 2단계에서 남은 것 (GPU 가 돌아오면)

- **씨앗 가중치 다시 굽기** — `runs/seed-cpu` 는 CPU 한 에포크(모양 확인용). P01 5.1 대로 `yolo11n-seg`·1024 로 굽고 `/data3/ForGIA/models/11n-synth-v2-1024.pt` 에 둔다
- **파이프라인 이미지 굽기** (`deploy/Dockerfile.pipeline`) · 시험 배포에서 `poll_nas.sh` 한 바퀴 → `check_db` → 화면
- **판정 기본값(63~2000 µm · conf 0.25)** 을 실사진으로
- `export_yolo.py` — 3단계(교정 테이블)와 함께
- **호스트 NVIDIA 드라이버 판 어긋남** (커널 580.173 / 라이브러리 580.178) — admin 재부팅. DiaRUGA 폴러도 같이 멈춰 있다

## 1단계에서 남은 것

- **그룹핑 지문에서 조명 기울기 빼기** — 합성 사진에서 다른 시야가 0.61 로 묶였다(001). 실사진이 오면 임계값과 함께 본다
- `scale.py` ⑴ Leica XML — 실사진 + 메타 한 벌이 오면
- 브라우저 시험 겹(`tests/browser/`) 바닥
- `ops/export_review.py` — 3단계(교정 테이블)와 함께

## 3단계 — 교정·동정

- `Taxon` 모델 + WoRMS 반입(`harvest_worms.py`) · `active` 로 자동완성 목록
- `ViewpointReview`·`ObjectReview`·`ForamObject` · 시야 화면 · 카탈로그 · `export_review` · `rebind`(재검출 뒤 교정 잇기) · `export_yolo`
- `check_db` 3(교정)·8(대표 이미지)번 · 1·2번의 교정 갈래
- `data.py` 의 집계 SQL 에 교정 조인(`_REP_CTE`·`_SUMMARY_SQL` 의 검토 열)

## 4단계 — 분류기

- `classify_crops.py`(ONNX) · `export_crops.py` · `Candidate.taxon_top` · 추천·확신도 UI
- Endless Forams 사전학습

## 5단계 — 산출·도감·오프라인

- `/compare/` → 시료 × 종 계수표 (분획·분할·건시료 무게 환산 · CSV)
- 도감 — 사내망 안에서만. PDF 는 연구실이 정한다
- 오프라인 검토기

## 운영·바깥

- GitHub 저장소 이름 `Forgia` → `ForGIA` (admin) · 로컬 원격 갱신
- Docker Hub 시크릿(`DOCKERHUB_USERNAME`·`DOCKERHUB_TOKEN`)을 저장소에
- nginx `include snippets/ForGIATest-subpath.conf` (sudo · 운영 것은 들어갔다)
- ~~DiaRUGA `views.py:741` 의 `attached` 버그를 저쪽에 알린다~~ — DiaRUGA `TODOs.md` 에 적었다 (2026-09-18)
