"""DiaRUGA v0.29.0 `web/viewer/catalog.py` 에서 왔다 — 그대로. 예시의 육상(BP) 줄만 뺐다.

카탈로그 번호 — 유공충 개체 하나를 사람이 읽고 적을 수 있는 이름으로 짚는다.

**규칙은 이 파일 하나뿐이다** (`naming.py`·`judge.py` 와 같은 자리). Django 도
cv2 도 안 부르는 순수 문자열 규칙이라 뷰어·스크립트가 전부 같은 것을 본다.
규칙이 갈라지면 **같은 개체가 두 자리에서 다른 번호를 받는다** — 그리고 번호는
논문·표에 적히는 것이라 그때는 이미 되돌릴 수 없다.

## 모양

    <지역>-<지점>-<시료>[-<관찰>]-g<시야>[-f<프레임>]-<위치>-<묶음>

    RS23-GC03-071-g03-1204_856_132_97-S1        남극 · SAM2 · 합성본
    RS23-GC03-071-g03-f28-1198_861_130_99-Y3    남극 · YOLO 3차 · 프레임 28
    RS23-GC03-369-100-g07-410_222_88_140-S1     관찰 100 (0 이면 생략한다)
    RS23-GC03-071-g03-m1a2b3c4d-M               사람이 그린 개체

## 저장하지 않는다 — 늘 계산해서 낸다

칼럼에 넣으면 층을 고치거나 재검출을 돌릴 때 저장된 값과 실제가 어긋나고, 어긋난
것은 예외가 안 나고 그냥 틀린다. 파생이면 어긋날 수가 없다.

**그래서 번호가 안정한 것은 재료가 안정하기 때문이다.** `mask_key` 는 사람이
경계를 고쳐도 안 바뀌고(그래서 화면이 키를 늘 실어 보낸다), 지웠다 되살려도
그대로다. 층·시야·묶음도 사람이 옮기지 않는 한 안 바뀐다.

## 위치는 bbox 전체다 — 줄이면 부딪힌다

짧게 만들려고 잘라 본 후보가 전부 실측에서 걸렸다 (2026-08-10, 이미지 한 장 안에서
같은 값을 받은 개체 수):

    x,y            1204x856          SAM 90 · YOLO 270
    x,y,너비        1204x856x132      SAM 10 · YOLO  30
    중심 좌표        1270x904          SAM  0 · YOLO  30
    해시 4자        K7QX              SAM  2 · YOLO   0
    bbox 전체       1204_856_132_97   SAM  0 · YOLO   0   ← 이것
    해시 5자        K7QX4             SAM  0 · YOLO   0

이미지 한 장에 개체가 최대 106개라 좌표를 자르면 붙어 있는 것들이 같은 번호를
받는다. **해시 5자도 오늘 0 이지만 그것은 재 봐서 0 인 것이고**, bbox 전체는
`Candidate` 의 `(detection, mask_key)` 유일 제약이 **보장한다** — 4자 해시가 실제로
2건 부딪힌 것을 보면 자료가 늘 때 5자도 언젠가 부딪힌다. 길이보다 이쪽을 고른다.

## 꼬리가 어느 검출인지 말한다

엔진(그리고 회차)마다 카탈로그를 **완전히 별개로** 둔다 (사용자 방침 2026-08-10).
같은 개체이라도 SAM2 와 YOLO 가 낸 bbox 가 달라 번호가 다르고, 동정도 따로
적는다(`ObjectReview` 의 열쇠에 `batch` 가 있어 이미 그렇다).

**꼬리에 두는 이유**: 앞쪽 `관찰-시야-위치` 가 엔진끼리 같은 모양이라 나란히 놓고
비교할 때 읽힌다. 코드는 `RunBatch.code` 가 들고 있고 사람이 정한다 — 라벨에서
자동으로 뽑으면 `yolo-3차`·`yolo-4차` 가 같은 글자로 눕는다.

**사람이 그린 개체는 `M` 이다.** 어느 묶음에도 안 속하는 것이 그 개체의 성질이라
(`ObjectReview.batch` 가 NULL) 엔진 코드를 붙일 자리가 없다.

## 음수 좌표와 `-` 는 받지 않는다

`data.CAND_KEY` 는 음수를 받지만 여기서는 안 받는다 — `-` 가 토막을 가르는 글자라
번호를 되돌릴 수 없어진다. 지금 자료에 음수 키는 0건이고, 들어오면 **번호를 못
만든다고 말한다**(`ValueError`). 부르는 쪽이 그것을 화면에 적으므로, 번호가 조용히
틀리는 대신 없는 것이 보인다.

층 코드에 `-` 가 있으면 `part()` 가 지운다(`GC-03` → `GC03`). 화면이 서는 것보다는
낫지만 **두 지점이 같은 토막으로 누울 수 있다** — 그것은 `check_db.py` 가 센다.
"""
import re

