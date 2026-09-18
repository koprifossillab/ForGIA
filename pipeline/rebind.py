#!/usr/bin/env python3
"""DiaRUGA v0.29.0 `migrate/rebind.py` 에서 왔다 — `pipeline/` 에 둔다(`segment_forams` 가 저장마다 부른다 · /srv 로 간다).

검출을 다시 돌려도 사람의 교정이 개체를 계속 가리키게 한다.

교정은 `Candidate` 가 아니라 **`(image, batch, mask_key)`** 에 붙는다. 그래서
검출을 다시 돌려 `Candidate` 행이 통째로 바뀌어도 교정 자체는 사라지지 않는다.
다만 `ObjectReview.candidate` 링크는 끊기므로 다시 맺어 줘야 하고, 그것이 이
파일이다.

**한 묶음 안에서만 다시 맺는다** (DiaRUGA P09 0단계). 묶음을 넘는 것은 이 파일이 하는
일이 아니다 — `removed`·`accepted` 는 애초에 넘으면 안 되고, `label`·`note` 는
넘되 **사람의 확인을 거친다.**

세 가지로 맺는다:

| 방법 | 언제 | bind_score |
|---|---|---|
| `exact` | 새 검출에 같은 `mask_key` 가 있다 | 1.0 |
| `iou` | 없지만 bbox 가 충분히 겹치는 개체가 있다 | 그 IoU |
| `orphan` | 아무것도 없다 | null |

**`orphan` 은 손실이 아니다.** 교정 행은 `geom` 에 기하를 스스로 들고 있어
검출기가 바뀌어도 읽힌다 — 지운 것도 학습의 음성 표본이다. 다만 뷰어가 그 개체를
못 그리므로 **조용히 넘기면 안 된다.** 부르는 쪽이 개수를 반드시 보고한다.

`manual` 은 건드리지 않는다. 사람이 직접 맺어 준 것을 기계가 다시 풀면 안 된다.

**같은 개체를 둘이 가져가지 않는다.** exact 를 먼저 전부 맺고, 남은 교정만
아직 임자 없는 개체와 IoU 로 맺는다. 점수가 높은 짝부터 가져간다.

이 규칙을 한 곳에만 두는 이유는 `judge.py` 와 같다 — `segment_forams.py`,
`group_focus_series.py`(DiaRUGA P02 6단계), 고아 화면(8단계)이 전부 이것을 쓴다.
"""
from collections import Counter


def mask_key(bbox) -> str:
    """review/*.json 과 뷰어 keyOf() 가 쓰는 규칙과 같아야 한다.

    import_json.py 에도 같은 것이 있다. 그쪽은 6단계가 끝나면 사라진다.
    """
    return "_".join(str(int(v)) for v in bbox)


def key_to_bbox(key: str):
    """mask_key 는 bbox 를 그대로 이어 붙인 것이라 되돌릴 수 있다.

    geom 이 비어 있는 옛 교정 행에서도 기하를 얻으려고 둔다.
    """
    try:
        x, y, w, h = (int(v) for v in key.split("_"))
    except ValueError:
        return None
    return [x, y, w, h]


