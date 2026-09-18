# HANDOFF

**2026-09-18** · 0단계(뼈대) 끝. 이어서 할 사람은 여기부터.

## 1. 한 줄 요약

저장소가 있고, 층 넷(`Site`·`Locality`·`Sample`·`Slide`)과 목록 화면·`/healthz`
가 빈 DB 로 뜨며, 시험 11개가 돈다. **파이프라인·배포·자료는 아직 없다.**
DiaRUGA 에서 무엇을 옮길지는 [P01](devlog/20260918_P01_port-from-DiaRUGA.md) 에
정했고, 물음 열둘도 같은 날 다 정했다(P01 5절).

| | 지금 |
|---|---|
| 뷰어 | 판 없음 — 개발 서버로만 뜬다 (`CLAUDE.md` "저장소 소스를 그대로 사내망에 띄운다") |
| 파이프라인 | 없다 (1단계) |
| DB | 없다 — `ForGIA.db` 는 개발 장비의 사본뿐 |
| 자료 | NAS `Forams/Foram_YOLO_microscopy_scaled_v2` — **합성** 자료 660장 (P01 5.1). 실사진은 장비가 아직 없다 |
| 단계 | **0 끝 → 1(층·반입) 시작 전** |

## 2. 지금 돌아가는 것

### 뷰어 (Django 5.2)

```
데이터셋 목록  /          지역 → 지점 → 시료 → 관찰. 소속 없는 관찰 수를 적는다
/healthz                  판 · 테이블 행 수 · 백업 나이. 슬라이드 0 이면 unhealthy (맞는 답이다)
```

`base.html` 에 테마(연보라 강조색 · `data-theme` · `localStorage` `forgia.theme`) ·
머리줄 · 워터마크(로고 원본 구성 그대로) · 자리 이름 워터마크(`FORGIA_ENV_LABEL`,
`DEBUG` 면 `Test Server`)가 있다.

### 시험

`python web/manage.py test viewer` — 11개 · 0.04초. `tests/base.py` 가 운영 DB·
`/data3` 를 가리키면 멈춘다. 브라우저 겹은 아직 없다 (화면 확인은 playwright 로
손으로 했다 — `CLAUDE.md` "확인하는 법").

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

## 4. 어디까지 왔나

| 단계 | 상태 |
|---|---|
| 0 뼈대 | **끝** (2026-09-18) |
| 1 층·반입 | 다음. `Viewpoint`~`Image` · `scan_nas`·`ingest_nas`·`group_focus_series`·`focus_stack` · `scale.py` · `ops/` · 시야 목록·지점·지도·시스템 설정(자료) |
| 2 검출 | 합성 자료로 YOLO 씨앗 가중치. `segment_forams` · 새 `judge` · GPU 시스템 잠금 |
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
