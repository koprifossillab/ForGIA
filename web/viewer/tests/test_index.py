"""목록 화면. 빈 DB 에서도 뜨고, 층이 있으면 층대로 내려간다."""
from .base import ForGIATestCase
from .factories import make_slide, make_world


class IndexTests(ForGIATestCase):
    def test_empty_db_renders(self):
        """0단계의 약속 — **빈 DB 로 목록이 뜬다.**"""
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "아직 슬라이드가 없다")
        self.assertContains(r, "ForGIA")

    def test_layers_listed(self):
        w = make_world()
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for needle in ("RS23", "로스해 23", "GC03", "71cm", "231cm",
                       "RS23-GC03 71cm &gt;125um", "&gt;125 µm", "&gt;63 µm", "1/8"):
            self.assertIn(needle, html, needle)
        # 분획이 없는 관찰은 분획 배지를 안 낸다 — 빈 배지가 뜨면 "분획이 없는
        # 시료" 와 "아직 안 채운 시료" 가 구별되지 않는다
        self.assertNotIn(">None", html)
        self.assertEqual(w["slides"][2].fraction_badge, "")

    def test_orphan_is_counted_not_hidden(self):
        """소속을 잃은 관찰은 목록에서 사라진다 (DiaRUGA 063) — 대신 수를 적는다."""
        make_world()
        make_slide(orphan=True, name="WAP13-GC47 116cm", fraction_um=None)
        r = self.client.get("/")
        self.assertContains(r, "소속이 없는 관찰 <b>1</b>")
        self.assertNotContains(r, "WAP13-GC47 116cm")

    def test_theme_tokens_both_themes(self):
        """색 토큰은 어두운 쪽과 밝은 쪽 **둘 다** 채운다 (DiaRUGA 107·201).
        한쪽에만 두면 그 테마에서 그 색이 사라진다 — 예외도 경고도 없다."""
        html = self.client.get("/").content.decode()
        dark = html.split(":root {", 1)[1].split("}", 1)[0]
        light = html.split(':root[data-theme="light"] {', 1)[1].split("}", 1)[0]
        for tok in ("--bg", "--panel", "--line", "--fg", "--dim", "--accent",
                    "--warn", "--good", "--danger", "--ok-bg", "--err-bg",
                    "--warn-bg", "--field-bg", "--wm-op", "--envwm-op",
                    "--group-bg"):
            self.assertIn(tok + ":", dark, f"{tok} 이 어두운 테마에 없다")
            self.assertIn(tok + ":", light, f"{tok} 이 밝은 테마에 없다")
        # 강조색은 연보라다 (사용자 2026-09-18) — 로고 원본의 색
        self.assertIn("--accent: #c4b5fd", dark)
        # 테마는 속성이 정한다 — 미디어 쿼리가 되살아나면 고른 것을 OS 가 덮는다
        self.assertNotIn("@media (prefers-color-scheme", html)
