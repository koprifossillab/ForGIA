# 004 — 배포 끝에 옛 이미지를 정리한다 (DiaRUGA 219 와 같은 고침)

2026-10-07 · `work/20261007-paleoadmin` · paleoadmin

## 왜

이 머신은 개발·운영·백업을 겸하고 루트 SSD 가 늘 빠듯하다. 이미지는 판마다 쌓이는데 치우는 단계가 없었다 — 2026-10-07 `docker system df` 로 이미지 61개·32.7 GB 중 15.5 GB 가 정리 가능했다(대부분 형제 저장소의 옛 판). 사람이 정했다: **정규 배포 과정에 넣고, 최근 2~3개만 남긴다.**

## 무엇을

`deploy/host/deploy.sh` 9단계 — **smoke 가 통과했을 때만** `prune_old_images` 를 부른다(`.guides/web/deployment.md §5.1`).

- 저장소마다 **만든 시각으로 최근 3개**(태그 글자순이 아니다 — `v0.9` 와 `v0.10` 은 글자순으로 거꾸로다).
- 지금 판(`$VER`)과 되돌리기에 쓸 앞 판(`$PREV`)은 개수와 상관없이 남긴다. 파이프라인 이미지는 `.env` 의 `PIPELINE_TAG` 를 지킨다.
- 컨테이너(멈춘 것·시험 인스턴스 포함)가 쓰는 이미지는 남긴다 — 지우려 해도 docker 가 거절하지만 목록에서 먼저 뺀다.
- 못 지운 것은 경고만. **정리 실패가 배포를 실패로 만들지 않는다.** `set -euo pipefail` 아래에서 지울 것이 없으면 `grep` 이 1 을 내 배포가 죽는 것을 시험 중에 보고 `|| true` 로 막았다.
- `PRUNE_DRY_RUN=1` 이면 목록만, `PRUNE_KEEP=0` 이면 건너뛴다.

## 버린 것

- 따로 `prune_images.sh` 를 두기 — `sync_to_srv.sh` 의 파일 목록 두 곳(이미지에서·저장소에서)을 같이 고쳐야 해서 deploy.sh 안의 함수로 했다.
- smoke 실패에도 정리하기 — 되돌리기에 앞 판 이미지가 필요하다.
- `docker system prune` — 다른 저장소의 것까지, 볼륨·네트워크까지 건드린다. 태그 없는 이미지만 `docker image prune -f`.

## 확인

`bash -n` 통과. 실제 이미지로 dry-run(forgia 2개·forgia-pipeline 1개 → 지울 것 없음). 다음 배포부터 돈다 — `/srv/ForGIA/bin/deploy.sh` 는 `sync_to_srv.sh --from-image` 가 이미지에서 꺼내므로 이 변경이 실린 판이 나간 **다음** 배포에서 처음 정리한다.
