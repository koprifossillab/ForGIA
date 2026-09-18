"""DiaRUGA v0.29.0 `web/viewer/shape.py` 에서 왔다 — 그대로 (텍스처는 애초에 여기서 안 잰다).

폴리곤 하나에서 계측값을 낸다. **순수 파이썬이다 — 임포트가 `math` 뿐이다.**

사람이 그린 마스크(P09 3단계)의 지표를 여기서 잰다. 엔진이 낸 개체는
`segment_forams.py` 가 cv2 로 재서 `Candidate` 에 넣어 두지만, **사람이 그린
것은 잰 사람이 없다** — 그렇다고 화면을 빈칸으로 두면 고장으로 읽힌다.

## 왜 뷰어 쪽에서 재는가

뷰어 컨테이너에는 numpy·cv2 가 없고 **그 경계가 이미지 크기를 200 MB 대로
유지한다**(파이프라인은 6 GB). 폴리곤에서 나오는 지표는 픽셀이 필요 없으므로
그 경계를 넘을 이유가 없다.

넘어야만 나오는 것은 **안 잰다** — `texture`(픽셀의 주파수)와
`predicted_iou`·`stability_score`(SAM 이 자기 마스크에 매긴 점수)다. 뒤 둘은
애초에 사람이 그린 것에는 뜻이 없다. 안 잰 칸은 `None` 이고, 화면이 **왜
비었는지 적는다**(빈칸은 고장처럼 보인다).

## 판정에는 안 쓴다

`judge.py` 는 사람이 그린 개체에 안 돈다 — **사람이 곧 판정이다**(P09 5.8).
여기 값은 계측 표와 말풍선이 읽는 것이지 통과·탈락을 가르는 근거가 아니다.
그래서 `judge` 의 경계값을 여기 옮겨 적지 않는다.

## 좌표 규약

`poly` 는 `[x0, y0, x1, y1, …]` 평탄 배열이다 — `Candidate.polygon` 과 같은
모양이라 그대로 주고받는다. 화면 좌표계는 y 가 아래로 커지지만 면적·모멘트는
부호만 뒤집힐 뿐이라 절댓값으로 받는다.
"""
import math

# 점이 셋 미만이면 도형이 아니다. `mask_points`·`hasPoly` 와 같은 문턱이다.
MIN_POINTS = 3


def points(poly):
    """평탄 배열 → `[(x, y), …]`. 짧으면 빈 목록이다."""
    if not poly or len(poly) < MIN_POINTS * 2:
        return []
    return [(float(poly[i]), float(poly[i + 1]))
            for i in range(0, len(poly) - 1, 2)]


def area(pts) -> float:
    """신발끈 공식. **절댓값이다** — 점의 방향(시계/반시계)에 안 매인다."""
    if len(pts) < MIN_POINTS:
        return 0.0
    s = 0.0
    for i, (x0, y0) in enumerate(pts):
        x1, y1 = pts[(i + 1) % len(pts)]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0


def perimeter(pts) -> float:
    if len(pts) < 2:
        return 0.0
    return sum(math.dist(pts[i], pts[(i + 1) % len(pts)])
               for i in range(len(pts)))


def bbox(pts):
    """`[x, y, w, h]` 정수. **`w`·`h` 는 최소 1이다** — 0이면 `mask_key` 가
    납작해지고 나중에 되살릴 때 bbox 가 사라진다."""
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, y0 = int(math.floor(min(xs))), int(math.floor(min(ys)))
    x1, y1 = int(math.ceil(max(xs))), int(math.ceil(max(ys)))
    return [x0, y0, max(1, x1 - x0), max(1, y1 - y0)]


def convex_hull(pts):
    """단조 체인(Andrew). 반시계 방향으로 껍질 점들을 돌려준다."""
    p = sorted(set(pts))
    if len(p) < MIN_POINTS:
        return list(p)

    def half(seq):
        out = []
        for q in seq:
            while len(out) >= 2:
                (ax, ay), (bx, by) = out[-2], out[-1]
                if (bx - ax) * (q[1] - ay) - (by - ay) * (q[0] - ax) > 0:
                    break
                out.pop()
            out.append(q)
        return out

    return half(p)[:-1] + half(reversed(p))[:-1]


def _clip_convex(subject, clip):
    """Sutherland–Hodgman. **`clip` 은 볼록이어야 한다**(여기서는 늘 타원이다).

    `subject` 는 오목해도 된다 — 개체 윤곽은 자주 오목하다.
    """
    out = list(subject)
    n = len(clip)
    for i in range(n):
        if not out:
            return []
        ax, ay = clip[i]
        bx, by = clip[(i + 1) % n]

        def inside(p):
            return (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) >= 0

        def cut(p, q):
            d1 = (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)
            d2 = (bx - ax) * (q[1] - ay) - (by - ay) * (q[0] - ax)
            t = d1 / (d1 - d2) if d1 != d2 else 0.0
            return (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))

        cur, out = out, []
        for j, q in enumerate(cur):
            p = cur[j - 1]
            if inside(q):
                if not inside(p):
                    out.append(cut(p, q))
                out.append(q)
            elif inside(p):
                out.append(cut(p, q))
    return out


