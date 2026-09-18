#!/usr/bin/env python3
"""사진 한 장의 µm/픽셀을 정한다 — **여기 하나뿐이다.** DiaRUGA `zen_meta.py` 의 자리.

µm/픽셀에는 계측값(면적·장단축)과 크기 관문이 걸려 있다. 상수로 박아 두면
**촬영 조건이 바뀌었을 때 예외도 경고도 없이 결과 전체가 배율만큼 어긋난다**
(DiaRUGA 015 — XML 의 대물렌즈가 100x 로 적혀 µm/px 가 2.5배 틀렸다). 그래서
값은 사진 곁에서 읽고, 어디서 읽었는지(`source`)를 함께 남긴다.

## 어디를 차례로 보나 (P01 5절)

    ⓪ 폴더의 scale.toml 에 override = true      → "toml"    사람이 못 박은 것이 이긴다
    ⑴ Leica LAS X 메타 (<이름>_Properties.xml)  → "leica"   장비가 오면 채운다 — 아래
    ⑵ EXIF ImageDescription 의 JSON               → "exif"    합성 자료가 이 자리다
    ⑶ 폴더의 scale.toml                            → "toml"    사람이 적은 배율
    ⑷ 파생 이미지 옆의 <이름>_scale.json          → "sidecar" 합성본 (우리가 만든다)
    ⑸ 기본값                                       → "default" 경고를 내고 쓴다

**⑴은 실제 파일을 보기 전에는 못 짠다.** 장비가 아직 없다(2026-09-18). LAS X 가
내보내는 XML 의 모양을 짐작으로 적어 두면 첫 실사진에서 조용히 틀린 값을 낼
수 있어, 지금은 **파일이 있으면 "아직 못 읽는다" 고 경고만 내고 다음으로
넘어간다.** 실사진 한 벌이 오면 `read_leica` 를 채우고 `tests/test_scale.py` 에
그 파일을 견본으로 넣는다.

## scale.toml

슬라이드 폴더(사진이 있는 디렉토리)에 사람이 둔다:

    um_per_pixel = 1.5625        # 필수
    override = false             # true 면 메타보다 이긴다 (DiaRUGA 의 um_per_pixel_override 자리)
    note = "실체 40x · 스테이지 마이크로미터로 2026-10-01 교정"

**`override` 가 있는 이유**: 장비 메타가 있어도 소프트웨어에 선택된 렌즈가
실물과 어긋날 수 있다(DiaRUGA 015). 그때 사람이 잰 값이 이겨야 하고, 그것을
코드가 아니라 자료 곁에 적는다.

tomllib 은 3.11+ 표준이다. 이 파일은 Django·cv2 를 안 부른다 — 뷰어에서도
파이프라인에서도 임포트할 수 있어야 한다.
"""
import datetime as dt
import json
import re
import sys
import tomllib
from pathlib import Path

# 메타도 사이드카도 없을 때만 쓰는 최후의 기본값. NAS 합성 자료의 실체 40x
# 설정값(1600 µm / 1024 px)이다 — 실물 교정값이 아니다. 실사진이 오면 바꾼다.
DEFAULT_UM_PER_PIXEL = 1.5625

# 실체·광학현미경에서 나올 수 있는 범위. 이 밖의 값이면 파싱을 잘못한 것으로 본다.
PLAUSIBLE_UM_PER_PIXEL = (0.01, 100.0)

TOML_NAME = "scale.toml"

_warned = set()


def _warn(key, msg):
    """같은 원인의 경고는 한 번만 — 배치에서 수백 줄이 쏟아지지 않게."""
    if key in _warned:
        return
    _warned.add(key)
    print(f"[scale] {msg}", file=sys.stderr)


def _to_float(s):
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return None


def _plausible(um, where):
    lo, hi = PLAUSIBLE_UM_PER_PIXEL
    if um is None:
        return None
    if not (lo <= um <= hi):
        _warn(f"implausible:{where}",
              f"{where}: {um:g} µm/px 는 현미경 범위 밖이라 쓰지 않는다")
        return None
    return um


# --- ⓪⑶ scale.toml -----------------------------------------------------------

def toml_path(img) -> Path:
    return Path(img).parent / TOML_NAME


def read_toml(img):
    """폴더의 `scale.toml`. 없으면 `None`, 있으면 `{"um_per_pixel", "override", "note"}`."""
    p = toml_path(img)
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except OSError:
        return None
    except tomllib.TOMLDecodeError as e:
        _warn(f"badtoml:{p}", f"{p}: 읽지 못했다 — {e}")
        return None
    um = _plausible(_to_float(data.get("um_per_pixel")), str(p))
    if um is None:
        _warn(f"notoml:{p}", f"{p}: um_per_pixel 이 없거나 틀렸다")
        return None
    return {"um_per_pixel": um, "override": bool(data.get("override", False)),
            "note": str(data.get("note", ""))}


# --- ⑴ Leica LAS X -------------------------------------------------------------

def leica_sidecar(img) -> Path:
    """LAS X 가 내보낼 때 곁에 두는 메타. **이름은 짐작이다** — 실제 파일을 보고 고친다."""
    img = Path(img)
    return img.with_name(img.stem + "_Properties.xml")


def read_leica(img):
    """**아직 못 읽는다.** 파일이 있으면 그 사실만 알린다 (머리말).

    채울 때 볼 것: LIF 계열 XML 의 `<DimensionDescription DimID="1"
    NumberOfElements="…" Length="…" Unit="m">` — 화소 크기가 `Length /
    NumberOfElements` 인지 `/(N-1)` 인지가 판마다 달라 **실측으로 정한다.**
    """
    p = leica_sidecar(img)
    if p.exists():
        _warn(f"leica:{p.parent}",
              f"{p.name}: Leica 메타가 있지만 아직 읽는 코드가 없다 — "
              f"scale.toml 이나 EXIF 로 넘어간다 (pipeline/scale.py 머리말)")
    return None


