"""정보 편집(`/d/<slug>/edit/`)과 시스템 설정 · 자료(`/system-settings/`).

DiaRUGA 063 의 함정 셋을 그대로 센다 — 붙이기만 할 때 남의 행을 빈 칸으로 덮지
않는가 · 아무것도 안 한 저장이 성공으로 보이지 않는가 · 지우기 문턱을 서버가
다시 검사하는가. 그리고 ForGIA 가 더한 칸(분획·분할·칸 수·건시료 무게)이 저장되는가.
"""
from django.urls import reverse

from . import factories as fx
from .base import ForGIATestCase
from ..models import Locality, Sample, Site, Slide


class EditTest(ForGIATestCase):
    def setUp(self):
        self.w = fx.make_world(slug="rs23", with_files=False, split_denom=None)

    def post(self, slug, **data):
        base = {"slide_name": "", "description": "", "obs_label": "",
                "fraction_um": "", "split_denom": "", "cells": "",
                "sample_code": "71cm", "depth_cm": "71", "dry_weight_g": "",
                "core_code": "GC03", "core_kind": "", "core_lat": "", "core_lon": "",
                "core_water_depth": "", "core_collected_at": "", "core_note": "",
                "site_code": "RS23", "site_name": "", "site_region": "", "site_area": "ant",
                "site_lat": "", "site_lon": "", "site_note": ""}
        base.update(data)
        return self.client.post(reverse("dataset_edit", args=[slug]), base)

    def test_forgia_fields_round_trip(self):
        r = self.post("rs23", fraction_um="63", split_denom="8", cells="60",
                      dry_weight_g="12.5", core_water_depth="850", site_region="로스해")
        self.assertContains(r, "저장했습니다")
        sl = Slide.objects.get(slug="rs23")
        self.assertEqual((sl.fraction_um, sl.split_denom, sl.cells), (63.0, 8, 60))
        self.assertEqual(sl.sample.dry_weight_g, 12.5)
        self.assertEqual(sl.sample.locality.water_depth_m, 850.0)
        self.assertEqual(sl.sample.locality.site.region, "로스해")
        self.assertContains(self.client.get("/"), "&gt;63 µm")
        self.assertContains(self.client.get("/"), "1/8")

    def test_bad_number_is_an_error_not_zero(self):
        r = self.post("rs23", split_denom="여덟")
        self.assertContains(r, "값을 읽지 못했습니다")
        self.assertIsNone(Slide.objects.get(slug="rs23").split_denom)

    def test_attach_does_not_overwrite_target_layers(self):
        """붙이기만 할 때 폼의 빈 칸을 남의 행에 쓰지 않는다 (DiaRUGA 063)."""
        loc = self.w.locality
        loc.water_depth_m = 850.0; loc.save()
        orphan = fx.make_slide(orphan=True, name="RS23-GC03 71cm >63um", slug="orphan",
                               fraction_um=63.0)
        r = self.post("orphan", attach_sample=str(self.w.sample.pk),
                      sample_code="", depth_cm="", core_code="", core_water_depth="",
                      site_code="", site_region="")
        self.assertContains(r, "시료에 붙였습니다")
        orphan.refresh_from_db(); loc.refresh_from_db()
        self.assertEqual(orphan.sample_id, self.w.sample.pk)
        self.assertEqual(loc.water_depth_m, 850.0)     # 빈 칸이 덮지 않았다

    def test_partial_codes_do_not_silently_succeed(self):
        orphan = fx.make_slide(orphan=True, name="WAP13-GC47 116cm", slug="wap",
                               fraction_um=None)
        r = self.post("wap", sample_code="116cm", core_code="", site_code="")
        self.assertContains(r, "모두 채워야 합니다")
        orphan.refresh_from_db()
        self.assertIsNone(orphan.sample_id)

    def test_create_layers_from_codes(self):
        orphan = fx.make_slide(orphan=True, name="WAP13-GC47 116cm", slug="wap",
                               fraction_um=None)
        r = self.post("wap", sample_code="116cm", depth_cm="116",
                      core_code="GC47", site_code="WAP13", site_region="남극반도")
        self.assertContains(r, "새로 만들어 붙였습니다")
        orphan.refresh_from_db()
        self.assertEqual(str(orphan.sample), "WAP13-GC47-116cm")
        self.assertEqual(orphan.sample.locality.site.region, "남극반도")


