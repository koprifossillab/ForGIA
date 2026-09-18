"""로스해 확대 지도 (199).

시료가 로스해에 몰려 있는데 전체 지도에서는 한 점으로 겹친다 — 지점 여섯이
300 km 안, 그 가운데 셋은 50 km 안이다. 확대 틀은 **지역이 아니라 지점**을
찍고, 이름표 자리를 서버가 골라 서로 안 덮게 한다.

여기서 잡는 것 넷.

1. **지역 좌표가 비어도 지점 좌표가 있으면 대략값으로 물러나지 않는다.** KPDC
   반입(197)이 지점에 좌표를 채우는데 지역 칸은 비어 있어, 로스해 네 지역이
   전부 "대략 위치" 한 점에 겹쳐 찍혔다
2. **틀 안팎을 가른다** — 잘려서 안 보이는 것과 없는 것은 달라야 한다
3. **가까운 지점의 이름표가 서로 안 겹친다** (RS21 의 코어 셋)
4. **경로가 유효하다** — 속성에 값이 든 것과 그 값이 유효한 것은 다르다
   (devlog 021 · 공백 하나로 지도가 백지가 됐다)
"""
import re

from django.urls import reverse

from . import factories as fx
from .base import ForGIATestCase
from .. import data, ross
from ..models import Locality, Site

# 실제 RS21·RS23 코어 좌표 (KPDC). 셋이 50 km 안에 있다
CORES = {
    ("RS21", "GC02"): (-77.399902, 176.299317),
    ("RS21", "GC03B"): (-77.973532, -173.558052),
    ("RS21", "GC04"): (-78.02658, -171.260235),
    ("RS21", "GC05"): (-78.289472, -170.45669),
    ("RS23", "GC03"): (-74.340488, 174.06828),
}


def _path_ok(d: str) -> bool:
    """SVG path 가 통째로 읽히는가 — 숫자 사이에 공백이 없고 전부 float 이다."""
    if " " in d or not d.startswith("M"):
        return False
    for sub in d.split("M")[1:]:
        for pair in sub.rstrip("Z").split("L"):
            x, y = pair.split(",")
            float(x), float(y)
    return True


class MapPointsFallbackTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        for i, ((sc, lc), (lat, lon)) in enumerate(CORES.items()):
            fx.make_world(slug=f"s{i}", site_code=sc, loc_code=lc,
                          sample_code=f"{10 * i + 1}cm", depth_cm=10 * i + 1,
                          with_files=False)
            Locality.objects.filter(site__code=sc, code=lc).update(lat=lat, lon=lon)
        # 지역 좌표는 비운 채 둔다 — 197 뒤의 운영 DB 가 이 모양이다
        assert Site.objects.get(code="RS21").lat is None

    def test_지점_좌표의_평균으로_찍고_대략값이_아니다(self):
        pts = {p["code"]: p for p in data.map_points("ant")}
        rs21 = pts["RS21"]
        self.assertTrue(rs21["exact"], "지점 좌표가 있는데 대략값으로 물러났다")
        self.assertEqual(rs21["approx_note"], "")
        lats = [la for (sc, _), (la, _) in CORES.items() if sc == "RS21"]
        lons = [lo for (sc, _), (_, lo) in CORES.items() if sc == "RS21"]
        x, y = data._polar_xy(sum(lats) / 4, sum(lons) / 4)
        self.assertAlmostEqual(rs21["x"], x, delta=0.2)
        self.assertAlmostEqual(rs21["y"], y, delta=0.2)
        # 두 지역이 한 점에 겹치지 않는다 — 197 앞에는 둘 다 (-75, 175) 였다
        self.assertGreater(abs(pts["RS23"]["x"] - rs21["x"]), 100)

    def test_지점마다_제_좌표를_싣는다(self):
        pts = {p["code"]: p for p in data.map_points("ant")}
        cores = {c["code"]: c for c in pts["RS21"]["cores"]}
        x, y = data._polar_xy(*CORES[("RS21", "GC05")])
        self.assertAlmostEqual(cores["GC05"]["x"], x, delta=0.2)
        self.assertAlmostEqual(cores["GC05"]["y"], y, delta=0.2)
        self.assertTrue(cores["GC05"]["exact"])

    def test_지점_좌표가_없으면_지역의_자리를_물려받는다(self):
        Locality.objects.filter(site__code="RS23").update(lat=None, lon=None)
        pts = {p["code"]: p for p in data.map_points("ant")}
        rs23 = pts["RS23"]
        # 지역 좌표도 없으니 대략값이고, 지점도 그것을 따른다
        self.assertFalse(rs23["exact"])
        core = rs23["cores"][0]
        self.assertEqual((core["x"], core["y"]), (rs23["x"], rs23["y"]))
        self.assertFalse(core["exact"])


class RossContextTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        for i, ((sc, lc), (lat, lon)) in enumerate(CORES.items()):
            fx.make_world(slug=f"s{i}", site_code=sc, loc_code=lc,
                          sample_code=f"{10 * i + 1}cm", depth_cm=10 * i + 1,
                          with_files=False)
            Locality.objects.filter(site__code=sc, code=lc).update(lat=lat, lon=lon)
        # 틀 밖 — 남극반도
        fx.make_world(slug="wap", site_code="WAP13", loc_code="GC47",
                      with_files=False)
        Site.objects.filter(code="WAP13").update(lat=-65.3676, lon=-64.455)

    def test_틀_안은_찍고_밖은_이름으로_낸다(self):
        ctx = ross.context(data.map_points("ant"))
        labels = {p["label"] for p in ctx["points"]}
        self.assertEqual(labels, {f"{s}-{c}" for s, c in CORES})
        self.assertEqual(ctx["outside"], ["WAP13-GC47"])

    def test_마커는_돌린_좌표이고_틀_안에_든다(self):
        ctx = ross.context(data.map_points("ant"))
        vx, vy, vw, vh = ross.VIEWBOX
        x, y = ross.to_xy(*data._polar_xy(*CORES[("RS21", "GC02")]))
        p = next(p for p in ctx["points"] if p["label"] == "RS21-GC02")
        self.assertAlmostEqual(p["x"], x, delta=0.2)
        self.assertAlmostEqual(p["y"], y, delta=0.2)
        for p in ctx["points"]:
            self.assertTrue(vx <= p["x"] <= vx + vw and vy <= p["y"] <= vy + vh, p)

    def test_가까운_지점의_이름표가_서로_안_겹친다(self):
        """RS21 의 GC03B·GC04·GC05 — 50 km 안에 셋. 전부 위에 얹으면 덮는다.

        상자를 `place_labels` 와 같은 어림으로 다시 그려 둘씩 대 본다. 어림이
        같은 것을 두 번 적는 셈이지만, 여기서 보는 것은 **고른 자리가 서로
        다르다**는 것이다 — 전부 (0, −d, middle) 이면 이 시험이 잡는다.
        """
        ctx = ross.context(data.map_points("ant"))
        font = 150 * ross.MARK
        char = font * 0.6
        boxes = []
        for p in ctx["points"]:
            w = char * len(p["label"])
            x, y = p["x"] + p["lx"], p["y"] + p["ly"]
            x0 = {"middle": x - w / 2, "start": x, "end": x - w}[p["anchor"]]
            boxes.append((p["label"], x0, y - font, x0 + w, y))
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                overlap = not (a[3] < b[1] or a[1] > b[3]
                               or a[4] < b[2] or a[2] > b[4])
                self.assertFalse(overlap, f"{a[0]} 와 {b[0]} 의 이름표가 겹친다")
        anchors = {p["anchor"] for p in ctx["points"]}
        self.assertGreater(len(anchors), 1, "전부 같은 자리에 얹었다")

    def test_구운_경로가_읽힌다(self):
        for name in ("LAND", "LAND_LINE", "SHELF", "SHELF_LINE"):
            self.assertTrue(_path_ok(getattr(ross, name)), name)
        # 틀 밖으로 크게 벗어난 정점이 없다 — 자르기가 안 됐으면 대륙 전체가 든다
        vx, vy, vw, vh = ross.VIEWBOX
        nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", ross.LAND)]
        xs, ys = nums[0::2], nums[1::2]
        self.assertGreaterEqual(min(xs), vx - 40)
        self.assertLessEqual(max(xs), vx + vw + 40)
        self.assertGreaterEqual(min(ys), vy - 40)
        self.assertLessEqual(max(ys), vy + vh + 40)

    def test_수심대는_얕은_것부터이고_경로가_읽힌다(self):
        """띠를 얕은 것부터 겹쳐 그려야 깊은 색이 위에 남는다 — 순서가 곧 그림이다."""
        depths = [d for d, _ in ross.BATHY]
        self.assertEqual(depths, sorted(depths))
        self.assertEqual(depths[0], 200)
        for d, path in ross.BATHY:
            self.assertTrue(_path_ok(path), f"{d} m")
        self.assertEqual(ross.BATHY_LEGEND, [0] + depths)

    def test_지점의_실측_수심을_싣는다(self):
        Locality.objects.filter(site__code="RS23").update(water_depth_m=512.0)
        ctx = ross.context(data.map_points("ant"))
        p = next(p for p in ctx["points"] if p["label"] == "RS23-GC03")
        self.assertEqual(p["water_depth_m"], 512.0)

    def test_틀과_viewBox_는_같은_사각형을_돌린_것이다(self):
        fx_, fy, fw, fh = ross.FRAME
        vx, vy, vw, vh = ross.VIEWBOX
        self.assertEqual((vw, vh), (fw, fh))
        # 틀의 네 귀퉁이를 돌리면 viewBox 의 네 귀퉁이다
        corners = {ross.to_xy(fx_, fy), ross.to_xy(fx_ + fw, fy + fh)}
        self.assertEqual(corners, {(-fx_, -fy), (vx, vy)})


class RossRenderTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        for i, ((sc, lc), (lat, lon)) in enumerate(CORES.items()):
            fx.make_world(slug=f"s{i}", site_code=sc, loc_code=lc,
                          sample_code=f"{10 * i + 1}cm", depth_cm=10 * i + 1,
                          with_files=False)
            Locality.objects.filter(site__code=sc, code=lc).update(lat=lat, lon=lon)
        fx.make_world(slug="wap", site_code="WAP13", loc_code="GC47",
                      with_files=False)
        Site.objects.filter(code="WAP13").update(lat=-65.3676, lon=-64.455)

    def html(self, area="ant"):
        r = self.client.get(reverse("index") + f"?area={area}")
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.content.decode()

    def test_남극_화면에_두_지도와_전환이_있다(self):
        html = self.html()
        self.assertIn('class="antmap rossmap"', html)
        self.assertIn('data-zoom="ross"', html)
        self.assertIn("로스해 틀 밖: WAP13-GC47", html)
        # 전체 지도 위의 틀 사각형 — 돌리기 전 좌표
        fx_, fy, fw, fh = ross.FRAME
        self.assertRegex(html, rf'class="zoomframe"[^>]*x="{fx_}" y="{fy}"\s+'
                              rf'width="{fw}" height="{fh}"')

    def test_확대_지도에는_지점_마커가_이름표_방향과_함께_있다(self):
        html = self.html()
        m = re.search(r'<svg class="antmap rossmap".*?</svg>', html, re.S)
        self.assertIsNotNone(m)
        svg = m.group(0)
        for s, c in CORES:
            self.assertIn(f">{s}-{c}</text>", svg)
        self.assertNotIn(">WAP13-GC47</text>", svg)
        # 방향은 클래스로 — 속성으로 주면 `.tag` 의 CSS 에 진다
        self.assertRegex(svg, r'class="tag a-(start|end)"')
        self.assertNotIn('text-anchor="start"', svg.split('class="sites"')[1])

    def test_수심_띠와_범례와_장보고_별이_그려진다(self):
        Locality.objects.filter(site__code="RS23").update(water_depth_m=512.0)
        html = self.html()
        svg = re.search(r'<svg class="antmap rossmap".*?</svg>', html, re.S).group(0)
        for d in (200, 1000, 2000, 3000, 4000):
            self.assertIn(f'<path class="b{d}" d="M', svg)
        self.assertIn('class="blegend"', svg)
        self.assertIn('<rect class="b0"', svg)
        # 우리 기지는 별 + 강조 글자, 남의 기지는 마름모
        self.assertRegex(svg, re.compile(
            r'class="lm-ours".*?<path class="star".*?class="hi"[^>]*>장보고기지<', re.S))
        self.assertRegex(svg, re.compile(
            r'class="lm-station".*?<rect[^>]*rotate\(45\).*?>맥머도기지<', re.S))
        # 실측 수심은 툴팁에
        self.assertIn("수심 512 m", svg)

