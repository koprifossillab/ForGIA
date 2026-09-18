"""DiaRUGA v0.29.0 `web/viewer/regroup.py` 에서 왔다 — 그대로.

시야를 프레임 경계에서 다시 가른다.

**그룹핑이 틀렸을 때 고치는 길은 여기 하나다** — 화면(`/d/<slug>/g/<n>/`)도
CLI(`resplit.py`)도 이 모듈을 부른다. 두 벌로 두면 한쪽만 고쳐지고, 그 종류의
어긋남은 사람이 화면을 눌러 보기 전까지 안 드러난다.

## 왜 부분 재분할인가

지금까지 있던 길은 `group_focus_series.py --force` 뿐이었는데 그것은 **슬라이드
전체를 다시 묶는다** — `slide.viewpoints.all().delete()` 한 줄에 그 슬라이드의
검출·교정이 통째로 딸려 간다. `am22-gc10b_25cm` 은 시야 26개 중 **4개**가
틀렸는데 `--force` 를 주면 26개가 다 날아가고 교정 100건이 사라진다. 4개를
고치려고 22개를 버리는 꼴이다.

여기서는 **가르는 시야만 지운다.** 나머지는 애초에 건드리지 않으므로 그 아래
검출·교정은 지킬 필요조차 없다 — 그대로 남아 있다.

## 지워지는 것은 진짜로 지워진다

`ObjectReview`·`ViewpointReview`·`Detection`·`Stack` 이 전부 `Viewpoint` 를
`CASCADE` 로 문다. 오래도록 `--force` 안내문이 "교정은 mask_key 로 남지만 고아가
된다" 고 적혀 있었는데 **거짓이었다** — `mask_key` 는 칼럼 값일 뿐이고 행이 없으면
아무것도 아니다.

그래서 이 도구의 값은 교정을 지키는 데 있지 않고 **다시 볼 것을 몇 개로 줄이는
데** 있다. 가른 시야는 사람이 다시 검토해야 한다. 부르는 쪽은 `preview()` 로
무엇이 사라지는지 **먼저 보여 주고** 확인을 받아야 한다.

## 밟을 곳 셋

- **`tag` 는 합성본 파일 이름을 낳는다** (`focus_stack.py` 가 `<tag>_focused.jpg`
  로 쓰고 `find_viewpoint()` 가 그것을 되읽는다). 그래서 **살아남는 시야의 `tag`
  는 건드리지 않는다.** 딸려 오는 것: `tag` 의 `g###` 접두가 `idx` 와 어긋날 수
  있다. 접두는 **만들어질 때의 번호**이고 신원은 `tag` 문자열 자체다 — 맞추려고
  이름을 바꾸면 디스크의 파일과 갈라진다
- **`idx` 는 다시 매긴다.** `(slide, idx)` 가 유일 제약이라 중간에 끼워 넣을 수
  없고, 무엇보다 뷰어가 이 순서로 시야를 늘어놓는다. 갈라진 조각을 목록 끝으로
  보내면 촬영 순서가 깨져 검토가 어려워진다. 충돌을 피해 **전부 큰 값으로 옮긴 뒤
  0부터 다시** 매긴다
- **하류는 손댈 것이 없다.** `focus_stack` 은 `Stack` 이 없는 시야만, `segment_
  diatoms` 는 `is_current` 검출이 없는 시야만 처리한다. 새로 생긴 시야가 정확히
  그것이다. 슬라이드를 `processing` 으로 돌려놓으면 **폴러가 1분 안에 이어서 하고**
  `mark_done_if_complete()` 가 `done` 으로 연다. 폴러는 `pending` 일 때만 다시
  묶으므로 방금 한 분할을 되돌리지 않는다
"""
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .images import ensure_frame_image
from .models import Frame, Run, Viewpoint

# idx 를 다시 매기는 동안 유일 제약에 걸리지 않도록 잠깐 치워 두는 자리.
# 슬라이드 하나의 시야 수보다 훨씬 크면 된다 (가장 큰 슬라이드가 86개다).
IDX_PARK = 100000


