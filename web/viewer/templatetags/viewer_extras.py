"""템플릿 태그. DiaRUGA v0.29.0 `templatetags/viewer_extras.py` 에서 1단계 것만 —
분류 관련 필터(`cls_label` 등)는 2단계에서 온다.
"""
import json
from urllib.parse import urlencode

from django import template
from django.urls import reverse
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def json_dumps(value):
    """
    <script type="application/json"> 안에 넣을 JSON.

    </script> 로 태그를 조기 종료시키는 것을 막아야 하므로 < 를 이스케이프한다.
    """
    text = json.dumps(value, ensure_ascii=False)
    return mark_safe(text.replace("<", "\\u003c").replace("\u2028", "\\u2028"))


@register.simple_tag
def thumb(rel, width=400):
    """축소본 URL. 폴더명에 공백이 있어 경로는 쿼리로 넘긴다.

    v= 는 원본 mtime 이다 — 그림이 바뀌면 주소도 바뀌므로 브라우저 캐시가
    옛 그림을 붙잡고 있을 수 없다.
    """
    if not rel:
        return ""
    from viewer import data
    return reverse("image") + "?" + urlencode({"p": str(rel), "w": int(width),
                                               "v": data.stamp(rel)})


@register.simple_tag
def rawimg(rel):
    """**원본** 이미지 URL — 축소하지 않는다 (DiaRUGA 129).

    `thumb` 에 0 을 주면 안 된다. `image` 뷰가 `w` 를 **있으면 축소본**으로
    읽고 `max(32, …)` 로 바닥을 깔아서, `w=0` 은 원본이 아니라 **32px 짜리**가
    된다. 조용히 다른 그림이 나오는 자리라 태그를 따로 둔다.
    """
    if not rel:
        return ""
    from viewer import data
    return reverse("image") + "?" + urlencode({"p": str(rel),
                                               "v": data.stamp(rel)})


@register.simple_tag
def cropurl(rel, bbox, width=200, rot=None, out=None):
    """검출 개체 하나만 잘라 낸 썸네일 URL.

    rot/out 이 있으면 개체를 세워서(장축을 세로로) 잘라 낸다.
    """
    if not rel or not bbox:
        return ""
    from viewer import data
    q = {"p": str(rel),
         "b": ",".join(str(int(round(float(v)))) for v in bbox),
         "w": int(width), "v": data.stamp(rel)}
    if rot is not None and out:
        q["rot"] = rot
        q["out"] = out
    return reverse("crop") + "?" + urlencode(q)


@register.simple_tag
def full(rel):
    """원본 URL."""
    if not rel:
        return ""
    from viewer import data
    return reverse("image") + "?" + urlencode({"p": str(rel), "v": data.stamp(rel)})


@register.filter
def mask_points(c):
    """개체 dict 의 폴리곤 → SVG `points`. 규칙은 `data.mask_points` 하나다."""
    from viewer import data
    return data.mask_points(c)


# --- 분류 (DiaRUGA 그대로 · 3단계) -------------------------------------------------
@register.filter
def cls_label(value):
    """분류 키 -> 사람이 읽는 이름. 정의는 data.CLASS_LABELS 한곳에 있다."""
    from viewer import data
    return data.CLASS_LABELS.get(value, "")


@register.filter
def cls_short(value):
    """분류 키 -> 약칭. 자리가 좁은 곳에서 쓴다 (없으면 전체 이름)."""
    from viewer import data
    return data.CLASS_SHORT.get(value, "")


@register.filter
def cls_badge(value):
    from viewer import data
    return data.CLASS_BADGE.get(value, "")


@register.simple_tag
def class_list():
    """분류 목록. {% class_list as classes %} 로 받아 쓴다."""
    from viewer import data
    return data.class_list()


@register.simple_tag
def hotkey_groups():
    """단축키 안내용. {% hotkey_groups as hotkeys %} 로 받아 쓴다."""
    from viewer import data
    return data.hotkey_groups()


@register.simple_tag
def class_json():
    """클라이언트가 메뉴를 만들 때 쓰는 분류 정의."""
    from viewer import data
    text = json.dumps(data.class_list(), ensure_ascii=False)
    return mark_safe(text.replace("<", "\\u003c"))