def _centroid_moments(pts):
    """면적 모멘트에서 `(cx, cy, m20, m02, m11)`. 없으면 `None`.

    `data.polygon_axis` 와 같은 계산이다 — **거기는 각도만 쓰고 여기는 축 길이도
    쓴다.** 규칙이 둘로 갈리면 화면의 회전 크롭과 계측 표가 어긋나므로, 값이
    달라지면 둘을 함께 고친다.
    """
    n = len(pts)
    if n < MIN_POINTS:
        return None
    a2 = sxx = syy = sxy = cx = cy = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a2 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
        sxx += cross * (x0 * x0 + x0 * x1 + x1 * x1)
        syy += cross * (y0 * y0 + y0 * y1 + y1 * y1)
        sxy += cross * (2 * x0 * y0 + x0 * y1 + x1 * y0 + 2 * x1 * y1)
    ar = a2 / 2.0
    if abs(ar) < 1e-9:
        return None
    cx /= 6.0 * ar
    cy /= 6.0 * ar
    return (cx, cy,
            sxx / (12.0 * ar) - cx * cx,
            syy / (12.0 * ar) - cy * cy,
            sxy / (24.0 * ar) - cx * cy)


def fit_ellipse(pts):
    """면적 모멘트가 같은 타원. `(cx, cy, a, b, deg)` — `a` 가 장반경이다.

    2차 모멘트가 같은 타원은 `a = 2√λ₁`, `b = 2√λ₂` 다(균일 밀도 타원의
    모멘트가 `a²/4`이므로). cv2 의 `fitEllipse` 와 같은 값은 아니지만 — 저쪽은
    윤곽점에 최소제곱을 맞춘다 — **채워진 영역을 대표하는 쪽이 이 자리에 맞다.**
    """
    m = _centroid_moments(pts)
    if m is None:
        return None
    cx, cy, m20, m02, m11 = m
    diff = math.hypot(m20 - m02, 2.0 * m11)
    l1 = (m20 + m02 + diff) / 2.0
    l2 = (m20 + m02 - diff) / 2.0
    if l1 <= 1e-9:
        return None
    a = 2.0 * math.sqrt(l1)
    b = 2.0 * math.sqrt(max(l2, 0.0))
    return cx, cy, a, b, math.degrees(0.5 * math.atan2(2.0 * m11, m20 - m02))


def _ellipse_points(cx, cy, a, b, deg, n=72):
    r = math.radians(deg)
    cos, sin = math.cos(r), math.sin(r)
    out = []
    for i in range(n):
        t = 2.0 * math.pi * i / n
        ex, ey = a * math.cos(t), b * math.sin(t)
        out.append((cx + ex * cos - ey * sin, cy + ex * sin + ey * cos))
    return out


def measure(poly, um_per_px=None) -> dict:
    """폴리곤 하나의 계측값. **못 재는 것은 `None` 이다.**

    돌려주는 열쇠는 `Candidate` 의 칸 이름과 같다 — 화면·계측 표가 엔진이 낸
    개체와 사람이 그린 개체를 **같은 코드로** 읽어야 하기 때문이다.
    """
    out = {k: None for k in
           ("area_px", "area_um2", "major_um", "minor_um", "long_side_um",
            "short_side_um", "aspect_ratio", "fill_ratio", "circularity",
            "convexity", "solidity", "elongation", "ellipse_iou",
            # 픽셀이 있어야 나오는 것과 SAM 고유의 것 — 여기서는 늘 None 이다
            "texture", "predicted_iou", "stability_score")}
    pts = points(poly)
    if len(pts) < MIN_POINTS:
        return out

    a = area(pts)
    if a <= 0:
        return out
    p = perimeter(pts)
    out["area_px"] = int(round(a))

    box = bbox(pts)
    if box:
        out["fill_ratio"] = round(a / (box[2] * box[3]), 4)
    if p > 0:
        out["circularity"] = round(min(1.0, 4.0 * math.pi * a / (p * p)), 4)

    hull = convex_hull(pts)
    ha, hp = area(hull), perimeter(hull)
    if ha > 0:
        out["solidity"] = round(min(1.0, a / ha), 4)
    if p > 0:
        out["convexity"] = round(min(1.0, hp / p), 4)

    ell = fit_ellipse(pts)
    if ell:
        cx, cy, ea, eb, deg = ell
        if eb > 1e-9:
            out["elongation"] = round(ea / eb, 4)
            out["aspect_ratio"] = out["elongation"]
        # 타원과의 IoU — 개체이 얼마나 타원에 가까운가. `judge` 가 엔진 개체에
        # 쓰는 지표라 사람이 그린 것에도 같은 자리에 있어야 읽힌다.
        ep = _ellipse_points(cx, cy, ea, eb, deg)
        inter = area(_clip_convex(pts, ep))
        union = a + area(ep) - inter
        if union > 0:
            out["ellipse_iou"] = round(inter / union, 4)
        if um_per_px:
            out["major_um"] = round(2.0 * ea * um_per_px, 3)
            out["minor_um"] = round(2.0 * eb * um_per_px, 3)
            # 회전한 외접 상자의 변 — `data.rotated_extent` 와 같은 뜻이다
            w, h = _rotated_wh(pts, -deg)
            lo, hi = sorted((w, h))
            out["long_side_um"] = round(hi * um_per_px, 3)
            out["short_side_um"] = round(lo * um_per_px, 3)
    if um_per_px:
        out["area_um2"] = round(a * um_per_px * um_per_px, 3)
    return out


def _rotated_wh(pts, deg):
    r = math.radians(deg)
    cos, sin = math.cos(r), math.sin(r)
    rx = [x * cos - y * sin for x, y in pts]
    ry = [x * sin + y * cos for x, y in pts]
    return max(rx) - min(rx), max(ry) - min(ry)
