"""개체 판정 규칙. **여기가 유일한 정의다.**

`segment_forams.py`(검출 직후)와 `refilter.py`(문턱 재조정)가 같은 함수를 쓴다 —
두 곳에 적어 두면 어긋나고, 어긋나면 "다시 걸렀더니 결과가 달라졌다" 는 일이 조용히
생긴다. DiaRUGA `judge.py` 와 같은 자리이고 같은 꼴(`Thresholds`·`classify`·
`apply`·`collapse_boxes`·`dedupe`)이다 — 뷰어의 문턱 화면·`check_db` 1번이 그
꼴을 본다.

## 규조와 다른 것 (P01 2절 ②)

DiaRUGA 의 관문은 셋이었다 — 크기 → 텍스처(areolae 주기 구조) → 형태(타원
IoU·볼록성·신장비로 원형/봉상). 투명한 규조각을 쇄설물·티끌에서 가르는 데 그
셋이 다 필요했다. 유공충은 **불투명하고 픽킹해 놓아 서로 떨어져 있어** 검출기가
낸 것이 곧 후보다. 남는 관문은 둘뿐이다:

    크기     타원 장축 µm 가 [min_um, max_um] 안인가 — 픽킹 분획(>63 µm)과 맞물린다
    확신도   검출기 conf 가 conf_min 이상인가 (`predicted_iou` 자리에 든 값)

"유공충인가 · 온전한가 · 무엇인가" 는 여기서 안 정한다 — 분류기(4단계)와 사람의
몫이고 `ClassDef`(형태·보존)·`Taxon`(종)으로 간다. 그래서 `classify` 가 내는
분류는 **`foram` 하나**다. 형태 지표(`elongation`·`solidity`·`ellipse_iou`)는
계속 재서 남긴다 — 관문이 아니라 나중에 볼 값이다.

**torch·cv2 에 기대지 않는다.** 문턱 재조정은 GPU 가 필요 없는 일이고, 뷰어와
처리를 갈라 담을 때 이 규칙은 가벼운 쪽에 있어야 한다.

받는 레코드는 dict 다. 필요한 값: `shape_ok · major_um · long_side_um ·
predicted_iou · bbox_xywh · area_px`.
"""

# 판정 문턱의 기본값. segment_forams.py 의 argparse 와 DB 의 ThresholdSet 이
# 이 값을 함께 쓴다. **실사진으로 다시 잡을 값이다** (P01 5절) — 지금 것은
# 픽킹 분획 >63 µm 와 합성 자료의 크기 범위(125~300 µm)를 보고 넉넉히 둔 것.
DEFAULTS = {
    "min_um": 63.0,
    "max_um": 2000.0,
    "conf_min": 0.25,
}
FIELDS = tuple(DEFAULTS)

# 판정이 내는 분류. `ClassDef.key` 와 같아야 한다 (migrations/0003 이 심는다).
PASS_CLS = "foram"


class Thresholds:
    """문턱 묶음. argparse.Namespace 처럼 속성으로 읽힌다.

    classify() 가 args 를 속성으로 읽으므로 Namespace 도 이 클래스도 그대로 쓸 수
    있다 — 스크립트와 DB 양쪽에서 같은 함수를 부를 수 있어야 한다.
    """

    def __init__(self, **kw):
        for f in FIELDS:
            setattr(self, f, kw.get(f, DEFAULTS[f]))

    def as_dict(self):
        return {f: getattr(self, f) for f in FIELDS}

    def __eq__(self, other):
        return isinstance(other, Thresholds) and self.as_dict() == other.as_dict()

    def __repr__(self):
        return f"Thresholds({self.as_dict()})"


