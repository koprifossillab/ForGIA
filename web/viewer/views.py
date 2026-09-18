"""뷰. DiaRUGA v0.29.0 `web/viewer/views.py`(3,120줄)에서 0단계에 필요한 둘만
가져왔다 — 목록과 `/healthz`. 나머지 화면은 단계마다 온다 (P01 4절).

`data.py`(DB → 뷰가 쓰는 dict, 읽기 전용) 도 아직 없다. 목록 하나에 그 층을
두는 것은 과해서 여기서 바로 질의한다 — 1단계에서 시야가 붙으면 그때 `data.py`
를 가져오고 이 뷰도 그리로 옮긴다.
"""
import os
import time

from django.conf import settings
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import render

from .models import Locality, Sample, Site, Slide

# `/healthz` 가 세는 테이블. 단계마다 늘어난다 — 1단계에서 `viewpoint`,
# 2단계에서 `detection`, 3단계에서 `objectreview`.
HEALTH_TABLES = [("site", Site), ("locality", Locality),
                 ("sample", Sample), ("slide", Slide)]


def index(request):
    """데이터셋 목록 — 지역 → 지점 → 시료 → 관찰.

    DiaRUGA 의 목록은 권역 탭·표/카드/지도 전환·검토 진행률까지 든 552줄 템플릿인데
    (`index.html`), 0단계에는 층만 있다. 그 화면은 1단계에서 시야와 함께 온다.

    **소속을 잃은 관찰을 따로 센다.** `Slide.sample` 이 `SET_NULL` 이라 시료를
    지우면 관찰이 조용히 소속을 잃고, 층으로 내려가는 목록에는 안 나온다 —
    500 도 404 도 아니라 그냥 사라진다 (DiaRUGA 063). 화면이 그 수를 적는다.
    """
    sites = (Site.objects
             .prefetch_related("localities__samples__slides")
             .annotate(n_slides=Count("localities__samples__slides",
                                      distinct=True)))
    orphans = Slide.objects.filter(sample__isnull=True).count()
    return render(request, "viewer/index.html", {
        "sites": sites,
        "n_slides": Slide.objects.count(),
        "orphans": orphans,
    })


def healthz(request):
    """판·DB·안전망 상태를 한 번에 낸다 (DiaRUGA `/healthz` 와 같은 모양).

    | 상태 | 코드 | 뜻 |
    |---|---|---|
    | `ok` | 200 | 정상 |
    | `degraded` | **200** | 서비스는 되는데 손상이 감지됐다 (백업이 낡았다 등) |
    | `unhealthy` | 503 | DB 를 못 열거나 자료가 통째로 없다 |

    **`degraded` 를 503 으로 두면 안 된다.** `deploy.sh` 의 기동 게이트가 200 을
    기다린다 — degraded 에 503 을 내면 "백업이 깨졌다" 는 신호가 배포 자체를 못
    끝내게 만든다. 배포를 막는 일은 `smoke.sh` 가 `status != ok` 로 한다.

    **가볍게 유지한다.** 인증이 없는 엔드포인트라 비싼 검사를 여기 두면 DoS
    표면이 된다. `count(*)` 넷과 `stat` 하나다.

    **`slide` 가 0 이면 unhealthy 다.** 마운트가 어긋나 컨테이너가 빈 DB 를 새로
    만들어도 "파일이 있는가" 검사는 통과한다 — `rows > 0` 만이 그것을 잡는다.
    0단계의 빈 DB 도 그래서 unhealthy 로 나온다. 맞는 답이다.

    **무결성 깃발(`db_sentinel`)은 1단계에서 온다** — `ops/` 와 함께.
    """
    info = {"status": "ok", "version": os.environ.get("IMAGE_TAG", "")}
    notes = []

    # 1) DB — 연결과 행 수. 못 열면 나머지는 볼 것도 없다.
    try:
        info["db"] = {name: model.objects.count() for name, model in HEALTH_TABLES}
    except Exception as e:                       # noqa: BLE001 — 무엇이 나오든 죽지 않는다
        info["status"] = "unhealthy"
        info["db"] = None
        notes.append(f"DB 를 읽지 못했다: {e}")
    else:
        if info["db"]["slide"] == 0:
            info["status"] = "unhealthy"
            notes.append("슬라이드가 0 이다 — DB 마운트가 어긋났을 수 있다")

    # 2) 백업 신선도. 문턱을 안 주면 알려만 준다 (settings.BACKUP_MAX_AGE_H 주석).
    age_h = None
    try:
        snaps = list(settings.BACKUP_DIR.glob("ForGIA_*.db"))
        if snaps:
            newest = max(snaps, key=lambda p: p.stat().st_mtime)
            age_h = round((time.time() - newest.stat().st_mtime) / 3600, 1)
    except OSError:
        pass
    info["backup"] = {"age_h": age_h, "max_age_h": settings.BACKUP_MAX_AGE_H}
    if settings.BACKUP_MAX_AGE_H and (age_h is None
                                      or age_h > settings.BACKUP_MAX_AGE_H):
        if info["status"] == "ok":
            info["status"] = "degraded"
        notes.append("백업 사본이 없다" if age_h is None else
                     f"백업이 낡았다 — 가장 새 사본이 {age_h} 시간 전 "
                     f"(문턱 {settings.BACKUP_MAX_AGE_H})")

    info["notes"] = notes
    code = 503 if info["status"] == "unhealthy" else 200
    resp = JsonResponse(info, status=code, json_dumps_params={"ensure_ascii": False})
    # 앞단이나 브라우저가 이 답을 캐시하면 지난 상태를 보고 판단하게 된다
    resp["Cache-Control"] = "no-store"
    return resp