def resolve_cuts(slide, specs):
    """자를 자리를 프레임으로 푼다. → (`{viewpoint_id: {프레임 이름}}`, 문제 목록)

    **하나라도 이상하면 부르는 쪽이 아무것도 하지 않아야 한다.** 절반만 갈라진
    슬라이드가 남으면 무엇이 옳은 상태인지 알 수 없게 된다.

    이름(`Snap-22131`)도 번호(`22131`)도 받는다. **슬라이드를 반드시 함께 건다** —
    프레임 이름은 카메라 일련번호라 슬라이드 사이에서 겹친다 (devlog 031).
    """
    cuts, bad = {}, []
    for spec in specs:
        spec = str(spec).strip()
        if not spec:
            continue
        fr = Frame.objects.filter(slide=slide, name=spec).first()
        if fr is None and spec.isdigit():
            fr = Frame.objects.filter(slide=slide, name=f"Snap-{spec}").first()
        if fr is None:
            bad.append(f"{spec}: 그런 프레임이 없다")
            continue
        if fr.viewpoint_id is None:
            bad.append(f"{fr.name}: 시야에 안 붙어 있다")
            continue
        tail = fr.viewpoint.frames.order_by("seq").last()
        if tail and tail.pk == fr.pk:
            bad.append(f"{fr.name}: 이미 시야 g{fr.viewpoint.idx} 의 마지막 "
                       f"프레임이다 — 여기서 가를 것이 없다")
            continue
        cuts.setdefault(fr.viewpoint_id, set()).add(fr.name)
    return cuts, bad


def build_plan(slide, cuts):
    """슬라이드의 최종 배열. → `[("keep"|"new", 원래 시야, 프레임들), ...]`

    안 건드리는 시야도 그대로 실어 나른다 — `idx` 를 다시 매기려면 전체 순서가
    한자리에 있어야 한다.
    """
    plan = []
    for vp in slide.viewpoints.order_by("idx"):
        frames = list(vp.frames.order_by("seq"))
        here = cuts.get(vp.pk)
        if not here:
            plan.append(("keep", vp, frames))
            continue
        runs, cur = [], []
        for f in frames:
            cur.append(f)
            if f.name in here:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        for piece in runs:
            plan.append(("new", vp, piece))
    return plan


def preview(slide, specs) -> dict:
    """무엇이 사라지고 무엇이 생기는지. **DB 를 건드리지 않는다.**

    화면은 이것을 확인 페이지로 그리고, CLI 는 이것을 찍는다. 확인을 거치지 않고
    `apply()` 로 바로 가는 길은 두지 않는다 — 검토가 끝난 시야를 지우는 일이다.
    """
    cuts, bad = resolve_cuts(slide, specs)
    if bad:
        return {"ok": False, "errors": bad, "splits": [], "totals": {}}
    if not cuts:
        return {"ok": False, "errors": ["자를 자리를 하나도 고르지 않았다"],
                "splits": [], "totals": {}}

    plan = build_plan(slide, cuts)
    before = slide.viewpoints.count()
    splits, tot_det, tot_rev = [], 0, 0
    for vp in slide.viewpoints.order_by("idx"):
        if vp.pk not in cuts:
            continue
        n_det = vp.detections.count()
        n_rev = vp.object_reviews.count()
        tot_det += n_det
        tot_rev += n_rev
        splits.append({
            "idx": vp.idx, "tag": vp.tag,
            "detections": n_det, "object_reviews": n_rev,
            # 완료는 묶음마다다(073) — 여기서는 **어느 묶음에서든** 본 적이
            # 있는가를 알리면 된다. 가르면 사람이 잃는 것을 덜 세게 된다.
            "reviewed": vp.reviews.filter(done=True).exists(),
            "stack": hasattr(vp, "stack"),
            "pieces": [[f.name for f in p]
                       for k, o, p in plan if k == "new" and o.pk == vp.pk],
        })
    created = sum(1 for k, _, _ in plan if k == "new")
    return {
        "ok": True, "errors": [],
        "before": before, "after": len(plan),
        "splits": splits, "untouched": before - len(cuts),
        "totals": {"split": len(cuts), "created": created,
                   "detections": tot_det, "object_reviews": tot_rev},
    }