class SettingsTest(ForGIATestCase):
    def setUp(self):
        self.w = fx.make_world(slug="rs23", with_files=False)
        self.url = reverse("system_settings")

    def test_overview_lists_layers_and_orphans(self):
        fx.make_slide(orphan=True, name="WAP13-GC47 116cm", slug="wap", fraction_um=None)
        r = self.client.get(self.url)
        self.assertContains(r, "소속 없는 관찰 1")
        self.assertContains(r, "WAP13-GC47 116cm")
        self.assertContains(r, "gravity core" if self.w.locality.collect_kind else "GC03")

    def test_create_site_locality_sample(self):
        self.client.post(self.url, {"act": "create", "kind": "site", "code": "AM22",
                                    "region": "아문센해", "area": "ant"})
        site = Site.objects.get(code="AM22")
        self.client.post(self.url, {"act": "create", "kind": "locality", "site": site.pk,
                                    "code": "GC10B", "collect_kind": "gravity core"})
        loc = Locality.objects.get(site=site, code="GC10B")
        self.assertEqual(loc.collect_kind, "gravity core")
        r = self.client.post(self.url, {"act": "create", "kind": "sample", "locality": loc.pk,
                                        "code": "25cm", "depth_cm": "25", "dry_weight_g": "9.75"})
        self.assertEqual(r.status_code, 302)
        sm = Sample.objects.get(locality=loc, code="25cm")
        self.assertEqual((sm.depth_cm, sm.dry_weight_g), (25.0, 9.75))

    def test_delete_is_blocked_server_side(self):
        """화면에서 막는 것은 막는 것이 아니다 — 서버가 다시 검사한다."""
        r = self.client.post(self.url, {"act": "delete", "kind": "sample",
                                        "pk": self.w.sample.pk}, follow=True)
        self.assertContains(r, "관찰 1개가 이 시료를 보고 있습니다")
        self.assertTrue(Sample.objects.filter(pk=self.w.sample.pk).exists())
        r = self.client.get(self.url)
        self.assertContains(r, 'class="btn del" disabled')

    def test_move_slide_and_detach(self):
        other = fx.make_sample(self.w.locality, code="231cm", depth_cm=231.0)
        self.client.post(self.url, {"act": "move_slide", "slide": self.w.slide.pk,
                                    "sample": other.pk})
        self.assertEqual(Slide.objects.get(pk=self.w.slide.pk).sample_id, other.pk)
        self.client.post(self.url, {"act": "move_slide", "slide": self.w.slide.pk, "sample": ""})
        self.assertIsNone(Slide.objects.get(pk=self.w.slide.pk).sample_id)
        self.assertContains(self.client.get("/"), "지점 미지정")


class PagesTest(ForGIATestCase):
    def setUp(self):
        self.w = fx.make_world(slug="rs23", n_viewpoints=2, dry_weight_g=12.5)

    def test_core_page(self):
        r = self.client.get(reverse("core", args=["RS23", "GC03"]))
        self.assertContains(r, "RS23-GC03")
        self.assertContains(r, "12.5")
        self.assertContains(r, "&gt;125 µm")
        self.assertEqual(self.client.get(reverse("core", args=["RS23", "NOPE"])).status_code, 404)
        r = self.client.get("/core/RS23/GC03/")
        self.assertEqual((r.status_code, r["Location"]), (302, "/loc/RS23/GC03/"))

    def test_group_page_navigates(self):
        r = self.client.get(reverse("group", args=["rs23", 0]))
        self.assertContains(r, "칸 1")
        self.assertContains(r, "g1 →")
        self.assertNotContains(r, "← g")
        self.assertEqual(self.client.get(reverse("group", args=["rs23", 9])).status_code, 404)

    def test_image_endpoint_is_sandboxed(self):
        rel = self.w.vp.frames.first().path
        r = self.client.get(reverse("image"), {"p": rel, "w": 64})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/jpeg")
        self.assertEqual(self.client.get(reverse("image"), {"p": "../../etc/passwd"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("image"), {"p": ""}).status_code, 404)
