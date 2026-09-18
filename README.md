# ForGIA

남극 시추코어 시료의 유공충(foraminifera) 픽킹 슬라이드 현미경 사진 분석
파이프라인 + 동정·교정 뷰어. **DiaRUGA**(규조)의 틀을 파일 단위로 골라 옮기고
그 위에 종 동정 분류기를 얹는다.

사진에서 개체를 찾아 µm 단위로 계측하고, 분류기가 종 후보를 내고, 웹 뷰어에서
사람이 확인·교정한다. 최종 산출은 **시료 × 종 군집 계수표**다.

```
NAS 새 폴더 ─[반입]→ 사진 ─[그룹핑]→ 시야 ─[합성]→ all-in-focus ─[검출]→ 개체
                                                                 ─[분류]→ 종 후보
                                                                 ─[교정]→ 사람
```

**원본은 `ForGIA.db` 다** (SQLite, WAL). 파이프라인과 뷰어가 같은 DB 를 읽고 쓴다.

> **지금은 0단계(뼈대)다.** 층 넷과 목록 화면·`/healthz`·배포 틀·시험 바닥이
> 있고, 파이프라인은 없다. 어디까지 왔는지는 [HANDOFF.md](HANDOFF.md).

## 자료의 층

```
권역   남극                              Site.area
 └ 지역   RS23                            Site
    └ 지점   GC03(시추코어)                Locality
       └ 시료   71cm                        Sample      ← 건시료 무게
          └ 관찰   >125um (1)                Slide       = 폴더 하나 = 픽킹 슬라이드 하나
             └ 시야 = 격자 한 칸           Viewpoint.cell
                └ 사진 → 검출 → 분류 → 교정
```

**분획·분할은 관찰의 것이다.** 같은 시료를 `>63um` 과 `>125um` 으로 따로 픽킹하면
슬라이드가 둘이다. 폴더 이름 규칙은 **`web/viewer/naming.py` 하나뿐이다**
(`<지역>-<지점> <깊이>cm >125um (관찰)` — 분획·관찰 토막은 없어도 된다).

## 구성

| 디렉토리 | 무엇이 | 운영(`/srv`)으로 가나 | 단계 |
|---|---|---|---|
| `web/viewer/` | Django 뷰어 | 이미지로 간다 | 0~ |
| `deploy/` | compose·nginx·호스트 스크립트 | **간다** | 0 |
| `pipeline/` | 반입·그룹핑·합성·검출·분류·판정 | **간다** | 1~4 |
| `ops/` | 백업·무결성·검사·내보내기 | **간다** | 1~ |
| `review/` | 교정의 git 감사 기록 | — | 3 |
| `docs/assets/logo/` | 로고 원본 | — | 0 |

무엇을 DiaRUGA 에서 얼마나 옮기는지, 어디가 다른지, 무엇을 정했는지는
[devlog/20260918_P01](devlog/20260918_P01_port-from-DiaRUGA.md).

## 설치

**requirements 가 넷으로 갈라져 있다.** 호스트 venv 는 `requirements.txt` 하나면
되고, 컨테이너가 나머지를 나눠 쓴다.

| 파일 | 누가 쓰나 |
|---|---|
| `requirements.txt` | 호스트 venv (`~/venv/ForGIA`) |
| `requirements-web.txt` | 뷰어 컨테이너 — Django · pillow · gunicorn |
| `requirements-pipeline.txt` | 파이프라인 컨테이너 — torch · opencv · onnxruntime |
| `requirements-yolo.txt` | ultralytics. `--no-deps` 로 파이프라인 위에 얹는다 |

```bash
python -m venv ~/venv/ForGIA && . ~/venv/ForGIA/bin/activate
pip install -r requirements-web.txt -r requirements-dev.txt   # 뷰어·시험만
python web/manage.py test viewer
```

## 배포

DiaRUGA 와 **같은 서버에 나란히** 뜬다 — `/srv/ForGIA`, nginx 80 의 `/ForGIA/`,
컨테이너 포트 8091(운영)·9092(시험). 뷰어와 파이프라인이 컨테이너 두 벌로 돌고
판이 따로다(`IMAGE_TAG` / `PIPELINE_TAG`). 자주 빠지는 함정은
[CLAUDE.md](CLAUDE.md) 에 모아 두었다.

## 문서

| | |
|---|---|
| [HANDOFF.md](HANDOFF.md) | **지금** 상태와 인수 사항 |
| [CLAUDE.md](CLAUDE.md) | 자주 쓰는 명령과 **자주 빠지는 함정** |
| [TODOs.md](TODOs.md) | 앞으로 할 일 |
| [CHANGELOG.md](CHANGELOG.md) | 판 이력 |
| `devlog/YYYYMMDD_PNN_*.md` | 계획 · `YYYYMMDD_NNN_*.md` 작업 기록 |

## 라이선스

**GNU Affero General Public License v3.0** ([LICENSE](LICENSE)). 검출 백엔드가 쓰는
Ultralytics YOLO 가 AGPL-3.0 이라 합쳐지는 이 저장소도 같은 라이선스로 나간다 —
DiaRUGA 와 같다. 사진·DB·학습 자료는 저장소에 없고 이 라이선스의 대상도 아니다.