def bbox_iou(a, b) -> float:
    """xywh 두 상자의 IoU."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def review_bbox(review):
    """교정 행의 기하. geom 이 원칙이고, 없으면 mask_key 에서 되살린다."""
    box = (review.geom or {}).get("bbox")
    if box and len(box) == 4:
        return [int(v) for v in box]
    return key_to_bbox(review.mask_key)


def rebind_viewpoint(viewpoint, detection, iou_min: float = 0.5) -> Counter:
    """**그 이미지·그 묶음의** 교정을 새 검출의 개체에 다시 맺는다.

    **부르는 쪽이 `is_current` 이동과 같은 트랜잭션에 넣어야 한다.** 중간에
    끊기면 뷰어가 "교정이 붙지 않은 새 검출"을 보여준다.

    ## 범위가 시야가 아니다 (DiaRUGA P09 0단계)

    예전에는 `filter(viewpoint=…)` 로 **그 시야의 교정을 전부** 가져왔다.
    `save_review` 는 P06 5a 에서 이미지로 좁혔는데 여기는 같이 안 좁혔고,
    시야마다 이미지가 하나라 안 드러났다.

    프레임별 검토로 가면 그 길로 **합성본 위의 교정이 프레임 검출에 옮겨
    붙는다** — 같은 시야라 좌표계가 같아서 IoU 가 실제로 맞는다. 묶음도 마찬가지다:
    SAM2 에 대고 "오검출" 이라 한 판단이 YOLO 의 통과 후보에 얹히면 사람은
    **자기가 지우지 않은 것이 지워져 있는 것**을 본다(실측 1,076건).

    **`batch` 가 없는 교정은 건드리지 않는다** — 사람이 그린 개체다(DiaRUGA P09 5.2).
    엔진에 대한 판단이 아니라 이미지에 대한 사실이라 다시 맺을 것이 없다.

    > **다른 묶음으로 넘어가는 `label`·`note` 물려주기는 여기가 아니다.** 그것은
    > 새 행을 만드는 일이고 **사람의 확인을 거친다**. 이 함수는 한 묶음 안에서
    > 검출을 다시 돌렸을 때 링크를 잇는 것만 한다.

    바뀐 행만 저장한다. 돌려주는 Counter 에 방법별 개수가 담긴다.
    """
    from viewer.models import ObjectReview          # 순환 임포트를 피한다

    batch = detection.batch
    if batch is None:
        # 묶음에 안 든 검출이다. 그 검출을 보고 한 교정도 batch 가 없는데,
        # 그 자리는 사람이 그린 개체의 자리이기도 하다 — 섞으면 안 된다.
        # 이런 실행은 지금까지 전부 검출을 하나도 안 남겼다(HANDOFF).
        return Counter()

    reviews = list(ObjectReview.objects.filter(image_id=detection.image_id,
                                               batch=batch))
    if not reviews:
        return Counter()

    cands = list(detection.candidates.all())
    by_key = {c.mask_key: c for c in cands}

    stat = Counter()
    claimed = set()
    left = []

    # 1) exact — 같은 mask_key 가 있으면 그것이다. 먼저 전부 가져간다.
    for r in reviews:
        if r.bind_method == "manual":
            stat["manual"] += 1
            continue
        c = by_key.get(r.mask_key)
        if c is not None:
            _set(r, c, "exact", 1.0)
            claimed.add(c.pk)
            stat["exact"] += 1
        else:
            left.append(r)

    # 2) iou — 남은 교정을 아직 임자 없는 개체와 맺는다. 점수 높은 짝부터.
    if left:
        free = [c for c in cands if c.pk not in claimed]
        pairs = []
        for r in left:
            rb = review_bbox(r)
            if rb is None:
                continue
            for c in free:
                s = bbox_iou(rb, c.bbox_xywh)
                if s >= iou_min:
                    pairs.append((s, r, c))
        pairs.sort(key=lambda t: -t[0])
        taken_r, taken_c = set(), set()
        for s, r, c in pairs:
            if id(r) in taken_r or c.pk in taken_c:
                continue
            _set(r, c, "iou", s)
            taken_r.add(id(r))
            taken_c.add(c.pk)
            stat["iou"] += 1
        left = [r for r in left if id(r) not in taken_r]

    # 3) orphan — 뷰어가 못 그린다. geom 은 남아 있으므로 되살릴 수는 있다.
    for r in left:
        _set(r, None, "orphan", None)
        stat["orphan"] += 1

    ObjectReview.objects.bulk_update(
        [r for r in reviews if getattr(r, "_rebind_dirty", False)],
        ["candidate", "bind_method", "bind_score", "geom"],
        batch_size=1000)
    return stat


def _set(review, cand, method, score):
    """바뀐 것이 있을 때만 표시해 둔다 — 안 바뀐 행까지 쓰지 않으려고."""
    cand_id = cand.pk if cand is not None else None
    geom = review.geom
    if cand is not None and not geom:
        # 기하는 모든 교정 행이 스스로 들고 있어야 한다 (DiaRUGA P02 §2.7)
        geom = {"bbox": cand.bbox_xywh, "polygon": cand.polygon}
    if (review.candidate_id == cand_id and review.bind_method == method
            and review.bind_score == score and review.geom == geom):
        return
    review.candidate_id = cand_id
    review.bind_method = method
    review.bind_score = score
    review.geom = geom
    review._rebind_dirty = True
