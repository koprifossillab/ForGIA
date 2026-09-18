# TODOs

## 1단계 — 층·반입 (다음)

- `Viewpoint`(`cell` 포함)·`Frame`·`Stack`·`Image` 모델 — DiaRUGA `models.py` 461~643
- `pipeline/scan_nas.py`·`ingest_nas.py`·`group_focus_series.py`·`focus_stack.py`·`runlog.py`·`schema_guard.py`·`batch_*.py` — 그대로 옮긴다
- **`pipeline/scale.py` 새로** — 폴더 `scale.toml` → EXIF `ImageDescription` 순. Leica XML 갈래는 실사진이 온 뒤. 합성 자료의 EXIF 로 시험한다
- `ops/backup_db.py`·`db_sentinel.py`·`check_db.py`·`export_review.py`·`sync_backup_nas.py`·`fetch_kpdc.py`
- `/healthz` 에 무결성 깃발(`db_sentinel`) 붙이기
- 뷰어: 시야 목록 `/d/<slug>/` · 지점 `/loc/…` · 남극 지도(`antarctica.py`·`ross.py`) · 시스템 설정(자료) — `data.py`·`manage_data.py` 를 그때 가져온다
- `deploy/poll_nas.sh` — 단계 이름·인자를 ForGIA 것으로
- `testdeploy.sh` 로 시험 배포를 한 번 띄워 본다 — `deploy/` 는 아직 한 번도 안 돌렸다
- 브라우저 시험 겹(`tests/browser/`) 바닥

## 2단계 — 검출

- 합성 자료(NAS `Forams/…_v2`)로 YOLO 씨앗 가중치 굽기 — `data.yaml` 의 `path` 를 이 머신으로
- `segment_forams.py`(=`segment_diatoms` 에서 SAM2 뺀 것) · 새 `judge.py`(크기 관문 + `conf_min`) · `refilter.py`
- **GPU 시스템 잠금** — DiaRUGA `segment_diatoms.py` 도 같이 고친다. 저쪽에 알릴 것
- 크롭·계측·문턱 화면 · `RunBatch` 운영 화면

## 3단계 — 교정·동정

- `Taxon` 모델 + WoRMS 반입(`harvest_worms.py`) · `active` 로 자동완성 목록
- `ViewpointReview`·`ObjectReview`·`ForamObject` · 시야 화면 · 카탈로그 · `export_review`

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
- nginx `include snippets/ForGIA-subpath.conf` (sudo)
- Leica LAS X 실사진 + 메타 한 벌이 오면 `scale.py` ⑴ 갈래