# 토막을 가르는 글자. 토막 안에는 절대 들어가지 않는다 — `part()` 가 지운다.
SEP = "-"

# 토막에 남길 글자. 나머지는 지운다.
_KEEP = re.compile(r"[^A-Z0-9]+")

# 엔진이 낸 개체의 위치 키. `data.CAND_KEY` 와 같은 모양인데 **음수를 안 받는다**
# (머리말 마지막 절).
POS_ENGINE = re.compile(r"^\d+_\d+_\d+_\d+$")
# 사람이 그린 개체의 키 (`data.MANUAL_KEY` 와 같다).
POS_MANUAL = re.compile(r"^m[0-9a-f]{8}$")

# 사람이 그린 개체의 꼬리. 어느 묶음에도 안 속한다.
MANUAL_CODE = "M"

# 번호 전체를 되읽는 규칙. 관찰 토막은 있을 수도 없을 수도 있고 **`g<시야>` 가 그
# 경계를 말한다** — 그래서 앞쪽을 세 토막으로 못 박아 둘 수 있다.
#
# **대소문자를 안 가린다.** 사람이 검색창에 소문자로 칠 수 있고, 그때 안 찾아지는
# 것은 규칙이 틀린 것이 아니라 화면이 쓸모없어지는 것이다. 되돌린 값은 아래에서
# 제 모양으로 눕힌다 — 층·묶음은 대문자, 손그림 키는 소문자다.
_RE = re.compile(
    r"^(?P<head>[A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+(?:-(?P<obs>\d+))?)"
    r"-g(?P<vp>\d+)"
    r"(?:-f(?P<frame>\d+))?"
    r"-(?P<pos>\d+_\d+_\d+_\d+|m[0-9a-f]{8})"
    r"-(?P<batch>[A-Z0-9]+)$", re.IGNORECASE)


def part(text) -> str:
    """토막 하나. 대문자·숫자만 남긴다.

    비면 `ValueError` 다 — 빈 토막을 넣으면 `RS23--071-…` 처럼 되읽을 수 없는
    번호가 나온다. **소속이 없는 개체는 번호가 없는 것이 맞다**: 부르는 쪽이
    그것을 화면에 적어서, 사람이 층을 채워야 한다는 것을 알 수 있게 한다.
    """
    out = _KEEP.sub("", str(text or "").upper())
    if not out:
        raise ValueError("번호를 만들 수 없다 — 토막이 비어 있다")
    return out


def sample_part(code) -> str:
    """시료 토막. `71cm` → `071`, `0901` → `0901`.

    **`Sample.code` 만 본다** — `depth_cm` 을 대신 쓰면 규칙이 둘이 되고, 소수점
    깊이(`71.5`)에서 두 규칙이 다른 값을 낸다. 코드는 사람이 보는 이름이고 12개
    슬라이드 전부 채워져 있다.

    숫자면 세 자리로 채운다 — `71`·`231`·`816` 이 섞여 있으면 표에서 자리가
    안 맞고, 정렬이 문자열 순서라 `816` 이 `71` 앞에 온다.
    """
    raw = re.sub(r"\s*cm\s*$", "", str(code or "").strip(), flags=re.IGNORECASE)
    out = part(raw)
    return out.zfill(3) if out.isdigit() else out


