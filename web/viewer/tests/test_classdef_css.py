"""DiaRUGA v0.29.0 `tests/test_classdef_css.py` 에서 왔다.

분류표(`ClassDef`)와 `base.html` 의 CSS 가 어긋나지 않는가 (DiaRUGA 038·040).

**분류를 더할 때 "테이블에 행 하나" 로 끝나지 않는다.** 여덟 칸에 `base.html`
의 CSS 까지다. 하나라도 비면 **예외는 안 나고 그 분류만 조용히 다르게 굴러간다**
— 마스크가 투명해 "지정은 되는데 화면에 안 보이는" 상태가 될 뻔했다.

색이 두 자리에 있는 것이 문제의 뿌리다. `ClassDef.color` 는 `"R,G,B"` 이고
`base.html` 에는 `.badge.<badge> { color: #RRGGBB }` 가 따로 있다 — **색은 아직
테이블에서 뿜어내지 않는다**(모델 머리말). 한쪽만 고치면 배지와 마스크가 다른
색이 되는데, 예외도 경고도 안 난다.

## 무엇을 못 보는지 먼저 적는다

시험은 **픽스처의 분류표**를 본다(`factories.CLASSES`, 운영 표를 옮겨 적은 것).
그래서 잡는 방향이 하나다.

    잡는다    `base.html` 의 CSS 가 지워지거나 색이 바뀌는 것
    못 잡는다  운영 DB 에 분류가 늘었는데 CSS 를 안 더한 것

뒤쪽은 `check_db.py` 의 "4. 분류" 몫이다 — 그쪽은 운영 DB 를 직접 읽는다.
**둘이 같은 것을 보는 것처럼 적으면 안 된다.**

## 손으로 옮겨 적지 않는다

색을 시험에 직접 써 두면 사본을 시험하는 것이 된다(HANDOFF 3.3). **렌더한
결과에서 떼어 와** DB 의 값과 맞춰 본다.
"""
import re

from django.test import Client
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from ..models import ClassDef

STYLE = re.compile(r"<style[^>]*>(.*?)</style>", re.S)
BADGE_RULE = re.compile(r"\.badge\.([\w-]+)\s*\{([^}]*)\}")
HEX = re.compile(r"color\s*:\s*#([0-9a-fA-F]{6})\b")


def rendered_css(html: str) -> str:
    """렌더한 페이지에서 CSS 를 떼어 온다.

    **`<style>` 이 하나도 없으면 그 자체가 고장이다** — `extends` 한 템플릿에서
    `block` 바깥에 적은 것은 렌더되지 않는데, 예외도 경고도 없다.
    """
    blocks = STYLE.findall(html)
    assert blocks, "렌더 결과에 <style> 이 하나도 없다"
    return "\n".join(blocks)


def hex_of(rgb: str) -> str:
    r, g, b = (int(v) for v in rgb.split(","))
    return f"{r:02x}{g:02x}{b:02x}"


class BadgeCssTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()

    def setUp(self):
        self.css = rendered_css(Client().get(reverse("index")).content.decode())
        self.rules = {name: body for name, body in BADGE_RULE.findall(self.css)}

    def test_활성_분류마다_배지_CSS_가_있다(self):
        """`badge` 는 CSS 클래스 이름이다 — 규칙이 없으면 배지가 맨 채로 나온다."""
        for c in ClassDef.objects.filter(active=True):
            with self.subTest(key=c.key):
                self.assertTrue(c.badge, f"{c.key} 에 badge 가 비어 있다")
                self.assertIn(c.badge, self.rules,
                              f"{c.key} 의 배지 `.badge.{c.badge}` 규칙이 "
                              f"base.html 에 없다")

    def test_배지_색이_ClassDef_와_같다(self):
        """**색이 두 자리에 있다.** 한쪽만 고치면 배지와 마스크가 다른 색이 된다."""
        for c in ClassDef.objects.filter(active=True):
            with self.subTest(key=c.key):
                body = self.rules.get(c.badge)
                if body is None:
                    self.skipTest("위 시험이 이미 잡는다")
                m = HEX.search(body)
                self.assertIsNotNone(m, f".badge.{c.badge} 에 color 가 없다")
                self.assertEqual(m.group(1).lower(), hex_of(c.color),
                                 f"{c.key}: CSS 는 #{m.group(1)} 인데 "
                                 f"ClassDef.color 는 {c.color} 다")

    def test_활성_분류의_여덟_칸이_다_차_있다(self):
        """`check_db.py` 는 단축키·색만 본다. 나머지도 여기서 센다.

        `counted`·`is_taxon` 은 불리언이라 "비어 있음" 이 없고, `sort_order` 는
        0 이 정상값이다 — 그 셋은 뺀다.
        """
        for c in ClassDef.objects.filter(active=True):
            for field in ("label", "short", "badge", "color", "hotkey"):
                with self.subTest(key=c.key, field=field):
                    self.assertTrue(getattr(c, field),
                                    f"{c.key} 의 {field} 가 비어 있다")

    def test_색은_R_G_B_꼴이다(self):
        """마스크는 이 문자열을 그대로 쓴다 — 모양이 틀리면 투명해진다."""
        for c in ClassDef.objects.filter(active=True):
            with self.subTest(key=c.key):
                parts = c.color.split(",")
                self.assertEqual(len(parts), 3, f"{c.key}: {c.color!r}")
                for v in parts:
                    self.assertTrue(v.strip().isdigit(), f"{c.key}: {c.color!r}")
                    self.assertLessEqual(int(v), 255, f"{c.key}: {c.color!r}")

    def test_꺼진_분류는_CSS_를_요구하지_않는다(self):
        """**분류를 되돌릴 때는 지우지 말고 `active=False` 로 끈다** — 행을
        지우면 그 분류로 붙인 교정이 이름 없는 분류가 된다. 끈 분류에까지
        CSS 를 요구하면 CSS 를 못 걷는다."""
        ClassDef.objects.create(key="꺼진것", label="꺼진 것", badge="없는배지",
                                color="1,2,3", hotkey="z", active=False,
                                sort_order=99)
        self.test_활성_분류마다_배지_CSS_가_있다()      # 안 터져야 한다


class MaskColorTest(ForGIATestCase):
    """마스크 색은 CSS 가 아니라 **표에서 화면으로 실려 간다.**

    배지와 달리 마스크는 `ClassDef.color` 를 그대로 쓴다. 그 값이 검토 화면까지
    실제로 닿는지를 본다 — 안 닿으면 "지정은 되는데 화면에 안 보이는" 상태다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)

    def test_분류_색이_검토_화면까지_실려_간다(self):
        html = Client().get(
            reverse("group", args=[self.w.slug, self.w.vp.idx])).content.decode()
        for c in ClassDef.objects.filter(active=True):
            with self.subTest(key=c.key):
                self.assertIn(c.color, html,
                              f"{c.key} 의 색 {c.color} 가 화면에 안 실렸다")


class TemplateHygieneTest(ForGIATestCase):
    """템플릿이 **문법을 잘못 써서 화면에 그대로 나오는** 것을 잡는다.

    - **Django 의 `{# #}` 는 한 줄짜리 주석이다.** 여러 줄이면 화면에 그대로
      나온다. `{% comment %}` 를 써야 한다
    - `extends` 한 템플릿에서 `block` 바깥에 적은 것은 렌더되지 않는다 —
      `<style>` 이 한 번도 먹은 적이 없었다

    둘 다 **예외도 경고도 없는** 종류의 고장이다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)
        fx.add_other_engine(cls.w.vp)

    def pages(self):
        w = self.w
        return [
            reverse("index"),
            reverse("system_settings"),
            reverse("thresholds_all"),
            reverse("dataset", args=[w.slug]),
            reverse("dataset_edit", args=[w.slug]),
            reverse("detections", args=[w.slug]),
            reverse("crops", args=[w.slug]),
            reverse("thresholds", args=[w.slug]),
            reverse("group", args=[w.slug, w.vp.idx]),
            reverse("core", args=[w.site.code, w.locality.code]),
        ]

    def test_템플릿_문법이_화면에_새지_않는다(self):
        c = Client()
        for url in self.pages():
            with self.subTest(url=url):
                html = c.get(url).content.decode()
                for token in ("{%", "{#", "{{"):
                    self.assertNotIn(
                        token, html,
                        f"{url} 에 템플릿 문법 {token} 이 그대로 나온다")

    def test_화면마다_style_이_실제로_렌더된다(self):
        """`block` 바깥에 적힌 `<style>` 은 조용히 사라진다."""
        c = Client()
        for url in self.pages():
            with self.subTest(url=url):
                html = c.get(url).content.decode()
                self.assertTrue(STYLE.findall(html),
                                f"{url} 에 <style> 이 하나도 없다")
