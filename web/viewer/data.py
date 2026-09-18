"""DB → 뷰가 쓰는 dict. **읽기 전용이라는 약속이 있다** — 쓰는 문은 `manage_data`
와 이 파일의 `save_*`·`merge_*`·`spread_*`(교정을 적는 함수는 DiaRUGA 와 같은
자리에 둔다 — 화면이 부르는 문이 하나여야 해서).

DiaRUGA v0.29.0 `web/viewer/data.py`(5,632줄)에서 왔다. 1단계에 목록·지도·시야·
지점·파이프라인 상태가, 2단계에 검출·집계·크롭 기하가, **3단계에 교정·동정·
카탈로그·묶기·손그림·번지기가** 왔다 — 그쪽 함수 이름·반환 형태를 그대로 두어
DiaRUGA 의 시험·템플릿이 그대로 돈다. 안 온 것: 한국 지도(`_korea_xy`)·노두·
코어 자료 곡선(`core_*`·`compare_*`, 5단계)·도감(`atlas_*`, 5단계).

ForGIA 에서 다른 자리:

- **종명이 문자열이 아니라 `Taxon` 이다.** `save_catalog_entry(species=…)` 는
  이름을 `resolve_taxon` 으로 찾아 FK 로 앉힌다 — 목록에 없는 이름은 거절한다.
  읽는 자리는 `ForamObject.species`(=`taxon.name`) 로 DiaRUGA 와 같다
- **되살린 개체의 짐작 분류는 `foram` 하나다** (`_guess_cls`). DiaRUGA 의 원형/봉상
  신장비 규칙이 없다 — `_SUMMARY_SQL`·`_COVER_SQL` 의 CASE 도 같이 바뀌었다
- 시야에 `cell`(격자 칸) · 관찰에 분획(`_obs`) · 프레임에 배율 출처(`_frames`)
"""
import json
import math
import re
import secrets
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.urls import reverse
from django.db import connection, transaction
from django.db.models import Case, Count, Prefetch, Q, When
from django.utils import timezone

from . import antarctica, catalog
from . import shape
from .models import (Candidate, ClassDef, Detection, ForamObject, Frame,
                     Image, Locality, ObjectReview,
                     Run, RunBatch, Site, Slide, Stack, Taxon, Viewpoint,
                     ViewpointReview)


# --- 분류 정의 -------------------------------------------------------------
# ClassDef 테이블이 원본이다. 다만 매 요청마다 읽을 값이 아니라(거의 바뀌지 않고
# 템플릿·클라이언트가 여러 번 묻는다) 프로세스 수명 동안 캐시한다.
# --- 분류 정의 -------------------------------------------------------------
# ClassDef 테이블이 원본이다. 다만 매 요청마다 읽을 값이 아니라(거의 바뀌지 않고
# 템플릿·클라이언트가 여러 번 묻는다) 프로세스 수명 동안 캐시한다.
_classes = None


def _class_rows():
    global _classes
    if _classes is None:
        _classes = list(ClassDef.objects.filter(active=True)
                        .values("key", "label", "short", "badge", "color",
                                "hotkey", "is_taxon", "counted", "sort_order"))
        # 약칭이 비면 전체 이름으로 메운다. **읽는 쪽마다 이 판단을 되풀이하지
        # 않게 여기서 한 번만 한다** — 한 곳이라도 빠뜨리면 그 화면만 빈칸이 된다.
        for r in _classes:
            r["short"] = r["short"] or r["label"]
    return _classes


def invalidate_classes():
    """분류 정의를 고쳤을 때 부른다."""
    global _classes
    _classes = None


def class_list() -> list[dict]:
    """분류 목록. 템플릿·클라이언트가 메뉴를 만들 때 쓴다.

    **`counted` 가 함께 간다** (183). 등급·자세는 완형에만 매기는데(`check_grade_pose`),
    검토 화면의 카탈로그 칸이 그 두 줄을 감추려면 어느 분류가 완형인지 알아야
    한다 — 카탈로그 화면은 `counted_keys` 를 따로 받아 쓰지만, 이쪽은 분류를
    **화면에서 바꿔 가며** 보므로 목록에 같이 실려야 한다.
    """
    return [{"key": r["key"], "label": r["label"], "short": r["short"],
             "badge": r["badge"], "color": r["color"], "taxon": r["is_taxon"],
             "hotkey": r["hotkey"], "counted": bool(r["counted"])}
            for r in _class_rows()]


def hotkey_groups() -> dict:
    """단축키 하나가 도는 분류들. 검토 화면의 안내 한 줄이 이것으로 그려진다.

    같은 키를 나눠 가진 분류는 누를 때마다 차례로 돈다(표 순서). 그래서 묶음의
    첫 분류가 "한 번 누르면 되는 것" 이고 나머지가 "더 누르면 나오는 것" 이다.

    **안내를 손으로 적지 않는 이유.** 분류를 더하면서 안내만 옛 목록으로 남는
    일이 이미 두 번 있었다(038 의 개수 줄, 목록의 분류 열). 키를 안 준 분류는
    안내에도 안 나온다 — 그것이 곧 "아직 안 배정했다" 는 표시다.
    """
    order, groups = [], {}
    for r in _class_rows():
        hot = (r["hotkey"] or "").strip()
        if not hot:
            continue
        if hot not in groups:
            order.append(hot)
            groups[hot] = []
        groups[hot].append({"key": r["key"], "label": r["label"],
                            "short": r["short"]})
    rows = [{"hotkey": h, "classes": groups[h]} for h in order]
    # `cycles` 는 "다시 누르면 넘어간다" 는 설명을 낼지 정한다. 도는 묶음이
    # 하나도 없으면 그 설명은 가리킬 것이 없다.
    return {"groups": rows, "cycles": any(len(g["classes"]) > 1 for g in rows)}


def counted_classes() -> list[dict]:
    """개체 수로 세는 분류. 목록 화면의 "검출" 칸이 더하는 것이 이것이다.

    **여기 없는 것은 파편과 미분류다.** 파편은 `ClassDef.counted=False` 로 꺼져
    있고, 미분류는 분류가 없어 애초에 어느 칸에도 들어가지 않는다. 둘 다 개체
    하나로 세면 밀도가 부풀기 때문에 뺀다.

    목록 표의 **열 머리**도 이 목록으로 만든다 — 슬라이드마다 0인 분류를 빼면
    줄마다 열 수가 달라져 세로로 안 맞는다. 비교하려고 표로 만든 화면이다.
    """
    return [{"key": r["key"], "label": r["label"], "short": r["short"]}
            for r in _class_rows() if r["counted"]]


def is_fragment(cls: str) -> bool:
    """파편으로 지정된 것인가 (`ClassDef.counted=False`).

    **분류가 없는 것은 파편이 아니다.** 아직 안 정했을 뿐이고, 정하면 완형일 수
    있다 — 미분류를 파편으로 묶으면 카탈로그에서 통째로 사라지고 동정 진행도의
    분모에서도 빠진다. **모르는 것과 아닌 것은 다르다.**
    """
    if not cls:
        return False
    row = next((r for r in _class_rows() if r["key"] == cls), None)
    return row is not None and not row["counted"]


def catalog_done(r: dict) -> bool:
    """카드 하나가 **동정 완료**인가 (사용자 기준 2026-08-11).

    종명만으로는 안 되고 **등급·자세까지 다 차야** 완료다. 종명은 무엇인지를
    말하고 등급·자세는 그것을 학습에 쓸 수 있는지를 말한다 — 진행도가 재려는
    것은 "이 개체에 대해 할 말을 다 했는가" 이지 이름만 붙였는가가 아니다.

    **파편은 이 물음의 대상이 아니다** — 부르는 쪽이 `is_fragment` 로 먼저 뺀다.
    """
    return bool(r.get("species") and r.get("grade") and r.get("pose"))


class _LabelMap(dict):
    """없는 키를 물어도 빈 문자열 — 템플릿에서 쓰기 편하게."""

    def __missing__(self, key):
        return ""


def _labels():
    return _LabelMap((r["key"], r["label"]) for r in _class_rows())


def _shorts():
    return _LabelMap((r["key"], r["short"]) for r in _class_rows())


def _badges():
    return _LabelMap((r["key"], r["badge"]) for r in _class_rows())


def _count_parts(counts: dict) -> list[str]:
    """개수 줄에 적을 토막들 — `["rod 12", "eucampia 3", …]`.

    **분류 목록을 손으로 적지 않는다.** 예전에는 부르는 자리마다 다섯을 박아
    두었는데(`rod`·`round`·`rod_frag`·`round_frag`·`eucampia`), **Chaetoceros 를
    더하면서 그 자리를 안 고쳐 개수 줄에서만 빠져 있었다** — 예외도 경고도 없이
    그 분류만 조용히 안 세어진다(`ClassDef` 머리말이 말하는 그 자리다).

    표의 차례(`sort_order`)를 그대로 따르므로 새 분류를 넣으면 저절로 낀다.
    """
    return [f"{k} {counts[k]}" for k in _class_rows_keys() if counts.get(k)]


def _class_rows_keys():
    return [r["key"] for r in _class_rows()]


def __getattr__(name):
    """CLASS_LABELS 같은 모듈 수준 이름을 유지한다(템플릿태그가 그렇게 쓴다)."""
    if name == "CLASS_LABELS":
        return _labels()
    if name == "CLASS_SHORT":
        return _shorts()
    if name == "CLASS_BADGE":
        return _badges()
    if name == "CLASSES":
        return tuple(r["key"] for r in _class_rows())
    if name == "TAXON_CLASSES":
        return tuple(r["key"] for r in _class_rows() if r["is_taxon"])
    raise AttributeError(name)


# bbox 로 만든 개체 키. 뷰어의 keyOf() 와 같은 규칙을 쓴다.
CAND_KEY = re.compile(r"^-?\d+_-?\d+_-?\d+_-?\d+$")


def cand_key(c) -> str:
    """마스크의 안정적인 식별자. dict 와 Candidate 둘 다 받는다.

    **dict 에 `key` 가 있으면 그것이 먼저다** (P09 2단계). 지금까지는 키가 곧
    bbox 문자열이라 기하에서 다시 만들어도 같았다. 그런데 **후보 없이 교정만
    남은 개체**는 그 등식이 깨질 수 있다 — 사람이 기하를 고치면(4단계) bbox 는
    바뀌고 키는 그대로여야 한다. 기하에서 키를 만들면 그 순간 **다른 개체가
    되어 옛 행이 지워진다.**

    화면의 `keyOf()` 도 같은 규칙이다. 둘이 갈라지면 화면이 보내는 키와 서버가
    아는 키가 어긋나고, `/review` 는 모르는 키를 지운다.
    """
    if isinstance(c, dict):
        if c.get("key"):
            return c["key"]
        b = c.get("bbox_xywh") or [0, 0, 0, 0]
    else:
        b = c.bbox_xywh
    return "_".join(str(int(v)) for v in b)


def stamp(rel: str) -> int:
    """이미지의 mtime. URL 에 넣어 "내용이 바뀌면 주소도 바뀌게" 만든다."""
    try:
        return int((Path(settings.DATA_ROOT) / rel).stat().st_mtime)
    except (OSError, TypeError, ValueError):
        return 0


# --- 개체 dict --------------------------------------------------------------
NUM_FIELDS = ("area_um2", "major_um", "minor_um", "long_side_um",
              "short_side_um", "aspect_ratio", "fill_ratio", "circularity",
              "convexity", "solidity", "elongation", "ellipse_iou",
              "texture", "predicted_iou", "stability_score")


def _cand_dict(c: Candidate) -> dict:
    """Candidate -> 예전 JSON 과 같은 모양의 dict.

    통과분의 id 는 아래에서 면적 순으로 다시 매기고, 탈락분은 원시 순번(raw_id)을
    그대로 쓴다 — 예전 JSON 이 그랬고, 내보내기로 되돌릴 수 있어야 한다.
    """
    d = {
        # **키를 늘 실어 보낸다** (P09 4단계). 지금까지는 기하에서 다시 만들어도
        # 같았는데, 사람이 경계를 고치면 bbox 가 바뀌고 키는 그대로여야 한다 —
        # 기하에서 만들면 그 순간 **다른 개체가 되어 옛 행이 지워진다.**
        # 안 고친 개체에서도 값이 같으므로 늘 넣어 그 갈래를 아예 없앤다.
        "key": c.mask_key,
        "bbox_xywh": [c.bbox_x, c.bbox_y, c.bbox_w, c.bbox_h],
        "center_xy": [c.center_x, c.center_y],
        "area_px": c.area_px,
        "shape_ok": c.shape_ok,
        "polygon": c.polygon,
    }
    for f in NUM_FIELDS:
        d[f] = getattr(c, f)
    # 확신도는 YOLO conf — DiaRUGA 는 SAM2 자리 이름(`predicted_iou`)을 그대로 뒀다.
    # 화면이 읽기 쉬운 이름으로 한 번 더 싣는다
    d["conf"] = c.predicted_iou
    if c.passed:
        d["cls"] = c.cls or None
    elif c.cls:
        # 중첩정리로 떨어진 것은 판정을 통과한 뒤 정리됐으므로 cls 가 있다
        d["cls"] = c.cls
    # 파일에 적혀 있던 id 를 그대로 낸다. 통과분은 아래에서 면적 순으로 다시
    # 매기지만, 유령(지운 것)은 이 값을 유지해야 예전과 같다.
    if c.raw_id is not None:
        d["id"] = c.raw_id
    if c.reject:
        d["reject"] = c.reject
    return d


def _key_bbox(key: str):
    """`mask_key` 에서 bbox 를 되살린다. 키가 `x_y_w_h` 일 때만 된다.

    `rebind.key_to_bbox` 와 같은 규칙이다. 저쪽을 임포트하지 않는 것은 뷰어가
    저장소 뿌리의 스크립트에 매이지 않게 하려는 것이고(컨테이너 안에서 코드가
    `/app` 이다), 규칙이 **되살리기 전용의 보조**라 갈라져도 값이 안 틀린다 —
    맞으면 쓰고 아니면 `geom` 을 본다.
    """
    try:
        x, y, w, h = (int(v) for v in key.split("_"))
    except ValueError:
        return None
    return [x, y, w, h]