@transaction.atomic
def apply_split(slide, specs, source: str = "") -> dict:
    """실제로 가른다. 한 트랜잭션이다.

    중간에 끊기면 시야 절반만 있는 슬라이드가 남고, 그 위에 검출을 돌리면 나머지
    절반이 조용히 빠진다 — `group_focus_series.save_grouping()` 이 통째로 한
    트랜잭션인 이유와 같다.
    """
    cuts, bad = resolve_cuts(slide, specs)
    if bad:
        raise ValueError(" · ".join(bad))
    if not cuts:
        raise ValueError("자를 자리를 하나도 고르지 않았다")

    plan = build_plan(slide, cuts)
    before = slide.viewpoints.count()
    doomed = [vp for vp in slide.viewpoints.order_by("idx") if vp.pk in cuts]
    lost_det = sum(vp.detections.count() for vp in doomed)
    lost_rev = sum(vp.object_reviews.count() for vp in doomed)
    created = sum(1 for k, _, _ in plan if k == "new")

    run = Run.objects.create(
        kind="group",           # 재분할도 그룹핑이다. 새 종류를 만들면 RUN_KIND
        status="running",       # 마이그레이션이 딸려 온다
        slide=slide,
        params={"tool": "regroup.apply_split", "slide": slide.slug,
                "after": sorted(n for s in cuts.values() for n in s),
                "source": source})

    # 프레임은 `SET_NULL` 이라 시야를 지워도 살아남는다. 살아남아야 한다 —
    # 다시 붙일 것이 그것이다.
    for vp in doomed:
        vp.delete()

    # 유일 제약을 피해 전부 치워 두고 0부터 다시 매긴다
    Viewpoint.objects.filter(slide=slide).update(idx=F("idx") + IDX_PARK)
    taken = set(Viewpoint.objects.filter(slide=slide)
                .values_list("tag", flat=True))

    for i, (kind, old, frames) in enumerate(plan):
        if kind == "keep":
            old.idx = i                       # tag 는 건드리지 않는다
            old.save(update_fields=["idx"])
            continue
        a, b = frames[0].acquired_at, frames[-1].acquired_at
        vp = Viewpoint.objects.create(
            slide=slide, idx=i, tag=_tag(i, frames, taken),
            n_frames=len(frames),
            span_sec=((b - a).total_seconds() if a and b else None),
            grouping_run=run)
        best = max(frames, key=lambda f: f.sharpness or 0.0)
        for f in frames:
            f.viewpoint = vp
            f.is_sharpest = (f.pk == best.pk)
            f.save(update_fields=["viewpoint", "is_sharpest"])
            # **이미지 행도 새 시야를 따라간다** (P06). 프레임은 살아남고
            # 시야만 갈리므로, 안 맞추면 테이블이 디스크와 조용히 어긋난다.
            ensure_frame_image(f)
        vp.sharpest_frame = best
        vp.save(update_fields=["sharpest_frame"])

    # 검출이 없는 시야가 생겼다. `done` 인 채로 두면 뷰어가 빈 화면을 검토하라고
    # 내준다 — 자동 처리가 끝나기 전에는 막아야 한다 (P01 §1).
    slide.state = "processing"
    slide.state_note = f"시야 재분할 — 합성·검출 대기 {created}개"
    slide.save(update_fields=["state", "state_note"])

    run.status = "done"
    run.finished_at = timezone.now()
    run.counts = {"viewpoints_before": before, "viewpoints_after": len(plan),
                  "split": len(doomed), "created": created,
                  "detections_lost": lost_det, "object_reviews_lost": lost_rev}
    run.save()

    return {"before": before, "after": len(plan), "split": len(doomed),
            "created": created, "detections_lost": lost_det,
            "object_reviews_lost": lost_rev, "run_id": run.pk,
            # 가른 첫 조각으로 돌려보낸다 — 사람이 방금 한 일을 눈으로 확인한다
            "first_idx": next(i for i, (k, _, _) in enumerate(plan)
                              if k == "new")}


def _tag(idx: int, frames, taken: set) -> str:
    """`group_focus_series.py` 와 같은 모양으로 짓는다.

    같은 슬라이드 안에서 부딪히면 안 된다 — 부딪히면 `focus_stack` 이 남의
    합성본을 보고 "이미 했다" 며 건너뛴다. 프레임 이름이 유일해서 실제로 부딪힐
    일은 없지만, 조용히 틀리는 자리라 확인하고 넘어간다.
    """
    base = f"g{idx:03d}_{frames[0].name}-{frames[-1].name.split('-')[-1]}"
    tag, n = base, 2
    while tag in taken:
        tag = f"{base}_r{n}"
        n += 1
    taken.add(tag)
    return tag