def classify(r, args):
    """(분류, 탈락사유). 분류가 None 이면 후보가 아니다.

    크기는 적합 타원의 장축으로 본다. bbox 긴 변은 비스듬히 누운 물체에서
    실제보다 커진다 (DiaRUGA 가 그렇게 4 µm 짜리를 10 µm 관문으로 통과시켰다).
    형태를 못 낸 마스크(`shape_ok=False`)는 판정 불가다 — 그 마스크로는 계측도
    못 한다.
    """
    if not r.get("shape_ok"):
        return None, "형태측정불가"
    major = r.get("major_um")
    if major is not None and not (args.min_um <= major <= args.max_um):
        return None, "장축범위밖"
    conf = r.get("predicted_iou")
    conf_min = getattr(args, "conf_min", None)
    if conf_min is not None and conf is not None and conf < conf_min:
        return None, "확신도부족"
    return PASS_CLS, None


def _cover(a, b):
    """a 의 bbox 가 b 안에 들어간 비율 (a 면적 기준)."""
    ax, ay, aw, ah = a["bbox_xywh"]
    bx, by, bw, bh = b["bbox_xywh"]
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return ix * iy / max(aw * ah, 1)


def collapse_boxes(records):
    """**bbox 가 같은 레코드를 하나로 접는다.** `(남긴 것, 버린 수)` 를 돌려준다.

    판정을 하기 **전에** 불러야 한다. DB 의 열쇠가 `mask_key`(= bbox 문자열)라
    같은 bbox 는 한 행밖에 못 들어가는데, 판정한 뒤에 넣으면서 버리면 **판정이
    본 집합과 저장된 집합이 달라진다** — `check_db` 1번이 아무리 다시 계산해도
    안 맞는다 (DiaRUGA 077). 면적이 큰 것을 남기고, 같으면 먼저 온 것을 둔다 —
    어느 쪽이든 **결정적**이어야 한다.
    """
    seen, out, dropped = {}, [], 0
    for r in records:
        key = tuple(int(v) for v in r["bbox_xywh"])
        prev = seen.get(key)
        if prev is None:
            seen[key] = len(out)
            out.append(r)
            continue
        dropped += 1
        if (r.get("area_px") or 0) > (out[prev].get("area_px") or 0):
            out[prev] = r
    return out, dropped


def dedupe(selected):
    """중첩 마스크 정리.

    YOLO 는 NMS 를 거쳐 나오지만 **작은 것이 큰 것 안에 든** 짝은 IoU 가 작아
    NMS 가 못 잡는다 (DiaRUGA 실측 중앙값 0.07). 픽킹 슬라이드에서는 붙어 놓인
    개체 둘을 하나로 감싼 마스크가 그 꼴이다 — 자식 2개 이상이 자기 면적의 절반
    이상을 설명하면 개별 개체가 아니라 덩어리로 보고 버린다.

    거의 같은 마스크는 하나로 — **확신도가 높은 쪽**을 남긴다 (DiaRUGA 는 텍스처).
    """
    keep = []
    for a in selected:
        kids = [b for b in selected
                if b is not a and b["area_px"] < a["area_px"] and _cover(b, a) > 0.85]
        if len(kids) >= 2 and sum(b["area_px"] for b in kids) > 0.5 * a["area_px"]:
            continue
        keep.append(a)

    keep.sort(key=lambda r: -(r.get("predicted_iou") or 0))
    out = []
    for r in keep:
        if not any(_cover(r, k) > 0.8 and _cover(k, r) > 0.8 for k in out):
            out.append(r)
    return out


def apply(records, args):
    """레코드 묶음에 판정을 건다. (통과분, 탈락분) 을 돌려준다."""
    passed, rejected = [], []
    for r in records:
        cls, why = classify(r, args)
        if cls:
            r["cls"] = cls
            passed.append(r)
        else:
            r["cls"] = None
            r["reject"] = why
            rejected.append(r)

    kept = dedupe(passed)
    keep_ids = {id(r) for r in kept}
    for r in passed:
        if id(r) not in keep_ids:
            # 중첩정리로 떨어진 것은 판정을 통과한 뒤 정리된 것이라 cls 를 남긴다
            r["reject"] = "중첩정리"
            rejected.append(r)
    return kept, rejected