def _orphan_dict(key: str, o, um_per_px=None) -> dict | None:
    """**후보 없이 교정만 남은 개체**를 화면이 그릴 수 있는 모양으로 (P09 2단계).

    두 가지가 여기로 온다.

    - **고아**(`bind_method="orphan"`) — 재검출에서 대응 후보를 못 찾은 것.
      사람의 판단은 살아 있는데 엔진이 그 자리에 아무것도 안 냈다
    - **사람이 그린 개체**(`source="manual"`) — 3단계부터 생긴다

    **안 그리면 다음 저장에 지워진다.** 화면은 자기가 아는 키만 보내고
    `save_review` 의 마지막 줄은 payload 에 없는 키를 지운다 — 사람이 아무것도
    안 하고 "검토 완료" 만 눌러도 사라진다. 시험이 그것을 재현해 두었다.

    기하가 없으면 `None` 이다. 그릴 것이 없으면 화면에 낼 수 없고, 그 상태는
    `check_db.py` 의 "교정이 기하를 갖고 있다" 가 따로 센다.
    """
    geom = o.geom or {}
    # **옛 모양도 읽는다** (`_member_row` 와 같은 줄 · 2026-09-03). 앉히기·묶기가
    # 한동안 `bbox_xywh` 로 적었는데, 못 읽으면 여기서 `None` 이 되어 **화면에
    # 안 그려지고 다음 저장이 그 행을 지운다** — 조용한 유실이다.
    bbox = geom.get("bbox") or geom.get("bbox_xywh") or _key_bbox(key)
    if not bbox or len(bbox) != 4:
        return None
    x, y, w, h = (int(v) for v in bbox)
    poly = geom.get("polygon") or []
    d = {
        # **키를 실어 보낸다.** 기하에서 다시 만들면 안 된다 — 4단계에서 사람이
        # 경계를 고치면 bbox 가 바뀌는데 키는 그대로여야 한다 (`cand_key`).
        "key": key,
        "bbox_xywh": [x, y, w, h],
        "center_xy": [x + w // 2, y + h // 2],
        "area_px": geom.get("area_px") or 0,
        "shape_ok": False,
        "polygon": list(poly),
        # **엔진이 낸 것이 아니라는 표시.** 화면이 이것으로 갈라 그린다 —
        # 지표가 비어 있는 이유이기도 하다(`Candidate` 가 없어 잰 적이 없다).
        "orphan": True,
        "source": o.source,
    }
    # **지표는 저장하지 않고 그때그때 잰다** (P09 3단계). 폴리곤이 원본이고
    # 지표는 거기서 나오는 값이라, 따로 넣어 두면 **기하를 고쳤을 때 낡는다**
    # (4단계에서 사람이 경계를 고친다). 개체 하나에 점 13~19개라 재는 값이
    # 싸고, 뷰어에 numpy·cv2 를 안 들이는 경계도 지킨다(`shape.py` 머리말).
    m = shape.measure(d["polygon"], um_per_px) if d["polygon"] else {}
    for f in NUM_FIELDS:
        d[f] = m.get(f)
    if m.get("area_px"):
        d["area_px"] = m["area_px"]
    if o.label:
        d["cls"] = o.label
    return d


def _guess_cls(c: dict) -> str | None:
    """수동으로 되살린 개체의 표시용 분류.

    DiaRUGA 는 신장비로 원형/봉상을 짐작했다. ForGIA 의 판정 분류는 `foram`
    하나라(P01 2절 ②) 형태를 잰 개체는 그것이고, 못 잰 것은 없다 —
    `_SUMMARY_SQL`·`_COVER_SQL` 의 CASE 와 같아야 한다.
    """
    return "foram" if c.get("elongation") is not None else None


def mask_class(c: dict) -> str:
    """마스크를 그릴 때 쓰는 클래스. 뷰어의 addPolygon() 과 같은 규칙이어야 한다.

    사람이 지정한 분류가 가장 먼저다 — 되살린 개체(manual)에 Eucampia 를
    지정했으면 Eucampia 색으로 보여야 한다.

    **부르는 자리는 없다.** 시야 목록이 이 규칙을 SQL 로 옮겨 갔다(`_COVER_SQL`,
    060). 규칙이 셋(여기 · SQL · 뷰어의 `addPolygon`)이 되면 어긋나므로, 다음에
    파이썬 쪽에서 마스크를 그릴 일이 생기면 **이것을 되쓰거나 함께 고친다.**
    """
    if c.get("cls_user") and c.get("cls"):
        return c["cls"]
    if c.get("manual"):
        return "manual"
    return c.get("cls") or "none"


def mask_points(c: dict) -> str:
    """SVG `points` 문자열. 점이 셋 미만이면 빈 문자열 — 그릴 수 없다."""
    p = c.get("polygon") or []
    if len(p) < 6:
        return ""
    return " ".join(f"{p[i]},{p[i + 1]}" for i in range(0, len(p) - 1, 2))


# --- 검출 + 교정 ------------------------------------------------------------
def _class_counts(kept: list[dict]) -> tuple[dict, list]:
    """통과분의 분류를 센다. **`fold_object_cls` 가 접은 뒤에 다시 부른다.**

    검출 화면 머리의 "(봉상 12, 원형 3, …)". 분류 이름이 템플릿에 박혀 있었다 —
    그래서 Chaetoceros 를 테이블에 넣어도 이 줄에만 안 나왔다. 테이블이 정하게 바꾼다.
    0 인 분류는 뺀다. 여기는 표가 아니라 한 줄이라 자리를 맞출 것이 없고,
    짧을수록 읽힌다.
    """
    counts = {r["key"]: 0 for r in _class_rows()}
    for d in kept:
        if d.get("cls") in counts:
            counts[d["cls"]] += 1
    counts["manual"] = sum(1 for d in kept if d.get("manual"))
    counts["labeled"] = sum(1 for d in kept if d.get("cls_user"))

    order = ([(r["key"], r["short"]) for r in _class_rows()]
             + [("manual", "수동"), ("labeled", "사람지정")])
    counts_list = [{"key": k, "label": lb, "n": counts[k]}
                   for k, lb in order if counts.get(k)]
    return counts, counts_list


def fold_object_cls(dets: list[dict]) -> None:
    """한 시야의 판들에 걸쳐 **개체의 유형을 하나로 접는다.**

    유형(`ForamObject.label`)은 개체에 살아서 사람이 지정한 것은 묶인 판들이
    이미 한 값을 본다 — 어긋날 수가 없다. **어긋나는 것은 아직 지정하지 않은
    개체다**: 그때 화면은 판마다 *엔진이 그 판에서 본 값*으로 떨어지는데, 그
    값이 판마다 다르다.

    - 같은 개체인데 초점면마다 `elongation` 이 문턱을 넘나든다. 되살린 판은
      `_guess_cls` 가 다시 재고 그쪽은 1.4~2.0 이 무분류라, 옆 판이 봉상인데
      혼자 무분류가 된다
    - 번지기(106)가 앉힌 손그림 판은 `Candidate` 가 없어 유형이 아예 없다.
      한 판만 엔진 후보에 붙어 있으면 **그 판만** 색이 있다

    사람은 한 개체을 판을 넘겨 가며 보므로, 넘길 때마다 색이 바뀌면 같은 것을
    보고 있는지 자체가 흔들린다. **아는 판이 있으면 그 값을 개체 전부가 쓴다** —
    아는 값이 여럿이면 많은 쪽, 그래도 같으면 면적이 가장 큰 판의 것이다(가장
    잘 보이는 판이다).

    **접은 값은 사람의 지정이 아니다.** `cls_user` 를 안 붙이므로 색의 우선순위
    (`addPolygon`)에서 사람 지정 아래에 있고, `cls_folded` 로 표시해 **화면이
    저장에 안 싣게** 한다 — 손그림의 `drawn` payload 는 `cls` 를 그대로
    `ForamObject.label` 에 적으므로(`_save_drawn`), 접은 값이 실려 가면
    **엔진의 추측이 사람이 지정한 유형 자리에 눌러앉는다.**

    **판 전부가 있는 자리에서만 부른다** — 시야 하나의 판들을 함께 물어 온
    `group_detail`·`candidate_rows` 가 그 자리다. 낱개로 여는
    `detection_for_viewpoint` 는 옆 판의 후보를 안 물어 와서 접을 근거가 없고,
    물어 오게 하면 개수를 세려고 자료를 물질화하는 그 실수가 된다.

    dets 를 제자리에서 고친다 — 분류가 바뀌면 그 판의 집계도 함께 바뀐다.
    """
    keys = {r["key"] for r in _class_rows()}
    members: dict[int, list] = {}
    for det in dets:
        for d in det.get("candidates") or []:
            if d.get("obj_id"):
                members.setdefault(d["obj_id"], []).append((det, d))

    touched = set()
    for group in members.values():
        if len(group) < 2:
            continue
        # 사람이 지정한 개체는 진실이 하나다 — 접을 것이 없다.
        if any(d.get("cls_user") for _, d in group):
            continue
        votes: dict[str, list[int]] = {}
        for _, d in group:
            if d.get("cls") in keys:
                votes.setdefault(d["cls"], []).append(d.get("area_px") or 0)
        if not votes:
            continue
        if len(votes) == 1 and len(next(iter(votes.values()))) == len(group):
            continue                      # 이미 판 전부가 같은 값이다
        win = max(votes, key=lambda k: (len(votes[k]), max(votes[k])))
        for det, d in group:
            if d.get("cls") == win:
                continue
            d["cls"] = win
            d["cls_folded"] = True
            touched.add(id(det))

    for det in dets:
        if id(det) in touched:
            det["counts"], det["counts_list"] = _class_counts(det["candidates"])


def _apply_review(det: Detection, reviews: dict, state) -> dict:
    """검출 결과에 교정을 얹어 예전 detection_for() 와 같은 dict 를 만든다.

    규칙은 JSON 시절과 같다 — **사람이 지웠다가 이긴다.** 문턱을 바꿔 개체가
    탈락분으로 옮겨가도 지운 것이 조용히 되살아나지 않아야 한다.
    """
    removed = {k for k, o in reviews.items() if o.removed}
    accepted = {k for k, o in reviews.items() if o.accepted}

    kept, gone, rejected = [], [], []
    n_auto = 0
    for c in det.candidates.all():
        key = c.mask_key
        d = _cand_dict(c)
        if c.passed:
            n_auto += 1
            (gone if key in removed else kept).append(d)
            continue
        # 탈락분: 지운 것은 유령으로, 되살린 것은 통과분으로. 나머지는 후보 풀.
        if key in removed:
            d["from_reject"] = True
            gone.append(d)
        elif key in accepted:
            d["manual"] = True
            d["from_reject"] = True
            d["cls"] = _guess_cls(d)
            kept.append(d)
        else:
            rejected.append(d)

    # **후보가 없는 교정도 그린다** (P09 2단계). 재검출이 낳은 고아와, 3단계부터
    # 사람이 그린 개체가 여기로 온다.
    #
    # **안 그리면 다음 저장에 지워진다** — 화면은 자기가 아는 키만 보내고
    # `save_review` 는 payload 에 없는 키를 지운다. 사람이 아무것도 안 하고
    # "검토 완료" 만 눌러도 사라진다. 시험이 그 갈래를 재현해 둔다
    # (`OrphanReviewSurvivesTest`).
    seen = {c.mask_key for c in det.candidates.all()}
    for key, o in reviews.items():
        if key in seen:
            continue
        d = _orphan_dict(key, o, det.um_per_pixel)
        if d is None:
            continue
        (gone if o.removed else kept).append(d)

    # 사람이 지정한 분류·메모를 얹는다. 원래 값은 cls_auto 로 남긴다.
    for d in kept + gone:
        o = reviews.get(cand_key(d))
        if not o:
            continue
        # **사람이 고친 기하가 엔진 것을 덮는다** (P09 4단계). `geom` 이 원본이고
        # 엔진의 `Candidate` 는 그대로 둔다 — 검출 이력에서 엔진이 낸 것과 사람이
        # 손댄 것을 못 가르게 되면 회차 비교가 무의미해진다 (P09 5.6).
        if o.geom_edited and (o.geom or {}).get("polygon"):
            d["polygon"] = list(o.geom["polygon"])
            if o.geom.get("bbox"):
                d["bbox_xywh"] = list(o.geom["bbox"])
                bx, by, bw, bh = d["bbox_xywh"]
                d["center_xy"] = [bx + bw // 2, by + bh // 2]
            d["geom_edited"] = True
            # **기하가 바뀌었으면 지표도 다시 잰다.** 안 재면 화면이 옛 모양의
            # 면적·장축을 새 마스크 옆에 적는다 — 예외 없이 틀린 숫자다.
            #
            # `texture`·`predicted_iou`·`stability_score` 는 **그대로 둔다**:
            # 픽셀이 있어야 나오는 값이라 여기서 못 재고, 엔진이 잰 영역과 거의
            # 같은 자리다. 다시 잰 것과 아닌 것이 섞이므로 **화면이 고쳤다는
            # 사실을 적는다**(말풍선).
            m = shape.measure(d["polygon"], det.um_per_pixel)
            for f in NUM_FIELDS:
                if f in ("texture", "predicted_iou", "stability_score"):
                    continue
                d[f] = m.get(f)
            if m.get("area_px"):
                d["area_px"] = m["area_px"]
        if o.label:
            if d.get("cls") != o.label:
                d["cls_auto"] = d.get("cls")
            d["cls"] = o.label
            d["cls_user"] = True
        if o.note:
            d["note"] = o.note
        # 동정 결과 (개체 카탈로그). **`cls` 와 다른 축이다** — `cls` 는 `ClassDef`
        # 가 정한 목록에서 고른 것이고 이쪽은 사람이 적은 종명이다.
        if o.species:
            d["species"] = o.species
        # 등급·자세 (개체 카탈로그). **둘 다 개체의 성질이라 묶인 판들이 한 값을
        # 함께 본다** (0035 로 등급이 판정에서 옮겨 왔다). 어느 판이 잘 보이는가는
        # `is_rep` 가 말한다. 조인은 `_reviews_prefetch` 가 이미 물어 왔다.
        if o.foram_object.grade:
            d["grade"] = o.foram_object.grade
        if o.foram_object.pose:
            d["pose"] = o.foram_object.pose
        # **어느 개체의 것인가** (P18). 카탈로그가 카드를 개체 단위로 모으는
        # 열쇠다 — 이 칸이 없는 후보는 **아직 아무도 안 본 것**이라 카드가 없다
        # (사용자 방침 2026-08-25: 개체는 사람이 손대기 전까지 없는 것이 맞다).
        d["obj_id"] = o.foram_object_id

    kept.sort(key=lambda r: -(r.get("area_px") or 0))
    for i, d in enumerate(kept):
        d["id"] = i
    for d in gone:
        d["removed"] = True

    counts, counts_list = _class_counts(kept)

    stem = Path(det.image_path).stem
    return {
        "image": det.image_path,
        "stem": stem,
        "size": [det.width, det.height],
        "scale": det.scale,
        "um_per_pixel": det.um_per_pixel,
        "um_per_pixel_native": det.um_per_pixel_native,
        "um_per_pixel_source": det.um_per_pixel_source or None,
        "um_per_pixel_backfilled": det.um_per_pixel_backfilled or None,
        "n_raw_masks": det.n_raw_masks,
        "n_sized": det.n_sized,
        "n_auto": n_auto,
        "n_candidates": len(kept),
        "counts": counts,
        "counts_list": counts_list,
        "thresholds": det.thresholds.as_dict() if det.thresholds else {},
        "candidates": kept,
        "removed_candidates": gone,
        "rejected": rejected,
        "n_removed": len(removed),
        "accepted_keys": sorted(accepted),
        # **사람이 그린 개체는 여기 안 담는다** (P09 3단계). 이 지도는 화면이
        # 저장 payload 로 되돌려 보내는 것인데, 그린 개체의 분류는 `drawn` 이
        # 통째로 나른다 — 양쪽에 실으면 `save_review` 가 같은 키로 **엔진 쪽
        # 행을 하나 더 만든다**(`batch` 가 달라 유일 제약에 안 걸린다).
        #
        # 분류 자체는 개체 dict 의 `cls` 로 이미 화면에 간다.
        #
        # **코멘트 지도는 없다** (0036). 검토 화면이 코멘트를 안 적기 때문이다 —
        # 되돌려 보낼 것이 없으면 지도도 없어야 한다. 읽는 것은 그대로다:
        # 위에서 개체 dict 의 `note` 에 실어 말풍선·표가 보여준다.
        "labels": {k: o.label for k, o in reviews.items()
                   if o.label and o.source != "manual"},
        "review_done": bool(state and state[0]),
        "review_note": (state[1] if state else ""),
        "source_dir": "out",
    }


def map_points(area: str | None = None,
               with_hidden: bool = False) -> list[dict]:
    """지도에 찍을 지역별 묶음. 슬라이드가 아니라 **지역 단위**다.

    좌표는 `Site.lat/lon` 이 원칙이고, 비어 있으면 관찰이 있는 지점 좌표의
    평균(DiaRUGA 199), 그것도 없으면 해역 대략값으로 물러난다. **어느 쪽인지
    반드시 함께 낸다** — 대략값을 실측처럼 보이게 두면 안 된다.

    **숨긴 슬라이드는 여기서도 뺀다** — 세 보기가 같은 것을 봐야 한다.
    """
    area = area or "ant"
    approx_sites, project = antarctica.APPROX_SITES, _polar_xy

    sites = (Site.objects.filter(area=area)
             .prefetch_related("localities__samples__slides"))

    def visible(qs):
        return [sl for sl in qs if with_hidden or not sl.hide_in_list]

    def slides_of(loc):
        return [sl for sm in loc.samples.all() for sl in visible(sm.slides.all())]

    out = []
    for site in sites:
        slides = [sl for loc in site.localities.all() for sl in slides_of(loc)]
        if not slides:
            continue
        base = re.sub(r"\d+$", "", site.code).upper()
        approx = approx_sites.get(base)
        loc_xy = [(loc.lat, loc.lon) for loc in site.localities.all()
                  if loc.lat is not None and loc.lon is not None
                  and slides_of(loc)]
        if site.lat is not None and site.lon is not None:
            lat, lon, exact, note = site.lat, site.lon, True, ""
        elif loc_xy:
            lat = sum(la for la, _ in loc_xy) / len(loc_xy)
            lon = sum(lo for _, lo in loc_xy) / len(loc_xy)
            exact, note = True, ""
        elif approx:
            lat, lon, exact, note = approx[0], approx[1], False, approx[2]
        else:
            continue                    # 어디인지 짐작할 수도 없으면 안 찍는다
        x, y = project(lat, lon)

        cores = []
        for loc in sorted(site.localities.all(), key=lambda c: c.code):
            rows = sorted(slides_of(loc), key=_slide_order)
            if not rows:
                continue
            if loc.lat is not None and loc.lon is not None:
                cx, cy = project(loc.lat, loc.lon)
                c_exact = True
            else:
                cx, cy, c_exact = x, y, exact
            slide_rows = [{
                "slug": sl.slug,
                "label": sl.name,
                "depth_cm": sl.depth_cm,
                "state": sl.state,
                **_obs(sl),
                "n_viewpoints": sl.viewpoints.count(),
                "reviewed": 0,          # 3단계
            } for sl in rows]
            cores.append({
                "code": loc.code,
                "n_slides": len(rows),
                "x": round(cx, 1), "y": round(cy, 1), "exact": c_exact,
                "water_depth_m": loc.water_depth_m,
                "n_viewpoints": sum(r["n_viewpoints"] for r in slide_rows),
                "slides": slide_rows,
            })

        out.append({
            "code": site.code,
            "label": site.region or site.name or site.code,
            "x": round(x, 1), "y": round(y, 1),
            "exact": exact, "approx_note": note,
            "n_slides": len(slides),
            "n_viewpoints": sum(c["n_viewpoints"] for c in cores),
            "cores": cores,
            "core_codes": [c["code"] for c in cores],
            "slugs": [s.slug for s in slides],
        })
    return out


# --- 지도 --------------------------------------------------------------------
def _polar_xy(lat: float, lon: float) -> tuple[float, float]:
    """EPSG:3031 로 투영해 SVG 좌표(km)로. antarctica.py 와 같은 식이어야 한다.

    상수를 여기 한 번 더 적지 않고 antarctica 에서 가져온다 — 식이 갈라지면
    지도와 마커가 어긋난다.
    """
    import math

    a, e = antarctica.WGS84_A, antarctica.WGS84_E

    def t(phi):
        s = e * math.sin(phi)
        return math.tan(math.pi / 4 + phi / 2) / (((1 + s) / (1 - s)) ** (e / 2))

    phi_c = math.radians(-71.0)
    mc = math.cos(phi_c) / math.sqrt(1 - e * e * math.sin(phi_c) ** 2)
    rho = a * mc * t(math.radians(lat)) / t(phi_c)
    lam = math.radians(lon)
    return rho * math.sin(lam) / 1000, -rho * math.cos(lam) / 1000


def slide_label(slug: str) -> str | None:
    """머리글에 쓸 이름만. 없으면 None."""
    return Slide.objects.filter(slug=slug).values_list("name", flat=True).first()


def candidate_rows(slug: str, batch_id=None, gone: bool = False,
                   all_images: bool = False, rejected: bool = False) -> list[dict]:
    """데이터셋 전체의 검출 개체를 한 목록으로. **시야를 한 번만 훑는다.**

    예전에는 뷰가 `dataset_detail()` 로 시야 74개를 훑은 뒤, 그룹마다 다시
    `group_detail()` 을 불러 같은 것을 또 만들었다 — 0.45초 + 0.67초. 뒤의 것이
    통째로 군더더기였다.

    `image_rel` 은 검출에 적힌 경로가 아니라 뷰어가 실제로 찾아낸 파일의
    상대경로다 — 크롭 요청이 그 경로로 이미지를 다시 열기 때문에, 검출 기록이
    절대경로인 경우에도 어긋나지 않아야 한다.

    `batch_id` 를 주면 그 묶음의 검출을 훑는다 (`current_detections` 와 같은
    규칙). 안 주면 검토 대상 묶음이고, 그것이 크롭 화면·계측 표·개체 카탈로그가
    보는 것이다.

    `gone=True` 면 **사람이 오검출로 지운 것**을 대신 훑는다 (P16 5.1). 섞어서
    내지 않는다 — 지운 것을 되살리는 화면과 동정하는 화면은 하는 일이 다르고,
    한 목록에 섞으면 카드 수가 몇 배가 된다.

    **어느 묶음인지는 여기서 한 번만 묻는다.** 예전에는 `current_detections` 가
    시야마다 `review_batch_id()` 를 다시 물었다 — 실측으로 한 화면에 390~560번이고
    (`bp09-0901` 시야 86개, `369cm` 시야 74개), 값은 매번 같다. 개수를 세려고
    자료를 물질화하지 말라는 것과 같은 이야기다(CLAUDE.md).
    """
    slide = Slide.objects.filter(slug=slug).first()
    if slide is None:
        return []

    if batch_id is None:
        batch_id = review_batch_id()
    # 검토 대상이 없으면 **빈 목록**이다 — `current_detections` 와 같은 규칙이고,
    # 조용히 아무 묶음이나 보여주지 않는다 (P10 3.6).
    if batch_id is None:
        return []

    rows = []
    for vp in _viewpoints_of(slide, only_current=True, batch_id=batch_id):
        # **이미지마다 자기 검출을 만든다** (P09 1단계). 시야 하나에 현재 검출이
        # 여럿일 수 있고(합성본 하나 + 프레임마다 하나), `_frames` 는 그것을
        # 경로로 맞춘다 — 검토 화면(`viewpoint_detail`)과 같은 자료다.
        dets = current_detections(vp, batch_id)
        if not dets:
            continue
        bmap = batch_ids_of(dets)
        by_path = {d.image.path: (d.image_id, _with_reviews(vp, d, bmap[d.pk]))
                   for d in dets if d.image_id}
        # **개체의 유형을 판들에 걸쳐 접는다.** 판을 여기서 전부 물어 왔으므로
        # 접을 근거가 있다 — 카드는 개체 단위인데 유형이 판마다 다르면 어느
        # 줄을 집었느냐가 카드의 유형을 정하게 된다.
        fold_object_cls([d for _, d in by_path.values()])
        st = getattr(vp, "stack", None)
        # 합성본 검출이 있으면 그쪽을, 없으면 각 프레임 검출을 훑는다.
        stacked = by_path.get(st.focused_path) if st else None
        # `frame_seq` 는 **개체가 프레임에서 나왔을 때만** 준다 — 합성본이면
        # `None` 이고, 카탈로그 번호의 `f` 토막이 그것을 그대로 따른다
        # (`catalog.catalog_no`). `f` 가 붙어 있느냐가 곧 "어느 이미지를 보고 잰
        # 것이냐" 를 말한다.
        if all_images:
            # **디스크를 안 짚는다.** `_frames` 는 프레임마다 `exists()` 를
            # 부르는데(캐러셀이 없는 판을 흐리게 그리려고), 카탈로그는 그것을
            # 쓸 일이 없다 — 시야 570개 × 프레임 넷이면 한 판에 `stat` 이
            # 2천 번이다. **개수를 세려고 자료를 물질화하지 말라**는 그 줄이다.
            frames = [(fr.name, by_path[fr.path][1], fr.path,
                       by_path[fr.path][0], fr.seq)
                      for fr in vp.frames.all() if fr.path in by_path]
        else:
            frames = [(f["name"], f["detection"], f["rel"], f["image_id"],
                       f["seq"])
                      for f in _frames(vp, by_path) if f["detection"]]
        if all_images:
            # **판을 안 고른다** (P18). 개체 카탈로그가 쓰는 갈래다 — 카드를 개체
            # 단위로 모으려면 **모든 판의 후보**가 있어야 한다. 합성본에서 안
            # 잡히고 어떤 프레임에서만 잡힌 개체이 여기서 들어온다(106).
            #
            # **중복은 부르는 쪽이 접는다.** 같은 개체이 판마다 한 줄씩 나오는데,
            # 그것을 하나로 모으는 열쇠가 `obj_id` 다.
            sources = ([(Path(st.focused_path).stem, stacked[1],
                         st.focused_path, stacked[0], None)] if stacked else [])
            sources += frames
        elif stacked:
            sources = [(Path(st.focused_path).stem, stacked[1],
                        st.focused_path, stacked[0], None)]
        else:
            # **프레임마다 자기 검출을 준다.** 예전에는 대표 검출 하나를 프레임
            # 수만큼 되돌려 같은 개체가 여러 줄로 나왔다.
            sources = frames
        for stem, d, image_rel, image_id, frame_seq in sources:
            # `rejected=True` 는 ForGIA 의 것 — **문턱이 떨어뜨린 것**을 훑는다
            # (지운 것 `gone` 과 다르다: 저쪽은 사람, 이쪽은 판정). 크롭 화면이
            # 문턱을 잡을 때 본다 (2단계)
            pick = ("rejected" if rejected else
                    "removed_candidates" if gone else "candidates")
            for c in d[pick]:
                rows.append({
                    "group_id": vp.idx,
                    "cell": vp.cell,
                    "stem": stem,
                    "image_rel": image_rel,
                    # 카탈로그가 번호를 만들고 묶음을 짚는 데 쓴다. 화면이
                    # `(이미지, 묶음, 키)` 로 개체를 짚으므로 셋이 함께 다녀야 한다.
                    "image_id": image_id,
                    "batch_id": d.get("batch_id"),
                    "frame_seq": frame_seq,
                    "reviewed": d.get("review_done"),
                    "um_per_pixel": d.get("um_per_pixel"),
                    **c,
                })
    return rows


def layer_codes(slide) -> dict | None:
    """카탈로그 번호의 앞쪽 토막 — 지역·지점·시료·관찰. 소속이 없으면 `None`.

    **소속을 잃은 관찰이 실제로 있었다** (063). 그때 `RS23--071-…` 같은 번호를
    내면 되읽을 수도 없으므로 **번호가 없는 것이 맞다** — 화면이 그것을 적어서
    사람이 층을 채워야 한다는 것을 알 수 있게 한다.
    """
    smp = slide.sample
    loc = smp.locality if smp else None
    site = loc.site if loc else None
    if not (smp and loc and site):
        return None
    return {"site": site.code, "locality": loc.code, "sample": smp.code,
            "obs_no": slide.obs_no}


def batch_codes() -> dict:
    """묶음 pk → 카탈로그 코드. **비어 있을 수 있다** — 아직 안 정한 묶음이다."""
    return dict(RunBatch.objects.values_list("id", "code"))


def catalog_no_for(row: dict, layer: dict | None, codes: dict) -> tuple:
    """개체 하나의 번호와, 못 만들었으면 그 이유. `(번호, 이유)`.

    **못 만든 것을 빈 문자열로만 내면 안 된다.** 화면이 "번호 없음" 만 적으면
    사람은 무엇을 채워야 하는지 알 수 없다 — 지우기 문턱을 버튼에 적는 것과
    같은 이야기다(063).

    **`M`(손그림)과 "코드를 아직 안 정했다" 를 안 섞는다.** `RunBatch.code` 가
    비었다고 `M` 을 붙이면 **엔진이 낸 개체가 손그림으로 기록된다** — 예외가
    안 나고 그냥 틀리는 종류다.
    """
    if layer is None:
        return "", "관찰이 소속(지역·지점·시료)을 잃었다"
    manual = row.get("source") == "manual"
    code = "" if manual else (codes.get(row.get("batch_id")) or "")
    if not manual and not code:
        return "", "이 묶음의 카탈로그 코드를 아직 안 정했다"
    try:
        return catalog.catalog_no(**layer, viewpoint=row["group_id"],
                                  mask_key=row["key"],
                                  frame_seq=row.get("frame_seq"),
                                  batch_code=code), ""
    except ValueError as e:
        return "", str(e)


def _reviews_prefetch(attr="object_reviews") -> Prefetch:
    """교정을 미리 물어 올 때 **개체까지 함께 문다** (P12).

    분류·종명이 `ForamObject` 로 옮겨 가면서 `o.label` 이 조인 하나가 됐다.
    안 물면 판정 수만큼 질의가 난다 — 목록 화면이 그 실수로 1.3초였다.
    """
    return Prefetch(attr, queryset=ObjectReview.objects
                    .select_related("foram_object", "foram_object__taxon"))


def reviews_of(vp) -> list:
    """그 시야의 교정들. **미리 물어 왔으면 그것을 쓴다.**

    부르는 자리가 둘이다 — 목록·크롭 화면은 `_reviews_prefetch()` 로 시야
    수백 개를 한 번에 물어 오고, 낱개로 여는 화면은 그냥 부른다. 뒤엣것에서
    `select_related` 를 빠뜨리면 개체마다 질의가 하나씩 는다.
    """
    cache = getattr(vp, "_prefetched_objects_cache", None) or {}
    if "object_reviews" in cache:
        return list(vp.object_reviews.all())
    return list(vp.object_reviews.select_related("foram_object", "foram_object__taxon"))


def judgement_for(vp, image, batch, key, cand=None) -> ObjectReview:
    """판정 행 하나를 가져오거나 세운다 — **개체를 함께 세우는 문 하나** (P12).

    `ObjectReview.foram_object` 는 비어 있을 수 없다. 그래서 행을 만드는 자리가
    셋(검토 저장 · 사람이 그리기 · 카탈로그)인데 **개체를 만드는 규칙이 셋이면
    안 된다** — 하나만 빠뜨려도 `IntegrityError` 이고, 더 나쁘게는 대표(`is_rep`)
    없는 개체가 서서 학습 자료를 뽑을 때 얼굴이 없는 개체가 된다.

    **새 개체는 늘 1:1 이고 자기가 대표다.** 묶는 것은 사람이 하는 일이라
    (P11) 여기서 앞서가지 않는다.
    """
    # **id 로도 인스턴스로도 불린다.** 뷰는 `review_batch_id()` 가 준 id 를 들고
    # 있고 저장 경로는 객체를 들고 있다 — 한쪽만 받으면 부르는 자리가 매번
    # 변환을 기억해야 하고, 빠뜨리면 `ValueError` 가 저장 한복판에서 난다.
    image_id = getattr(image, "pk", image)
    batch_id = getattr(batch, "pk", batch)
    row = ObjectReview.objects.filter(
        image_id=image_id, batch_id=batch_id, mask_key=key).select_related(
        "foram_object", "foram_object__taxon").first()
    if row is not None:
        return row
    obj = ForamObject.objects.create(viewpoint=vp, batch_id=batch_id)
    row = ObjectReview.objects.create(
        viewpoint=vp, image_id=image_id, batch_id=batch_id, mask_key=key,
        foram_object=obj, is_rep=True, candidate=cand,
        bind_method="exact" if cand else "orphan",
        bind_score=1.0 if cand else None)
    # **새 개체는 자기가 앵커다** (P18). 멤버가 하나뿐이라 고를 것이 없고, 나중에
    # 무엇을 묶어 넣어도 이 값이 그대로 남는 것이 요점이다 — 카탈로그 번호가
    # 묶는 행위에 안 움직인다.
    ForamObject.objects.filter(pk=obj.pk).update(anchor=row)
    return row


def confirm_kept(vp: Viewpoint, batch) -> dict:
    """**"검토 완료" 는 남는 마스크를 확인하는 것이다** — 그것을 행으로 적는다.

    돌려주는 것은 `{이미지 id: [mask_key, …]}` — 새로 세운 것만 담는다.

    ## 왜

    지금까지 "이건 개체이 맞다" 는 판단은 **행의 부재로만** 기록됐다. 사람이
    틀린 것을 지우고 완료를 누르면 남은 것이 곧 맞다고 본 것인데, DB 에는
    아무것도 안 남는다. 그래서

    - **학습 자료의 양성 표본이 암묵적이다.** 음성(지운 것)은 `geom` 까지 들고
      있는데 양성은 "안 지웠다" 로만 있다
    - **개체 수를 셀 때 통과분이 안 잡힌다.** `ForamObject` 는 사람이 무언가
      말한 마스크에만 서므로, 프레임에 겹쳐 잡힌 것을 하나로 세는 층이 통과분
      위에는 얹히지 않는다

    ## `auto_confirmed=True` 로 적는다 — `accepted` 가 아니다

    `accepted` 는 *엔진이 떨어뜨린 것을 사람이 되살린* 사건이고 이것은 *엔진이
    통과시킨 것을 사람이 확인한* 사건이다. 하나로 뭉치면 화면의 "복구 N" 이
    통과분 수백 개로 부풀고, **"엔진이 놓친 것 몇 건" 을 다시 못 센다** —
    회차 성적을 읽는 근거가 그것이다 (사용자 판단 2026-08-11).

    화면은 이 칸을 모르므로 `save_review` 의 청소가 지우지 않도록 `keys` 에
    얹는다 — 종명·`geom_edited` 와 같은 갈래다.

    ## 시야 전체를 훑는다

    완료 표시는 `(시야, 묶음)` 단위라 그 주장도 시야 단위다. payload 는 이미지
    하나를 대표하지만, 그 이미지만 적으면 **프레임 넷 중 하나만 표시된다.**

    ## **남는 개체**가 대상이다 — 통과분만이 아니다

    화면·집계가 "남는다" 를 세는 식과 **같은 식을 쓴다**:

        (통과 AND NOT 지움) OR (탈락 AND 되살림)

    처음엔 "통과분이고 교정 행이 아직 없는 것" 만 적었다. 그러면 **분류를 붙인
    마스크와 되살린 탈락분에 확인 표시가 안 붙는다** — 사람이 가장 확실하게 개체이라고
    본 것들이다. 실측으로 `sam2-전수` 의 완료 309시야에서 **211 / 921** 만
    잡혔다(23%). 세는 식이 둘이면 이렇게 갈라진다.

    **지운 것에는 안 붙는다.** 그것이 이 확인의 반대말이다.

    **이미 있는 행의 다른 칸은 안 건드린다** — 분류·코멘트·기하는 그대로 두고
    `auto_confirmed` 만 세운다. 그 칸은 덮어쓰는 것이 아니라 **더해지는 사실**이다.
    """
    if batch is None:
        return {}
    out = {}
    for det in current_detections(vp, batch_id=getattr(batch, "pk", batch)):
        rows = {r.mask_key: r for r in ObjectReview.objects
                .filter(image=det.image_id, batch=batch)}
        made = []
        for cand in det.candidates.all():
            row = rows.get(cand.mask_key)
            kept = ((cand.passed and not (row and row.removed))
                    or (not cand.passed and row and row.accepted))
            if not kept or (row and row.auto_confirmed):
                continue
            if row is None:
                row = judgement_for(vp, det.image_id, batch, cand.mask_key,
                                    cand)
                row.geom = {"bbox": cand.bbox_xywh, "polygon": cand.polygon}
            row.auto_confirmed = True
            row.save(update_fields=["auto_confirmed", "geom"])
            made.append(cand.mask_key)
        if made:
            out[str(det.image_id)] = made
    return out


def prune_objects(ids) -> int:
    """멤버가 하나도 안 남은 개체를 걷는다. 돌려주는 것은 지운 수.

    판정 행을 지우면 그 개체가 유령으로 남는다 — 예외는 안 나고 개체 수를 세는
    자리마다 하나씩 는다. **판정을 지우는 자리는 전부 여기를 지난다.**

    `ForamObject` 를 먼저 지우면 `CASCADE` 가 살아 있는 판정까지 끌고 가므로
    **반드시 판정을 지운 뒤에** 부른다.
    """
    if not ids:
        return 0
    ghosts = (ForamObject.objects.filter(pk__in=set(ids), members__isnull=True)
              .values_list("pk", flat=True))
    n, _ = ForamObject.objects.filter(pk__in=list(ghosts)).delete()
    return n


def merge_into_object(obj: ForamObject, rows) -> int:
    """판정 여럿을 **한 개체로 모은다.** 돌려주는 것은 대표가 된 행의 pk.

    `rows` 는 `[(ObjectReview, 대표인가)]` 다. 아무도 대표로 표시되지 않으면
    첫 행이 대표가 된다 — **대표 없는 개체는 학습 자료를 뽑을 때 얼굴이 없다.**

    **부르는 자리가 둘이다** — 사람이 화면에서 묶을 때(`save_object_link`)와
    그린 마스크가 판마다 번질 때다. 둘이 같은 일을 따로 짜면 **규칙이 둘이
    된다**: 104 가 분류 번지기에서, 105 가 카탈로그에서 그렇게 당했다(번지게
    하는 코드가 한 곳에만 있어 다른 길로 들어온 저장이 안 번졌다).

    순서가 있다.

    1. **떠나올 개체의 코멘트를 먼저 거둔다** — 아래 "코멘트는 잇는다"
    2. **옛 대표를 먼저 내린다** — `is_rep` 은 개체당 하나라는 유일 제약이 있어,
       새 대표를 세우기 전에 옛 대표가 남아 있으면 부딪힌다
    3. 행을 옮기면서 **떠나온 개체를 적어 둔다**
    4. 대표를 세우고, **그다음에** 빈 개체를 걷는다 — `prune_objects` 머리말대로
       판정을 옮긴 뒤라야 `CASCADE` 가 살아 있는 판정을 안 끌고 간다

    ## 코멘트는 잇는다 (0036)

    코멘트가 개체로 오면서 **모으는 순간 값이 부딪힌다.** 분류·종명은 부딪히면
    거절하고 사람이 고르게 한다(`save_object_link` 의 그 자리) — 값을 하나 골라야
    뜻이 서기 때문이다. **글은 다르다**: 판 셋에 다른 말이 적혀 있으면 그 셋이
    전부 이 개체에 대한 사실이고, 하나를 고르면 나머지는 **재생성 불가한
    사람의 글**인 채로 사라진다.

    그래서 이어 붙인다 — 그릇의 것을 앞에, 같은 말은 한 번만, 줄바꿈으로.
    **떠나오는 개체는 곧 `prune_objects` 가 걷으므로 여기서 안 거두면 그
    자리에서 없어진다.**
    """
    rows = list(rows)
    if not rows:
        raise ValueError("모을 판정이 없다")
    notes, joined = [obj.note], []
    notes += [row.foram_object.note for row, _ in
              sorted(rows, key=lambda t: (not t[1], t[0].pk))]
    for n in notes:
        n = (n or "").strip()
        if n and n not in joined:
            joined.append(n)
    joined = "\n".join(joined)
    if joined != obj.note:
        obj.note = joined
        obj.save(update_fields=["note", "updated_at"])
    ObjectReview.objects.filter(foram_object=obj, is_rep=True).update(
        is_rep=False)
    orphaned, rep_pk = [], None
    for row, rep in rows:
        if row.foram_object_id != obj.pk:
            orphaned.append(row.foram_object_id)
            row.foram_object = obj
        row.is_rep = False
        row.save(update_fields=["foram_object", "is_rep"])
        if rep:
            rep_pk = row.pk
    if rep_pk is None:
        rep_pk = rows[0][0].pk
    ObjectReview.objects.filter(pk=rep_pk).update(is_rep=True)
    prune_objects(orphaned)
    # **그릇의 앵커는 그대로다** (P18) — 묶어도 번호가 안 움직이는 것이 이
    # 칸의 요점이라 여기서 손대지 않는다. 다만 그릇이 앵커를 안 갖고 있었으면
    # (옛 자료·유령을 걷은 뒤) 이 자리에서 세운다.
    reanchor([obj.pk])
    return rep_pk


def geom_box(geom) -> list | None:
    """기하 스냅샷의 bbox. **키가 두 가지다** — 어느 쪽이든 받는다.

    `ObjectReview.geom` 은 `{"bbox": …}` 인데, P12 이전의 `ObjectLinkMember.geom`
    이 `{"bbox_xywh": …}` 로 들어온 것이 그대로 남아 있다 — 흡수하면서 옛 모양을
    다시 쓰지 않았을 뿐 **이미 앉은 값은 그 모양이다.**

    한쪽만 읽으면 **빈 값이 나오고 예외는 안 난다** — 실제로 그랬다: 묶은 카드의
    크롭 상자가 비어 조용히 원래 상자로 되돌아갔고, 다른 판의 그림을 이 개체의
    옛 자리로 잘라 냈다(2026-08-10). 읽는 자리를 하나로 모아 둔다.
    """
    g = geom or {}
    box = g.get("bbox") or g.get("bbox_xywh")
    if not box or len(box) != 4:
        return None
    return [int(v) for v in box]


def _member_area(m) -> int:
    """묶음 멤버가 담은 개체의 넓이. 기하 스냅샷이 원본이고, 없으면 키에서 잰다."""
    box = geom_box(m.geom) or _key_bbox(m.mask_key) or [0, 0, 0, 0]
    return int(box[2]) * int(box[3])


def link_mains(slide) -> dict:
    """`(이미지, 묶음, 키)` → **그 묶음의 대표 멤버** (P11 · 152).

    묶음은 "초점면 여러 장의 이 마스크들이 한 개체" 을 말한다. 카탈로그 카드는
    그중 한 판을 그리는데, **그 한 판은 대표(`is_rep`)다.**

    ## 예전에는 가장 큰 멤버를 골랐다 (2026-08-10 ~ 2026-08-25)

    *동정은 개체가 크고 선명하게 보일 때 된다* 는 이유였다. 그런데 대표는 이미
    따로 있었고 — **묶기 패널이 사람이 묶기를 누른 판을 대표로 세운다**
    (`repKey = curKey`, ★ 로 바꿀 수 있다) — 그래서 **같은 개체를 두 자리가
    다른 판으로 말하고 있었다.** 실측으로 묶음 680개 중 **410개(60%)** 가
    그랬다. `is_rep` 은 "학습 자료로 뽑을 때, 목록에 보일 때 이 판을 쓴다"
    (`ObjectReview.is_rep`)이므로 **카드와 학습 자료가 다른 얼굴을 쓰고
    있었다는 뜻**이다.

    **사람이 고른 것을 기계가 다시 고르지 않는다** (사용자 방침 2026-08-25).
    크기는 짐작이고 대표는 판단이다.

    ## 대표가 성치 않을 때만 짐작한다

    `is_rep` 이 없거나(`check_db` 8번이 세는 상태) 그 판이 오검출로 지워졌으면
    (151) 산 멤버 중 가장 큰 것으로 물러난다 — **빈 얼굴을 내놓지 않는다.**
    지운 판이 대표인 것은 `check_db` 가 따로 세고 저장할 때마다 옮겨 준다.

    **번호는 안 바뀐다. 그림만 바꾼다.** 번호가 묶는 행위에 따라 움직이면 이미
    적어 둔 번호가 무효가 된다 — 그래서 `frame_seq` 는 개체가 실제로 나온
    이미지를 그대로 따르고, 여기서 고른 것은 `view` 로만 나간다.

    **멤버가 하나뿐인 묶음은 건너뛴다.** 묶은 것이 아니라 만들다 만 것이고,
    그림을 바꿀 이유가 없다.
    """
    out = {}
    # **묶은 것만 가져온다.** P12 이후 판정마다 개체가 하나씩 서므로, 거르지
    # 않으면 슬라이드의 개체 수천 개를 전부 물질화한다 — 세는 것을 물질화하지
    # 말라는 그 줄이다.
    links = (ForamObject.objects.filter(viewpoint__slide=slide)
             .annotate(n_members=Count("members")).filter(n_members__gte=2)
             .prefetch_related("members__image"))
    for link in links:
        members = list(link.members.all())
        # **사람이 세운 대표를 그대로 쓴다** (152).
        best = next((m for m in members if m.is_rep and not m.removed), None)
        if best is None:
            # 대표가 없거나 지운 판이다. **지운 판은 얼굴이 못 된다** (151) —
            # `obj#8094` 은 지운 프레임이 189×258 로 멤버 중 가장 커서, 카드가
            # 계속 **그 지운 마스크**로 그려지고 있었다.
            #
            # **전부 지웠으면 옛 규칙 그대로다** — 「지운 것」 화면이 그 카드를
            # 그려야 하고, 거기서는 지운 것이 곧 볼 것이다.
            live = [m for m in members if not m.removed] or members
            best = max(live, key=_member_area)
        for m in members:
            out[(m.image_id, m.batch_id, m.mask_key)] = (best, len(members))
    return out


def review_batch_info() -> dict | None:
    """검토 대상 묶음의 이름과 카탈로그 코드. 없으면 `None`.

    개체 카탈로그의 머리에 **어느 엔진의 판인지**를 적는다 (사용자 방침
    2026-08-10). 화면이 검토 대상 묶음 하나만 따라가므로 고르는 장치는 없고,
    엔진을 갈려면 관리 화면에서 검토 대상을 갈면 **검토·크롭·카탈로그가 함께**
    옮겨 간다 — 화면마다 다른 판을 보는 상태가 아예 안 생긴다.

    `code` 가 비어 있을 수 있다. 그러면 그 묶음의 개체는 번호가 안 나고
    (`catalog_no_for`), 화면이 그 이유를 적는다.
    """
    b = (RunBatch.objects.filter(for_review=True)
         .values("id", "label", "code").first())
    return dict(b) if b else None


def catalog_rows(slug: str, gone: bool = False) -> list[dict]:
    """개체 카탈로그 화면 한 판. `candidate_rows` 에 번호·동정·묶음을 얹는다.

    **`candidate_rows` 를 다시 짜지 않는다.** 어느 이미지의 개체를 낼 것인가
    (합성본이 있으면 합성본, 없으면 프레임)는 이미 그 함수가 정하고 있고, 규칙이
    둘이 되면 크롭 화면과 카탈로그가 서로 다른 개체를 센다.

    **여기 담기는 것은 통과분과 사람이 되살린 것이다** (사용자 방침 2026-08-10).
    동정할 대상이 그것이고, 지운 것까지 내면 카드가 몇 배가 된다.

    **`gone=True` 는 그 반대쪽이다** (P16 5.1 · 사용자 방침 2026-08-14). 지운
    것만 낸다 — 카탈로그에서 지울 수 있게 되었으니 **되살릴 자리도 같은 화면에
    있어야 한다.** 섞지 않는 것은 위 방침 그대로다.

    **검토 대상 묶음 하나만 따라간다** (사용자 방침 2026-08-10). 엔진마다 카탈로그가
    완전히 별개인 것은 그대로인데(`ObjectReview` 의 열쇠에 `batch` 가 있다), 두
    판을 나란히 여는 장치는 두지 않는다 — 관리 화면에서 검토 대상을 갈면
    **검토·크롭·카탈로그가 함께** 옮겨 가고, 그러면 화면마다 다른 판을 보는
    상태가 아예 안 생긴다. 051 이 그 상태에서 났다.

    번호 차례로 늘어놓는다 — 시야, 그 안에서 위아래·좌우 순이다. 크롭 화면은
    크기 내림차순인데(의심스러운 것을 뒤로 모으려고) 카탈로그는 **번호를 따라
    읽는 표**라 차례가 달라야 한다.
    """
    slide = (Slide.objects.filter(slug=slug)
             .select_related("sample__locality__site").first())
    if slide is None:
        return []

    layer = layer_codes(slide)
    codes = batch_codes()
    facts = object_facts(slide)

    # **모든 판을 훑는다** (P18) — 카드를 개체 단위로 모으려면 프레임에만 잡힌
    # 것도 손에 있어야 한다.
    raw = candidate_rows(slug, gone=gone, all_images=True)

    # 개체마다 한 줄. **고르는 것은 대표의 줄이다** (152) — 카드가 그리는 그림·
    # 지표·저장이 전부 그 판의 것이어야 화면과 숫자가 안 갈린다.
    picked, extra = {}, {}
    for r in raw:
        oid = r.get("obj_id")
        if oid is None:
            # **아직 아무도 안 본 후보다.** 개체가 없으니 카드도 없다
            # (사용자 방침 2026-08-25). 검토 완료를 누르면 `confirm_kept` 가
            # 개체를 세우고 그때 카드가 생긴다.
            continue
        f = facts.get(oid)
        if f is None:
            continue
        here = (r.get("image_id"), r["key"])
        # 대표의 줄을 먼저, 없으면 앵커의 줄, 그것도 없으면 처음 만난 줄.
        rank = 0 if here == f["rep"] else (1 if here == f["anchor"] else 2)
        cur = picked.get(oid)
        if cur is None or rank < cur[0]:
            picked[oid] = (rank, r)
        # **번호는 판정마다 하나씩 있다** (P18 "공개·인용"). 묶기 전에 적어 둔
        # 번호가 묶은 뒤에도 찾아져야 하므로 전부 들고 다닌다.
        extra.setdefault(oid, []).append(r)

    rows = []
    for oid, (_rank, r) in picked.items():
        f = facts[oid]
        # **번호는 앵커에서 뽑는다** (P18). 대표를 ★ 로 옮겨도 번호가 안 움직인다 —
        # 얼굴은 대표, 이름은 앵커로 축을 갈라 두었다.
        src = f["anchor_row"] or r
        r["catalog_no"], r["catalog_why"] = catalog_no_for(src, layer, codes)
        # **번호가 어느 판의 것인지 적는다.** 카드는 개체인데 번호는 판정 하나의
        # 이름이라, 안 적으면 `f03` 이 카드 전체를 설명하는 것처럼 읽힌다.
        r["no_from"] = src.get("frame_seq")
        r["no_here"] = (src.get("image_id"), src.get("key")) == \
                       (r.get("image_id"), r.get("key"))
        # 멤버들의 번호 — 검색이 이 중 아무것이나 맞춘다.
        r["member_nos"] = [
            no for no in
            (catalog_no_for(m, layer, codes)[0] for m in extra.get(oid, []))
            if no]
        r.setdefault("species", "")
        r.setdefault("note", "")
        r.setdefault("grade", "")
        r.setdefault("pose", "")
        # **고른 줄이 대표가 아니면 대표를 그린다** (152). 대표 판에 현재
        # 검출이 없을 때가 그 자리다 — 그림·상자만 갈고 번호·지표는 이 줄의
        # 것으로 둔다(섞으면 화면이 다른 판의 숫자를 이 그림 옆에 적는다).
        if (f["rep"] and f["rep"] != (r.get("image_id"), r.get("key"))
                and f["rep_row"]):
            r["view"] = f["rep_row"]
        if f["n"] > 1:
            r["linked_n"] = f["n"]
            # **푸는 데 필요한 것은 `link_id` 하나다** (P16 5.3).
            r["link_id"] = oid
        rows.append(r)

    rows.sort(key=lambda r: (r["group_id"],
                             (r.get("bbox_xywh") or [0, 0])[1],
                             (r.get("bbox_xywh") or [0, 0])[0]))
    return rows


def _member_view(m):
    """멤버 하나를 **그릴 수 있는 모양**으로. 기하가 없으면 `None`.

    `link_mains` 가 쓰던 것과 같은 재료다 — 판정이 `geom` 스냅샷을 스스로 들고
    있어(CLAUDE.md) 그 판에 현재 검출이 없어도 그릴 수 있다.
    """
    geom = m.geom or {}
    box = geom_box(geom) or _key_bbox(m.mask_key)
    if not (box and geom.get("polygon")):
        return None
    return {"rel": m.image.path, "bbox_xywh": list(box),
            "polygon": list(geom["polygon"])}


def object_facts(slide, viewpoint=None) -> dict:
    """개체마다 **대표·앵커·멤버 수** (P18). 열쇠는 개체 id.

    카탈로그가 카드를 개체 단위로 모을 때 쓴다. **한 번만 묻는다**(105) —
    카드마다 물으면 한 판에 질의가 수백 번이다.

    `rep`·`anchor` 는 `(이미지 id, mask_key)` 다. 카드의 줄을 고르는 데 쓰고,
    `anchor_row` 는 번호를 만드는 재료다 — 앵커가 **다른 판**일 수 있으므로
    그 판의 `frame_seq` 가 함께 필요하다.

    **`viewpoint` 를 주면 그 시야만 본다** (183). 검토 화면은 시야 하나를 여는
    자리라 슬라이드 전체를 훑으면 안 쓸 개체를 수천 개 물어 온다 — 카탈로그는
    한 판이 슬라이드 전체라 안 주고, 검토 화면은 준다. **규칙은 한 함수다** —
    갈라 두면 두 화면이 앵커를 다르게 고르는 날이 온다.
    """
    out = {}
    # **경로로 맞춘다** — `Frame` 에는 `Image` 로 가는 FK 가 없고 `path` 가 유일
    # 열쇠다 (`_frames` 와 같은 규칙). 프레임마다 되짚으면 시야 하나에 질의가
    # 프레임 수만큼 는다.
    fseq = dict(Frame.objects.filter(slide=slide).values_list("path", "seq"))
    imgs = Image.objects.filter(viewpoint__slide=slide)
    objs = (ForamObject.objects.filter(viewpoint__slide=slide)
            .select_related("anchor", "viewpoint")
            .prefetch_related("members__image"))
    if viewpoint is not None:
        imgs = imgs.filter(viewpoint=viewpoint)
        objs = objs.filter(viewpoint=viewpoint)
    seqs = {pk: fseq.get(path) for pk, path in imgs.values_list("pk", "path")}
    for obj in objs:
        members = list(obj.members.all())
        if not members:
            continue                       # 유령 — `check_db` 가 센다
        rep = next((m for m in members if m.is_rep), None)
        anchor = obj.anchor
        # **앵커가 이 개체의 멤버가 아니면 안 쓴다.** 그런 상태는 `check_db` 가
        # 세는 고장이고, 그대로 쓰면 남의 판정으로 번호를 만든다.
        if anchor is not None and anchor.foram_object_id != obj.pk:
            anchor = None
        if anchor is None:
            anchor = min(members, key=lambda m: m.pk)
        out[obj.pk] = {
            "n": len(members),
            "rep": (rep.image_id, rep.mask_key) if rep else None,
            # **대표를 그릴 재료.** 대표 판에 현재 검출이 없으면 후보 줄이 없어
            # 카드가 다른 판의 줄로 떨어지는데, 그때도 **그림은 대표여야 한다**
            # (152). 그 자리를 옛 `view` 가 메운다.
            "rep_row": _member_view(rep) if rep else None,
            "anchor": (anchor.image_id, anchor.mask_key),
            "anchor_row": {"group_id": obj.viewpoint.idx,
                           "image_id": anchor.image_id,
                           "key": anchor.mask_key,
                           "batch_id": anchor.batch_id,
                           "source": anchor.source,
                           "frame_seq": seqs.get(anchor.image_id)},
        }
    return out


def attach_catalog(vp: Viewpoint, slide: Slide, by_image: dict) -> None:
    """검토 화면의 후보에 **개체 카탈로그 정보**를 얹는다 (183).

    `by_image` 는 `{이미지 id: 검출 dict}`. 후보 dict 를 **그 자리에서** 고친다 —
    캐러셀이 갈아 끼울 판들(`_review_shot`)이 같은 리스트를 그대로 물고 가므로
    한 번 얹으면 판 전부가 본다.

    **번호는 앵커에서 뽑는다** (P18 · `catalog_rows` 와 같은 규칙). 규칙이 갈라지면
    같은 개체가 검토 화면과 카탈로그 화면에서 다른 번호를 받고, **번호는 논문·표에
    적히는 것이라 그때는 이미 되돌릴 수 없다**(`catalog.py` 머리말).

    **개체가 없는 후보에는 아무것도 안 얹는다.** 아직 아무도 안 본 마스크라
    카드가 없는 것이 맞고(사용자 방침 2026-08-25), 여기서 "번호가 될 값" 을
    미리 적으면 화면이 **없는 카드를 있는 것처럼** 말하게 된다.
    """
    facts = object_facts(slide, viewpoint=vp)
    if not facts:
        return
    layer = layer_codes(slide)
    codes = batch_codes()
    # 개체마다 한 번만 만든다 — 후보마다 만들면 한 시야에서 같은 값을 백 번 짠다
    nos = {oid: catalog_no_for(f["anchor_row"], layer, codes)
           for oid, f in facts.items()}
    for image_id, det in by_image.items():
        for d in (det.get("candidates") or []) + (det.get("removed_candidates") or []):
            f = facts.get(d.get("obj_id"))
            if f is None:
                continue
            no, why = nos[d["obj_id"]]
            d["catalog_no"] = no
            d["catalog_why"] = why
            # **번호가 어느 판의 것인지 적는다** (카드의 `no_from` 과 같은 자리).
            # 개체가 판 여럿에 걸쳐 있어 안 적으면 `f03` 이 이 판을 가리키는
            # 것처럼 읽힌다.
            d["no_from"] = f["anchor_row"]["frame_seq"]
            d["no_here"] = f["anchor"] == (image_id, d["key"])
            d["linked_n"] = f["n"]
            d["is_rep"] = f["rep"] == (image_id, d["key"])


def catalog_no_of(vp: Viewpoint, obj_id: int) -> str:
    """개체 하나의 카탈로그 번호. 못 만들면 빈 문자열 (183).

    **저장 응답이 쓴다.** 검토 화면의 카탈로그 칸에서 처음 적는 순간 개체가
    생기는데(`_catalog_target`), 그때 번호가 안 오면 칸은 "아직 카드가 없습니다"
    인 채로 남는다 — **방금 만든 카드를 없는 것처럼** 말하는 화면이 된다.

    **규칙은 `catalog_rows` 와 같다** — 앵커에서 뽑는다(P18). 한 자리만 고치면
    같은 개체가 두 화면에서 다른 번호를 받는다.
    """
    slide = vp.slide
    f = object_facts(slide, viewpoint=vp).get(obj_id)
    if f is None:
        return ""
    no, _why = catalog_no_for(f["anchor_row"], layer_codes(slide), batch_codes())
    return no or ""


SPECIES_MAX = 120


def check_grade_pose(label: str, grade="", pose="") -> None:
    """이 분류에 **등급·자세를 매길 수 있는가.** 못 매기면 `ValueError`.

    **파편에는 주지 않는다** (2026-08-11 사용자). 파편(`ClassDef.counted=0`)은
    완형을 유추할 수 있어도 확실하지 않고, 무엇보다 *한 개체로 인정하는 규칙을
    만족하지 못한 것*이라 우수성을 물을 자리가 아니다.

    **화면이 칸을 안 보여주는 것으로는 안 막은 것이다** (063). `.tools` 를 CSS 로
    감췄다고 믿었는데 세 화면이 계속 도구를 내보이고 있던 적이 있다(051) — 그때
    사람은 되는 줄 알고 한 시야를 검토했다.

    **비우는 것은 늘 된다.** 분류를 파편으로 바꾸는 길에서 이미 매긴 값을
    걷어내야 하는데, 그 길까지 막으면 고칠 방법이 없어진다.
    """
    grade, pose, label = str(grade or ""), str(pose or ""), str(label or "")
    if grade and grade not in dict(ForamObject.GRADE):
        raise ValueError(f"모르는 등급이다: {grade}")
    if pose and pose not in dict(ForamObject.POSE):
        raise ValueError(f"모르는 자세다: {pose}")
    if not (grade or pose):
        return
    row = next((r for r in _class_rows() if r["key"] == label), None)
    if label and row is None:
        raise ValueError(f"모르는 유형이다: {label}")
    # 분류를 아직 안 정한 개체가 흔하다 — 매기는 순서를 강제하지 않는다.
    if row is not None and not row["counted"]:
        raise ValueError(
            f"파편에는 등급·자세를 매기지 않는다 (유형 {label}). 이미 매긴 것이 "
            f"있으면 먼저 비울 것")


def _catalog_target(vp: Viewpoint, image, key: str):
    """카드가 짚은 판정 행을 가져오거나 세운다. `(행, 이미지 id)` 를 돌려준다.

    **짚는 규칙이 하나여야 한다** (P16 4.1). 카탈로그의 문이 셋으로 늘면서
    (동정 · 지우기 · 일괄) 같은 검사를 세 벌 적게 됐는데, 그러면 한 자리만
    고치는 날에 나머지가 조용히 옛 규칙으로 돈다 — `save_review` 와 이 함수가
    같은 검사를 따로 들고 있다가 053 이 난 자리와 같은 모양이다.

    **`(slug, gid)` 로 시야를 · id 로 이미지를 · `mask_key` 로 개체를** 짚고,
    못 짚으면 `ValueError` 다. 조용히 대표 이미지에 앉히지 않는다.
    """
    if vp is None:
        raise ValueError("모르는 시야다")

    dets = current_detections(vp)
    image_id = getattr(image, "pk", image)
    cur = next((d for d in dets if d.image_id == image_id), None)
    if cur is None:
        # **조용히 대표 이미지에 앉히지 않는다** (`save_review` 와 같은 이유).
        # 사람이 보고 있던 것과 다른 자리에 판단이 쌓인다.
        raise ValueError("그 이미지에는 이 시야의 현재 검출이 없다 — 저장하지 않았다")
    batch = cur.batch
    if batch is None:
        raise ValueError("이 시야의 현재 검출이 묶음에 안 들어 있다 — 저장하지 않았다")

    key = str(key or "")
    cand = next((c for c in cur.candidates.all() if c.mask_key == key), None)
    # **사람이 그린 개체는 `batch=NULL` 에 산다** (P09 5.2). 카드에는 함께
    # 나오므로 저장도 그 줄을 찾아가야 한다 — 엔진 줄에 앉히면 같은 키로 행이
    # 둘이 되고 화면에 두 번 나온다.
    manual = ObjectReview.objects.filter(
        image=image_id, batch__isnull=True, mask_key=key).first()
    row_batch = None if manual else batch

    obj = (manual or ObjectReview.objects.filter(
        image=image_id, batch=batch, mask_key=key)
        .select_related("foram_object", "foram_object__taxon").first())
    if obj is None:
        # **현재 검출에 없는 키는 받지 않는다** (`save_review` 와 같은 문).
        # 다른 화면을 보고 보낸 것이고, 받으면 화면에 없는 개체가 생긴다.
        if cand is None:
            raise ValueError(
                f"현재 검출에 없는 개체다 — 저장하지 않았다 (키 {key})")
        obj = judgement_for(vp, image_id, row_batch, key, cand)
        # 기하는 모든 교정 행이 들고 있는다 — 검출기가 바뀌어도 읽혀야 한다
        obj.geom = {"bbox": cand.bbox_xywh, "polygon": cand.polygon}
    return obj, image_id


def _catalog_prune(obj) -> bool:
    """표시가 하나도 안 남았으면 그 줄을 지운다. 남겼으면 `True`.

    **사람이 그린 개체는 남긴다** — 그 줄이 곧 개체다.

    **묶음의 멤버도 남긴다** (P12). 소속이 곧 이 행이라, 값을 비웠다는 이유로
    지우면 **묶음에서 한 판이 조용히 빠진다.** 묶음을 푸는 문은 `/link` 이고
    카탈로그 카드가 아니다 — `save_review` 의 청소가 얹는 것과 같은 갈래다.
    """
    dobj = obj.foram_object
    linked = dobj.members.count() > 1
    empty = not (obj.removed or obj.accepted or obj.auto_confirmed or dobj.label
                 or dobj.note or dobj.species or obj.geom_edited or linked
                 # **등급·자세도 표시다** — 안 세면 종명을 비우는 한 번에 등급까지
                 # 함께 사라진다. 105 가 이 자리에서 실패 둘을 봤다.
                 or dobj.grade or dobj.pose)
    if empty and obj.source != "manual":
        oid = obj.foram_object_id
        obj.delete()
        prune_objects([oid])
        # 지운 줄이 앵커였으면 남은 멤버로 넘긴다 (P18).
        reanchor([oid])
        return False
    return True


def set_catalog_removed(vp: Viewpoint, image, key: str, removed: bool) -> dict:
    """개체 **하나**를 오검출로 지우거나, 지운 것을 되돌린다 (P16 5.1).

    ## `/review` 를 안 쓰는 이유는 `save_catalog_entry` 와 같다

    그 POST 는 **그 (이미지, 묶음) 의 교정 전체를 갈아치운다.** 카드는 그 시야의
    교정 전체를 알지 못하므로 그 전제를 만들 수가 없다. 여기는 짚은 한 줄만
    건드린다.

    ## `accepted` 가 아니라 `removed` 다

    **축이 둘이다.** `accepted` 는 *엔진이 떨어뜨린 것을 사람이 되살린* 것이고
    (문턱 아래 후보), 여기는 *통과한 것을 사람이 지운* 것이다. 섞으면 탈락
    펼침판이 세는 수가 어긋난다.

    ## 묶인 개체는 못 지운다

    `save_object_link` 가 **지운 마스크는 못 묶는다**로 막아 둔 것의 반대쪽이다 —
    묶어 놓고 지우면 *이 개체는 오검출이면서 실재한다* 가 된다. 거절하면서
    **몇 장이 걸렸는지 말한다**: 무엇을 먼저 치워야 하는지 모르면 사람은 같은
    단추를 다시 누른다 (063).

    되돌려서 표시가 하나도 안 남으면 그 줄을 지운다 (`_catalog_prune`).
    """
    obj, _image_id = _catalog_target(vp, image, key)

    if removed:
        n = obj.foram_object.members.count()
        if n > 1:
            raise ValueError(
                f"묶음 {n}장에 들어 있는 개체다 — 먼저 묶음을 푸세요")

    obj.removed = bool(removed)
    # **`geom` 도 함께 쓴다** (180 C1 · 2026-09-03). `_catalog_target` 이 새 줄을
    # 세울 때 후보의 기하를 얹어 주는데, `update_fields=["removed"]` 로 저장하면
    # 그 값이 안 써져 **기하 없는 줄**이 앉는다. 같은 문을 쓰는
    # `save_catalog_entry` 는 전체 `save()` 라 남으므로, **부르는 쪽에 따라 행이
    # 기하를 갖거나 못 갖는** 상태였다.
    #
    # 기하 없는 교정은 재검출로 후보를 잃는 순간 **폴리곤 없는 음성 표본**이
    # 된다 — 모든 교정 행이 `geom` 을 스스로 들고 있어야 검출기가 바뀌어도
    # 읽힌다(P02 §2.7).
    obj.save(update_fields=["removed", "geom", "updated_at"])
    kept = _catalog_prune(obj)
    return {"removed": bool(removed), "kept": kept}


def save_catalog_entry(vp: Viewpoint, image, key: str, *, species=None,
                       cls=None, note=None, grade=None, pose=None) -> dict:
    """개체 **하나**의 동정을 고친다 (개체 카탈로그 6단계).

    ## 왜 `/review` 를 안 쓰는가

    그 POST 는 **그 (이미지, 묶음) 의 교정 전체를 갈아치운다** — "뷰어는 늘
    전체를 보낸다" 가 전제다. 017·027·053 이 전부 그 줄에서 났고 두 번은 운영
    자료를 잃었다(14건 · 37건). 카탈로그는 카드 하나를 고치는 화면이라 그 전제를
    만들 수 없고, 만들 이유도 없다.

    **여기는 짚은 개체 한 줄만 건드린다.** 지우는 범위가 없으므로 그 계열의
    사고가 구조적으로 안 생긴다.

    ## 무엇을 고치는가

    `species`(종명) · `cls`(유형) · `note`(코멘트) 셋. `None` 은 **안 고친다** 는
    말이고 `""` 는 **비운다** 는 말이다 — 둘을 같이 다루면 카드가 안 보내는 칸을
    저장이 지운다(`drawn` 과 같은 규칙).

    **삭제·되살림·기하는 안 건드린다.** 기하는 검토 화면이 하는 판단이고,
    삭제·되살림은 **층이 달라 문을 갈랐다** (P16 4.2 · `set_catalog_removed`) —
    이 다섯은 개체(`ForamObject`)에 붙고 삭제는 `(이미지, 묶음, mask_key)` 에
    붙는다. 층이 다른 것을 한 payload 에 실으면 116 이 난 자리가 된다.

    ## 아무것도 안 남으면 그 줄을 지운다

    `save_review` 와 같은 규칙이다 — 표시가 사라진 행을 남겨 두면 "교정 전체
    초기화" 가 안 되고 그 행을 세는 자리가 어긋난다. **사람이 그린 개체는
    예외다**: 그 줄이 곧 개체라서 지우면 개체가 사라진다.
    """
    if vp is None:
        raise ValueError("모르는 시야다")

    obj, image_id = _catalog_target(vp, image, key)

    # **분류·종명은 개체에 적는다** (P12). 묶여 있으면 그 개체를 나눠 가진 다른
    # 판들이 같은 값을 보게 된다 — 104 가 저장할 때마다 번지게 하던 일이
    # 구조로 바뀐 자리다. **번질 것이 없으니 전파 코드도 없다.**
    dobj = obj.foram_object
    if species is not None:
        # **종은 `Taxon` 행이다** (P01 2절 ③). 이름을 받아 찾는다 — 없는 이름은
        # 거절한다. 쓰인 학명은 자동완성 목록에 켜진다 (`resolve_taxon`)
        dobj.taxon = resolve_taxon(species)
    if cls is not None:
        cls = str(cls)
        # **모듈 수준 `CLASSES` 는 `__getattr__` 로만 난다** — 이 파일 안에서
        # 맨 이름으로 부르면 `NameError` 다. 밖에서 `data.CLASSES` 로 쓰는 것과
        # 다르다.
        if cls and cls not in [r["key"] for r in _class_rows()]:
            raise ValueError(f"모르는 유형이다: {cls}")
        dobj.label = cls
    if note is not None:
        # **코멘트도 개체에 앉는다** (0036). 분류·종명과 같은 자리가 됐다 —
        # 판마다 다른 말을 적는 칸이 아니라 *이 개체을 두고 하는 말*이다.
        dobj.note = str(note).replace("\r\n", "\n").strip()
    # **등급도 자세도 개체에 앉는다** (0035). 둘 다 *이 개체이 어떤가* 이고,
    # 어느 판으로 보여줄지는 `is_rep` 가 따로 말한다.
    if grade is not None:
        dobj.grade = str(grade).strip()
    if pose is not None:
        dobj.pose = str(pose).strip()
    # **고친 뒤의 모습으로 검사한다.** 이 한 번의 저장이 분류를 파편으로 바꾸면서
    # 등급은 그대로 두는 길이 있는데, 보낸 값만 보면 그것이 통과한다 — 남는 것은
    # "파편인데 A" 인 행이다.
    check_grade_pose(dobj.label, dobj.grade, dobj.pose)
    dobj.save(update_fields=["label", "taxon", "pose", "grade", "note",
                             "updated_at"])
    obj.save()

    # 화면이 고쳐야 할 다른 판들 — **쓰지 않는다, 알리기만 한다** (P12).
    # 개체가 값을 들고 있으므로 이미 다 반영돼 있지만, 다른 판의 화면 상태는
    # 열릴 때 받은 것이라 그것을 모른다. 그 판에서 다음 저장이 나가면 자기가
    # 아는 빈 값을 보내 **방금 적은 것을 지운다** (104 에서 겪은 자리다).
    # **코멘트도 번진다** (0036). 안 세면 다른 판의 화면이 방금 적은 글을 모른
    # 채로 다음 저장을 내보내 **그 자리에서 지운다** — 분류가 그랬던 자리다(104).
    spread = ({} if (cls is None and species is None and note is None)
              else linked_siblings(dobj, image_id))

    # 표시가 하나도 안 남았으면 지운다 (`_catalog_prune` — 지우기 쪽과 같은 규칙).
    n_spread = sum(len(v) for v in spread.values())
    if not _catalog_prune(obj):
        return {"species": "", "cls": "", "note": "", "grade": "", "pose": "",
                # 줄이 지워졌으면 카드도 없다 — 번호도 개체도 있을 자리가 아니다
                "catalog_no": "", "obj_id": None,
                "kept": False, "spread": n_spread}
    return {"species": dobj.species, "cls": dobj.label, "note": dobj.note,
            "grade": dobj.grade, "pose": dobj.pose,
            # **번호와 개체 id 를 함께 낸다** (183). 검토 화면의 카탈로그 칸은
            # 처음 적는 순간 개체가 생기는 자리라, 안 주면 방금 만든 카드가
            # 화면에서 "없는 것" 으로 남는다 — 그 화면은 `obj_id` 로 카드가
            # 있는지를 가른다.
            "catalog_no": catalog_no_of(vp, dobj.pk),
            "obj_id": dobj.pk,
            "kept": True, "spread": n_spread}


def species_seen(slug: str = "") -> list[str]:
    """자동완성 목록 — **켠 학명**(`Taxon.active`) 전부다.

    DiaRUGA 는 이미 적은 종명 문자열을 모아 줬다. 여기서는 `Taxon` 표가 있어
    **쓰는 것만 켠 목록**이 그 자리다 (P01 5절) — 동정에 한 번 쓰이면
    `resolve_taxon` 이 켜므로 "이미 쓴 것" 은 이 목록에 다 들어 있다.
    `slug` 는 DiaRUGA 와 같은 이유로 안 본다 — 옆 시료에서 쓴 이름이야말로
    지금 쓰려는 것이다.
    """
    return list(Taxon.objects.filter(active=True)
                .values_list("name", flat=True).order_by("name"))


def resolve_taxon(name, *, activate: bool = True):
    """이름 → `Taxon` 행. 규칙은 `Taxon.resolve` 하나다 (없는 이름은 `ValueError`)."""
    return Taxon.resolve(name, activate=activate)


# --- 배율 --------------------------------------------------------------------
def scales_by_slide() -> dict:
    """슬라이드마다의 µm/px. 배율을 바꿔 찍으면 슬라이드마다 다르다.

    **검출 행에서 세고, 검출이 없는 슬라이드는 프레임에서 센다** — 검출 전에도
    목록에 배율이 보여야 한다. 한 슬라이드 안에 값이 갈리면 가장 많은 쪽을 쓴다
    — 갈린 것 자체는 `check_db` 가 따로 잡는다.
    """
    per: dict[str, dict[float, int]] = {}
    seen = set()
    for slug, um in (Detection.objects.reviewing().filter(um_per_pixel__isnull=False)
                     .values_list("viewpoint__slide__slug", "um_per_pixel")):
        per.setdefault(slug, {})
        k = round(um, 9)
        per[slug][k] = per[slug].get(k, 0) + 1
        seen.add(slug)
    for slug, um in (Frame.objects.filter(um_per_pixel__isnull=False)
                     .exclude(slide__slug__in=seen)
                     .values_list("slide__slug", "um_per_pixel")):
        per.setdefault(slug, {})
        k = round(um, 9)
        per[slug][k] = per[slug].get(k, 0) + 1
    return {slug: max(c, key=c.get) for slug, c in per.items()}


def preview_detection(vp: Viewpoint) -> dict | None:
    """검출 전에도 같은 화면을 쓰려고 만드는 빈 검출.

    자동 처리가 도는 동안에도 사람은 "무엇이 찍혔나" 를 봐야 한다. 그렇다고 화면을
    따로 만들 이유는 없다 — 캐러셀(합성본·프레임)은 `_shots.html` 이
    `stack`·`frames` 로 그리고 검출과 무관하다. 개체가 0개인 검출을 넘겨 주면
    같은 화면이 그대로 돌고, 검토 도구만 CSS 로 잠그면 된다.

    크기는 합성본에서 읽는다. `Frame.width/height` 는 아직 비어 있고, 겹쳐 그릴
    개체가 없으므로 못 읽어도 화면은 멀쩡하다.
    """
    st = getattr(vp, "stack", None)
    if st is None or not st.focused_path:
        return None

    w = h = None
    try:
        from PIL import Image                                 # noqa: PLC0415
        with Image.open(Path(settings.DATA_ROOT) / st.focused_path) as im:
            w, h = im.size          # 헤더만 읽는다 — 픽셀을 디코딩하지 않는다
    except (OSError, ValueError):
        pass

    done, note = review_state(vp)
    return {
        "image": st.focused_path,
        "stem": Path(st.focused_path).stem,
        "size": [w, h],
        "scale": st.resize_scale or 1.0,
        "um_per_pixel": st.um_per_pixel,
        "um_per_pixel_native": st.native_um_per_pixel,
        "um_per_pixel_source": st.um_per_pixel_source or None,
        "um_per_pixel_backfilled": None,
        "n_raw_masks": 0, "n_sized": 0, "n_auto": 0, "n_candidates": 0,
        "counts": {}, "thresholds": {},
        "candidates": [], "removed_candidates": [], "rejected": [],
        "n_removed": 0, "accepted_keys": [], "labels": {}, "notes": {},
        "review_done": done,
        "review_note": note,
        "source_dir": "out",
        # 이 화면은 아직 검출이 없다 — 템플릿이 도구를 감추는 데 쓴다
        "preview_only": True,
    }


def batches_to_run() -> list[dict]:
    """새 자료가 들어왔을 때 **어떤 순서로 어느 묶음을 채우는가** (DiaRUGA 079).

    검토 중인 묶음이 먼저, 나머지는 최근 것부터. **조리법이 없으면 안 돈다.**
    가중치 파일이 없으면 목록에 남기되 `ready=False` 와 이유를 함께 준다.
    """
    root = Path(settings.DATA_ROOT)
    rows = []
    for b in RunBatch.objects.filter(kind="detect").exclude(recipe={}):
        r = dict(b.recipe)
        ready, why = True, ""
        w = r.get("weights")
        if r.get("backend", "yolo") == "yolo":
            if not w:
                ready, why = False, "조리법에 가중치가 없다"
            else:
                path = Path(w) if Path(w).is_absolute() else root / w
                if not path.exists():
                    ready, why = False, f"가중치 파일이 없다: {path}"
        rows.append({"batch": b, "recipe": r, "ready": ready, "why": why})
    rows.sort(key=lambda x: (not x["batch"].for_review,
                             -x["batch"].started_at.timestamp()))
    return rows


def review_state(vp, batch_id=None):
    """그 시야의 `(검토 완료, 시야 코멘트)` — **완료는 묶음마다다** (073).

    `done` 은 "이 묶음이 낸 검출을 여기서 다 봤다" 이고, `note` 는 "이 시야가
    이러이러하다" 이다. 뒤엣것은 묶음을 갈아도 참이라 `batch=NULL` 줄에 산다
    (`ViewpointReview` 머리말).

    **읽는 자리를 하나로 모은다** — 흩어져 있으면 뜻이 바뀔 때 전부 틀리는데
    예외가 안 난다(P10 1단계에서 `is_current` 로 같은 일을 했다).
    """
    if batch_id is None:
        batch_id = review_batch_id()
    done, note = False, ""
    for r in ViewpointReview.objects.filter(viewpoint=vp):
        if r.batch_id is None:
            note = r.note
        elif r.batch_id == batch_id:
            done = r.done
    return done, note


def done_viewpoints(batch_id=None, *, slide=None, ids=None) -> set:
    """검토 완료로 표시된 시야 pk 들. **고른 묶음 것만 센다** (073)."""
    if batch_id is None:
        batch_id = review_batch_id()
    if batch_id is None:
        return set()
    qs = ViewpointReview.objects.filter(done=True, batch_id=batch_id)
    if slide is not None:
        qs = qs.filter(viewpoint__slide=slide)
    if ids is not None:
        qs = qs.filter(viewpoint_id__in=ids)
    return set(qs.values_list("viewpoint_id", flat=True))


def review_batch_label() -> str:
    return (RunBatch.objects.filter(for_review=True)
            .values_list("label", flat=True).first() or "")


def batches_elsewhere(slide=None, vp=None) -> dict:
    """시야 pk → **검토 대상이 아닌 묶음 중 검출이 있는 것**들의 이름 (DiaRUGA P10).
    검출이 아예 없는 것과 다른 묶음에는 있는 것은 다른 말이다."""
    rb = review_batch_id()
    qs = Detection.objects.filter(is_current=True)
    if slide is not None:
        qs = qs.filter(viewpoint__slide=slide)
    if vp is not None:
        qs = qs.filter(viewpoint=vp)
    if rb is not None:
        qs = qs.exclude(run__batch_id=rb)
    out = defaultdict(list)
    for vp_id, label in (qs.values_list("viewpoint_id", "run__batch__label")
                         .distinct()):
        if label and label not in out[vp_id]:
            out[vp_id].append(label)
    return out


def review_batch_id():
    """검토 대상 묶음의 pk. 없으면 `None` (DiaRUGA P10). 서버 설정이라 요청마다 한 번."""
    return (RunBatch.objects.filter(for_review=True)
            .values_list("id", flat=True).first())


# --- 파이프라인 상태 (시스템 설정 · 파이프라인) ------------------------------
def pipeline_status() -> dict:
    """파이프라인이 지금 어떤 상태인가 (DiaRUGA 098).

    셋을 모은다 — 정찰(`logs/last_scan.json` 의 나이) · 실행(`Run` 최근 것) ·
    밀린 슬라이드(`done` 이 아닌 것 전부와 각각의 진행). 해석은 화면이 먼저
    한다(경고 줄).
    """
    now = timezone.now()

    scan = {"exists": False, "age_min": None, "slides": [], "error": ""}
    scan_path = Path(settings.DATA_ROOT) / "logs" / "last_scan.json"
    try:
        st = scan_path.stat()
        scan["exists"] = True
        scan["age_min"] = round((now.timestamp() - st.st_mtime) / 60, 1)
        d = json.loads(scan_path.read_text(encoding="utf-8"))
        scan["slides"] = [{"rel": r.get("rel", ""), "state": r.get("state", ""),
                          "jpgs": r.get("jpgs", 0),
                          "stable_min": round(r.get("stable_min") or 0, 1)}
                         for r in d.get("slides", [])]
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        scan["error"] = f"{type(e).__name__}: {e}"

    runs = []
    for r in (Run.objects.select_related("slide", "batch")
              .order_by("-started_at")[:12]):
        dur = None
        if r.finished_at:
            dur = round((r.finished_at - r.started_at).total_seconds() / 60, 1)
        runs.append({
            "kind": r.kind, "status": r.status,
            "slide": r.slide.slug if r.slide else "",
            "batch": r.batch.label if r.batch else "",
            "started_at": r.started_at, "finished_at": r.finished_at,
            "minutes": dur,
            "error": (r.error or "")[:200],
        })

    busy = []
    for sl in Slide.objects.exclude(state="done").order_by("pk"):
        vps = Viewpoint.objects.filter(slide=sl).count()
        busy.append({
            "slug": sl.slug, "name": sl.name, "state": sl.state,
            "note": sl.state_note or "",
            "n_frames": Frame.objects.filter(slide=sl).count(),
            "n_vps": vps,
            "n_stacks": Stack.objects.filter(viewpoint__slide=sl).count(),
            "n_det_vps": (Detection.objects.filter(viewpoint__slide=sl, is_current=True)
                          .values("viewpoint_id").distinct().count()),
            "discovered_at": sl.discovered_at, "copied_at": sl.copied_at,
        })

    last_done = (Run.objects.exclude(finished_at=None)
                 .order_by("-finished_at").first())

    warnings = []
    running = [r for r in runs if r["status"] == "running"]
    if (scan["exists"] and scan["age_min"] is not None
            and scan["age_min"] > 5 and not running):
        warnings.append(f"정찰이 {scan['age_min']:.0f}분째 없다 — 폴러가 멈춰 "
                        "있을 수 있다 (cron 은 1분마다 돈다)")
    if busy and not running:
        newest = last_done.finished_at if last_done else None
        idle_min = ((now - newest).total_seconds() / 60) if newest else None
        if idle_min is None or idle_min > 15:
            warnings.append(
                f"끝나지 않은 슬라이드가 {len(busy)}개 있는데 도는 실행이 없다 — "
                "폴러가 데리러 오지 않는 상태일 수 있다 (DiaRUGA 097 이 그 모양이었다)")

    return {"scan": scan, "runs": runs, "busy": busy,
            "last_done": last_done, "warnings": warnings, "now": now}


def object_links_of(vp) -> list[dict]:
    """이 시야의 같은 개체 묶음 (P11). 화면이 그대로 쓰는 모양으로 낸다.

    멤버는 `(image, mask_key)` 로 화면의 마스크와 만난다 — 화면 쪽 `keyOf` 와
    같은 규칙이다. batch 는 안 낸다: 화면은 검토 대상 묶음 하나만 보고 있고,
    다른 묶음의 마스크는 애초에 안 그려진다.

    **멤버가 둘 이상인 것만 낸다.** P12 이후 판정마다 개체가 하나씩 서지만
    화면이 말하는 "묶음" 은 여전히 *여러 판이 한 개체* 인 것이다 — 1:1 까지
    내면 화면이 마스크마다 묶음 표시를 그린다.
    """
    out = []
    for l in (ForamObject.objects.filter(viewpoint=vp)
              .annotate(n_members=Count("members")).filter(n_members__gte=2)
              .prefetch_related("members")):
        out.append({
            "id": l.pk,
            "members": [{"image": m.image_id, "mask_key": m.mask_key,
                         "rep": m.is_rep} for m in l.members.all()],
        })
    return out


def current_detections(vp: Viewpoint, batch_id=None) -> list:
    """그 시야의 현재 검출들. **여럿일 수 있다** (P09 1단계).

    합성본에 하나 + 프레임마다 하나가 된다 — YOLO 는 합성본이 아니라 원본
    프레임을 보므로 갈아타면 그 모양이다(실측: `yolo-3차` 는 시야 452개에 프레임
    검출 1,310개, 합성본 검출 314개).

    **이미지마다 하나라는 것이 불변식이다.** 0025 마이그레이션이 그것을 확인하고
    통과했다 — 어긋나면 어느 묶음의 판단인지 정할 수 없어 교정을 못 앉힌다.

    `batch_id` 를 주면 **그 묶음**을 본다. 안 주면 검토 대상 묶음이고, 그것이
    지금까지의 뜻 그대로다 — **기본값을 바꾸면 안 된다.** 부르는 자리가 열 곳이
    넘고, 뜻이 바뀌는데 자리가 흩어져 있으면 전부 틀리면서 예외는 안 난다.

    묶음을 짚어 부르는 것은 **개체 카탈로그**다 (2026-08-10). 검토는 한 번에 한
    묶음이지만 동정은 엔진마다 따로 채우기로 했고, 그러려면 검토 대상이 아닌
    묶음도 읽을 수 있어야 한다.
    """
    dets = [d for d in vp.detections.all() if d.is_current]
    # **`is_current` 하나로는 모자란다** (P10). 그것은 "그 묶음 안에서 최신"
    # 이고, 어느 묶음을 볼지는 `RunBatch.for_review` 가 정한다 — 화면이 보는
    # 것은 **둘 다 켜진 검출**이다.
    #
    # 검토 대상이 없으면 **빈 목록**이다. 조용히 아무 묶음이나 보여주면 사람이
    # 무엇을 검토하고 있는지 모른 채 교정을 쌓는다 (P10 3.6). 그 상태는
    # `check_db.py` 가 센다.
    rb = review_batch_id() if batch_id is None else batch_id
    if rb is None:
        return []
    bmap = batch_ids_of(dets)
    return [d for d in dets if bmap.get(d.pk) == rb]


def representative_detection(vp: Viewpoint, dets=None):
    """집계가 세는 검출 하나 (P09 5.3).

    **검토는 모든 이미지에서 하되 집계는 시야마다 하나에서 낸다.** 합성본 1 +
    프레임 3.6 을 다 세면 같은 개체이 4.6번 세어져 밀도가 그만큼 부푼다 —
    **학습 자료로는 맞고 계측 통계로는 틀리다.**

    합성본이 있으면 합성본이다. 없으면(싱글턴 시야 153개) 그 프레임이다.
    지금 `is_current` 가 시야당 하나인 것이 하던 역할 그대로다.
    """
    dets = current_detections(vp) if dets is None else dets
    return (next((d for d in dets if d.image and d.image.kind == "stack"), None)
            or next(iter(dets), None))


def batch_ids_of(dets) -> dict:
    """검출 pk → 묶음 pk. **조인을 한 번에 한다.**

    `Detection.batch` 는 `run` 을 타고 두 번 조인한다. 프레임마다 부르면 시야
    하나에 조인이 열 번씩 걸린다 — 개수를 세려고 자료를 물질화하지 말라는 것과
    같은 이야기다(CLAUDE.md).
    """
    run_ids = {d.run_id for d in dets if d.run_id}
    if not run_ids:
        return {d.pk: None for d in dets}
    by_run = dict(Run.objects.filter(id__in=run_ids)
                  .values_list("id", "batch_id"))
    return {d.pk: by_run.get(d.run_id) for d in dets}


def detection_for_viewpoint(vp: Viewpoint, image=None) -> dict | None:
    """시야에 붙은 현재 검출 결과 (교정 반영).

    **`image` 를 주면 그 이미지의 것을 낸다** (P09 1단계). 안 주면 대표 이미지의
    것이다 — 합성본이 있으면 합성본, 없으면 그 프레임.
    """
    dets = current_detections(vp)
    if image is not None:
        image_id = getattr(image, "pk", image)
        det = next((d for d in dets if d.image_id == image_id), None)
    else:
        det = representative_detection(vp, dets)
    if det is None:
        return None
    return _with_reviews(vp, det, batch_ids_of([det]).get(det.pk))


def _with_reviews(vp: Viewpoint, det, batch_id) -> dict:
    """검출 하나에 **그 검출의 교정만** 얹는다.

    **교정을 `(image, batch)` 로 거른다.** 안 거르면 두 가지가 화면에 새어 든다:

    - **다른 이미지의 교정** — 합성본에서 한 판단이 프레임 화면에 얹혀 보인다.
      같은 시야라 좌표계가 같아서 `mask_key` 가 실제로 맞는다
    - **다른 묶음의 교정** — SAM2 시절 "오검출" 이 YOLO 검출에 얹힌다. 사람은
      **자기가 지우지 않은 것이 지워져 있는 것**을 본다(실측 1,076건, P09 4.4)

    둘 다 예외가 안 나고 **그럴듯한 화면**이 나오는 종류다.
    """
    # **사람이 그린 개체는 묶음을 안 가린다** (P09 5.2). `batch=NULL` 은 어느
    # 회차에도 안 속한다 — 엔진에 대한 판단이 아니라 **이미지에 대한 사실**이라
    # 회차를 갈아타도 그 자리에 있어야 한다. 거르면 화면에서 사라지고, 사라지면
    # 다음 저장에 지워진다(2단계에서 본 그 길이다).
    reviews = {o.mask_key: o for o in reviews_of(vp)
               if o.image_id == det.image_id
               and (o.batch_id == batch_id or o.batch_id is None)}
    # **`batch_id` 를 넘긴다.** 안 넘기면 `review_state` 가 검토 대상 묶음을
    # 시야마다 다시 묻는다 — 크롭 화면 한 판에 390~560번이었다. 그리고 뜻으로도
    # 이쪽이 맞다: 지금 보고 있는 검출의 묶음에 대한 완료 표시여야 한다.
    out = _apply_review(det, reviews, review_state(vp, batch_id))
    # **어느 묶음을 보고 있는가.** 개체 카탈로그가 번호의 꼬리를 여기서 얻고,
    # 저장할 때 `(이미지, 묶음, 키)` 로 개체를 짚는다. `_apply_review` 는 검출
    # 하나만 알고 묶음은 부르는 쪽이 짚어 주므로 여기서 얹는다.
    out["batch_id"] = batch_id
    return out


def review_blocked(stem_or_slide) -> str:
    """검토를 막아야 하면 그 이유를, 아니면 빈 문자열을 돌려준다.

    슬라이드(최상위 폴더) 하나를 단위로 연다. 자동으로 할 수 있는 처리 — 그룹핑,
    합성, 검출, 문턱 적용 — 이 다 끝나야 `state="done"` 이 되고, 그때 검토가 열린다
    (P01 §1, `segment_forams.mark_done_if_complete`).

    반쯤 처리된 슬라이드를 검토하면 아직 안 돌아간 시야의 검출이 뒤늦게 들어오면서
    이미 본 화면이 바뀐다. 사람의 판단이 재생성 불가라 그 상황을 만들면 안 된다.
    """
    slide = stem_or_slide
    if isinstance(stem_or_slide, str):
        vp = _viewpoint_of(stem_or_slide)
        if vp is None:
            return ""
        slide = vp.slide
    if slide is None or slide.state == "done":
        return ""
    if slide.state == "failed":
        why = slide.state_note or "원인을 확인해야 합니다"
        return (f"자동 처리 중에 확인이 필요한 문제가 생겼습니다 — {why}. "
                f"확인하신 뒤에 검토를 열 수 있습니다.")
    note = f" ({slide.state_note})" if slide.state_note else ""
    return (f"자동 처리가 아직 끝나지 않았습니다{note}. "
            f"끝나는 대로 검토를 열겠습니다.")


def _viewpoints_by_stem(stem: str) -> list:
    """stem 이 가리키는 시야들. **여럿이면 여럿을 그대로 돌려준다.**

    합성본은 `<tag>_focused`, 싱글턴은 프레임 이름(`Snap-21171`)이다.

    **프레임 이름은 슬라이드끼리 겹친다** — 카메라 일련번호라 같은 날 이어 찍으면
    번호대가 이어진다(260803 두 슬라이드에서 143종). `Frame` 에 `(slide, name)`
    유일 제약이 있는 것이 곧 "이름만으로는 못 찾는다" 는 뜻인데, 여기가 그 제약을
    안 쓰고 `.first()` 로 아무거나 집고 있었다. 고르는 일은 부르는 쪽에 맡긴다.
    """
    qs = (Viewpoint.objects
          .prefetch_related("detections__candidates", _reviews_prefetch()))
    if stem.endswith("_focused"):
        return list(qs.filter(tag=stem[: -len("_focused")]))
    ids = (Frame.objects.filter(name=stem)
           .values_list("viewpoint_id", flat=True).distinct())
    return list(qs.filter(id__in=list(ids)))


def _viewpoint_of(stem: str) -> Viewpoint | None:
    """stem 하나로 시야를 찾는다. **모호하면 None 이다.**

    이름이 겹쳐 둘 이상이 나오면 **아무것도 돌려주지 않는다.** 아무거나 집으면
    엉뚱한 시야가 열리고, 그 화면에서 저장하면 `save_review` 가 그 시야의 교정을
    통째로 갈아치운다 — 사본에서 재현했다: 싱글턴 시야 135개 중 12개가 자기
    stem 으로 자기를 못 찾았고, "검토 완료" 만 누른 빈 payload 하나가 남의 시야
    교정 7건을 지웠다(2026-08-05). 017·027 과 같은 계열이다.

    **부르는 쪽은 `find_viewpoint()` 를 쓴다** — 거기는 `(slug, gid)` 로 짚어서
    모호할 일이 없다.
    """
    vps = _viewpoints_by_stem(stem)
    return vps[0] if len(vps) == 1 else None


def find_viewpoint(stem: str = "", slug: str = "",
                   gid=None) -> tuple[Viewpoint | None, str]:
    """저장 요청이 가리키는 시야. `(시야, 오류)` 를 돌려준다.

    **`(slug, gid)` 가 있으면 그것이 정답이다.** stem 은 이름이라 겹칠 수 있지만
    슬라이드 슬러그와 시야 번호는 주소 그 자체다 — 화면이 이미 둘 다 알고 있으므로
    보내지 못할 이유가 없다.

    stem 은 그때 **검증용**으로만 쓴다: 그 시야의 현재 검출 이미지와 다르면
    "다른 화면을 보고 보낸 것" 이므로 받지 않는다. 화면과 저장 대상이 어긋난
    채로 통과하는 길을 하나도 남기지 않기 위해서다.

    `(slug, gid)` 가 없으면 stem 으로 찾되 **모호하면 거절한다.**
    """
    if slug and gid is not None:
        vp = (Viewpoint.objects
              .prefetch_related("detections__candidates", _reviews_prefetch())
              .filter(slide__slug=slug, idx=gid).first())
        if vp is None:
            return None, f"모르는 시야다: {slug} g{gid}"
        if stem:
            # **시야의 현재 검출은 여럿이다** (P09 1단계 · 055). 이미지마다 하나씩
            # 있고, 화면은 캐러셀로 그중 하나를 띄운다. 그런데 여기서 `.first()`
            # 로 하나만 집어 비교하고 있었다 — 053 과 같은 실수다.
            #
            # 그래서 **프레임을 보며 한 교정이 통째로 거절됐다**: 화면이 프레임
            # 판으로 렌더되면 `stem` 이 `Snap-22119` 인데 집힌 것은 합성본이라
            # 409 였다. 사람 쪽에서는 마스크가 지워진 채로 보이고 회색 글씨
            # 한 줄만 지나가서, 오검출 둘을 지운 것이 DB 에 하나도 안 남았다
            # (실사용 보고 2026-08-10 · am22-gc10b 25cm g0).
            #
            # 견줄 것은 **그 시야가 가진 판들**이다. 그중 하나면 "이 시야를 보고
            # 보낸 것" 이 맞고, 어느 판에 앉힐지는 `image` 가 따로 짚는다
            # (`save_review` 가 그 이미지에 현재 검출이 없으면 거절한다).
            stems = {Path(d.image_path).stem
                     for d in vp.detections.all() if d.is_current and d.image_path}
            # 검출이 아직 없는 시야(검토 준비 중)는 견줄 대상이 없다. 그쪽은
            # `review_blocked` 가 따로 막는다.
            if stems and stem not in stems:
                return None, (f"화면과 저장 대상이 어긋난다 — {slug} g{gid} 의 "
                              f"판은 {', '.join(sorted(stems))} 인데 "
                              f"{stem} 을 보냈다")
        return vp, ""

    vps = _viewpoints_by_stem(stem)
    if not vps:
        return None, f"모르는 이미지다: {stem}"
    if len(vps) > 1:
        where = ", ".join(f"{v.slide.slug} g{v.idx}" for v in vps[:4])
        return None, (f"이 이름의 시야가 {len(vps)}개다 — 어느 것인지 알 수 없어 "
                      f"저장하지 않았다 ({where}). 화면을 새로고침할 것")
    return vps[0], ""


def detection_for(stem: str) -> dict | None:
    """stem 으로 찾는다. 예전 시그니처를 유지하려고 남겨 둔 경로다."""
    vp = _viewpoint_of(stem)
    return detection_for_viewpoint(vp) if vp else None


def _stack_dict(st: Stack, det: dict | None) -> dict:
    return {
        "focused_rel": st.focused_path,
        "depth_rel": st.depth_path or None,
        "stem": Path(st.focused_path).stem,
        "detection": det,
    }


def stack_for(tag: str) -> dict | None:
    st = (Stack.objects.filter(viewpoint__tag=tag)
          .select_related("viewpoint")
          .prefetch_related("viewpoint__detections__candidates",
                            "viewpoint__object_reviews__foram_object__taxon").first())
    if st is None:
        return None
    return _stack_dict(st, detection_for_viewpoint(st.viewpoint))


# --- 집계 -------------------------------------------------------------------
def _summary_by_sql(slide: Slide) -> dict:
    """집계를 SQL 로 낸다. 개체 dict 를 만들지 않는다.

    **왜 따로 두는가.** 목록 화면은 개수 열 몇 개만 쓰는데, 예전에는 슬라이드마다
    모든 시야의 검출 dict 를 통째로 만들었다 — 개체 34,219개를 폴리곤(9 MB)까지
    파이썬으로 올렸다. 목록 한 장에 1.2초가 걸렸고 슬라이드가 늘면 선형으로 는다.

    **판정 규칙은 `_apply_review` 와 같아야 한다.** 다르면 목록과 상세 화면의
    숫자가 어긋나고, 그 숫자가 보고서에 실린다. 아래 세 줄이 그 규칙이다:

      통과분 중 사람이 지운 것을 뺀다 + 탈락분 중 사람이 되살린 것을 더한다
      분류는 사람이 지정한 것이 먼저, 되살린 것은 신장비로 짐작, 나머지는 자동 판정
      "사람지정" 은 통과분만 센다

    **교정은 `(image, mask_key)` 로 짚는다 — `viewpoint` 가 아니다.** 055 에서
    교정의 열쇠가 그리로 옮겨 갔는데 이 자리가 따라가지 않아 **인덱스가 발밑에서
    사라졌고** 목록 화면이 0.5 → 2.0초가 됐다(058). 그리고 **프레임별 검토
    (P06 5b)가 붙으면 `viewpoint` 는 값도 틀린다** — 한 시야에 이미지가 여럿이
    되는 순간 다른 이미지의 교정까지 끌어온다.

    세는 일은 `_summary_rows` 가 한다(원시 SQL 인 이유는 그쪽 머리말에).
    """
    per_cls = {r["key"]: 0 for r in _class_rows()}
    rows = _summary_rows("v.slide_id = %s", [slide.id])
    row = rows.get(slide.id) or {"per_cls": {}, "n_detected": 0,
                                 "n_auto": 0, "n_labeled": 0}
    per_cls.update({k: v for k, v in row["per_cls"].items() if k in per_cls})

    # 검출이 돈 **시야 수** — 평균의 분모다. **검출 수가 아니다**: 시야 하나에
    # 현재 검출이 여럿이면(합성본 + 프레임마다) 분모가 4.6배가 되어 시야당 평균이
    # 그만큼 작아진다. 분자는 대표 이미지 하나만 세므로 둘이 어긋난다 (P09 5.3).
    detected_groups = (Detection.objects
                       .filter(viewpoint__slide=slide).reviewing()
                       .values("viewpoint_id").distinct().count())
    return {"per_cls": per_cls, "n_detected": row["n_detected"],
            "n_auto": row["n_auto"], "n_labeled": row["n_labeled"],
            "detected_groups": detected_groups}


# 개체 하나하나를 파이썬으로 올리지 않고 세는 SQL. **원시 SQL 인 이유가 있다.**
#
# ORM 으로 쓰면 교정을 상관 서브질의로 찾게 되는데(`Exists`·`Subquery`), 그러면
# **후보마다 서너 번**의 인덱스 조회가 돈다 — 후보 97,299개에 400,000 번이다.
# 교정은 `(image, mask_key)` 가 유일하므로 **왼쪽 조인 한 번**이면 같은 값이
# 나오고 행도 안 불어난다: 실측 500 → 50 ms (058).
#
# Django ORM 은 이 조인을 낼 수 없다. 관계가 FK 가 아니라 `(image_id, mask_key)`
# 짝이기 때문이다 — `ObjectReview.candidate` FK 는 재검출에서 끊기라고 만든
# 것이라 조인 열쇠로 쓰면 안 된다(P02).
#
# **판정 규칙은 `_apply_review`·`_guess_cls` 와 같아야 한다.** 아래 CASE 두 개가
# 그 규칙이고, 경계값(1.4 · 2.0 · 20.0)까지 같아야 목록과 상세 화면의 숫자가
# 어긋나지 않는다.
#
# **시야마다 대표 이미지 하나만 센다** (P09 5.3). 예전에는 `d.is_current` 인 검출을
# 전부 세었는데, 시야마다 현재 검출이 하나일 때만 같은 뜻이다. 프레임별 검출이
# 올라오면 **같은 개체이 합성본 1 + 프레임 3.6 장에서 4.6번 세어져** 밀도가 그만큼
# 부푼다 — 학습 자료로는 맞고 계측 통계로는 틀리다. 아래 `rep` 가 그 하나를 고르고,
# 규칙은 `representative_detection` 과 같다(합성본이 있으면 합성본).
#
# **교정을 묶음으로도 맞춘다.** `(image, mask_key)` 만으로 맺으면 묶음을 갈아탄 뒤
# 같은 이미지에 남아 있는 **옛 회차의 교정이 새 후보에 붙는다.** 키가 겹칠 확률은
# 낮지만(엔진이 다르면 `exact` 가 0%다) 겹치면 `LEFT JOIN` 이 행을 불려 개수가
# 조용히 는다. `IS` 로 비교하는 것은 사람이 그린 개체가 `batch IS NULL` 이라서다.
_SUMMARY_SQL = """
WITH rep AS (
    SELECT d.id AS det_id, d.viewpoint_id, d.image_id, run.batch_id,
           ROW_NUMBER() OVER (
               PARTITION BY d.viewpoint_id
               ORDER BY CASE i.kind WHEN 'stack' THEN 0 ELSE 1 END, d.id) AS rn
      FROM viewer_detection d
      JOIN viewer_image i ON i.id = d.image_id
      LEFT JOIN viewer_run run ON run.id = d.run_id
      LEFT JOIN viewer_runbatch rb ON rb.id = run.batch_id
     -- **검토 대상 묶음의 것만** (P10 1단계). `is_current` 만 보면 나란히 쌓아
     -- 둔 다른 엔진의 검출까지 세어 개수가 몇 배가 된다.
     WHERE d.is_current AND rb.for_review
)
SELECT v.slide_id AS slide_id,
       CASE WHEN o.label IS NOT NULL AND o.label <> '' THEN o.label
            WHEN NOT c.passed THEN CASE
                 WHEN c.elongation IS NOT NULL THEN 'foram'
                 ELSE '' END
            ELSE c.cls END                                          AS eff_cls,
       -- 종명은 분류 아래 한 단계다 (202). 목록 화면은 `_summary_rows` 가
       -- 접어서 못 보고, 비교 화면만 이 축을 쓴다.
       COALESCE(t.name, '')                                         AS species,
       COUNT(*) FILTER (WHERE (c.passed AND NOT COALESCE(r.removed, 0))
                           OR (NOT c.passed AND COALESCE(r.accepted, 0)))   AS n_kept,
       COUNT(*) FILTER (WHERE c.passed)                                     AS n_auto,
       COUNT(*) FILTER (WHERE ((c.passed AND NOT COALESCE(r.removed, 0))
                            OR (NOT c.passed AND COALESCE(r.accepted, 0)))
                          AND o.label IS NOT NULL AND o.label <> '')        AS n_labeled
FROM viewer_candidate c
JOIN rep ON rep.det_id = c.detection_id AND rep.rn = 1
JOIN viewer_viewpoint v ON v.id = rep.viewpoint_id
LEFT JOIN viewer_objectreview r
       ON r.image_id = rep.image_id AND r.mask_key = c.mask_key
      AND r.batch_id IS rep.batch_id
-- 분류는 개체에 산다 (P12). `LEFT JOIN` 이라 판정이 없으면 `o.label` 도 NULL 이고,
-- 아래 `IS NOT NULL` 검사가 예전 그대로 걸린다.
LEFT JOIN viewer_foramobject o ON o.id = r.foram_object_id
LEFT JOIN viewer_taxon t ON t.id = o.taxon_id
WHERE {where}
GROUP BY v.slide_id, eff_cls, species

UNION ALL

-- **사람이 그린 개체** (P09 3단계). `Candidate` 가 없어 위 질의가 못 본다 —
-- 화면에는 보이는데 목록의 숫자에는 없는 상태가 된다.
--
-- 이것도 **대표 이미지에서만** 센다. 엔진 개체와 같은 규칙이어야 목록과 상세
-- 화면이 안 어긋난다 — 프레임에 그린 것은 안 세어진다는 뜻이고, 그것이 밀도의
-- 정의(시야 하나에 판 하나)와 맞는다.
--
-- `n_auto` 는 0 이다. 엔진이 낸 것이 아니라 사람이 만든 것이라, "자동 검출
-- 몇 개" 에 섞이면 엔진 성적을 잘못 읽는다.
SELECT v.slide_id AS slide_id,
       COALESCE(NULLIF(o.label, ''), '') AS eff_cls,
       COALESCE(t.name, '')                                      AS species,
       COUNT(*)                                                  AS n_kept,
       0                                                         AS n_auto,
       COUNT(*) FILTER (WHERE o.label IS NOT NULL AND o.label <> '') AS n_labeled
FROM viewer_objectreview r
JOIN viewer_foramobject o ON o.id = r.foram_object_id
LEFT JOIN viewer_taxon t ON t.id = o.taxon_id
JOIN rep ON rep.image_id = r.image_id AND rep.rn = 1
JOIN viewer_viewpoint v ON v.id = rep.viewpoint_id
WHERE r.batch_id IS NULL AND r.source = 'manual' AND {where}
GROUP BY v.slide_id, eff_cls, species
"""


# 시야 목록의 표지에 얹을 마스크. **화면에 남는 개체만** 가져온다.
#
# 그리는 것은 시야당 다섯 남짓인데 예전에는 후보를 전부 dict 로 만들어(탈락분
# 8,586개까지) 그중에서 골랐다 — 폴리곤이 JSONField 라 만드는 값이 곧 파싱
# 비용이다(060). 남는 개체의 조건은 `_summary_rows` 와 같은 규칙이다.
#
# **표시 분류는 `mask_class` 와 같아야 한다** — 사람이 지정한 것이 먼저, 되살린
# 것은 `manual`(짐작한 분류가 아니라 되살렸다는 사실을 색으로 보인다), 나머지는
# 자동 판정. 순서도 같아야 한다: 넓은 것부터 그려야 작은 개체가 안 묻힌다.
# **여기도 대표 이미지 하나만 본다** (`_SUMMARY_SQL` 과 같은 `rep`). 표지에 얹는
# 마스크라 여럿을 그리면 같은 개체이 겹쳐 그려지고, `n_kept` 도 함께 부푼다 —
# 그 수가 시야 목록의 "검토" 칸이다.
_COVER_SQL = """
WITH rep AS (
    SELECT d.id AS det_id, d.viewpoint_id, d.image_id, run.batch_id,
           ROW_NUMBER() OVER (
               PARTITION BY d.viewpoint_id
               ORDER BY CASE i.kind WHEN 'stack' THEN 0 ELSE 1 END, d.id) AS rn
      FROM viewer_detection d
      JOIN viewer_image i ON i.id = d.image_id
      LEFT JOIN viewer_run run ON run.id = d.run_id
      LEFT JOIN viewer_runbatch rb ON rb.id = run.batch_id
     -- **검토 대상 묶음의 것만** (P10 1단계). `is_current` 만 보면 나란히 쌓아
     -- 둔 다른 엔진의 검출까지 세어 개수가 몇 배가 된다.
     WHERE d.is_current AND rb.for_review
)
SELECT rep.viewpoint_id, c.polygon,
       CASE WHEN o.label IS NOT NULL AND o.label <> '' THEN o.label
            WHEN NOT c.passed THEN 'manual'
            ELSE COALESCE(NULLIF(c.cls, ''), 'none') END AS mask_cls
FROM viewer_candidate c
JOIN rep ON rep.det_id = c.detection_id AND rep.rn = 1
JOIN viewer_viewpoint v ON v.id = rep.viewpoint_id
LEFT JOIN viewer_objectreview r
       ON r.image_id = rep.image_id AND r.mask_key = c.mask_key
      AND r.batch_id IS rep.batch_id
-- 분류는 개체에 산다 (P12). `LEFT JOIN` 이라 판정이 없으면 `o.label` 도 NULL 이고,
-- 아래 `IS NOT NULL` 검사가 예전 그대로 걸린다.
LEFT JOIN viewer_foramobject o ON o.id = r.foram_object_id
WHERE v.slide_id = %s
  AND ((c.passed AND NOT COALESCE(r.removed, 0))
       OR (NOT c.passed AND COALESCE(r.accepted, 0)))
ORDER BY rep.viewpoint_id, c.area_px DESC
"""


def _kept_masks(slide: Slide) -> tuple[dict[int, list[dict]], dict[int, int]]:
    """시야 번호(pk) → (표지에 그릴 마스크, 남는 개체 수). 질의 하나.

    **개수는 마스크 수가 아니다.** 폴리곤이 점 셋 미만이면 그릴 수 없어 마스크를
    안 만드는데(`mask_points` 와 같은 문턱), 그 개체도 화면에는 세어져야 한다.
    """
    masks: dict[int, list[dict]] = {}
    n_kept: dict[int, int] = {}
    with connection.cursor() as cur:
        cur.execute(_COVER_SQL, [slide.id])
        for vp_id, poly, cls in cur.fetchall():
            n_kept[vp_id] = n_kept.get(vp_id, 0) + 1
            p = json.loads(poly) if poly else []
            if len(p) < 6:
                continue
            masks.setdefault(vp_id, []).append(
                {"points": " ".join(f"{p[i]},{p[i + 1]}"
                                    for i in range(0, len(p) - 1, 2)),
                 "cls": cls})
    return masks, n_kept


def _summary_rows(where: str, params: list) -> dict[int, dict]:
    """슬라이드 번호 → 집계. `eff_cls` 로 묶어 온 것을 파이썬에서 접는다.

    **분류 목록을 SQL 에 박지 않는다.** `ClassDef` 에 행을 더하면 저절로 따라온다
    (038~040 과 같은 방향). 모르는 분류로 나온 개체도 `n_detected` 에는 들어간다 —
    화면의 분류 열에만 안 보인다.

    SQL 은 `(분류, 종명)` 으로 묶어 오고 여기서 종명을 접는다 — `per_cls` 는
    예전 그대로이고, `per_sp` 에 `(분류, 종명) → 수` 를 따로 남긴다(202 의
    비교 화면이 쓴다). **두 벌의 SQL 을 두지 않는다** — 세는 규칙이 갈리면
    목록과 비교 화면이 같은 슬라이드에 다른 수를 낸다.
    """
    out: dict[int, dict] = {}
    with connection.cursor() as cur:
        # `{where}` 가 두 번 들어간다 (UNION 의 양쪽) — 파라미터도 두 벌이다.
        cur.execute(_SUMMARY_SQL.format(where=where), list(params) * 2)
        for slide_id, eff_cls, species, n_kept, n_auto, n_labeled in cur.fetchall():
            r = out.setdefault(slide_id, {"per_cls": {}, "per_sp": {},
                                          "n_detected": 0,
                                          "n_auto": 0, "n_labeled": 0})
            if n_kept:
                r["per_cls"][eff_cls] = r["per_cls"].get(eff_cls, 0) + n_kept
                if species:
                    k = (eff_cls, species)
                    r["per_sp"][k] = r["per_sp"].get(k, 0) + n_kept
            r["n_detected"] += n_kept
            r["n_auto"] += n_auto
            r["n_labeled"] += n_labeled
    return out


def _slide_summary(slide: Slide) -> dict:
    """목록 화면의 집계.

    **세는 일은 `_summary_by_sql` 하나로 모았다.** 예전에는 `dataset_detail` 이
    이미 만든 개체 dict 를 넘겨 파이썬에서 더하는 갈래가 따로 있었는데, 그
    갈래가 있으려면 화면이 개체를 전부 물질화해야 했다 — 시야 목록이 후보
    11,048개를 만들어 그중 370개만 그리고 있었다(060). 두 길의 값이 슬라이드
    11개 전부에서 같은 것을 확인하고 SQL 쪽만 남겼다.
    """
    vps = Viewpoint.objects.filter(slide=slide)
    n_groups = vps.count()
    sizes = list(vps.values_list("n_frames", flat=True))
    n_img = sum(sizes)

    r = _summary_by_sql(slide)
    per_cls = r["per_cls"]
    n_detected, n_auto = r["n_detected"], r["n_auto"]
    n_labeled, n_counts = r["n_labeled"], r["detected_groups"]
    counts = [None] * n_counts     # 개수만 쓴다

    rv = ObjectReview.objects.filter(viewpoint__slide=slide)
    agg = rv.aggregate(
        removed=Count("id", filter=Q(removed=True)),
        accepted=Count("id", filter=Q(accepted=True)),
    )
    # **코멘트는 개체를 센다** (0036). 판정으로 세면 묶인 개체가 판 수만큼
    # 세어져, 한 번 적은 글이 화면에서 넷으로 보인다 — 사람이 적은 횟수와
    # 어긋나는 숫자다. 분류(`n_labeled`)가 개체 기준인 것과 같은 자리다.
    n_noted = (ForamObject.objects.filter(viewpoint__slide=slide)
               .exclude(note="").count())
    # 코멘트는 시야의 것이라 묶음과 무관하고, 완료는 묶음마다다 (073)
    vrs = ViewpointReview.objects.filter(viewpoint__slide=slide)

    class_counts = [{"key": k, "label": _labels()[k], "n": v}
                    for k, v in per_cls.items() if v]
    # 세는 분류만 더한 값. 파편·미분류가 빠진다 (counted_classes 머리말).
    # 0 인 분류도 자리를 남긴다 — 목록 표의 열이 줄마다 같아야 세로로 맞는다.
    counted = [{**c, "n": per_cls.get(c["key"], 0)} for c in counted_classes()]
    n_counted = sum(c["n"] for c in counted)
    return {
        "n_groups": n_groups,
        "n_images": n_img,
        "mean_size": round(n_img / n_groups, 1) if n_groups else 0,
        "singletons": sum(1 for s in sizes if s == 1),
        "max_size": max(sizes) if sizes else 0,
        "n_stacks": Stack.objects.filter(viewpoint__slide=slide).count(),
        "detected_groups": len(counts),
        "n_auto": n_auto,
        "n_detected": n_detected,
        "mean_detected": (round(n_detected / len(counts), 1) if counts else None),
        # 목록 화면이 쓰는 값. 시야당도 같은 분자로 낸다 — 분자가 다르면
        # "검출 ÷ 시야" 가 "시야당" 과 안 맞아 어느 쪽이 틀렸는지 알 수 없다.
        "n_counted": n_counted,
        "mean_counted": (round(n_counted / len(counts), 1) if counts else None),
        "counted": counted,
        "class_counts": class_counts,
        "n_removed": agg["removed"],
        "n_accepted": agg["accepted"],
        "n_labeled": n_labeled,
        "n_noted": n_noted,
        "n_group_notes": vrs.exclude(note="").count(),
        "reviewed_groups": len(done_viewpoints(slide=slide)),
    }


def _slide_order(sl):
    """관찰을 세우는 순서 — 시료의 깊이, 분획, 관찰 번호. `Slide.Meta.ordering` 과 같아야 한다."""
    depth = sl.sample.depth_cm if sl.sample_id else None
    return (depth is None, depth or 0, sl.fraction_um or 0, sl.obs_no, sl.name)


def _obs(slide) -> dict:
    """행에 싣는 관찰 정보. 목록·지점 페이지·지도가 같은 열쇠를 쓴다."""
    return {
        "obs_no": slide.obs_no,
        "obs_label": slide.obs_label,
        "obs_badge": slide.obs_badge,
        "fraction_um": slide.fraction_um,
        "fraction_badge": slide.fraction_badge,
        "split_denom": slide.split_denom,
        "hidden": slide.hide_in_list,
        "excluded": slide.exclude_from_totals,
    }


def datasets(area: str | None = None) -> list[dict]:
    """**숨긴 슬라이드도 담아 돌려준다.** 거르는 자리는 `datasets_by_locality()` 다."""
    scales = scales_by_slide()
    out = []
    slides = _slides_in_order(Slide.objects.all())
    if area and area != AREA_ALL:
        slides = slides.filter(sample__locality__site__area=area)
    for slide in slides:
        sample = slide.sample
        loc = sample.locality if sample else None
        site = loc.site if loc else None
        out.append({
            "slug": slide.slug,
            "label": slide.name,
            "image_dir": slide.image_dir,
            "corr_thresh": slide.corr_thresh,
            "site": (site.region or site.name or site.code) if site else "",
            "site_code": site.code if site else "",
            "core": loc.code if loc else "",
            "sample_code": sample.code if sample else "",
            "depth_cm": sample.depth_cm if sample else None,
            "description": slide.description,
            "um_per_pixel": scales.get(slide.slug),
            "state": slide.state,
            "state_note": slide.state_note,
            "missing_dir": not (Path(settings.DATA_ROOT)
                                / slide.image_dir).is_dir(),
            **_obs(slide),
            **_slide_summary(slide),
        })
    return out
# 권역 탭의 "전체" 값
AREA_ALL = "all"


def area_tabs(selected: str | None = None) -> dict:
    """목록 위의 [남극|전체] 갈래. 지금은 권역이 하나라 탭이 둘이다 — 늘면 따라온다.

    **아무것도 주지 않으면 `전체` 다.** `전체` 는 지역(Site)이 안 붙은 슬라이드
    까지 담는다 — 새로 반입된 슬라이드는 지역이 정해지기 전까지 어느 권역에도
    없어서, 이 탭이 없으면 있다는 것만 알리고 열어 볼 길이 없다 (DiaRUGA).
    """
    counts = dict(Slide.objects.filter(sample__locality__site__isnull=False)
                  .values_list("sample__locality__site__area")
                  .annotate(n=Count("id")))
    tabs = [{"key": k, "label": v, "n": counts.get(k, 0)} for k, v in Site.AREA]
    tabs.append({"key": AREA_ALL, "label": "전체", "n": Slide.objects.count()})
    keys = [t["key"] for t in tabs]
    if selected not in keys:
        selected = AREA_ALL
    for t in tabs:
        t["on"] = t["key"] == selected
    return {
        "tabs": tabs,
        "selected": selected,
        "is_all": selected == AREA_ALL,
        "orphans": Slide.objects.filter(sample__isnull=True).count(),
    }


def datasets_total(rows: list[dict]) -> dict:
    """목록 표의 합계 줄. 합칠 수 있는 것만 합친다.

    **합계는 `집계 제외`(`excluded`)만 읽고 `숨김` 은 안 읽는다** — 읽으면 보기
    토글 한 번에 같은 자료가 다른 숫자를 낸다. 숨긴 행이 합계에 들어 있으면
    세어서 알린다(`n_hidden_in`).
    """
    counted_rows = [r for r in rows if not r.get("excluded")]
    keys = ("n_images", "n_groups", "n_stacks", "n_detected", "n_counted",
            "reviewed_groups")
    total = {k: sum(r.get(k) or 0 for r in counted_rows) for k in keys}
    per = {c["key"]: 0 for c in counted_classes()}
    for r in counted_rows:
        for c in r.get("counted") or []:
            if c["key"] in per:
                per[c["key"]] += c["n"]
    total["counted"] = [{**c, "n": per[c["key"]]} for c in counted_classes()]
    total["n_excluded"] = len(rows) - len(counted_rows)
    total["n_hidden_in"] = sum(1 for r in counted_rows if r.get("hidden"))
    return total


def datasets_by_locality(rows: list[dict], with_hidden: bool = False) -> list[dict]:
    """목록을 지점으로 묶는다. 표·카드 둘 다 이것을 쓴다.

    **줄 순서는 이미 지역→지점→깊이다**(`datasets()`) — 나온 순서대로 묶기만
    한다. 지점이 안 붙은 슬라이드는 "지점 미지정" 묶음이 된다 — 어디에도 안
    들어가면 목록에서 사라진다. **빈 묶음이 되어도 안 지운다**(머리줄 숫자는
    숨긴 것까지 센 값이라 그것을 남긴다).
    """
    out, at = [], {}
    for r in rows:
        key = (r.get("site_code") or "", r.get("core") or "")
        g = at.get(key)
        if g is None:
            g = at[key] = {
                "key": f"{key[0]}/{key[1]}",
                "site_code": key[0],
                "site": r.get("site") or "",
                "core": key[1],
                "no_core": not key[1],
                "all_rows": [],
            }
            out.append(g)
        g["all_rows"].append(r)
    for g in out:
        g["totals"] = datasets_total(g["all_rows"])
        g["rows"] = [r for r in g["all_rows"]
                     if with_hidden or not r.get("hidden")]
        g["n"] = len(g["rows"])
        g["n_hidden"] = len(g["all_rows"]) - g["n"]
    return out


# --- 산출 비교 (202) ---------------------------------------------------------
def _slide_head(slide: Slide) -> dict:
    """비교 표의 열 머리 하나. 목록의 행과 같은 열쇠를 쓴다(`datasets`)."""
    sample = slide.sample
    loc = sample.locality if sample else None
    site = loc.site if loc else None
    return {
        "slug": slide.slug,
        "label": slide.name,
        "site": (site.region or site.name or site.code) if site else "",
        "site_code": site.code if site else "",
        "core": loc.code if loc else "",
        "sample_code": sample.code if sample else "",
        "depth_cm": sample.depth_cm if sample else None,
        "sample_kind": loc.kind if loc else "core",
        **_obs(slide),
    }


def _slides_in_order(qs):
    """목록과 같은 차례 — 지역 → 지점 → 시료 깊이 → 분획 → 관찰 번호."""
    return (qs.select_related("sample__locality__site")
              .order_by("sample__locality__site__code",
                        "sample__locality__code", "sample__depth_cm",
                        "fraction_um", "obs_no", "name"))


# --- 지점 --------------------------------------------------------------------
def locality_detail(site_code: str, loc_code: str,
                    with_hidden: bool = False) -> dict | None:
    """지점 하나 — 깊이 방향으로 본 시료·관찰. DiaRUGA `locality_detail` 의 골자만.

    그쪽의 코어 자료 곡선(P17 · `CoreSeries`)·노두 사진은 5단계 / 안 온다.
    """
    loc = (Locality.objects.select_related("site")
           .filter(site__code=site_code, code=loc_code).first())
    if loc is None:
        return None
    scales = scales_by_slide()
    rows, n_hidden = [], 0
    for sm in loc.samples.order_by("depth_cm", "code").prefetch_related("slides"):
        slides = sorted(sm.slides.all(), key=_slide_order)
        for sl in slides:
            if sl.hide_in_list and not with_hidden:
                n_hidden += 1
                continue
            rows.append({
                "sample_code": sm.code, "depth_cm": sm.depth_cm,
                "dry_weight_g": sm.dry_weight_g,
                "slug": sl.slug, "label": sl.name, "state": sl.state,
                "um_per_pixel": scales.get(sl.slug),
                **_obs(sl), **_slide_summary(sl),
            })
        if not slides:
            rows.append({"sample_code": sm.code, "depth_cm": sm.depth_cm,
                         "dry_weight_g": sm.dry_weight_g, "slug": None})
    return {
        "site": loc.site, "loc": loc,
        "title": f"{loc.site.code}-{loc.code}",
        "rows": rows, "n_hidden": n_hidden,
        "n_samples": loc.samples.count(),
        "n_slides": Slide.objects.filter(sample__locality=loc).count(),
        "totals": datasets_total([r for r in rows if r.get("slug")]),
    }


def _viewpoints_of(slide: Slide, only_current: bool = False, light: bool = False,
                   batch_id=None):
    """시야와 그 아래를 한 번에 당겨 온다.

    **`light` 는 검출을 아예 안 당긴다.** 시야 목록은 개체를 SQL 로 세고 마스크만
    따로 받으므로(`_kept_masks`) 검출 행도 후보도 필요 없다(060).

    **`only_current` 를 켜면 현재 검출의 후보만 당긴다.** 켜지 않으면 쌓아 둔
    묶음(YOLO 등)의 후보까지 전부 올라온다 — `RS23-GC03 369cm` 에서 후보
    28,697개 중 **17,649개가 YOLO 것**이고, 시야 목록은 그것을 한 개도 안 쓴다.
    `Candidate.polygon` 이 JSONField 라 올리는 값이 파싱 비용으로 그대로 나온다
    (059 에서 이 화면의 `json.raw_decode` 31,563회가 그것이었다).

    **끄고 부르는 자리가 있다** — `engine_viewpoint` 는 `?batch=` 로 고른 묶음의
    검출을 봐야 하므로 현재 검출만 당기면 화면이 빈다. 그래서 기본을 켜지 않고
    부르는 쪽이 고르게 뒀다.

    **`batch_id` 는 `only_current` 의 범위를 그 묶음으로 바꾼다** (개체 카탈로그,
    2026-08-10). `reviewing()` 은 검토 대상 묶음으로 못 박혀 있어서, 그대로 두면
    다른 묶음을 짚어 열어도 **후보가 한 개도 안 올라오고 화면이 빈다** — 예외도
    404 도 없이 "검출이 없다" 로 보인다. 여기와 `current_detections` 가 같은
    묶음을 봐야 하고, 어긋나면 조용히 빈 화면이 된다.
    """
    qs = (Viewpoint.objects.filter(slide=slide)
          .select_related("sharpest_frame", "stack"))
    if light:
        return qs.prefetch_related("frames")
    dets = Detection.objects.all()
    if only_current:
        # **검토 대상 묶음의 것만** (P10 1단계). `is_current` 만 걸면 나란히
        # 쌓아 둔 다른 엔진의 검출까지 딸려 와 화면이 섞인다.
        dets = (dets.filter(is_current=True, run__batch_id=batch_id)
                if batch_id is not None else dets.reviewing())
    # **`image` 를 함께 당긴다.** 부르는 쪽이 `d.image.path` 로 검출을 이미지에
    # 맞춘다(`by_path`) — 안 붙이면 검출마다 질의가 하나씩 붙고, 크롭 화면처럼
    # 시야를 전부 훑는 자리에서 그대로 백 번이 된다.
    return qs.prefetch_related(
        "frames",
        Prefetch("detections",
                 queryset=dets.select_related("image")
                              .prefetch_related("candidates")),
        _reviews_prefetch())


def dataset_detail(slug: str) -> dict | None:
    slide = Slide.objects.filter(slug=slug).first()
    if slide is None:
        return None

    # **개체를 dict 로 만들지 않는다** (060). 이 화면이 개체에서 쓰는 것은
    # 표지에 얹을 마스크와 그 수뿐인데, 예전에는 시야마다 `detection_for_viewpoint`
    # 로 후보를 전부 만들어(이 슬라이드 11,048개) 그중 370개를 그렸다.
    cover_masks, n_kept = _kept_masks(slide)
    # **검토 대상 묶음의 것만**, 그리고 시야마다 하나 (P10 1단계). 프레임별
    # 검출이 올라오면 한 시야에 여럿이라 dict 가 아무거나 집는다 — 합성본을
    # 먼저 놓아 `representative_detection` 과 같은 것을 고르게 한다.
    dets = {}
    for d in (Detection.objects.filter(viewpoint__slide=slide).reviewing()
              .select_related("image")
              .order_by("viewpoint_id",
                        Case(When(image__kind="stack", then=0), default=1),
                        "id")
              .values("viewpoint_id", "image_path", "width", "height")):
        dets.setdefault(d["viewpoint_id"], d)
    reviewed = done_viewpoints(slide=slide)
    # **이 묶음에 검출이 없는 시야** (P10 4단계). 목록에서도 세어 낸다 — 시야를
    # 하나씩 열어 보고서야 아는 것은 063 이 "소속을 잃은 행은 그냥 사라진다" 로
    # 배운 자리와 같다.
    elsewhere = batches_elsewhere(slide=slide)

    groups = []
    for vp in _viewpoints_of(slide, light=True):
        det = dets.get(vp.id)
        st = getattr(vp, "stack", None)

        # 목록의 대표 그림은 합성본이 원칙이다 — 그룹을 대표하는 그림이 검출을
        # 돌린 그림과 같아야 목록과 상세가 어긋나지 않는다 (`_cover_of`)
        cover_rel = _cover_of(vp)

        # 표지에 검출 마스크를 얹는다. 검출을 돌린 이미지와 표지가 같을 때만 —
        # 다른 이미지의 좌표를 얹으면 조용히 어긋난 그림이 된다.
        masks, size = [], None
        if det and cover_rel and Path(det["image_path"]).stem == Path(cover_rel).stem:
            size = [det["width"], det["height"]]
            masks = cover_masks.get(vp.id, [])

        groups.append({
            "id": vp.idx,
            "cell": vp.cell,
            "n": vp.n_frames,
            "tag": vp.tag,
            # 이 묶음에 검출이 없다. `elsewhere` 가 비어 있으면 아무 데도 없는
            # 것이고, 차 있으면 **다른 묶음에는 있다** — 화면이 갈라 적는다.
            "missing": det is None,
            "elsewhere": elsewhere.get(vp.id, []),
            "span_sec": round(vp.span_sec or 0, 1),
            "sharpest": vp.sharpest_frame.name if vp.sharpest_frame else None,
            "cover_rel": cover_rel,
            "cover_size": size,
            "masks": masks,
            "has_stack": st is not None,
            "n_detected": n_kept.get(vp.id, 0) if det else None,
            "reviewed": vp.id in reviewed,
        })

    return {
        "slug": slug,
        "label": slide.name,
        # **이 묶음에 검출이 없는 시야를 목록에서 센다** (P10 4단계). 시야를 하나씩
        # 열어 보고서야 아는 것은 늦다 — 063 의 "소속을 잃은 행은 그냥 사라진다"
        # 와 같은 자리다. 다른 묶음에는 있는 것을 따로 세어 **돌릴 것이 남았는가**
        # 와 **묶음을 잘못 골랐는가**를 가른다.
        "missing_groups": sum(1 for g in groups if g["missing"]),
        "missing_elsewhere": sum(1 for g in groups
                                 if g["missing"] and g["elsewhere"]),
        "review_batch": review_batch_label(),
        # groups_*.json 은 파이프라인에서 빠졌다(P02 7단계). 파일 이름 대신
        # 시료가 무엇이고 어떤 배율로 찍혔는지를 보인다.
        "corr_thresh": slide.corr_thresh,
        "site": (slide.site.region or slide.site.name
                 or slide.site.code) if slide.site else "",
        "site_code": slide.site.code if slide.site else "",
        "core": slide.locality.code if slide.locality else "",
        "sample_code": slide.sample.code if slide.sample_id else "",
        "depth_cm": slide.depth_cm,
        "um_per_pixel": scales_by_slide().get(slide.slug),
        "groups": groups,
        **_obs(slide),
        **_slide_summary(slide),
    }


def _review_shot(d: dict, image_id: int, rel: str = "") -> dict:
    """캐러셀이 판을 바꿀 때 갈아 끼울 것 — **교정 상태까지 통째로**.

    읽기 전용 쪽(`engine_viewpoint._shot`)은 그릴 것만 넘기면 된다. 저장을 안 해
    사람이 표시할 것이 없어서다. 검토 화면은 **판마다 교정이 따로**이므로
    지운 것·되살린 것·분류·코멘트가 함께 가야 하고, 저장이 어느 이미지로 갈지도
    (`image`) 실려야 한다 (P09 1단계).

    `image` 가 빠지면 화면은 프레임을 보여 주면서 저장은 대표 이미지로 간다 —
    **예외도 경고도 없이** 사람이 보고 있던 것과 다른 자리에 판단이 쌓인다.
    """
    c = d["counts"]
    parts = _count_parts(c)
    return {
        "image": image_id,
        # **원본 경로** (P11). 묶기 팝업이 다른 판의 크롭을 청할 때 쓴다 —
        # 화면의 크롭 요청(`/crop?p=…`)이 경로를 받기 때문이다.
        "rel": rel,
        "candidates": d["candidates"],
        "gone": d["removed_candidates"],
        "rejected": d["rejected"],
        "accepted": d["accepted_keys"],
        "labels": d["labels"],
        "summary": (f"후보 {d['n_candidates']}개"
                    + (f" ({', '.join(parts)})" if parts else "")
                    + f" · 원시 {d['n_raw_masks']}"
                    + (f" → 크기통과 {d['n_sized']}" if d["n_sized"] else "")
                    + f" · 탈락 {len(d['rejected'])}개"),
    }


def _frames(vp: Viewpoint, by_path: dict | None = None) -> list[dict]:
    """프레임 목록. `by_path` 는 이미지 경로 → `(이미지 pk, 검출 dict)`.

    **프레임마다 자기 검출을 담는다** (P09 1단계). 예전에는 현재 검출이 프레임에
    붙은 싱글턴 시야일 때만 한 장이 받았다 — 시야마다 검출이 하나라는 전제다.

    **경로로 맞춘다.** `Image.path` 가 유일 열쇠라 `Frame.path` 와 그대로 만난다 —
    프레임마다 `frame.image` 를 되짚으면 시야 하나에 질의가 프레임 수만큼 는다.
    """
    by_path = by_path or {}
    frames = list(vp.frames.all())
    values = [f.sharpness for f in frames if f.sharpness is not None]
    top = max(values) if values else 0
    out = []
    for f in frames:
        out.append({
            # 배율과 그 출처 — 사진 화면이 프레임 줄에 적는다 (`scale.py`)
            "um_per_pixel": f.um_per_pixel,
            "um_per_pixel_source": f.um_per_pixel_source,
            "name": f.name,
            # 촬영 시각은 사진에 딸린 XML 이, 반입 시각은 우리 시스템이 안다.
            # 둘은 다른 것이고 되짚을 때 둘 다 필요하다.
            "acquired_at": f.acquired_at,
            "created_at": f.created_at,
            "sharpness": f.sharpness,
            # 그룹 내 최고 선명도 대비 비율 — 막대 길이로 쓴다.
            "sharp_pct": (round(100 * f.sharpness / top)
                          if f.sharpness and top else 0),
            "is_sharpest": f.is_sharpest,
            # 폴더 안 순서. **카탈로그 번호의 `f` 토막이 이것이다** — 시야 안에서
            # 겹치지 않는다(실측 570 시야 0건). 프레임 이름은 슬라이드끼리
            # 겹치므로(053) 번호에 쓸 수 없다.
            "seq": f.seq,
            "rel": f.path,
            "exists": (Path(settings.DATA_ROOT) / f.path).exists(),
            # 그 프레임 이미지에 붙은 현재 검출. 없으면 None 이고, 캐러셀에서
            # 그 판으로 가면 마스크가 비워진다(`swapDet`).
            "detection": (by_path.get(f.path) or (None, None))[1],
            "image_id": (by_path.get(f.path) or (None, None))[0],
        })
    return out


def group_detail(slug: str, gid: int, run_id: int | None = None) -> dict | None:
    """검토 화면 한 장.

    `run_id` 를 주면 **그 묶음이 낸 검출**을 대신 그린다(051). 엔진을 갈아 끼우는
    일이 검토 화면 안에서 되어야 한다 — 예전에는 `/engine/` 이라는 다른 화면으로
    나가야 했고, 나갔다 돌아오면 어느 시야를 보고 있었는지 잃었다.

    **그때는 읽기 전용이다.** 교정은 `mask_key`(bbox 문자열)로 붙는데 엔진이
    다르면 거의 전부 어긋난다. `readonly` 를 켜서 화면이 저장을 아예 보내지 않게
    하고(`_detection.html` 의 `ro-<uid>`), 서버는 `save_review` 가 현재 검출에
    없는 키를 거절해 한 겹 더 막는다. **화면에서 감추는 것은 막는 것이 아니다**
    — 027 이 정확히 그 자리에서 났다(교정 37건).
    """
    if run_id is not None:
        ctx = engine_viewpoint(
            slug, gid, run_id,
            # 옆 시야로 가도 **고른 엔진을 그대로 들고 간다.** 비교는 같은
            # 조건으로 여러 시야를 훑는 일이라, 한 칸 옮길 때마다 현재 검출로
            # 돌아가면 무엇을 보고 있었는지 잃는다.
            link=lambda i: (reverse("group", args=[slug, i])
                            + f"?batch={run_id}"))
        if ctx is None:
            return None
        slide = Slide.objects.filter(slug=slug).first()
        ctx["review_blocked"] = review_blocked(slide)
        ctx["readonly"] = True
        ctx["batch_run_id"] = run_id
        # 오른쪽 칸의 카탈로그는 여기서도 놓인다 — **읽기 전용이지만 목록은
        # 있어야 한다** (183). 빈 셀렉트를 내밀면 잠긴 것이 아니라 고장 난
        # 것으로 읽힌다.
        ctx["species_seen"] = species_seen()
        ctx["grades"] = ForamObject.GRADE
        ctx["poses"] = ForamObject.POSE
        return ctx

    slide = Slide.objects.filter(slug=slug).first()
    if slide is None:
        return None
    # 묶음 갈래는 위에서 이미 돌아갔다 — 여기는 현재 검출만 본다
    vp = _viewpoints_of(slide, only_current=True).filter(idx=gid).first()
    if vp is None:
        return None

    ids = list(Viewpoint.objects.filter(slide=slide)
               .order_by("idx").values_list("idx", flat=True))
    pos = ids.index(gid)

    # **다음 미검토 시야.** 옆 시야로 한 칸씩 가는 것과 다른 일이다 — 검토가
    # 드문드문 남으면(`am22-gc10b_25cm` 이 22/30 이었다) 한 칸씩 눌러 이미 본
    # 것을 계속 지나야 한다.
    #
    # **뒤를 먼저 보고, 없으면 앞으로 돌아간다.** 뒤만 보면 뒤쪽을 다 본 순간
    # 버튼이 죽는데 앞쪽에 남은 것이 있다. 돌아갔다는 것은 화면이 말한다 —
    # 말없이 뒤로 보내면 같은 자리를 도는 것으로 읽힌다.
    # **고른 묶음에서 완료한 것만 건너뛴다** (073). 묶음을 갈면 그 묶음의
    # 검출은 아직 아무도 안 본 것이라 다시 돌아야 한다.
    done = set(ViewpointReview.objects
               .filter(viewpoint__slide=slide, done=True,
                       batch_id=review_batch_id())
               .values_list("viewpoint__idx", flat=True))
    todo = [i for i in ids if i not in done and i != gid]
    ahead = [i for i in todo if i > gid]
    todo_id = ahead[0] if ahead else (todo[0] if todo else None)

    # **이미지마다 자기 검출을 만든다** (P09 1단계). 시야 하나에 현재 검출이
    # 여럿일 수 있고(합성본 하나 + 프레임마다 하나) 캐러셀이 그 사이를 오간다.
    # 교정은 `_with_reviews` 가 `(image, batch)` 로 걸러 얹는다 — 안 거르면
    # 합성본에서 한 판단이 프레임 화면에 얹혀 보인다.
    dets = current_detections(vp)
    bmap = batch_ids_of(dets)
    by_path = {d.image.path: (d.image_id, _with_reviews(vp, d, bmap[d.pk]))
               for d in dets if d.image_id}
    # **개체의 유형을 판들에 걸쳐 접는다.** 캐러셀이 판을 넘길 때 같은 개체의
    # 색이 바뀌면 안 된다 — 근거는 `fold_object_cls` 머리말.
    fold_object_cls([d for _, d in by_path.values()])
    # 후보에 카탈로그 정보를 얹는다 (183) — 오른쪽 칸이 고른 개체의 카드를 그린다.
    # **여기서 한 번만 한다.** 캐러셀이 갈아 끼울 판들이 이 후보 리스트를 그대로
    # 물고 가므로(`_review_shot`), 판마다 다시 물으면 105 의 그 실수가 된다.
    attach_catalog(vp, slide, dict(by_path.values()))

    st = getattr(vp, "stack", None)

    frames = _frames(vp, by_path)
    # 검출이 아직 없으면 빈 검출을 넘겨 같은 화면을 쓴다 (도구만 잠근다)
    stack = (_stack_dict(st, (by_path.get(st.focused_path) or (None, None))[1]
                             or preview_detection(vp))
             if st else None)

    # **캐러셀이 갈아 끼울 판들.** 읽기 전용 갈래와 같은 구조인데(`engine_viewpoint`)
    # 교정 상태와 저장 대상(`image`)까지 실린다 — 그 화면은 읽기 전용이라 그릴
    # 것만 넘기면 됐다.
    shots, pool = {}, []
    if stack and st and st.focused_path in by_path:
        stack["detkey"] = STACK_KEY
        iid, sd = by_path[st.focused_path]
        shots[STACK_KEY] = _review_shot(sd, iid, st.focused_path)
        pool.append({"key": STACK_KEY, "rel": st.focused_path, "det": sd,
                     "image": iid, "name": "합성본"})
    for f in frames:
        if f["detection"] and f["image_id"]:
            shots[f["name"]] = _review_shot(f["detection"], f["image_id"], f["rel"])
            pool.append({"key": f["name"], "rel": f["rel"],
                         "det": f["detection"], "image": f["image_id"],
                         "name": f["name"]})

    # **처음 열리는 판.** 집계가 세는 대표(`representative_detection`)와 **다른
    # 일이다** — 저쪽은 밀도를 위해 늘 합성본을 골라야 하고, 이쪽은 사람에게
    # 무엇을 먼저 보일지를 정한다.
    #
    # 합성본에 개체가 있으면 합성본이다. 아니면 **개체가 가장 많은 판**으로
    # 물러난다. `engine_viewpoint` 가 실측으로 정한 규칙 그대로다 — "합성본이
    # 있으면" 으로 하면 합성본만 비고 프레임에는 개체가 있는 시야에서 **빈 판이
    # 먼저 열려 검출이 아무것도 없는 줄 알게 된다.** YOLO 는 합성본 검출이
    # 314개로 시야 355개보다 적어 그 상태가 실제로 41개 난다.
    stack_p = next((p for p in pool if p["key"] == STACK_KEY
                    and p["det"]["candidates"]), None)
    best = stack_p or max(pool, key=lambda p: len(p["det"]["candidates"]),
                          default=None)
    # 검출이 아직 없는 시야 — 합성만 끝난 상태다. 빈 검출을 넘겨 **같은 화면을
    # 쓴다**(사진은 보이고 도구만 잠긴다). 저장 대상은 없다.
    if best is None and stack:
        best = {"key": STACK_KEY, "rel": st.focused_path,
                "det": stack["detection"], "image": None, "name": "합성본"}
    det = best["det"] if best else None

    return {
        "slug": slug,
        "label": slide.name,
        "id": gid,
        "cell": vp.cell,
        "n": vp.n_frames,
        "tag": vp.tag,
        "span_sec": round(vp.span_sec or 0, 1),
        "sharpest": vp.sharpest_frame.name if vp.sharpest_frame else None,
        "frames": frames,
        "stack": stack,
        # 캐러셀이 판을 바꿀 때 갈아 끼울 자료. 판이 하나뿐이면 안 넘긴다 —
        # `_detection.html` 의 `swapDet` 이 자료가 없으면 곧장 빠져나가므로
        # 지금까지와 똑같이 돈다.
        "shot_dets": shots if len(shots) > 1 else None,
        # 같은 개체 묶음 (P11). 판이 하나뿐이면 묶을 것이 없다 — 안 낸다.
        "links": object_links_of(vp) if len(shots) > 1 else [],
        # 처음 열리는 판. 캐러셀이 움직이면 JS 가 `image` 까지 따라 바꾼다.
        "base_rel": best["rel"] if best else None,
        "base_det": det,
        "base_name": best["name"] if best else None,
        "base_image": best["image"] if best else None,
        "prev_id": ids[pos - 1] if pos > 0 else None,
        "next_id": ids[pos + 1] if pos < len(ids) - 1 else None,
        "prev_url": (reverse("group", args=[slug, ids[pos - 1]])
                     if pos > 0 else None),
        "next_url": (reverse("group", args=[slug, ids[pos + 1]])
                     if pos < len(ids) - 1 else None),
        # 다음 미검토. `todo_left` 는 지금 시야를 뺀 수다 — 이 시야를 아직 안
        # 끝냈을 수도 있어서, "남은 것" 에 지금 보고 있는 것을 세면 눌러 갈 곳의
        # 수와 안 맞는다.
        "todo_id": todo_id,
        "todo_url": (reverse("group", args=[slug, todo_id])
                     if todo_id is not None else None),
        "todo_left": len(todo),
        "todo_back": todo_id is not None and todo_id < gid,
        # 자동 처리가 안 끝났으면 검토를 막는다 (P01 §1). 저장도 서버에서 거절한다.
        "review_blocked": review_blocked(slide),
        # **왜 비었는가** (P10 4단계). 검출이 아예 없는 것과 **다른 묶음에는
        # 있는 것**은 다른 말이다 — 뒤엣것을 "검출은 아직입니다" 라고 적으면
        # 사람이 돌리러 간다. 조용히 다른 묶음으로 물러나지도 않는다(P10 3.6).
        "review_batch": review_batch_label(),
        "elsewhere": batches_elsewhere(vp=vp).get(vp.id, []),
        # 오른쪽 칸의 카탈로그 칸이 쓰는 것들 (183). 카탈로그 화면과 **같은
        # 목록을 같은 자리에서** 받는다 — 두 화면이 다른 값을 내밀면 같은 개체를
        # 두 번 다르게 적게 된다.
        "species_seen": species_seen(),
        "grades": ForamObject.GRADE,
        "poses": ForamObject.POSE,
    }


def mark_all_reviewed(slug: str, done: bool) -> dict | None:
    """슬라이드의 시야 전체를 검토 완료로 / 미검토로 돌린다.

    **`done` 만 건드린다.** 같은 행의 `note`(시야 코멘트)도, `ObjectReview`
    (삭제·되살림·분류·코멘트)도 손대지 않는다 — 그쪽이 재생성 불가한 자료다.
    미검토로 돌릴 때 행을 지우면 적어 둔 시야 코멘트가 함께 날아간다. 그래서
    지우지 않고 깃발만 내린다 (`ClassDef` 를 지우지 않고 `active=False` 로
    끄는 것과 같은 결이다).

    돌려주는 것은 **실제로 바뀐 수**다. 이미 그 상태이던 것은 안 센다 — "30개를
    표시했습니다" 가 사실은 8개였다면 사람이 무엇을 한 것인지 알 수 없다.
    """
    slide = Slide.objects.filter(slug=slug).first()
    if slide is None:
        return None
    # **고른 묶음에만 찍는다** (073). 묶음을 안 정하면 어느 검출을 다 봤다는
    # 말인지가 없다 — 조용히 아무 데나 찍는 대신 안 한다.
    batch_id = review_batch_id()
    if batch_id is None:
        return {"changed": 0, "total": 0, "no_batch": True}
    ids = list(Viewpoint.objects.filter(slide=slide)
               .values_list("id", flat=True))
    with transaction.atomic():
        # 검토로 표시할 때만 없는 행을 만든다. 미검토로 돌릴 때는 만들 것이
        # 없다 — 행이 없는 것이 곧 미검토다.
        made = 0
        if done:
            have = set(ViewpointReview.objects
                       .filter(viewpoint_id__in=ids, batch_id=batch_id)
                       .values_list("viewpoint_id", flat=True))
            missing = [i for i in ids if i not in have]
            ViewpointReview.objects.bulk_create(
                [ViewpointReview(viewpoint_id=i, batch_id=batch_id, done=True)
                 for i in missing])
            made = len(missing)
        # `.update()` 는 `auto_now` 를 안 태운다 — 손으로 찍는다. 언제 뒤집혔는지
        # 모르면 나중에 되짚을 수가 없다.
        changed = (ViewpointReview.objects
                   .filter(viewpoint_id__in=ids, batch_id=batch_id,
                           done=not done)
                   .update(done=done, updated_at=timezone.now()))
    return {"changed": changed + made, "total": len(ids)}


# --- 교정 저장 --------------------------------------------------------------
def save_done(vp: Viewpoint, done: bool) -> dict:
    """**검토 완료 표시 하나만 쓴다** — 교정은 손대지 않는다 (116 덧).

    ## 완료와 교정은 층이 다르다

    완료는 `(시야, 묶음)` **한 줄**이고(073) 교정은 `(이미지, 묶음)` 마다다.
    그런데 `/review` 가 둘을 한 payload 로 받고 있었다 — 표시 하나를 켜는
    요청이 **그 판의 교정 전체를 갈아치우는** 요청이기도 했다(`save_review` 의
    마지막 줄이 payload 에 없는 행을 지운다).

    그래서 "어느 판을 고르고 있나" 가 완료에까지 걸렸고, 갈래 둘이 났다.

    1. 지나가는 판을 거르는 자리(074)가 **완료까지 삼켰다** — 화면은 완료로
       바뀌고 다음 시야로 넘어가는데 서버는 요청을 못 받는다 (116)
    2. **검출이 없는 판**(깊이 맵)을 고르면 화면이 `image` 를 못 실어 저장이
       **대표 이미지**로 가고, 그 판의 교정이 지워진다 — 116 을 검토하다
       사본에서 재현했다(합성본 교정 2건 → 0건, 2026-08-13)

    **켜는 데 지울 것이 딸려 갈 이유가 없다.** 판의 교정은 400 ms 지연 저장과
    판을 옮길 때의 `flushSave` 가 이미 내보낸다.

    ## 묶음은 화면이 안 짚는다

    검토 대상 묶음이 곧 그것이다 — `current_detections` 가 이미 거기로 좁힌다.
    화면이 짚게 하면 완료가 다시 "어느 판을 보고 있나" 에 매달린다. 대신
    **현재 검출이 없으면 오류로 말한다**: 조용히 아무 묶음에나 찍으면 무엇을 다
    봤다는 말인지가 없다 (`mark_all_reviewed` 와 같은 규칙).
    """
    cur = representative_detection(vp)
    if cur is None:
        raise ValueError("이 시야에는 현재 검출이 없다 — 저장하지 않았다")
    if cur.batch is None:
        raise ValueError(
            "이 시야의 현재 검출이 묶음(batch)에 안 들어 있다 — 저장하지 "
            "않았다. batch_runs.py 로 묶은 뒤 다시 시도할 것")
    ViewpointReview.objects.update_or_create(
        viewpoint=vp, batch=cur.batch, defaults={"done": done})
    # **확인 표시를 여기서 단다** (180 B2 · 2026-09-03). 예전에는 `save_review`
    # 가 `done` 을 함께 받아 그 자리에서 달았는데, 완료를 판 payload 에서 떼면서
    # (그래야 다른 탭의 마스크 저장이 이 표시를 되돌리지 않는다) 그 자리가
    # 없어졌다. **완료를 누르는 순간이 곧 "남는 것을 확인했다" 는 순간**이라
    # 여기가 제자리다.
    signed = confirm_kept(vp, cur.batch) if done else {}
    out = {"done": done}
    if signed:
        out["auto_confirmed"] = sum(len(v) for v in signed.values())
    return out


def save_note(vp: Viewpoint, note: str) -> dict:
    """**시야 코멘트 하나만 쓴다** (180 B2 · 2026-09-03).

    코멘트도 완료와 같은 층이다 — `(시야, batch=NULL)` 한 줄이고 판마다 다르지
    않다. 그런데 판 payload 에 함께 실려 다녔고, 서버는 그것으로 그 줄을 덮으며
    **비면 지웠다.** 같은 시야를 두 탭으로 열면 한쪽에서 적은 글을 다른 탭의
    마스크 저장 한 번이 지운다 — 어느 화면도 아무 말을 안 한다.

    **코멘트는 사람이 쓴 글이라 재생성 불가다** (073 이 묶음에서 뗀 것과 같은
    이유). 완료(`save_done`)와 같은 자리에 둔다.
    """
    if note:
        ViewpointReview.objects.update_or_create(
            viewpoint=vp, batch=None, defaults={"note": note})
    else:
        # 비웠으면 지운다 — 빈 줄을 남겨 두면 "코멘트가 있는 시야" 를 세는
        # 자리가 어긋난다. 완료 줄(`batch` 가 있는 줄)은 안 건드린다.
        ViewpointReview.objects.filter(viewpoint=vp, batch=None).delete()
    return {"note": bool(note)}


def save_review(vp: Viewpoint, done, note, removed, accepted,
                labels: dict, notes: dict | None = None, image=None,
                drawn=None, edits=None) -> dict | None:
    """뷰어가 보낸 교정을 DB 에 쓴다. 예전에는 review/<stem>_review.json 이었다.

    **`notes` 는 안 쓴다** (0036). 개체 코멘트는 카탈로그에서만 적고, 이 화면은
    읽기만 한다 — `note` 인자(시야 코멘트)와는 다른 것이다. 인자를 남겨 둔 것은
    **배포 중에 열려 있던 옛 탭**이 그것을 실어 보내기 때문이고, 그때 오류를
    내면 그 저장에 함께 실린 삭제·되살림까지 잃는다. **조용히 무시하는 쪽이
    맞다** — 이 화면이 안 보내는 값은 이 화면이 지울 수도 없어야 한다.

    키(mask_key)마다 한 행이고, 아무 표시도 남지 않은 행은 지운다 — 그래야
    "교정 전체 초기화" 가 예전처럼 깨끗하게 동작한다.

    **시야는 부르는 쪽이 짚어서 넘긴다** (`find_viewpoint`). 예전에는 여기서
    stem 으로 찾았는데, 프레임 이름이 슬라이드끼리 겹쳐 **엉뚱한 시야가 잡히는
    길**이 있었다 — 이 함수는 마지막에 그 시야의 교정을 통째로 갈아치우므로
    잘못 짚으면 남의 판단이 사라진다.

    **이미지도 부르는 쪽이 짚는다** (P09 1단계). 시야 하나에 현재 검출이 여럿일
    수 있는데(합성본 하나 + 프레임마다 하나) 예전에는 여기서 `.is_current` 인 것 중
    **아무거나** 집었다 — 시야마다 하나일 때만 맞는 코드다. 그대로 두고 YOLO 를
    프레임 검출로 올리면 **합성본에서 한 교정이 프레임 행으로 앉거나 그 반대가
    된다.** 053 과 같은 계열이고, 마지막 줄이 그 범위를 갈아치운다.

    `image` 를 안 주면 대표 이미지다 — 옛 화면(배포 중에 열려 있던 탭)이 그렇고,
    시야마다 이미지가 하나이던 시절과 결과가 같다.
    """
    if vp is None:
        return None

    # **어느 이미지·어느 묶음에 대한 교정인가** (P06 5a · P09 5.1). 열쇠가
    # `(image, batch, mask_key)` 라 이것이 없으면 행을 만들 수도, 지울 범위를
    # 정할 수도 없다.
    dets = current_detections(vp)
    if image is None:
        cur = representative_detection(vp, dets)
    else:
        image_id = getattr(image, "pk", image)
        cur = next((d for d in dets if d.image_id == image_id), None)
        if cur is None:
            # **남의 이미지를 짚었거나 그 이미지에 현재 검출이 없다.** 둘 다
            # 오류로 말한다 — 조용히 대표 이미지에 앉히면 사람이 보고 있던 것과
            # 다른 자리에 판단이 쌓인다.
            raise ValueError(
                "그 이미지에는 이 시야의 현재 검출이 없다 — 저장하지 않았다")
    if cur is None or cur.image_id is None:
        raise ValueError("이 시야에는 현재 검출이 없다 — 저장하지 않았다")
    image = cur.image
    batch = cur.batch
    # **묶음이 없으면 받지 않는다.** `batch=None` 은 **사람이 그린 개체의
    # 자리다**(P09 5.2) — 엔진 교정을 거기 앉히면 두 종류가 한 이름 아래 섞이고,
    # 아래 삭제 줄이 사람이 그린 것을 쓸어 간다. 지금 현재 검출은 전부 묶음에
    # 들어 있으므로 이 갈래는 안 밟히지만, 밟히면 **오류로 말한다** — 조용히
    # 저장한 척하는 갈래를 남기지 않는다.
    if batch is None:
        raise ValueError(
            "이 시야의 현재 검출이 묶음(batch)에 안 들어 있다 — 저장하지 "
            "않았다. batch_runs.py 로 묶은 뒤 다시 시도할 것")

    removed, accepted = set(removed), set(accepted)
    keys = removed | accepted | set(labels)

    # **그린 개체의 키는 이 목록들이 나르지 않는다** (P09 5.2·5.10 · 2026-09-03).
    #
    # 그린 개체는 `batch=NULL` 에 살고 **지우는 것은 `drawn` 에서 빠지는 것**이다.
    # 그런데 화면은 지운 것을 `cands` 에 남겨 흔적을 보이므로 그 키가 `removed`
    # 에 실려 왔고, 아래 `known` 은 `(image, batch)` 만 보니 **"현재 검출에 없는
    # 개체" 로 저장 전체가 거절됐다.** 한 번 그 상태가 되면 그 시야의 이후 저장이
    # 계속 실패한다 — 실사용에서 하루에 101건이 그렇게 날아갔다(2026-09-03 ·
    # wap13 g26 51건 · rs23 g11 39건 외). 사람은 같은 검토를 몇 번이고 다시 했다.
    #
    # **오류로 물리지 않고 흘린다** — 옛 탭의 `notes` 와 같은 갈래다(머리말).
    # 배포 중에 열려 있던 탭은 여전히 이 키를 실어 보내는데, 거절하면 그 저장에
    # 함께 실린 삭제·되살림까지 잃는다. 흘리면 **그 지우기 하나만 안 되고**
    # 나머지는 남는다 — 새로고침하면 화면이 서버와 다시 맞는다.
    manual_keys = {k for k in keys if MANUAL_KEY.match(k)}
    if manual_keys:
        keys -= manual_keys
        removed -= manual_keys
        accepted -= manual_keys
        labels = {k: v for k, v in labels.items() if k not in manual_keys}

    # **없는 것과 빈 것이 다르다** (`_save_drawn` 과 같은 규칙). `edits` 가 아예
    # 없으면 **고치기를 모르는 옛 화면**이다 — 배포 중에 열려 있던 탭이 그렇고,
    # 그 저장 한 번이 사람이 고친 기하를 전부 지우면 안 된다. 그때는 이미 고쳐
    # 둔 행의 키를 `keys` 에 얹어 삭제 대상에서 뺀다.
    #
    # 있으면 **그것이 전부다** — 화면은 늘 전체를 보낸다(`drawn` 과 같다).
    # 거기 없는 키는 고친 적이 없다는 말이고, `keys` 에 안 들어가면 아래 삭제
    # 줄이 지운다.
    if edits is None:
        keys |= set(ObjectReview.objects
                    .filter(image=image, batch=batch, geom_edited=True)
                    .values_list("mask_key", flat=True))
    # 그린 개체의 기하도 `drawn` 이 나른다 — 여기로 오면 위와 같은 자리에서
    # 걸린다(화면은 이미 갈라 보내지만 옛 탭이 있다).
    edits = {str(k): list(v or []) for k, v in (edits or {}).items()
             if not MANUAL_KEY.match(str(k))}
    # **이미 그 값으로 앉아 있는 것은 다시 검사하지 않는다** (180 A2 · 2026-09-03).
    #
    # 화면은 고친 기하를 **매번 전부** 싣고(뷰어는 늘 전체를 보낸다) 서버는 그
    # 전부를 다시 검사했다. 하나라도 지금 검출의 크기 밖이면 저장 전체가
    # 거절되는데, **저장될 때는 통과한 값**이 나중에 밖이 되는 길이 있다 —
    # `--scale` 을 빠뜨린 재검출로 `Detection.width/height` 가 절반이 되면
    # 고쳐 둔 기하가 전부 밖이 된다. 그러면 그 판은 **새로고침해도** 아무것도
    # 저장할 수 없다: 값이 DB 에서 다시 오므로 화면을 고쳐도 안 풀린다.
    #
    # 검사가 막으려는 것은 **새로 들어오는 나쁜 값**이다. 안 바뀐 값을 매번 다시
    # 재는 것은 지난 판단을 인질로 잡는 일이다.
    stored = {o.mask_key: ((o.geom or {}).get("polygon") or [])
              for o in ObjectReview.objects.filter(
                  image=image, batch=batch, mask_key__in=list(edits))}
    def same_as_stored(k, poly):
        try:
            return ([float(v) for v in stored.get(k) or []]
                    == [float(v) for v in poly])
        except (TypeError, ValueError):
            return False               # 숫자가 아니면 검사 쪽이 말하게 둔다

    for k, poly in edits.items():
        if not poly:                   # 빈 것은 "엔진 것으로 되돌린다" 는 말이다
            continue
        if same_as_stored(k, poly):
            continue                   # 안 바뀌었다 — 그때 통과한 값이다
        check_polygon(poly, (cur.width, cur.height), k)
    # **고친 기하도 표시다.** `keys` 에 안 넣으면 같은 저장의 마지막 줄이
    # "표시가 사라진 행" 으로 보고 지운다.
    keys |= set(edits)

    # **카탈로그 카드가 적는 칸이 든 행은 이 payload 가 대표하지 않는다**
    # (종명 2026-08-10 · 등급·자세 2026-08-11 · **코멘트 2026-08-12** · 0036).
    #
    # 넷 다 `/d/<슬라이드>/catalog/` 가 적고 **검토 화면은 그 칸들을 안 보낸다.**
    # 그런데 그것만 채운 행은 삭제·되살림·유형이 전부 비어 있어, 얹지 않으면
    # 아래 삭제 줄이 "표시가 사라진 행" 으로 보고 지운다 — 사람이 아무것도 안
    # 하고 **"검토 완료" 만 눌러도 통째로 사라진다.** 017·027·053 이 전부 그
    # 줄에서 났고, 넷 다 현미경을 보며 적는 것이라 재생성 불가다.
    #
    # **코멘트가 여기로 온 것이 0036 이다.** 그 전에는 이 화면이 코멘트를
    # 보내서 `keys` 가 알아서 지켰는데, 적는 자리를 카탈로그로 모으면서
    # 종명·등급·자세와 같은 갈래가 됐다 — **한 줄에 함께 둔다.**
    #
    # **`exclude(a="", b="", c="", d="")` 는 넷이 모두 빈 행만 뺀다** — 하나라도
    # 값이 있으면 남긴다. 따로 물으면 질의가 넷이 되고, 칸이 늘 때마다 한 줄씩
    # 더하다가 빠뜨린다. **규칙이 하나면 자리도 하나여야 한다.**
    #
    # **`edits` 와 달리 늘 얹는다.** 저쪽은 "고치기를 아는 화면이면 그것이 전부"
    # 라 갈래가 있지만, `/review` 는 **어느 판에서도** 이 칸들을 보내지 않는다.
    #
    # **청소는 계속 된다** — 값을 비우면 이 집합에서 빠지므로, 그 다음 저장이
    # 아무 표시도 안 남은 행을 예전처럼 지운다.
    keys |= set(ObjectReview.objects
                .filter(image=image, batch=batch)
                .exclude(foram_object__taxon__isnull=True,
                         foram_object__grade="",
                         foram_object__pose="",
                         foram_object__note="")
                .values_list("mask_key", flat=True))

    # **확인 표시가 든 행도 이 payload 가 대표하지 않는다** (2026-08-11).
    #
    # `auto_confirmed` 는 "검토 완료" 가 남는 개체에 다는 확인 표시인데(`confirm_kept`),
    # **검토 화면은 그 칸을 모른다** — 얹지 않으면 다음 저장이 "표시가 사라진
    # 행" 으로 보고 지운다. 종명·`geom_edited` 와 같은 갈래다.
    keys |= set(ObjectReview.objects
                .filter(image=image, batch=batch, auto_confirmed=True)
                .values_list("mask_key", flat=True))

    # **묶음의 멤버인 행도 이 payload 가 대표하지 않는다** (P12).
    #
    # 소속이 곧 판정 행이 됐다 — 예전에는 `ObjectLinkMember` 로 따로 있어서
    # 교정 행이 지워져도 묶음은 멀쩡했다. 지금 그 행을 청소가 지우면 **묶음에서
    # 한 판이 조용히 빠진다**: 검토 화면은 묶음을 만들지도 지우지도 않으므로
    # (`/link` 이 따로 있다) payload 에 없다는 것이 "묶음에서 뺐다" 는 뜻일 수가
    # 없다. 종명·`geom_edited` 를 얹는 것과 **같은 갈래**다 — 이 화면이 모르는
    # 칸은 이 화면이 대표하지 못한다.
    #
    # **혼자인 개체는 얹지 않는다.** 그것은 소속이 아니라 1:1 껍데기이고,
    # 표시가 사라졌으면 예전처럼 지워야 한다(개체는 `prune_objects` 가 걷는다).
    keys |= set(ObjectReview.objects
                .filter(image=image, batch=batch)
                .annotate(n_siblings=Count("foram_object__members"))
                .filter(n_siblings__gte=2)
                .values_list("mask_key", flat=True))
    # **그 검출의 개체만 본다.** 예전에는 시야의 현재 검출 전부를 훑었는데,
    # 시야마다 현재 검출이 하나일 때만 같은 뜻이다 — 여럿이면 **프레임 A 의 키가
    # 프레임 B 의 화면에서 통과한다.** `mask_key` 는 프레임끼리 45% 겹치므로
    # 우연이 아니라 흔하게 통과하고, 아래 삭제 줄은 이미지로 좁혀져 있어
    # **엉뚱한 이미지의 키로 만든 행이 남는다** (P09 1단계).
    by_key = {c.mask_key: c for c in cur.candidates.all()}

    # **현재 검출에 없는 키는 받지 않는다.** 사람은 화면에 있는 것만 표시할 수
    # 있고, 화면은 현재 검출을 그린다. 그 밖의 키가 섞여 오면 다른 검출을 보고
    # 보낸 것이다 — 아래에서 `keys` 에 없는 행을 전부 지우므로, 그대로 두면
    # **엉뚱한 화면의 클릭 한 번이 그 시야의 교정을 통째로 갈아치운다.**
    #
    # 실제로 그렇게 잃었다: 시험용 화면(/engine/)이 YOLO 검출을 그리고 있었는데
    # 마스크를 클릭하자 그 키 하나만 담긴 POST 가 나갔고, 369cm g32 의 교정
    # 37건이 지워졌다. CSS 로 도구를 감춘 것으로는 못 막는다.
    #
    # 이미 교정 행이 있는 키는 통과시킨다 — 재바인딩에서 고아가 된 것들이
    # 그렇고, 그것들은 사람이 화면에서 지울 수 있어야 한다.
    known = set(by_key) | set(ObjectReview.objects
                              .filter(image=image, batch=batch)
                              .values_list("mask_key", flat=True))
    unknown = keys - known
    if unknown:
        raise ValueError(
            f"현재 검출에 없는 개체 {len(unknown)}개가 섞여 있다 — 저장하지 "
            f"않았다. 다른 검출을 보고 있지 않은지 확인할 것 "
            f"(예: {sorted(unknown)[:3]})")

    # **완료는 그 묶음에, 코멘트는 시야에** (073). 한 줄에 담아 두었더니
    # `sam2-전수` 에서 붙인 완료가 `yolo-3차` 화면에도 붙어 있었다 — 아직 아무도
    # 안 본 검출이 "검토 완료" 로 보이고, "다음 미검토" 가 그 시야를 건너뛴다.
    #
    # 코멘트는 묶음을 갈아도 참이고 **사람이 쓴 글이라 재생성 불가**다. 완료와
    # 함께 묶음에 매달면 묶음을 갈 때마다 사라진다.
    #
    # **`None` 이면 안 건드린다** (180 B2 · 2026-09-03). 지금 화면은 이 둘을 판
    # payload 에 안 싣고 자기 문(`save_done`·`save_note`)으로 보낸다 — 시야에
    # 붙는 것을 판 저장이 나르면 **같은 시야를 두 탭으로 열었을 때 한쪽이 켠
    # 완료와 적은 글을 다른 탭의 마스크 저장 한 번이 되돌린다.** 116 이 완료를
    # 떼어 낸 것의 나머지 절반이다.
    #
    # 값이 오면 그때는 쓴다 — **배포 중에 열려 있던 옛 탭**이 그렇고, 그쪽의
    # 완료·코멘트를 무시하면 그 탭에서 한 일이 조용히 사라진다.
    if done is not None:
        ViewpointReview.objects.update_or_create(
            viewpoint=vp, batch=batch, defaults={"done": bool(done)})
    if note is not None:
        if note:
            ViewpointReview.objects.update_or_create(
                viewpoint=vp, batch=None, defaults={"note": note})
        else:
            # 비웠으면 지운다 — 빈 줄을 남겨 두면 "코멘트가 있는 시야" 를 세는
            # 자리가 어긋난다. 완료 줄은 건드리지 않는다.
            ViewpointReview.objects.filter(viewpoint=vp, batch=None).delete()
    for key in keys:
        cand = by_key.get(key)
        obj = judgement_for(vp, image, batch, key, cand)
        obj.removed = key in removed
        obj.accepted = key in accepted
        if cand and obj.candidate_id != cand.id:
            obj.candidate = cand
            obj.bind_method = "exact"
            obj.bind_score = 1.0
        # 기하는 모든 교정 행이 들고 있는다 — 검출기가 바뀌어도 읽혀야 하고,
        # 지운 것도 학습의 음성 표본이다 (P02 §2.7)
        if cand and not obj.geom:
            obj.geom = {"bbox": cand.bbox_xywh, "polygon": cand.polygon}
        # **사람이 고친 기하** (P09 4단계). 빈 폴리곤은 "엔진 것으로 되돌린다" 는
        # 말이라 깃발을 내리고 엔진의 기하를 다시 넣는다.
        if key in edits:
            poly = edits[key]
            if poly:
                obj.geom = {"bbox": shape.bbox(shape.points(poly)),
                            "polygon": poly}
                obj.geom_edited = True
            elif cand:
                obj.geom = {"bbox": cand.bbox_xywh, "polygon": cand.polygon}
                obj.geom_edited = False
            else:
                # 되돌릴 엔진 개체가 없다(고아) — 기하를 지우면 못 그린다
                raise ValueError(
                    f"되돌릴 엔진 개체가 없다 — 이 개체는 검출에 대응이 없다 "
                    f"(키 {key})")
        obj.save()

    # 표시가 사라진 행은 지운다.
    #
    # **범위가 시야가 아니라 `(이미지, 묶음)` 이다** (P06 5a · P09 5.1). 시야로
    # 지우면 프레임별 검토를 붙이는 날 **그 시야의 다른 이미지 교정까지 쓸어
    # 간다** — 017·027·053 이 전부 이 줄에서 났다. 묶음까지 넣으면 **다른 엔진을
    # 보고 있는 화면의 payload 가 이 묶음의 교정에 닿을 수가 없다** — 027 이
    # 구조적으로 불가능해진다.
    #
    # **`batch=None` 인 행은 안 지운다.** 사람이 그린 개체이고, 그것은 어느
    # 묶음에도 속하지 않아 이 payload 가 대표하지 않는다 (P09 5.2). 지우는 것은
    # 3단계에서 `drawn` 목록이 맡는다.
    # **분류를 개체에 앉힌다** (P12). 묶여 있으면 그 개체를 나눠 가진 다른 판이
    # 같은 값을 보게 된다 — 104 가 저장할 때마다 판마다 적어 넣던 일이 자리
    # 하나로 바뀌었다.
    #
    # **`keys` 가 아니라 `known` 을 훑는다.** 지정을 *물리면* 그 키는 payload 의
    # `labels` 에서 사라지고 다른 표시도 없으면 `keys` 에도 안 들어온다 — `keys`
    # 만 보면 **물림이 개체에 안 닿아 다른 판에 옛 분류가 남는다.** 옛
    # `_spread_link_labels` 도 같은 이유로 `known` 을 봤다.
    #
    # **코멘트는 여기 없다** (0036). 개체에 살지만 적는 자리가 카탈로그 하나라,
    # 이 화면이 보내지 않는 값을 이 화면이 쓸 일이 없다.
    touched = []
    for row in (ObjectReview.objects.filter(image=image, batch=batch,
                                            mask_key__in=known)
                .select_related("foram_object", "foram_object__taxon")):
        want = labels.get(row.mask_key, "")
        dobj = row.foram_object
        if dobj.label != want:
            dobj.label = want
            dobj.save(update_fields=["label", "updated_at"])
            touched.append(dobj)

    stale = (ObjectReview.objects.filter(image=image, batch=batch)
             .exclude(mask_key__in=keys))
    orphaned = list(stale.values_list("foram_object_id", flat=True))
    stale.delete()
    # **판정을 지운 뒤에 개체를 걷는다.** 순서를 바꾸면 `CASCADE` 가 살아 있는
    # 판정까지 끌고 간다.
    prune_objects(orphaned)
    # **앵커를 잃었으면 다시 세운다** (P18). 지운 행이 앵커였으면 `SET_NULL` 로
    # 비는데, 그대로 두면 그 개체의 카탈로그 번호를 만들 재료가 없다.
    reanchor(orphaned)

    # **지운 판이 대표면 얼굴을 옮긴다** (151). 묶인 판을 지우는 것은 허용하되
    # (사용자 방침 2026-08-25) 개체의 얼굴이 오검출이 되는 것만 막는다.
    # 청소 뒤에 한다 — 먼저 하면 곧 지워질 행을 세울 수 있다.
    _reelect_removed_reps(
        ObjectReview.objects.filter(image=image, batch=batch,
                                    mask_key__in=removed)
        .values_list("foram_object_id", flat=True))

    n_drawn, drawn_spread, links_moved = _save_drawn(
        vp, image, drawn, (cur.width, cur.height), batch)

    # **완료를 누르면 남는 개체에 확인 표시를 단다** (2026-08-11). 청소가 끝난 뒤에
    # 한다 — 먼저 하면 방금 세운 행이 `keys` 에 없어 그 자리에서 지워진다.
    signed = confirm_kept(vp, batch) if done else {}

    # 묶인 판들에 **화면 상태를 맞추라고 알린다** (P12 · 옛 104 의 전파 자리).
    # 값은 이미 개체 하나에 앉아 있어 고칠 것이 없다 — 다른 판의 화면이 그것을
    # 모르는 것만 남았다.
    spread = linked_siblings(touched, getattr(image, "pk", image))

    out = {"removed": len(removed), "accepted": len(accepted),
           "labels": len(labels)}
    if n_drawn is not None:
        out["drawn"] = n_drawn
    if drawn_spread:
        # **화면이 이것을 받아야 한다** — `linked` 와 같은 이유이고, 이쪽은 더
        # 급하다. 저쪽은 분류가 되돌아가는 것이고 이쪽은 **마스크가 통째로
        # 사라지는 것**이다 (`_spread_drawn` 머리말).
        out["drawn_spread"] = drawn_spread
    if links_moved:
        # **묶은 그 순간에 사슬이 뜨게 한다** (사용자, 2026-08-12). 화면의
        # `links` 는 페이지가 열릴 때 한 번 받고 `/link` 응답으로만 갈렸다 —
        # 그래서 마스크를 그리면 복제는 놓이는데 **묶였다는 표시는 새로고침
        # 전까지 안 떴다.** 사람이 "번졌다" 를 알아채는 근거가 그 배지다.
        #
        # **묶음이 움직였을 때만 싣는다.** 저장마다 실으면 시야의 개체를 매번
        # 다시 물질화하는데, 사슬이 안 바뀐 저장이 대부분이다.
        out["links"] = object_links_of(vp)
    if spread:
        # **화면이 이것을 받아야 한다.** 다른 판의 상태는 화면이 열릴 때 받은
        # 것이라 지금 번진 분류를 모르고, 그 판에서 다음 저장이 나가면 "표시가
        # 사라진 행" 으로 보고 지운다 — 뷰어는 늘 전체를 보낸다.
        out["linked"] = spread
    if signed:
        # **화면은 흡수할 것이 없다.** `auto_confirmed` 는 payload 에 없는 칸이라
        # 청소가 위에서 이미 지켜 준다 — 수만 알려 준다.
        out["auto_confirmed"] = sum(len(v) for v in signed.values())
    return out


def linked_siblings(objs, except_image_id) -> dict:
    """이 개체를 나눠 가진 **다른 판의 마스크들**. `{이미지 id: {mask_key: 분류}}`.

    **분류만 싣는다.** 개체에 사는 값은 분류 말고도 넷이지만(종명·등급·자세·
    코멘트) **검토 화면이 되돌려 보내는 것은 분류뿐**이라, 나머지는 알려 줄
    이유가 없다 — 화면이 안 보내는 값은 화면이 지울 수도 없다(0036 에서 코멘트가
    그쪽으로 갔다).

    **아무것도 쓰지 않는다 — 알리기만 한다.** P12 이후 분류는 개체 하나에 살아
    이미 모든 판에 반영돼 있다. 예전(104)의 `_spread_link_labels` 는 판마다 행을
    고쳐 앉히는 일이었고, 그것을 지키는 검사와 소급 불가 문제를 함께 달고
    있었다. 지금 남은 일은 **화면에 알리는 것뿐**이다.

    알려야 하는 이유는 그대로다: 다른 판의 화면 상태는 열릴 때 받은 것이라
    방금 바뀐 분류를 모르고, 그 판에서 다음 저장이 나가면 **자기가 아는 빈 값을
    보내 지운다** (뷰어는 늘 전체를 보낸다).

    `except_image_id` 는 방금 저장한 판이다 — 그 화면은 이미 알고 있다.
    """
    objs = [objs] if isinstance(objs, ForamObject) else list(objs)
    if not objs:
        return {}
    by_obj = {o.pk: o for o in objs}
    out = {}
    for m in (ObjectReview.objects.filter(foram_object_id__in=by_obj)
              .exclude(image_id=except_image_id)
              .values_list("foram_object_id", "image_id", "mask_key")):
        oid, image_id, key = m
        out.setdefault(str(image_id), {})[key] = by_obj[oid].label
    return out


# 사람이 그린 개체의 키. **불투명하다** — bbox 를 넣으면 "키가 곧 기하" 라는
# 죽은 규약이 이어져서, 사람이 경계를 고쳤을 때(4단계) 키를 파싱한 쪽이 실제
# 모양과 다른 값을 얻는다 (P09 5.4).
MANUAL_KEY = re.compile(r"^m[0-9a-f]{8}$")


# 폴리곤 점 수의 상한. 점 찍기로 만드는 것이라 수십을 넘을 이유가 없고(엔진이
# 내는 것도 중앙 13~19점이다), 상한이 없으면 한 번의 POST 로 DB 를 부풀릴 수 있다.
MAX_POINTS = 400


def check_polygon(poly, size, key=""):
    """사람이 보낸 폴리곤을 받을 수 있는지 본다. 돌려주는 것은 `[x, y, w, h]`.

    **그리기와 고치기가 같은 검사를 지난다** — 갈라지면 한쪽으로만 들어오는
    값이 생기고, 그 값은 화면·학습 자료 어디서 터질지 모른다.

    못 받을 것은 **오류로 말한다.** 조용히 고쳐 앉히면 사람이 보낸 것과 다른
    것이 저장되고, 화면은 "저장됨" 이라고 적는다.
    """
    where = f" (키 {key})" if key else ""
    if len(poly) < shape.MIN_POINTS * 2 or len(poly) > MAX_POINTS * 2:
        raise ValueError(
            f"폴리곤의 점 수가 {shape.MIN_POINTS}~{MAX_POINTS} 를 벗어난다 "
            f"({len(poly) // 2}점){where}")
    try:
        poly[:] = [float(v) for v in poly]
    except (TypeError, ValueError):
        raise ValueError(f"폴리곤에 숫자가 아닌 값이 있다{where}")
    box = shape.bbox(shape.points(poly))
    if box is None:
        raise ValueError(f"폴리곤에서 상자를 못 만든다{where}")
    # **이미지 밖은 안 받는다.** 밖에 있는 개체는 그릴 수도 잴 수도 없고,
    # 학습 자료로 나가면 좌표가 뒤집힌 라벨이 된다.
    w, h = size
    if w and h and (box[0] < 0 or box[1] < 0
                    or box[0] + box[2] > w or box[1] + box[3] > h):
        raise ValueError(f"폴리곤이 이미지 밖으로 나간다{where}")
    return box


def _save_drawn(vp: Viewpoint, image, drawn, size=(0, 0),
                batch=None) -> tuple[int | None, dict, bool]:
    """사람이 그린 개체를 저장한다 (P09 3단계).

    돌려주는 것은 `(남은 개수, 번진 것, 묶음이 움직였는가)`.

    **`None` 이면 손대지 않는다.** payload 에 `drawn` 이 아예 없는 것과 빈
    목록인 것은 다르다 — 앞은 **그리기를 모르는 옛 화면**(배포 중에 열려 있던
    탭)이고 뒤는 "그린 것이 하나도 없다" 는 말이다. 둘을 같이 다루면 옛 탭의
    저장 한 번이 사람이 그린 개체를 전부 지운다.

    **`batch` 가 `None` 이다.** 엔진에 대한 판단이 아니라 이미지에 대한 사실이라
    어느 회차에도 안 속한다 — 그래서 묶음을 갈아타도 안 사라진다 (P09 5.2).

    **지우는 것은 행을 지우는 것이다.** 엔진이 낸 것은 `removed=True` 로 남겨
    음성 표본이 되지만, 사람이 그리다 만 것을 그렇게 남기면 **"여기 개체
    없다" 를 다음 회차에 가르치게 된다** (P09 5.10).
    """
    if drawn is None:
        return None, {}, False

    keep = []
    for item in drawn:
        key = str(item.get("key") or "")
        if not MANUAL_KEY.match(key):
            raise ValueError(f"사람이 그린 개체의 키가 규칙에 안 맞는다: {key!r}")
        poly = list(item.get("polygon") or [])
        # 크기는 **검출**에서 받는다 — `Image.width`·`height` 는 nullable 이라
        # 안 채워진 행이 있고(그러면 검사가 조용히 통과한다), 화면이 좌표를
        # 만드는 근거도 검출의 `size` 다.
        box = check_polygon(poly, size, key)

        # **코멘트는 안 받는다** (0036). 그린 개체의 코멘트도 카탈로그에서
        # 적는다 — 여기서 받으면 화면이 안 적는 값을 화면이 보내게 되고, 그
        # 값은 빈 칸이라 **카탈로그에서 적어 둔 글을 저장 한 번이 지운다.**
        # 옛 탭이 실어 보내는 `note` 는 그래서 그냥 흘린다.
        keep.append((key, {"bbox": box, "polygon": poly},
                     item.get("cls") or ""))

    for key, geom, cls in keep:
        obj = judgement_for(vp, image, None, key)
        obj.source = "manual"
        obj.bind_method = "manual"
        obj.geom = geom
        obj.save()
        # 분류는 개체에 앉는다 (P12)
        dobj = obj.foram_object
        if dobj.label != cls:
            dobj.label = cls
            dobj.save(update_fields=["label", "updated_at"])

    # **번지는 것은 청소보다 먼저다.** 청소는 이 이미지의 행만 보므로 순서가
    # 뜻을 안 바꾸지만, 번지기가 세운 행이 청소에 걸릴 자리를 아예 안 만든다.
    spread = _spread_drawn(vp, image, batch, keep)

    stale = (ObjectReview.objects.filter(image=image, batch__isnull=True)
             .exclude(mask_key__in=[k for k, *_ in keep]))
    orphaned = list(stale.values_list("foram_object_id", flat=True))
    stale.delete()
    prune_objects(orphaned)
    # **대표를 잃은 개체가 남는다** — 번지기가 만든 자리다 (아래 머리말).
    _reelect_reps(orphaned)
    # 앵커도 같은 자리에서 빈다 (P18).
    reanchor(orphaned)
    # 번지면 묶음이 생기고, 지우면 멤버가 빠져 묶음이 아니게 될 수 있다.
    # **둘 다 화면의 사슬을 갈아야 하는 순간이다** (부르는 쪽 주석).
    return len(keep), spread, bool(spread or orphaned)


def spread_targets(vp: Viewpoint, src_id: int, batch_id) -> list:
    """**어느 판에 앉히는가** — 이 회차에 현재 검출이 있는 판, 자기 빼고 (106).

    **부르는 자리가 둘이다** — 그린 마스크가 번질 때(`_spread_drawn`)와 사람이
    검출 마스크를 앉힐 때(`spread_detection` · P19). 둘이 대상을 따로 고르면
    **규칙이 둘이 된다**(104·105 가 그렇게 당했다).

    **복제할 판을 엔진 이름으로 고르지 않는다.** *이 회차에 현재 검출이 있는
    판*으로 잡으면 SAM(시야당 판 하나)과 단독 프레임 시야는 앉힐 곳이 없어
    **자동으로 지나간다.**

    검출이 없는 판에는 **넣으면 안 된다.** 그 판의 화면은 마스크를 안 그리고
    저장도 못 받아(`save_review` 가 거절한다) **손댈 수 없는 유령**이 된다.
    """
    if batch_id is None:
        return []
    return list(Image.objects.filter(
        viewpoint=vp, detections__is_current=True,
        detections__run__batch_id=batch_id).exclude(pk=src_id).distinct())


def spread_rep_image_id(targets, src_id: int) -> int:
    """**얼굴이 될 판** — 대상 중 합성본, 없으면 `src` (106 · 116).

    `src` 는 *그린 판*이 아니라 **저장할 때 열려 있던 판**이라 그것으로 정하면
    판을 옮겨 다닐 때마다 대표가 따라다닌다. `is_rep` 은 "학습 자료로 뽑을 때,
    목록에 보일 때 이 판을 쓴다" 이므로 고를 기준은 *어디서 눌렀나* 가 아니라
    **어느 판이 이 개체을 가장 잘 보여주나** 다 — 합성본이 그것이다.
    """
    return next((t.pk for t in targets if t.kind == "stack"), src_id)


def new_manual_key() -> str:
    """손그림 키를 하나 만든다 — `MANUAL_KEY` 를 만족하는 `m########`.

    **화면이 만들던 것을 서버도 만든다** (P19 4.1). 검출 마스크를 다른 판에
    앉힐 때 원본 키(`736_615_189_258`)를 그대로 쓰면 `_save_drawn` 이
    `ValueError` 를 던져 **그 판의 다음 저장이 통째로 거절된다** — 앉힌 마스크
    하나 때문에 그 판의 교정 전부가 안 들어간다.

    같은 개체이므로 **판마다 다른 키를 주지 않는다.** 유일 제약은
    `(image, mask_key)` 라 같은 키가 여러 판에 앉는 것이 정상이고,
    `_spread_drawn` 이 그린 마스크를 번지게 할 때와 같은 모양이다.
    """
    while True:
        key = "m%08x" % secrets.randbelow(1 << 32)
        # **부딪히면 다시 뽑는다.** 32비트라 부딪힐 일이 드물지만, 부딪히면
        # 남의 마스크에 얹혀 그 판의 교정이 뒤바뀐다 — 드문 것과 안 나는 것은
        # 다르다.
        if not ObjectReview.objects.filter(mask_key=key).exists():
            return key


def spread_free_targets(obj, targets, key):
    """이 개체가 **이미 멤버를 둔 판은 건너뛴다** (P11 약속 1 · P19 4.3).

    `(foram_object, image)` 에 유일 제약이 있다 — *한 이미지에서 하나*. 안
    거르고 앉히면 `IntegrityError` 로 **저장이 500 이 된다.**

    **다만 그 멤버가 바로 우리가 앉히려는 그 행이면 그대로 쓴다.** 그린 마스크가
    저장할 때마다 다시 번지는 길(`_spread_drawn`)이 그 자리다 — 이미 있는 행을
    `judgement_for` 가 그대로 돌려주므로 옮길 것이 없다.

    **P19 전에는 이 갈래가 안 났다.** 그린 마스크만 번질 때는 개체의 멤버가 곧
    번진 판들이라 늘 "그 행" 이었다. 검출 마스크를 앉히면서 **판정 하나(원본
    회차) + 그린 마스크 여럿(복제본)** 인 섞인 개체가 생겼고, 그 개체의 복제본이
    있는 판에서 저장하면 옛 번지기가 **원본이 앉아 있는 판으로 되번진다.**
    브라우저 시험이 그것을 500 으로 잡았다 (157).
    """
    have = {m.image_id: m for m in
            ObjectReview.objects.filter(foram_object=obj)}
    out = []
    for t in targets:
        m = have.get(t.pk)
        if m is None or (m.batch_id is None and m.mask_key == key):
            out.append(t)
    return out


def _spread_drawn(vp: Viewpoint, image, batch, keep) -> dict:
    """그린 마스크를 **같은 시야의 다른 판에도 앉히고 한 개체로 묶는다** (106).

    돌려주는 것은 `{이미지 id: [{key, cls, geom}]}` — **화면이 이것을 받아야
    한다**(아래 "반쪽으로 넣으면 잃는다").

    ## 되는 근거는 촬영 방식이다

    **같은 시야는 스테이지가 안 움직인다.** 초점만 다르므로 좌표가 그대로 맞는다
    — 기계가 "같은 개체인 것 같다" 고 판정하는 것이 아니라 찍는 방법이 보장한다.
    106 이 접은 C 안(프레임 개체 수를 세어 보여주기)과 갈리는 자리가 정확히
    여기다.

    ## 복제할 판을 엔진 이름으로 고르지 않는다

    *이 회차에 현재 검출이 있는 판*으로 잡는다. 그러면 SAM(시야당 판 하나)과
    단독 프레임 시야는 복제할 곳이 없어 **자동으로 지나간다** — "YOLO 일 때만"
    을 이름으로 판정할 이유가 없다.

    검출이 없는 판에는 **넣으면 안 된다.** 그 판의 화면은 마스크를 안 그리고
    저장도 못 받아(`save_review` 가 거절한다) **손댈 수 없는 유령**이 된다.

    ## 한 개체를 나눠 갖는다

    같은 개체을 옮겨 그린 것이라 뜻이 그렇다. 그래서 자세(`ForamObject.pose`)
    ·분류·종명·등급·코멘트가 **한 벌이다**(0035·0036 뒤로 등급과 코멘트도 개체에
    산다). 어느 판으로 보여줄지는 `is_rep` 가 말한다. 판마다 개체를 따로 두면 이 값들을 판 수만큼
    매겨야 하고 `check_db` 10번이 그 어긋남을 못 잡는다.

    묶는 일은 `merge_into_object` 가 한다 — 사람이 화면에서 묶을 때와 **같은
    문**이다. `judgement_for` 는 행마다 개체를 새로 세우므로(P12) 그대로 두면
    판 수만큼 개체가 서서 "안 묶인 여럿" 이 된다.

    ## 대표는 합성본이다 — 그린 판이 아니라 (실사용 2026-08-13)

    처음에는 **그린 판**을 대표로 삼았다("기준이 된 판이라 그렇다"). 그런데
    `src` 는 *그린 판*이 아니라 **저장할 때 열려 있던 판**이다. 08-09 에
    합성본에 그린 마스크를 오늘 프레임 판을 열어 둔 채 저장하니 그 프레임이
    `src` 가 되고, 합성본이 target 으로 밀리면서 **대표가 조용히 옮겨갔다** —
    실사용에서 개체 아홉이 그렇게 흐린 단일 프레임을 얼굴로 갖게 됐다.
    새 대표는 가장 선명한 프레임도 아니었다. 그때 열려 있던 판일 뿐이다.

    `is_rep` 은 **"학습 자료로 뽑을 때, 목록에 보일 때 이 판을 쓴다"** 이므로
    (`ObjectReview.is_rep` 머리말) 고를 기준은 *어디서 눌렀나* 가 아니라
    **어느 판이 이 개체을 가장 잘 보여주나** 여야 한다. 합성본이 그것이다 —
    all-in-focus 로 합친 그림이고, 판 넷 중 하나를 고르는 문제가 아니다.

    합성본이 없는 시야(싱글턴·아직 안 합친 것)는 `src` 를 그대로 쓴다. 그 판을
    지우면 `_reelect_reps` 가 다시 세운다.

    ## 반쪽으로 넣으면 잃는다

    복제한 마스크는 **다른 판의 화면이 모른다.** 그 화면은 열릴 때 받은 상태를
    들고 있고 `/review` 는 payload 에 없는 그린 개체를 지우므로, 프레임 3에
    그리고 프레임 5로 넘어가 저장하면 **복제가 그 자리에서 사라진다.** 그래서
    번진 것을 응답에 실어 화면이 자기 상태에 얹는다 — 104 가 분류에서 지난
    함정이고 `linked_siblings` 가 같은 이유로 있다.
    """
    src_id = getattr(image, "pk", image)
    batch_id = getattr(batch, "pk", batch)
    if not keep or batch_id is None:
        return {}
    targets = spread_targets(vp, src_id, batch_id)
    if not targets:
        return {}
    rep_image_id = spread_rep_image_id(targets, src_id)

    out = {}
    for key, geom, cls in keep:
        src = ObjectReview.objects.select_related("foram_object", "foram_object__taxon").get(
            image_id=src_id, batch__isnull=True, mask_key=key)
        # **이 개체가 이미 멤버를 둔 판은 건너뛴다** — 그 판에 원본 판정이
        # 앉아 있으면 유일 제약에 걸려 저장이 500 이 된다 (157).
        free = spread_free_targets(src.foram_object, targets, key)
        rows = [(src, src_id == rep_image_id)]
        for tgt in free:
            row = judgement_for(vp, tgt, None, key)
            row.source = "manual"
            row.bind_method = "manual"
            row.geom = geom
            row.save()
            rows.append((row, tgt.pk == rep_image_id))
            out.setdefault(str(tgt.pk), []).append(
                {"key": key, "cls": cls, "geom": geom})
        # **저장할 때마다 다시 번지지 않는다** — `judgement_for` 가 있는 행을
        # 그대로 돌려주고, 이미 이 개체에 속한 행은 옮길 것이 없다.
        merge_into_object(src.foram_object, rows)
    return out


def spread_detection(vp: Viewpoint, image, batch, key, geom, cand=None) -> dict:
    """**검출 마스크를 같은 시야의 다른 판에도 앉히고 한 개체로 묶는다** (P19).

    돌려주는 것은 `{"spread": {이미지 id: [{key, cls, geom}]}, "n": 앉힌 판 수,
    "key": 복제본의 키}` — **화면이 `spread` 를 자기 상태에 얹어야 한다**
    (`_spread_drawn` 머리말 "반쪽으로 넣으면 잃는다").

    ## 왜 있나

    검출이 **한두 판에서만 훌륭하게 잡히는** 일이 있다. P18 로 카드 하나가 개체
    하나가 되면서 프레임에서만 잡힌 개체도 카탈로그에 나오는데, 멤버가
    하나뿐이면 **그 흐린 프레임이 곧 얼굴이다** — 152 가 얼굴을 사람 손에
    돌려놨어도 고를 것이 없다. 잘 잡힌 마스크를 합성본에 앉히고 묶으면 얼굴이
    선다.

    되는 근거는 `_spread_drawn` 과 같다 — **같은 시야는 스테이지가 안 움직인다.**
    그 근거는 마스크가 사람 손에서 나왔든 검출기에서 나왔든 똑같이 성립한다.

    ## 복제본은 **사람이 그린 마스크**다 (`batch=None`)

    회차에 붙은 판정으로 앉히면 안 된다 (P19 3.1).

    - `check_db` 554번의 *"멤버의 마스크가 실재한다"* 는 회차 붙은 멤버에
      `Candidate` 행을 요구한다 — 검출기가 안 낸 마스크라 후보가 없어 앉히는
      족족 dangling 으로 걸린다
    - `_with_reviews` 가 교정을 `(image, batch)` 로 거르는데 **`batch=None` 만
      늘 보여준다** — SAM/YOLO 라디오를 돌려도 남아야 하는 것이 맞다
    - 뜻이 그렇다: *"이 판에도 이 개체이 여기 있다"* 는 **엔진에 대한 판단이
      아니라 이미지에 대한 사실**이다

    **원본은 안 건드린다.** 잘 잡힌 그 판의 판정은 회차에 붙은 채 그대로 있고,
    개체는 **판정 하나(원본) + 그린 마스크 여럿(복제본)** 이 된다.

    ## 얼굴은 혼자일 때만 옮긴다

    대상 중에 합성본이 있으면 그것이 대표다 — **이 기능의 목적이 그것이다.**
    **다만 이미 묶여 있는 개체이면 안 옮긴다**: 멤버가 둘 이상이라는 것은
    사람이 ★ 로 골랐다는 뜻이고, **사람이 고른 것을 기계가 다시 고르지 않는다**
    (152 · 사용자 방침 2026-08-25).

    ## 겹치는 검출은 안 본다 (사용자 방침 2026-08-25)

    검출이 있는 판이면 겹치는 것이 있든 없든 앉힌다. **사람이 누르기 전에 이미
    고른 것**이기 때문이다 — 겹치는 마스크를 묶는 게 낫겠으면 묶기로 가고,
    아니면 이 문으로 온다. 되물으려면 IoU 문턱을 정해야 하는데 그것이 곧
    "이 둘이 같은 개체 같다" 는 판정이고, **기계가 정체를 판정하지 않는다**(P11).

    **건너뛰는 것이 딱 하나 있다 — 이미 이 개체의 멤버가 있는 판이다.** 그것은
    짐작이 아니라 사실이고(`ObjectReview.foram_object`), 안 거르면 **한 판에
    같은 개체의 마스크가 둘** 선다.
    """
    src_id = getattr(image, "pk", image)
    batch_id = getattr(batch, "pk", batch)

    # 원본 판정을 확보한다 — **회차에 붙은 채로 둔다**(머리말).
    src = judgement_for(vp, src_id, batch_id, key, cand)
    if not src.geom and geom:
        src.geom = geom
        src.save(update_fields=["geom"])
    obj = src.foram_object
    geom = src.geom or geom

    members = list(ObjectReview.objects.filter(foram_object=obj))
    new_key = new_manual_key()
    targets = spread_free_targets(obj, spread_targets(vp, src_id, batch_id),
                                  new_key)
    if not targets:
        return {"spread": {}, "n": 0, "key": ""}

    # **얼굴은 혼자일 때만 옮긴다**(머리말). 이미 묶여 있으면 지금 대표를 그대로
    # 둔다 — `merge_into_object` 가 그릇의 `is_rep` 를 전부 내리므로, 지킬
    # 대표는 `rows` 에 함께 실어야 살아남는다.
    alone = len(members) <= 1
    if alone:
        rep_image_id = spread_rep_image_id(targets, src_id)
        rows = [(src, src_id == rep_image_id)]
    else:
        rep_image_id = None
        rows = [(m, bool(m.is_rep)) for m in members]

    cls = obj.label or ""
    out = {}
    for tgt in targets:
        row = judgement_for(vp, tgt, None, new_key)
        row.source = "manual"
        row.bind_method = "manual"
        row.geom = geom
        row.save()
        rows.append((row, tgt.pk == rep_image_id))
        out[str(tgt.pk)] = [{"key": new_key, "cls": cls, "geom": geom}]
    merge_into_object(obj, rows)
    return {"spread": out, "n": len(targets), "key": new_key}


def reanchor(ids) -> int:
    """앵커를 잃은 개체에 앵커를 다시 세운다 (P18). 돌려주는 것은 세운 수.

    앵커 판정이 지워지면 `SET_NULL` 이라 칸이 빈다 — 그러면 **카탈로그 번호를
    만들 재료가 없어진다.** 남은 멤버 중 **가장 오래된 것**으로 넘긴다(그 개체가
    선 순서를 따르는 것이고, 마이그레이션 `0040` 이 채운 규칙과 같다).

    **번호가 바뀐다.** 조용히 바뀌면 안 되는 자리라 화면이 그렇게 적어야 한다 —
    앵커를 잃는 것은 *그 판을 지웠다* 는 뜻이고 사람이 한 일이다.

    **멤버가 하나도 없으면 안 세운다.** 그런 개체는 `prune_objects` 가 걷고,
    남아 있으면 `check_db` 가 유령으로 센다.
    """
    if not ids:
        return 0
    n = 0
    for obj in ForamObject.objects.filter(pk__in=set(ids), anchor__isnull=True):
        row = obj.members.order_by("pk").first()
        if row is None:
            continue
        ForamObject.objects.filter(pk=obj.pk).update(anchor=row)
        n += 1
    return n


def _reelect_removed_reps(ids) -> int:
    """**대표가 지워졌으면 살아 있는 판으로 옮긴다** (151). 돌려주는 것은 옮긴 수.

    `_reelect_reps` 와 다른 자리다 — 저쪽은 대표 **행이 사라진** 것이고 여기는
    행은 있는데 그 판이 오검출로 지워진 것이다. 예외는 안 나고, `is_rep` 의
    유일 제약도 "둘 이상" 만 막아 이 상태를 통과시킨다.

    **묶인 판을 지우는 것은 막지 않기로 했다** (사용자 방침 2026-08-25) —
    묶음은 정체에 대한 말이고 오검출 판정은 판마다 다르다. 그러면 남는 해악은
    하나다: **개체의 얼굴이 오검출이 되는 것.** 그것만 여기서 막는다.

    고르는 기준은 116 그대로다 — **합성본이 있으면 합성본**, 없으면 남은 것 중
    가장 크게 보이는 판(`link_mains` 와 같은 기준이라 카드와 얼굴이 안 갈린다).

    **살아 있는 판이 하나도 없으면 안 옮긴다.** 전부 오검출인 개체는 얼굴을
    고를 자리가 없고, 그것은 `check_db` 가 따로 세는 상태다.
    """
    if not ids:
        return 0
    n = 0
    for obj in (ForamObject.objects.filter(pk__in=set(ids),
                                            members__is_rep=True,
                                            members__removed=True)
                .distinct().prefetch_related("members__image")):
        live = [m for m in obj.members.all() if not m.removed]
        if not live:
            continue
        stacks = [m for m in live if m.image.kind == "stack"]
        want = (stacks or live)
        row = max(want, key=_member_area)
        # **옛 대표를 먼저 내린다** — `is_rep` 은 개체당 하나라는 유일 제약이
        # 있어 순서를 바꾸면 `IntegrityError` 다 (`merge_into_object` 와 같은 줄).
        ObjectReview.objects.filter(foram_object=obj, is_rep=True).update(
            is_rep=False)
        ObjectReview.objects.filter(pk=row.pk).update(is_rep=True)
        n += 1
    return n


def _reelect_reps(ids) -> int:
    """대표를 잃은 개체에 대표를 다시 세운다. 돌려주는 것은 세운 수.

    **번지기가 만든 자리다.** 그린 마스크가 판마다 번지면 개체 하나에 멤버가
    여럿이고 **대표는 합성본**인데(116), 그 판에서 마스크를 지우면 대표만 빠지고
    나머지 판의 복제는 남는다(방침 2 — 한 판에서 지워도 다른 판은 남긴다).
    그러면 **멤버는 있는데 대표가 없는 개체**가 선다.

    예외는 안 난다. `is_rep` 의 유일 제약은 "둘 이상" 만 막고 **0개는 못 막는다**
    — `check_db` 8번이 세는 그 상태이고, 학습 자료를 뽑을 때 얼굴이 없는 개체가
    된다. `prune_objects` 바로 뒤에서 부른다: 멤버가 하나도 안 남은 개체는 이미
    걷혔으므로 여기 걸리는 것은 **살아남았는데 대표가 없는** 것뿐이다.
    """
    if not ids:
        return 0
    n = 0
    for obj in ForamObject.objects.filter(pk__in=set(ids)).exclude(
            members__is_rep=True):
        # 남은 멤버 중 하나를 세운다. 어느 판이 될지는 뜻이 없다 — 합성본이
        # 사라진 뒤라 "이 개체을 가장 잘 보여주는 판" 이라는 것이 이미
        # 없기 때문이다(116). 남은 것은 초점면이 다른 프레임들뿐이다.
        row = obj.members.order_by("pk").first()
        if row is None:
            continue
        ObjectReview.objects.filter(pk=row.pk).update(is_rep=True)
        n += 1
    return n


def polygon_axis(poly) -> tuple[float, float] | None:
    """마스크의 주축 각도(도)와 축 비율 — 채워진 영역의 2차 모멘트로."""
    if not poly or len(poly) < 6:
        return None
    xs = [float(v) for v in poly[0::2]]
    ys = [float(v) for v in poly[1::2]]
    n = len(xs)
    a2 = sxx = syy = sxy = 0.0
    cx = cy = 0.0
    for i in range(n):
        j = (i + 1) % n
        x0, y0, x1, y1 = xs[i], ys[i], xs[j], ys[j]
        cross = x0 * y1 - x1 * y0
        a2 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
        sxx += cross * (x0 * x0 + x0 * x1 + x1 * x1)
        syy += cross * (y0 * y0 + y0 * y1 + y1 * y1)
        sxy += cross * (2 * x0 * y0 + x0 * y1 + x1 * y0 + 2 * x1 * y1)
    area = a2 / 2.0
    if abs(area) < 1e-9:
        return None
    cx /= 6.0 * area
    cy /= 6.0 * area
    m20 = sxx / (12.0 * area) - cx * cx
    m02 = syy / (12.0 * area) - cy * cy
    m11 = sxy / (24.0 * area) - cx * cy
    ang = 0.5 * math.atan2(2.0 * m11, m20 - m02)
    diff = math.hypot(m20 - m02, 2.0 * m11)
    l1 = (m20 + m02 + diff) / 2.0
    l2 = (m20 + m02 - diff) / 2.0
    ratio = math.sqrt(l1 / l2) if l2 > 1e-9 else 999.0
    return math.degrees(ang), ratio


def rotated_extent(poly, deg: float) -> tuple[int, int]:
    r = math.radians(deg)
    cos, sin = math.cos(r), math.sin(r)
    xs = [float(v) for v in poly[0::2]]
    ys = [float(v) for v in poly[1::2]]
    rx = [x * cos - y * sin for x, y in zip(xs, ys)]
    ry = [x * sin + y * cos for x, y in zip(xs, ys)]
    return (max(1, int(round(max(rx) - min(rx)))),
            max(1, int(round(max(ry) - min(ry)))))
# 이 비율 아래면 방향을 정할 수 없다 — 원형을 굳이 돌려 보간으로 흐리지 않는다.
UPRIGHT_MIN_RATIO = 1.15


def crop_geometry(c: dict, rotate: bool = True) -> dict | None:
    """갤러리 크롭의 회전량과 결과 크기(px). 주축을 세로로 세운다."""
    poly = c.get("polygon")
    if not poly or len(poly) < 6:
        return None
    deg = 0.0
    if rotate:
        axis = polygon_axis(poly)
        if axis and axis[1] >= UPRIGHT_MIN_RATIO:
            deg = 90.0 - axis[0]
            while deg > 90:
                deg -= 180
            while deg < -90:
                deg += 180
    w, h = rotated_extent(poly, deg)
    m = max(3, round(0.08 * max(w, h)))
    ow, oh = w + 2 * m, h + 2 * m
    return {"rot": round(deg, 2), "out": f"{ow},{oh}", "out_w": ow, "out_h": oh}


def nice_length(v: float) -> float:
    """1·2·5 계열로 떨어뜨린다 — 눈금은 읽기 좋은 값이어야 한다."""
    if v <= 0:
        return 0.0
    e = 10.0 ** math.floor(math.log10(v))
    m = v / e
    return (1 if m < 1.5 else 2 if m < 3.5 else 5 if m < 7.5 else 10) * e


def scalebar_for(out_w: int, um_per_px: float, frac: float = 0.4) -> dict | None:
    """크롭 썸네일에 얹을 스케일바. 이미지 폭에 대한 백분율로 준다."""
    if not out_w or not um_per_px:
        return None
    um_w = out_w * um_per_px
    target = um_w * frac
    if target <= 0:
        return None
    e = 10.0 ** math.floor(math.log10(target))
    m = target / e
    bar = (5 if m >= 5 else 2 if m >= 2 else 1) * e
    if bar <= 0:
        return None
    label = f"{bar:g} µm" if bar >= 1 else f"{bar:.1f} µm"
    return {"pct": round(100.0 * bar / um_w, 2), "um": bar, "label": label}


# --- 파일 접근 --------------------------------------------------------------
def safe_image_path(rel: str) -> Path | None:
    """p= 로 들어온 상대경로를 실제 파일로 바꾼다.

    IMAGE_DIRS 안에 실제로 들어 있는 파일만 허용한다. symlink 까지 풀어서
    비교하므로 ../ 나 링크로 바깥을 가리키는 경로는 통과하지 못한다.
    """
    if not rel:
        return None
    root = Path(settings.DATA_ROOT).resolve()
    try:
        target = (root / rel).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not target.is_file():
        return None
    for allowed in settings.IMAGE_DIRS:
        base = (root / allowed).resolve()
        if target.is_relative_to(base):
            return target
    return None


# --- 다른 엔진 결과 보기 (시험용) ---------------------------------------------
def _engine_pick(dets):
    """한 시야에 쌓인 검출들 중 **화면이 그릴 것 하나씩** 고른다.

    같은 자리에 검출이 여럿 남을 수 있다. 두 가지 경로로 그렇게 된다:

    - 빠진 프레임만 다시 돌려도 `--keep-current` 는 이미 있는 것 위에 또 쌓는다
    - 첫 시도가 실패해 다시 돌린 것이 그대로 남아 있다 (SAM2 #37 → #39)

    **현재로 올라간 것, 그 다음 나중 것**을 고른다. 아무 것이나 고르면 화면과
    묶음 칸의 개수가 어긋나고 — 실제로 그렇게 어긋났다 — 첫 시도의 죽은 결과가
    그 묶음의 성적표인 것처럼 보인다.
    """
    by_frame, stack_det = {}, None
    for d in sorted(dets, key=lambda d: (d.is_current, d.pk)):
        # 무엇에 붙은 검출인가는 `image.kind` 가 말한다 (P06 5a). 예전에는
        # `target` + nullable `frame` 이라는 다형 연관을 흉내 냈다.
        if d.image_id is None:
            continue
        if d.image.kind == "frame":
            by_frame[d.image.frame_id] = d
        elif d.image.kind == "stack":
            stack_det = d
    return by_frame, stack_det


def engine_detection(det: Detection) -> dict:
    """`is_current` 가 아닌 검출 하나를 화면이 쓰는 dict 로.

    **교정을 얹지 않는다.** 교정은 `mask_key`(bbox 문자열)로 붙는데 엔진이 다르면
    거의 전부 어긋난다. 억지로 얹으면 "사람이 지운 것" 이 엉뚱한 개체에 붙어
    보이게 된다. 이 화면의 목적은 **엔진이 무엇을 냈는가** 를 그대로 보는 것이다.

    `_apply_review` 에 빈 교정을 넘겨 같은 dict 모양을 얻는다 — 모양을 여기서
    다시 만들면 `_detection.html` 이 기대하는 칸이 하나라도 빠질 때 조용히
    깨진다.
    """
    return _apply_review(det, {}, None)


def engine_viewpoint(slug: str, gid: int, run_id: int,
                     link=None) -> dict | None:
    """시험 화면 한 장. `group_detail` 과 같은 모양이되 검출만 갈아 끼운다.

    YOLO 는 **프레임마다** 검출을 낸다(합성본이 아니라 원본을 본다). 그래서
    프레임 하나하나에 각자의 검출이 붙는다 — `group_detail` 이 싱글턴에 쓰던
    길과 같다.

    `link` 는 이웃 시야의 주소를 만드는 함수다. **부르는 쪽이 준다** — 엔진을
    갈아 끼운 상태(`/d/…?batch=`)는 그 상태로 옆 시야로 가야 하기 때문이다.
    주소를 여기 박아 두면 "다음 시야" 를 누를 때마다 현재 검출로 튀어나간다
    (051 이전에 실제로 그랬다). 한때 `/engine/` 이라는 다른 화면도 이 함수를
    썼고 그쪽은 거기 머물러야 했다 — 그 화면은 075 에서 지웠다.
    """
    slide = Slide.objects.filter(slug=slug).first()
    if slide is None:
        return None
    vp = _viewpoints_of(slide).filter(idx=gid).first()
    if vp is None:
        return None
    if link is None:
        # **여기서 만드는 주소는 검토 화면의 `?batch=` 다** (075). 예전에는
        # `/engine/` 로 갔는데 그 화면을 지웠으므로, 남겨 두면 `NoReverseMatch`
        # 로 **화면이 통째로 500** 이 된다. 들어오는 값이 404 가 되는 것과
        # 나가는 값을 못 만드는 것은 고장의 크기가 다르다 (057).
        def link(i):
            return reverse("group", args=[slug, i]) + f"?batch={run_id}"

    # 묶음이면 형제 실행까지 본다 — 한 슬라이드가 한 실행이라 시야를 열면
    # 그 시야를 만든 실행은 하나뿐이지만, 주소의 실행 번호가 다른 슬라이드
    # 것일 수 있다.
    # **현재 검출도 함께 본다.** 예전에는 쌓아 둔 것만 봤다 — 검토 화면과
    # 겹치지 않게 하려는 뜻이었다. 그런데 SAM2 묶음은 결과의 대부분이 현재로
    # 올라가 있어서, 묶음을 고를 수 있게 되자 "SAM2 칸을 눌렀는데 아무것도
    # 없다" 가 됐다. 비교하려면 그 묶음이 **실제로 낸 것**을 봐야 한다.
    # 이 화면은 읽기 전용이고 교정을 얹지 않으므로 검토 화면을 건드리지 않는다.
    ids = set(engine_run_ids(run_id))
    by_frame, stack_det = _engine_pick(
        [d for d in vp.detections.all() if d.run_id in ids])

    # **엔진마다 검출을 다는 자리가 다르다.** SAM2 는 합성본 한 장에만 달고,
    # YOLO 는 프레임마다 + 합성본에도 단다. 프레임만 보면 SAM2 묶음은 통째로
    # 빈 화면이 된다 — 묶음을 고를 수 있게 된 뒤로는 사람이 그 빈 화면을 직접
    # 누르게 된다.

    frames = []
    all_frames = list(vp.frames.all())
    # 선명도 막대는 그룹 내 최고 대비 비율이다. 빠뜨리면 `_shots.html` 이
    # `width:%` 라는 값 없는 CSS 를 내고 막대가 통째로 사라진다.
    values = [f.sharpness for f in all_frames if f.sharpness is not None]
    top = max(values) if values else 0
    for f in all_frames:
        d = by_frame.get(f.id)
        frames.append({
            "name": f.name,
            "acquired_at": f.acquired_at,
            "created_at": f.created_at,
            "sharpness": f.sharpness,
            "sharp_pct": (round(100 * f.sharpness / top)
                          if f.sharpness and top else 0),
            "is_sharpest": f.is_sharpest,
            "rel": f.path,
            "exists": (Path(settings.DATA_ROOT) / f.path).exists(),
            "detection": engine_detection(d) if d else None,
        })

    ids = list(Viewpoint.objects.filter(slide=slide)
               .order_by("idx").values_list("idx", flat=True))
    pos = ids.index(gid)
    st = getattr(vp, "stack", None)

    # **한 화면에서 캐러셀로 갈아 본다.** 프레임마다 화면을 따로 그리면 세로로
    # 늘어서서 비교할 수가 없다 — 같은 자리에서 프레임만 바뀌어야 "이 개체이
    # 어느 초점면에서 잡혔나" 가 보인다. 판 하나를 그리고 나머지는 캐러셀이
    # 갈아 끼울 자료로 넘긴다.
    def _shot(d):
        c = d["counts"]
        parts = _count_parts(c)
        return {
            "candidates": d["candidates"],
            "rejected": d["rejected"],
            "summary": (f"후보 {d['n_candidates']}개"
                        + (f" ({', '.join(parts)})" if parts else "")
                        + f" · 원시 {d['n_raw_masks']}"
                        + (f" → 크기통과 {d['n_sized']}" if d['n_sized'] else "")
                        + f" · 탈락 {len(d['rejected'])}개"),
        }

    stack = (_stack_dict(st, engine_detection(stack_det))
             if st and stack_det else None)
    if stack:
        stack["detkey"] = STACK_KEY

    # 캐러셀이 갈아 끼울 판들. 합성본도 프레임과 같은 자격으로 넣는다.
    pool = [{"key": f["name"], "rel": f["rel"], "det": f["detection"]}
            for f in frames if f["detection"]]
    if stack:
        pool.append({"key": STACK_KEY, "rel": stack["focused_rel"],
                     "det": stack["detection"]})

    shots = {p["key"]: _shot(p["det"]) for p in pool}
    # **개체가 있는 합성본이면 거기서 시작한다.** 시야로 들어가면 그 시야를
    # 대표하는 한 장이 먼저 보여야 하고, 그게 합성본이다 — 검토 화면
    # (`group_detail`)도 같은 규칙이라 두 화면이 같은 자리에서 열린다.
    #
    # 아니면 **개체가 가장 많은 판**으로 물러난다. 그것이 원래 규칙이었고 이유가
    # 있다 — 빈 판이 먼저 열리면 검출이 아무것도 없는 줄 알게 된다.
    #
    # **"합성본이 있으면" 이 아니라 "개체가 있으면" 이다.** 처음엔 합성본을
    # 무조건 골랐는데, 합성본만 비고 프레임에는 개체가 있는 시야가 yolo-1차에서
    # 10개 나왔다 — 옛 규칙이 막으려던 바로 그 화면을 다시 만들 뻔했다.
    #
    # 실측: 이 규칙 전에는 YOLO 묶음의 여러장 시야 317개 중 **247개**가 합성본이
    # 아닌 프레임에서 열렸다. SAM2 는 합성본에만 검출이 있어 0개였고, 그래서
    # 엔진을 갈아 끼울 때마다 시작 판이 달라 보였다.
    stack_p = next((p for p in pool if p["key"] == STACK_KEY
                    and p["det"]["candidates"]), None)
    best = stack_p or max(pool, key=lambda p: len(p["det"]["candidates"]),
                          default=None)
    return {
        "slug": slug, "label": slide.name, "id": gid, "tag": vp.tag,
        "cell": vp.cell,
        "run_id": run_id,
        "n": vp.n_frames,
        "sharpest": vp.sharpest_frame.name if vp.sharpest_frame else None,
        "frames": frames,
        "stack": stack,
        "base_rel": best["rel"] if best else None,
        "base_det": best["det"] if best else None,
        "base_name": (("합성본" if best["key"] == STACK_KEY else best["key"])
                      if best else None),
        "shot_dets": shots,
        "n_objects": sum(len(p["det"]["candidates"]) for p in pool),
        "n_frames_with_det": sum(1 for p in pool if p["key"] != STACK_KEY),
        "has_stack_det": stack is not None,
        "prev_id": ids[pos - 1] if pos > 0 else None,
        "next_id": ids[pos + 1] if pos < len(ids) - 1 else None,
        # **이 화면 안에 머문다.** 사진 띠의 시야 이동 단추가 검토 화면 주소를
        # 박아 두고 있어서, 여기서 "다음 시야" 를 누르면 /d/ 로 튀어나갔다.
        "prev_url": link(ids[pos - 1]) if pos > 0 else None,
        "next_url": link(ids[pos + 1]) if pos < len(ids) - 1 else None,
    }


# 캐러셀에서 합성본을 가리키는 열쇠. 프레임 이름(`Snap-…`)과 겹칠 수 없어야
# 한다 — 겹치면 프레임을 눌렀는데 합성본 검출이 얹힌다.
STACK_KEY = "__stack__"


def engine_run_ids(run_id: int) -> list[int]:
    """실행 하나가 묶음에 속하면 그 묶음의 실행 전부를, 아니면 자기만.

    화면은 묶음을 하나처럼 다룬다. 주소에는 실행 번호가 들어가지만, 그 실행이
    묶여 있으면 형제들까지 함께 보여야 "전체를 한 번 훑은 것" 이 한 화면에 온다.
    """
    r = Run.objects.filter(pk=run_id).select_related("batch").first()
    if r is None:
        return []
    if r.batch_id:
        return list(Run.objects.filter(batch_id=r.batch_id)
                    .values_list("pk", flat=True))
    return [r.pk]


def batches_for_viewpoint(slug: str, gid: int,
                          run_id: int | None = None) -> list[dict]:
    """이 시야에 쌓여 있는 묶음들. 검출 화면에서 갈아 끼우는 데 쓴다.

    **같은 시야를 보면서 엔진을 바꿀 수 있어야 한다.** 목록으로 나갔다가 다른
    묶음으로 들어오면 어느 시야를 보고 있었는지 잃는다 — 비교는 같은 자리를
    번갈아 보는 일이다.

    주소에는 실행 번호가 들어가지만 화면은 묶음 단위로 다루므로, 각 묶음에서
    **아무 실행 하나**를 대표로 준다(`engine_run_ids` 가 형제까지 편다).

    칸마다 개체 합계를 함께 준다. 눌러 보기 전에 "여기서는 몇 개를 잡았나" 를
    비교하는 것이 이 화면의 목적이고, 그 수가 묶음을 고르는 근거이기 때문이다.
    """
    vp = (Viewpoint.objects.filter(slide__slug=slug, idx=gid)
          .prefetch_related("detections__run__batch").first())
    if vp is None:
        return []
    # 검토 화면에서 부를 때는 "지금 보고 있는 묶음" 이 없다 — 그때는 아무것도
    # 켜지지 않는다.
    here = set(engine_run_ids(run_id)) if run_id else set()

    per = defaultdict(list)
    for d in vp.detections.all():
        if d.run_id is None:
            continue
        r = d.run
        per[("b", r.batch_id) if r.batch_id else ("r", r.pk)].append(d)

    # **화면이 세는 것과 같은 것을 센다.** 화면은 문턱을 통과한 것만 그리므로
    # 칸이 원시 개수를 보이면 눌러 보고 "아까 그 수가 아닌데" 가 된다. 교정은
    # 얹지 않는 화면이라 `passed` 하나로 갈린다(`_apply_review`).
    out = []
    for key, dets in per.items():
        by_frame, stack_det = _engine_pick(dets)
        picked = list(by_frame.values()) + ([stack_det] if stack_det else [])
        rep = next((d for d in picked if d.run_id in here), picked[0])
        out.append({
            "label": (rep.run.batch.label if rep.run.batch_id
                      else f"실행 #{rep.run_id}"),
            "backend": (rep.run.params or {}).get("backend", "?"),
            "run_id": rep.run_id,
            "on": any(d.run_id in here for d in picked),
            # **검토 대상 묶음인가** — 이 값이 읽기 전용을 가른다 (P10 1단계).
            # 예전에는 `any(d.is_current …)` 였는데, 정규화 뒤에는 모든 묶음의
            # 검출이 `is_current` 라 **전부 편집 가능해 보인다.** 어느 묶음을
            # 검토하는지는 `RunBatch.for_review` 하나가 정한다.
            "current": bool(rep.run and rep.run.batch_id
                            and rep.run.batch.for_review),
            "n": Candidate.objects.filter(
                detection__in=picked, passed=True).count(),
        })
    return sorted(out, key=lambda g: g["label"])


# 화면에 적을 엔진 이름. 백엔드 이름(`Run.params["backend"]`)은 모델 판까지
# 들어 있어서(`sam2`) 고르는 칸에는 길다. **여기 없는 것은 대문자로 그대로
# 낸다** — 새 백엔드(`sam3` 등)가 이름 없이 사라지지 않게.
ENGINE_LABELS = {"yolo": "YOLO"}


def engines_from_batches(batches: list[dict]) -> list[dict]:
    """묶음 목록을 라디오 한 줄로 (DiaRUGA 051 의 `engines_from_batches`).

    DiaRUGA 는 **엔진 단위로** 접었다 — SAM 과 YOLO 를 비교하는 화면이라
    `yolo-1차`·`yolo-3차` 가 따로 서면 무엇을 누를지 알 수 없었다. ForGIA 는
    백엔드가 YOLO 하나라(P01) 그렇게 접으면 **라디오가 하나만 남아 회차를
    비교할 수 없다.** 여기서는 **묶음마다** 한 칸이다 — 비교하는 것이 엔진이
    아니라 씨앗 가중치와 재학습 가중치의 회차다. 열쇠·이름 모두 묶음 이름이고,
    `batch_label`·`n_batches` 는 화면이 같은 모양으로 읽게 그대로 둔다.

    **교정이 되는 것을 맨 앞에 둔다.** 검토 화면의 기본 자리이고, 라디오는
    왼쪽부터 읽힌다. 나머지는 실행 번호가 큰 것(나중 것)부터.
    """
    out = [{
        **b,
        "key": b["label"],
        "label": b["label"],
        "batch_label": b["label"],
        "n_batches": 1,
    } for b in batches]
    return sorted(out, key=lambda e: (not e["current"], -e["run_id"]))


# --- 시야 목록 · 시야 하나 ------------------------------------------------------
def _cover_of(vp: Viewpoint) -> str | None:
    """목록의 대표 그림. 합성본이 원칙이고, 없으면 가장 선명한 프레임."""
    st = getattr(vp, "stack", None)
    if st:
        return st.focused_path
    fr = vp.sharpest_frame or next(iter(vp.frames.all()), None)
    if fr and (Path(settings.DATA_ROOT) / fr.path).exists():
        return fr.path
    return None


