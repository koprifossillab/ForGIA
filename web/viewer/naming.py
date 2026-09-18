"""폴더 이름에서 층을 읽는 규칙 — **여기 하나뿐이다.**

DiaRUGA v0.29.0 `web/viewer/naming.py` 에서 왔다. 저쪽은 규칙이 둘(남극·육상)
인데 ForGIA 는 남극 코어만 다루므로(P01 5절) 남극 규칙 하나에 **분획 토막**을
더했다. 육상 규칙이 필요해지면 DiaRUGA 에서 그때 가져온다.

Django 도 cv2 도 안 부르는 순수 문자열 규칙이라 **뷰어·파이프라인·마이그레이션이
전부 같은 것을 본다.** 규칙이 갈라지면 같은 폴더가 두 자리에 다르게 앉는다.

## 층

    권역   남극                              Site.area
     └ 지역   RS23                            Site
        └ 지점   GC03                          Locality
           └ 시료   71cm                        Sample
              └ 관찰   (1) · (2)                Slide     = 픽킹 슬라이드 하나
                 └ 시야 = 격자 한 칸           Viewpoint.cell

## 폴더 규칙

    RS23-GC03 71cm                 <지역>-<지점> <깊이>cm
    RS23-GC03 71cm >125um          + 분획 (체 눈 크기, µm). 없으면 비운다
    RS23-GC03 71cm >125um (1)      + 관찰 접미사. 없으면 0

**분획 토막은 `>` 로 시작하고 `um` 으로 끝난다.** `125-250um` 처럼 구간을 받을
자리는 아직 없다 — 유공충 픽킹은 보통 아래 눈만 말한다. 필요해지면 `Sample`
에 상한 칸을 더하고 여기서 읽는다.
"""
import re

# 남극 규칙. `re.match` 라 뒤에 분획·관찰 접미사가 붙어도 앞쪽을 그대로 뽑는다.
SAMPLE_NAME = re.compile(
    r"^(?P<site>[A-Za-z0-9]+)-(?P<loc>[A-Za-z0-9]+)\s+(?P<depth>[\d.]+)\s*cm",
    re.IGNORECASE)

# 분획 토막. `>125um` · `> 63 um` · `>150µm` 를 다 받는다.
FRACTION = re.compile(r">\s*(?P<um>\d+(?:\.\d+)?)\s*(?:um|µm|μm)", re.IGNORECASE)

# 관찰 접미사 — 시료 하나를 처리 방법이나 회차를 달리해 여러 번 관찰한 것.
# **폴더에는 숫자만 받는다** (DiaRUGA 2026-08-05). 글자를 받으면 슬러그가
# 뭉개져 서로 다른 관찰이 같은 슬러그로 부딪히고, `update_or_create(slug=…)` 라
# 한쪽이 다른 쪽을 덮어쓴다. 뜻은 사람이 `obs_label` 에 적는다.
OBS_SUFFIX = re.compile(r"\s*\((\d+)\)\s*$")


def parse_obs_no(folder: str) -> int:
    """폴더명 끝의 `(1)`·`(2)` 를 읽는다. 없으면 `0`.

    **`0` 을 저장하고 화면에서만 감춘다.** 비워 두면 "아직 안 읽은 것" 과
    "접미사가 없던 것" 이 구별되지 않는다.
    """
    m = OBS_SUFFIX.search(folder or "")
    return int(m.group(1)) if m else 0


def base_name(folder: str) -> str:
    """관찰 접미사를 뗀 이름 — 같은 시료의 관찰들이 공유하는 것이다.

    **분획은 남긴다.** 같은 시료라도 분획이 다르면 다른 슬라이드고, 그것을
    같은 관찰의 회차로 묶으면 안 된다.
    """
    return OBS_SUFFIX.sub("", folder or "").strip()


def parse_fraction(folder: str) -> float | None:
    """`>125um` 을 읽는다. 없으면 `None` — 그때는 부르는 쪽이 아무것도 안 쓴다."""
    m = FRACTION.search(folder or "")
    return float(m.group("um")) if m else None


def parse_folder(folder: str) -> dict:
    """폴더 이름 하나를 층으로 가른다.

    돌려주는 것(모르는 자리는 `None`):

        site_code · loc_code · sample_code · depth_cm · fraction_um · obs_no

    규칙에 안 맞으면 `site_code`·`loc_code` 가 `None` 이다. **그때는 부르는
    쪽이 아무것도 쓰지 않는다** — 사람이 채운 것을 자동값이 지우면 안 된다
    (DiaRUGA 063).
    """
    folder = folder or ""
    base = base_name(folder)
    out = {"site_code": None, "loc_code": None, "sample_code": None,
           "depth_cm": None, "fraction_um": parse_fraction(base),
           "obs_no": parse_obs_no(folder)}

    m = SAMPLE_NAME.match(base)
    if m:
        d = m.group("depth")
        out.update(site_code=m.group("site").upper(),
                   loc_code=m.group("loc").upper(),
                   # 화면에 그대로 쓰는 시료 이름. `71.0cm` 이 아니라 `71cm` 다
                   sample_code=f"{float(d):g}cm" if d else None,
                   depth_cm=float(d) if d else None)
    return out