def position_part(mask_key) -> str:
    """위치 토막. 엔진 개체는 bbox 전체, 사람이 그린 것은 그 키 그대로.

    **되돌릴 수 있어야 한다** — 번호에서 `mask_key` 를 얻지 못하면 번호로 개체를
    찾을 수 없고, 그러면 번호가 이름표이기만 하고 열쇠가 아니게 된다.
    """
    key = str(mask_key or "").strip()
    if POS_ENGINE.match(key) or POS_MANUAL.match(key):
        return key
    raise ValueError(f"번호를 만들 수 없다 — 위치 키가 규칙에 안 맞는다: {key!r}")


def catalog_no(*, site, locality, sample, viewpoint, mask_key,
               obs_no=0, frame_seq=None, batch_code="") -> str:
    """개체 하나의 카탈로그 번호. 재료가 모자라면 `ValueError` 다.

    `frame_seq` 는 **개체가 프레임에서 나왔을 때만** 준다 — 합성본이면 `None`.
    `f` 가 붙어 있느냐가 곧 "어느 이미지를 보고 잰 것이냐" 를 말한다.

    `batch_code` 가 비면 사람이 그린 개체로 본다 (`ObjectReview.batch` 가 NULL 인
    자리). 엔진 개체인데 코드가 없는 것은 부르는 쪽에서 걸러야 한다 — 여기서
    `M` 을 붙이면 엔진이 낸 것이 손그림으로 기록된다.
    """
    head = [part(site), part(locality), sample_part(sample)]
    if obs_no:
        head.append(part(str(obs_no)))

    try:
        vp = int(viewpoint)
    except (TypeError, ValueError):
        raise ValueError(f"번호를 만들 수 없다 — 시야 번호가 아니다: {viewpoint!r}")
    tail = [f"g{vp:02d}"]
    if frame_seq is not None:
        try:
            tail.append(f"f{int(frame_seq):02d}")
        except (TypeError, ValueError):
            raise ValueError(
                f"번호를 만들 수 없다 — 프레임 순번이 아니다: {frame_seq!r}")

    tail.append(position_part(mask_key))
    tail.append(part(batch_code) if batch_code else MANUAL_CODE)
    return SEP.join(head + tail)


def parse(text):
    """번호를 되읽는다. 규칙에 안 맞으면 `None`.

    돌려주는 것은 `catalog_no` 의 인자와 같은 이름이라 **되돌린 것으로 다시 번호를
    만들면 원래 문자열이 나온다** — 시험이 그것을 확인한다. 층 토막은 정규화된
    뒤라 원래 코드(`GC-03`)로는 안 돌아가므로 `site`·`locality`·`sample` 은
    **찾는 데 쓰고 표시에는 쓰지 않는다.**
    """
    m = _RE.match(str(text or "").strip())
    if not m:
        return None
    head = m.group("head").upper().split(SEP)
    pos = m.group("pos")
    return {
        "site": head[0],
        "locality": head[1],
        "sample": head[2],
        "obs_no": int(m.group("obs")) if m.group("obs") else 0,
        "viewpoint": int(m.group("vp")),
        "frame_seq": int(m.group("frame")) if m.group("frame") else None,
        # 손그림 키는 소문자 16진수다 (`data.MANUAL_KEY`) — 대문자로 두면 그 키로
        # 교정 행을 찾지 못한다.
        "mask_key": pos.lower() if pos[:1].lower() == "m" else pos,
        "batch_code": m.group("batch").upper(),
    }


def batch_code_seed(label: str) -> str:
    """묶음 코드를 사람이 아직 안 정했을 때 쓸 첫 제안. **번호에는 쓰지 않는다.**

    `sam2-전수` → `SAM2`, `yolo-3차` → `YOLO3`. 관리 화면이 빈 칸에 미리 채워 넣는
    용도이고, 실제 번호는 `RunBatch.code` 에 사람이 정한 것만 쓴다 — 자동값이
    번호로 새면 라벨을 고치는 순간 이미 적어 둔 번호가 바뀐다.
    """
    return _KEEP.sub("", str(label or "").upper())[:8]
