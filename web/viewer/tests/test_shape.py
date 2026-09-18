"""DiaRUGA v0.29.0 `tests/test_shape.py` 에서 왔다.

`shape.py` — 폴리곤에서 계측값을 낸다 (DiaRUGA P09 3단계).

**아는 값으로 잰다.** 정사각형·원·2:1 타원은 답이 손으로 나오므로, 시험이
"지금 나오는 값" 을 베껴 적는 것이 아니라 **틀렸는지**를 말한다. 베껴 적으면
계산을 잘못 고쳐도 시험만 함께 틀어져 통과한다.

이 지표는 **판정에 안 쓰인다**(`judge` 는 사람이 그린 개체에 안 돈다). 계측 표와
말풍선이 읽는 값이라, 틀리면 예외가 아니라 **그럴듯한 숫자**가 나온다.
"""
import math

from django.test import SimpleTestCase

from .. import shape


def square(s=10.0, x=0.0, y=0.0):
    return [x, y, x + s, y, x + s, y + s, x, y + s]


def ellipse(a, b, n=240, cx=0.0, cy=0.0):
    out = []
    for i in range(n):
        t = 2 * math.pi * i / n
        out += [cx + a * math.cos(t), cy + b * math.sin(t)]
    return out


class ShapeBasicsTest(SimpleTestCase):
    """Django 도 DB 도 안 쓴다 — `SimpleTestCase` 로 족하다."""

    def test_점이_셋_미만이면_도형이_아니다(self):
        for poly in ([], [1, 2], [1, 2, 3, 4], None):
            with self.subTest(poly=poly):
                self.assertEqual(shape.points(poly), [])
                self.assertIsNone(shape.measure(poly)["area_px"])

    def test_정사각형의_면적과_둘레(self):
        pts = shape.points(square(10))
        self.assertAlmostEqual(shape.area(pts), 100.0)
        self.assertAlmostEqual(shape.perimeter(pts), 40.0)

    def test_방향이_반대여도_면적이_같다(self):
        """신발끈은 방향에 따라 부호가 뒤집힌다 — **절댓값이어야 한다.**

        화면 좌표계는 y 가 아래로 커져서 같은 도형이 반대로 감긴다.
        """
        cw = shape.points(square(10))
        ccw = list(reversed(cw))
        self.assertAlmostEqual(shape.area(cw), shape.area(ccw))

    def test_bbox_는_최소_1이다(self):
        """0이면 `mask_key` 가 납작해져 나중에 되살릴 때 bbox 가 사라진다."""
        flat = [0, 0, 10, 0, 20, 0]           # 한 줄로 늘어선 점
        self.assertEqual(shape.bbox(shape.points(flat)), [0, 0, 20, 1])

    def test_볼록껍질은_안쪽_점을_버린다(self):
        pts = shape.points(square(10)) + [(5.0, 5.0)]     # 한가운데 점
        hull = shape.convex_hull(pts)
        self.assertEqual(len(hull), 4)
        self.assertNotIn((5.0, 5.0), hull)


class ShapeMeasureTest(SimpleTestCase):

    def test_정사각형(self):
        m = shape.measure(square(10))
        self.assertEqual(m["area_px"], 100)
        # 4πA/P² = 4π·100/1600 = π/4
        self.assertAlmostEqual(m["circularity"], round(math.pi / 4, 4), places=3)
        self.assertAlmostEqual(m["solidity"], 1.0, places=3)
        self.assertAlmostEqual(m["convexity"], 1.0, places=3)
        self.assertAlmostEqual(m["fill_ratio"], 1.0, places=3)
        self.assertAlmostEqual(m["elongation"], 1.0, places=2)

    def test_원(self):
        m = shape.measure(ellipse(50, 50))
        self.assertAlmostEqual(m["area_px"], round(math.pi * 2500), delta=30)
        self.assertAlmostEqual(m["circularity"], 1.0, places=2)
        self.assertAlmostEqual(m["solidity"], 1.0, places=2)
        self.assertAlmostEqual(m["elongation"], 1.0, places=2)
        # 원은 자기 타원과 거의 같다
        self.assertGreater(m["ellipse_iou"], 0.99)

    def test_길쭉한_타원(self):
        m = shape.measure(ellipse(60, 20))
        self.assertAlmostEqual(m["elongation"], 3.0, places=1)
        self.assertGreater(m["ellipse_iou"], 0.99)
        # 원형도는 1보다 한참 낮아야 한다 — 봉상과 원형을 가르는 감각이다
        self.assertLess(m["circularity"], 0.9)

    def test_오목한_도형은_solidity_가_낮다(self):
        """개체 윤곽은 자주 오목하다 — 볼록껍질 대비 비율이 그것을 잡는다."""
        # ㄷ 자
        poly = [0, 0, 30, 0, 30, 10, 10, 10, 10, 20, 30, 20, 30, 30, 0, 30]
        m = shape.measure(poly)
        self.assertLess(m["solidity"], 0.8)
        self.assertLess(m["convexity"], 1.0)

    def test_µm_는_배율을_받아야_나온다(self):
        m0 = shape.measure(square(10))
        self.assertIsNone(m0["area_um2"], "배율 없이 µm 값이 나왔다")
        self.assertIsNone(m0["major_um"])

        m = shape.measure(square(10), um_per_px=0.1)
        self.assertAlmostEqual(m["area_um2"], 100 * 0.01, places=3)
        self.assertAlmostEqual(m["long_side_um"], 1.0, places=2)
        self.assertAlmostEqual(m["short_side_um"], 1.0, places=2)

    def test_긴_변과_짧은_변은_회전_상자에서_온다(self):
        """축에 안 맞게 놓인 봉상 개체 — **축정렬 상자로 재면 부푼다.**

        봉상 개체의 축정렬 상자는 중앙값 52%가 배경이라는 실측이 있다(DiaRUGA P04).
        """
        m = shape.measure(ellipse(60, 10), um_per_px=1.0)
        long_ax, short_ax = m["long_side_um"], m["short_side_um"]
        # 기울여도 같은 값이 나와야 한다
        r = math.radians(37)
        tilted = []
        pts = shape.points(ellipse(60, 10))
        for x, y in pts:
            tilted += [x * math.cos(r) - y * math.sin(r),
                       x * math.sin(r) + y * math.cos(r)]
        t = shape.measure(tilted, um_per_px=1.0)
        self.assertAlmostEqual(t["long_side_um"], long_ax, delta=0.6)
        self.assertAlmostEqual(t["short_side_um"], short_ax, delta=0.6)

    def test_못_재는_것은_None_이다(self):
        """**픽셀이 있어야 나오는 것과 SAM 고유의 것.** 0 으로 채우면 잰 값처럼
        보이고, 빼 버리면 화면이 칸을 못 그린다."""
        m = shape.measure(square(10), um_per_px=0.1)
        for k in ("texture", "predicted_iou", "stability_score"):
            with self.subTest(k=k):
                self.assertIsNone(m[k])

    def test_칸_이름이_Candidate_와_같다(self):
        """화면·계측 표가 엔진이 낸 개체와 사람이 그린 개체를 **같은 코드로**
        읽는다 — 이름이 갈라지면 한쪽만 빈칸이 된다."""
        from .. import data
        m = shape.measure(square(10), um_per_px=0.1)
        for f in data.NUM_FIELDS:
            with self.subTest(f=f):
                self.assertIn(f, m)
