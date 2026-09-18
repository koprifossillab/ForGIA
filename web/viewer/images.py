"""DiaRUGA v0.29.0 `web/viewer/images.py` 에서 왔다. `Image` 행을 만드는 **문 하나** (P06 4단계).

파이프라인의 네 자리(`group_focus_series` · `focus_stack` · `segment_forams` ·
`import_json`)와 시야 가르기(`regroup`)가 전부 여기를 지난다. 만드는 규칙이
여러 곳에 흩어지면 **한 곳만 옛 규칙으로 남는다** — 이 저장소가 분류 표(037~040)
에서 겪은 그것과 같은 종류다.

## 열쇠는 `path` 다

`DATA_ROOT` 기준 상대경로이고 파일 하나에 행 하나다. 그래서 **몇 번을 불러도
같다** — 다시 합성하든, 다시 검출하든, 시야를 갈랐다 붙이든.

## 링크는 늘 새로 맞춘다

`viewpoint`·`frame`·`stack` 은 시간이 지나면 바뀐다. 그룹핑 전 프레임은 시야가
없고, 시야 가르기는 프레임을 다른 시야로 옮기며, 다시 합성하면 `Stack` 행이
새로 생긴다. **그때마다 이 함수가 다시 불려 링크를 맞춘다** — 안 맞추면 테이블이
디스크와 조용히 어긋난다.
"""
from .models import Image


def ensure_image(path, kind, viewpoint=None, frame=None, stack=None,
                 width=None, height=None) -> Image:
    """`path` 의 이미지 행. 없으면 만들고, 있으면 링크를 맞춰 돌려준다.

    **비우지는 않는다.** `viewpoint=None` 으로 불렀다고 이미 붙어 있는 시야를
    떼지 않는다 — 부르는 쪽이 아는 것만 주면 되게 하려는 것이다. 떼는 일은
    `Viewpoint` 가 지워질 때 `SET_NULL` 이 알아서 한다.
    """
    img, made = Image.objects.get_or_create(
        path=path,
        defaults={"kind": kind, "viewpoint": viewpoint, "frame": frame,
                  "stack": stack, "width": width, "height": height})
    if made:
        return img

    fields = []
    # kind 는 파일의 성격이라 안 바뀐다. 바뀌었다면 같은 경로를 다른 것으로 쓴
    # 것이므로 맞춰 두고 넘어간다 — 여기서 멈출 일은 아니다.
    for name, val in (("kind", kind), ("viewpoint", viewpoint),
                      ("frame", frame), ("stack", stack),
                      ("width", width), ("height", height)):
        if val is None or getattr(img, name) == val:
            continue
        setattr(img, name, val)
        fields.append(name)
    if fields:
        img.save(update_fields=fields)
    return img


def ensure_stack_images(stack) -> Image:
    """합성본과 깊이맵을 함께. 합성본 행을 돌려준다.

    **깊이맵도 이미지다** — 볼 수 있는 파일이고 캐러셀이 띄운다. 다만 검출은
    안 붙는다(Z 좌표가 없는 상대값이라 검출 대상이 아니다). 그 사실이 이제
    `kind` 로 테이블에 적힌다.
    """
    img = ensure_image(stack.focused_path, "stack",
                       viewpoint=stack.viewpoint, stack=stack)
    if stack.depth_path:
        ensure_image(stack.depth_path, "depth",
                     viewpoint=stack.viewpoint, stack=stack)
    return img


def ensure_frame_image(frame) -> Image:
    """촬영본 한 장. 그룹핑 전에는 `viewpoint` 가 비어 있다."""
    return ensure_image(frame.path, "frame", viewpoint=frame.viewpoint,
                        frame=frame, width=frame.width, height=frame.height)
