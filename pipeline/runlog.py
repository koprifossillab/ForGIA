#!/usr/bin/env python3
"""DiaRUGA v0.29.0 `pipeline/runlog.py` 에서 왔다. 실행 이력(`Run`)을 열고 닫는 규칙을 한 곳에 둔다.

**닫히지 않은 실행이 남는다.** 컨테이너가 OOM 이나 `docker kill` 로 죽으면
`finally` 가 돌지 않아 `status="running"` 인 채로 영원히 남는다. 실제로 두 건이
세 시간 동안 "돌고 있음" 으로 남아 있었다.

그러면 이력의 뜻이 흐려진다 — "지금 뭐가 돌고 있나" 와 "무엇으로 돌렸었나" 를
같은 테이블에서 물을 수 없게 된다.

**같은 종류는 한 번에 하나만 돈다**(GPU 잠금·flock 이 그것을 보장한다). 그래서
새 실행이 시작할 때 같은 종류의 옛 `running` 을 닫아도 안전하다. 죽은 것을
치우는 일을 살아 있는 것이 한다.
"""
from datetime import timedelta

from django.utils import timezone


def close_stale(kind: str, older_than_min: float = 5.0) -> int:
    """같은 종류의 오래된 `running` 을 `failed` 로 닫는다. 닫은 개수를 돌려준다.

    `older_than_min` 을 두는 이유: 방금 시작한 형제 실행을 닫지 않기 위해서다.
    같은 종류가 겹치지 않는다는 전제가 깨졌을 때 피해를 줄인다.
    """
    from viewer.models import Run                       # 순환 임포트를 피한다

    cut = timezone.now() - timedelta(minutes=older_than_min)
    stale = list(Run.objects.filter(kind=kind, status="running",
                                    started_at__lt=cut))
    for r in stale:
        r.status = "failed"
        r.error = (r.error or "") + (
            " · " if r.error else "") + "끝나지 않은 채 남아 있었다 (다음 실행이 닫음)"
        r.finished_at = timezone.now()
        r.save(update_fields=["status", "error", "finished_at"])
    return len(stale)


def batch(kind: str, label: str, note: str = ""):
    """이름으로 묶음을 찾거나 만든다. 같은 이름이면 같은 묶음이다.

    파이프라인은 슬라이드 단위로 돌기 때문에 "전체를 한 번 훑었다" 는 작업이
    실행 여럿으로 흩어진다. 명령을 슬라이드마다 나눠 부르더라도 같은 이름표를
    주면 한 덩어리로 남는다.
    """
    from viewer.models import RunBatch                  # noqa: PLC0415

    obj, made = RunBatch.objects.get_or_create(
        kind=kind, label=label, defaults={"note": note})
    if made:
        print(f"  묶음 '{label}' 을 새로 만들었다")
    return obj


def start(kind: str, batch_label: str = "", batch_note: str = "", **fields):
    """새 실행을 연다. 옛 것을 먼저 치운다.

    `batch_label` 을 주면 그 이름의 묶음에 매단다 — 여러 슬라이드에 걸친 한 번의
    작업을 나중에 한 덩어리로 보기 위해서다.
    """
    from viewer.models import Run                       # noqa: PLC0415

    n = close_stale(kind)
    if n:
        print(f"  끝나지 않은 {kind} 실행 {n}개를 닫았다")
    if batch_label:
        fields["batch"] = batch(kind, batch_label, batch_note)
    return Run.objects.create(kind=kind, status="running", **fields)
