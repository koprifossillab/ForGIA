"""DiaRUGA v0.29.0 `tests/test_batch_scope.py` 에서 왔다.

**어느 묶음이 어느 권역을 보는가** (2026-08-26 · `pipeline/batch_scope.py`).

검출 방침이 권역마다 갈렸다 — 남극은 YOLO 로 학습시키고 한국은 SAM 으로만
본다. 폴러는 조리법이 적힌 묶음마다 **새 슬라이드 전부**를 돌기 때문에, 방침을
말로만 정해 두면 자료가 들어오는 순간 조용히 어겨진다(실제로 한국 BP 두
슬라이드에 `yolo-3차` 검출 467건이 들어와 있었다).

**되살려서 잡히는 것**을 본다: `areas` 를 떼면 아래 시험들이 무너져야 한다.
"""
import sys
from pathlib import Path

from .base import ForGIATestCase
from . import factories as fx

# `batch_scope` 는 `pipeline/` 에 있다 — 저장소에서는 디렉토리가 갈려 있다
# (`/srv/DiaRUGA/scripts` 는 평평해서 그쪽에서는 그냥 옆에 있다).
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pipeline"))
import batch_scope                                                  # noqa: E402


class AllowedAreasTest(ForGIATestCase):
    """규칙 자체 — Django 를 안 탄다."""

    def test_적혀_있지_않으면_전부_본다(self):
        """**옛 묶음은 아무것도 안 바뀌어야 한다.**"""
        self.assertIsNone(batch_scope.allowed_areas({"backend": "yolo"}))
        self.assertTrue(batch_scope.allows({"backend": "yolo"}, "kr"))
        self.assertTrue(batch_scope.allows(None, "ant"))

    def test_적힌_권역만_본다(self):
        r = {"backend": "yolo", "areas": ["ant"]}
        self.assertTrue(batch_scope.allows(r, "ant"))
        self.assertFalse(batch_scope.allows(r, "kr"))

    def test_쉼표로_적어도_읽는다(self):
        self.assertEqual(batch_scope.allowed_areas({"areas": "ant, kr"}),
                         ["ant", "kr"])
        self.assertTrue(batch_scope.allows({"areas": "ant,kr"}, "kr"))

    def test_빈_목록은_전부가_아니다(self):
        """**비어 버린 값이 '전부' 로 읽히면 막으려던 것이 통과한다.**"""
        self.assertEqual(batch_scope.allowed_areas({"areas": []}), [])
        self.assertFalse(batch_scope.allows({"areas": []}, "ant"))

    def test_권역을_모르면_거른다(self):
        """소속이 끊긴 슬라이드. **모르겠으면 통과이면 문이 아니다.**"""
        r = {"areas": ["ant"]}
        self.assertFalse(batch_scope.allows(r, ""))
        self.assertFalse(batch_scope.allows(r, None))
        self.assertIn("권역을 알 수 없다", batch_scope.why(r, "", "yolo-3차"))

    def test_이유를_말한다(self):
        """조용히 빠지면 '왜 이 슬라이드만 비어 있나' 를 나중에 묻게 된다."""
        why = batch_scope.why({"areas": ["ant"]}, "kr", "yolo-3차")
        self.assertIn("yolo-3차", why)
        self.assertIn("kr", why)
        self.assertEqual(batch_scope.why({"areas": ["ant"]}, "ant"), "")


class SlideAreaTest(ForGIATestCase):
    """슬라이드에서 권역을 어떻게 읽는가 — **소속은 `Slide` 에 없다.**"""

    def area_of(self, slide):
        import segment_diatoms                                      # noqa: PLC0415
        return segment_diatoms.slide_area(slide)

    def test_시료를_거쳐_올라간다(self):
        w = fx.make_world(slug="rs23", area="ant", site_code="RS23")
        self.assertEqual(w.slide.sample.locality.site.area, "ant")

    def test_다른_권역도_그대로_올라간다(self):
        # ForGIA 는 남극뿐이지만 `Site.area` 는 그대로 오르는 값이다 (P01)
        w = fx.make_world(slug="xx-01", area="kr", site_code="XX", loc_code="X01",
                          sample_code="1cm", depth_cm=1.0)
        self.assertEqual(w.slide.sample.locality.site.area, "kr")

    def test_소속이_끊기면_빈_값이다(self):
        """예외로 죽지 않는다 — 무엇을 할지는 `batch_scope` 가 정한다."""
        w = fx.make_world(slug="rs23")
        w.slide.sample = None
        self.assertEqual(
            getattr(getattr(getattr(w.slide, "sample", None),
                            "locality", None), "site", None), None)