# --- ⑵ EXIF ImageDescription ---------------------------------------------------

def _exif(img):
    """PIL 로 EXIF 를 연다. PIL 이 없거나 파일을 못 열면 빈 dict."""
    try:
        from PIL import Image as PILImage
        with PILImage.open(img) as im:
            return dict(im.getexif())
    except Exception:                       # noqa: BLE001 — 메타는 없어도 된다
        return {}


def read_exif(img):
    """`ImageDescription`(0x010e) 이 JSON 이면 `um_per_pixel` 을 찾는다.

    맨 위나 `microscope` 아래를 본다 — NAS 합성 자료(P01 5.1)가 뒤엣것이다.
    """
    desc = _exif(img).get(0x010E)
    if not desc:
        return None
    try:
        data = json.loads(desc)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    v = data.get("um_per_pixel")
    if v is None and isinstance(data.get("microscope"), dict):
        v = data["microscope"].get("um_per_pixel")
    return _plausible(_to_float(v), Path(img).name)


def read_timestamp(img):
    """촬영 시각. EXIF `DateTimeOriginal`(0x9003) → `DateTime`(0x0132). 없으면 None.

    그룹핑이 보조 신호로만 쓴다 — 없다고 멈추지 않는다.
    """
    ex = _exif(img)
    raw = None
    try:
        from PIL import Image as PILImage
        with PILImage.open(img) as im:
            raw = im.getexif().get_ifd(0x8769).get(0x9003)
    except Exception:                       # noqa: BLE001
        raw = None
    raw = raw or ex.get(0x0132)
    if not raw:
        return None
    m = re.match(r"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})", str(raw))
    if not m:
        return None
    try:
        return dt.datetime(*map(int, m.groups()), tzinfo=dt.timezone.utc)
    except ValueError:
        return None


# --- ⑷ 사이드카 ----------------------------------------------------------------

def scale_sidecar(img) -> Path:
    """`..._focused.jpg` → `..._focused.jpg_scale.json` (우리가 만든다). DiaRUGA 와 같은 이름."""
    img = Path(img)
    return img.with_name(img.name + "_scale.json")


def read_sidecar(img):
    side = scale_sidecar(img)
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
    except OSError:
        return None
    except ValueError:
        data = {}
    v = _plausible(_to_float(data.get("um_per_pixel")), side.name)
    if v is None:
        _warn(f"badsidecar:{side}", f"{side.name}: um_per_pixel 을 읽지 못했다")
    return v


def write_scale_sidecar(img, um_per_pixel, **extra):
    """파생 이미지 옆에 픽셀 크기를 남긴다. 원본 메타가 없는 합성본용."""
    path = scale_sidecar(img)
    payload = {"um_per_pixel": um_per_pixel, **extra}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path


# --- 문 하나 ------------------------------------------------------------------

def scaling_for(img, default=DEFAULT_UM_PER_PIXEL):
    """이미지 하나의 µm/픽셀. 항상 dict — `um_per_pixel` · `source` · `path`.

    `source` 를 결과와 DB(`Frame.um_per_pixel_source`)에 같이 남겨 두면 나중에
    계측값을 의심할 때 어디서 온 값인지 바로 알 수 있다.
    """
    img = Path(img)

    toml = read_toml(img)
    if toml and toml["override"]:
        return {"um_per_pixel": toml["um_per_pixel"], "source": "toml",
                "path": str(toml_path(img))}

    um = read_leica(img)
    if um is not None:
        return {"um_per_pixel": um, "source": "leica", "path": str(leica_sidecar(img))}

    um = read_exif(img)
    if um is not None:
        return {"um_per_pixel": um, "source": "exif", "path": str(img)}

    if toml:
        return {"um_per_pixel": toml["um_per_pixel"], "source": "toml",
                "path": str(toml_path(img))}

    um = read_sidecar(img)
    if um is not None:
        return {"um_per_pixel": um, "source": "sidecar", "path": str(scale_sidecar(img))}

    _warn(f"default:{img.parent}",
          f"{img.name}: 스케일 근거가 없어 기본값 {default:g} µm/px 를 쓴다 — "
          f"촬영 조건이 다르면 계측값이 통째로 틀린다. 폴더에 {TOML_NAME} 을 둘 것")
    return {"um_per_pixel": float(default), "source": "default", "path": None}


class ScaleLog:
    """한 번의 실행에서 픽셀 크기가 섞이면 알린다 (DiaRUGA 와 같다).

    시료마다 배율이 다른 폴더를 한꺼번에 돌리면 계측값을 서로 비교할 수 없게
    되는데, 숫자만 봐서는 알아채기 어렵다. 값이 바뀌는 순간에 알린다.
    """

    def __init__(self):
        self.first = None       # (이름, µm/px)
        self.seen = set()

    def add(self, name, um_per_pixel):
        if self.first is None:
            self.first = (name, um_per_pixel)
            return
        ref_name, ref = self.first
        if abs(um_per_pixel - ref) <= ref * 1e-3:
            return
        key = round(um_per_pixel, 9)
        if key in self.seen:
            return
        self.seen.add(key)
        print(f"[scale] 경고: {name} 은 {um_per_pixel:.6f} µm/px 로 "
              f"{ref_name}({ref:.6f})와 다르다 — 촬영 조건이 섞였다. "
              "계측값을 한데 모아 비교하면 안 된다.", file=sys.stderr)
