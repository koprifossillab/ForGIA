"""DiaRUGA v0.29.0 `tests/test_dataset_edit.py` 에서 왔다.

정보 편집(`/d/<slug>/edit/`)이 소속 없는 관찰을 시료에 붙이는 갈래 (DiaRUGA 205).

063 이 말한 "아무것도 안 한 저장이 성공으로 보이는" 그 갈래가 **코드로 새로
만들어 붙이는 길**에 남아 있었다 — 지역·지점·시료 행은 생기고 "새로 만들어
붙였습니다" 도 뜨는데 `slide.sample` 은 그대로 비어 있었다. `attached` 를
`sample.save()` **앞에서** 재서 새 시료의 `pk`(None)와 소속 없는 관찰의
`sample_id`(None)가 같아 보였던 것이다. 기존 시료에 붙이는 길은 `pk` 가 있어
멀쩡했고, 그래서 여태 안 걸렸다. ForGIA 1단계 이식 중에 발견(2026-09-18).

**셋 다 `slide.sample` 을 DB 에서 다시 읽어 본다** — 응답 문구는 그때도
성공이었다.
"""
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from ..models import Locality, Sample, Site, Slide


def _orphan(slug="wap", name="WAP13-GC47 116cm"):
    """소속 없는 관찰 — 폴더 이름이 규칙에 안 맞아 파이프라인이 못 붙인 것."""
    return Slide.objects.create(name=name, slug=slug,
                                image_dir=f"photos/260918/{slug}", sample=None)


class DatasetEditAttachTest(ForGIATestCase):

    def setUp(self):
        self.w = fx.make_world(slug="rs23", with_files=False)

    def post(self, slug, **data):
        base = {"slide_name": "", "description": "", "obs_label": "",
                "um_per_pixel_override": "",
                "sample_code": "", "depth_cm": "", "sample_note": "",
                "core_code": "", "locality_kind": "core", "core_kind": "",
                "core_lat": "", "core_lon": "", "core_water_depth": "",
                "core_collected_at": "", "core_note": "",
                "site_code": "", "site_name": "", "site_region": "",
                "site_area": "ant", "site_lat": "", "site_lon": "", "site_note": ""}
        base.update(data)
        return self.client.post(reverse("dataset_edit", args=[slug]), base)

    def test_코드로_새로_만들어_붙이면_실제로_붙는다(self):
        orphan = _orphan()
        r = self.post("wap", sample_code="116cm", depth_cm="116",
                      core_code="GC47", site_code="WAP13", site_region="남극반도")
        self.assertContains(r, "새로 만들어 붙였습니다")
        orphan.refresh_from_db()
        # **문구가 아니라 행을 본다.** 문구는 버그가 있을 때도 떴다.
        self.assertIsNotNone(orphan.sample_id)
        self.assertEqual(orphan.sample.code, "116cm")
        self.assertEqual(orphan.sample.depth_cm, 116.0)
        self.assertEqual(orphan.sample.locality.code, "GC47")
        self.assertEqual(orphan.sample.locality.site.code, "WAP13")
        self.assertEqual(orphan.sample.locality.site.region, "남극반도")

    def test_있는_지역_지점에_새_시료만_만들어_붙인다(self):
        """위 두 층은 이미 있고 시료만 새로 — 지역·지점이 둘로 갈라지면 안 된다."""
        orphan = _orphan(slug="rs23b", name="RS23-GC03 231cm")
        r = self.post("rs23b", sample_code="231cm", depth_cm="231",
                      core_code=self.w.locality.code, site_code=self.w.site.code)
        self.assertContains(r, "시료 231cm")
        orphan.refresh_from_db()
        self.assertEqual(orphan.sample.locality_id, self.w.locality.pk)
        self.assertEqual(Site.objects.filter(code=self.w.site.code).count(), 1)
        self.assertEqual(Locality.objects.filter(code=self.w.locality.code).count(), 1)
        self.assertEqual(Sample.objects.filter(locality=self.w.locality).count(), 2)

    def test_기존_시료에_붙이는_길은_그대로_된다(self):
        orphan = _orphan(slug="orphan", name="RS23-GC03 71cm (2)")
        r = self.post("orphan", attach_sample=str(self.w.sample.pk))
        self.assertContains(r, "시료에 붙였습니다")
        orphan.refresh_from_db()
        self.assertEqual(orphan.sample_id, self.w.sample.pk)

    def test_이미_붙어_있으면_다시_붙였다고_하지_않는다(self):
        r = self.post("rs23", sample_code=self.w.sample.code, depth_cm="71",
                      core_code=self.w.locality.code, site_code=self.w.site.code)
        self.assertContains(r, "저장했습니다")
        self.assertNotContains(r, "붙였습니다")
