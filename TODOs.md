# TODOs

## 1단계에서 남은 것

- **시험 배포** — `sudo mkdir /srv/ForGIA` 뒤 `deploy/host/testdeploy.sh`. `deploy/` 는 아직 한 번도 안 돌았다
- **그룹핑 지문에서 조명 기울기 빼기** — 합성 사진에서 다른 시야가 0.61 로 묶였다(001). 실사진이 오면 임계값과 함께 본다
- `scale.py` ⑴ Leica XML — 실사진 + 메타 한 벌이 오면
- 브라우저 시험 겹(`tests/browser/`) 바닥
- `ops/export_review.py` — 3단계(교정 테이블)와 함께

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
- **DiaRUGA `views.py:741` 의 `attached` 버그를 저쪽에 알린다** (001)
