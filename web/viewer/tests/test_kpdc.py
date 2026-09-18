"""DiaRUGA v0.29.0 `test_kpdc.py` 에서 왔다 — 노두 갈래를 뺐다.

KPDC 코어 메타데이터 긁기 (197).

네트워크는 안 쓴다 — 파서는 실제 페이지를 잘라 둔 픽스처로, 채우기 규칙은
지점 객체로 본다. 잡는 것:

- 페이지에서 좌표·수심·길이·채취일·첨부 목록이 **그 값으로** 나오는가
- 항목이 둘이면(`RS14-GC04`) 최신 것을 고르는가 · `RS21-GC03` 이 `RS21-GC03B` 를 안 줍는가
- **사람이 넣은 값을 안 덮는가** (063 의 그 줄 — `WAP13-GC47` 이 실제 사례다)
- 다각형 좌표·기간 범위는 **안 쓰는가** (짐작해서 채우면 틀린 값이 앉는다)
- 폴러가 그룹핑 뒤에 부르고 **실패해도 멈추지 않는가**
- 화면에 링크가 뜨는가
"""
import datetime as dt
import importlib.util
import re
import sys
from pathlib import Path

from django.urls import reverse

from . import factories as fx
from .base import ForGIATestCase
from .. import kpdc
from ..models import Locality, Site

_ROOT = Path(__file__).resolve().parents[3]
_PAGE = (Path(__file__).parent / "assets" / "kpdc_rs21-gc02.html").read_text()

_SEARCH = """
<a href="/search/aaaaaaaa-0000-0000-0000-000000000001">[KOPRI-KPDC-00000510] Sediment core RS14-GC04 data (Old ID)</a>
<a href="/search/aaaaaaaa-0000-0000-0000-000000000002">[KOPRI-KPDC-00002544] Gravity Core from Antarctic ROSS Sea (RS14-GC04)</a>
<a href="/search/aaaaaaaa-0000-0000-0000-000000000003">[KOPRI-KPDC-00001835] Gravity core from Antarctic Ross Sea (RS21-GC03B)</a>
"""


def _loc(**kw):
    site = Site.objects.create(code="RS21", area="ant")
    return Locality.objects.create(site=site, code="GC02", **kw)


class ParseTest(ForGIATestCase):
    def test_detail(self):
        d = kpdc.parse_detail(_PAGE)
        self.assertEqual(d["entry_id"], "KOPRI-KPDC-00001836")
        self.assertEqual(d["doi"], "10.22663/KOPRI-KPDC-00001836")
        self.assertEqual(d["title"], "Gravity core from Antarctic Ross Sea (RS21-GC02)")
        self.assertEqual((d["lat"], d["lon"]), (-77.399902, 176.299317))
        self.assertEqual(d["coverage"], "POINT")
        self.assertEqual(d["water_depth_m"], 625.0)
        self.assertEqual(d["core_length_m"], 2.17)
        self.assertEqual(d["collected_at"], "2020-12-13")
        self.assertEqual(d["fields"]["Instruments"], "GRAVITY CORER")
        self.assertEqual(d["views"], 1358)
        # 첨부 — 이름·분류·크기·상태. 전부 "Request required" 다
        self.assertEqual(len(d["files"]), 13)
        self.assertEqual(d["files"][0], {
            "category": "Rawdata", "name": "RS21-GC02_(0-119).jpg",
            "description": "Core photograph", "bytes": 2284489,
            "status": "Request required", "file_id": ""})
        self.assertTrue(all(f["status"] == "Request required" for f in d["files"]))
        self.assertEqual([v["version"] for v in d["versions"]], [5, 4, 3, 2, 1])
        # 지도 레이어 목록도 <dl> 이라 걸러야 한다
        self.assertNotIn("Base Layer", d["fields"])
        self.assertNotIn("Coastline", d["fields"])

    def test_polygon_and_range_are_not_used(self):
        page = (_PAGE
                .replace('<p class="point-header">POINT</p>',
                         '<p class="point-header">POLYGON</p>')
                .replace("2020-12-13 ~ 2020-12-13", "2020-12-01 ~ 2020-12-31"))
        d = kpdc.parse_detail(page)
        self.assertEqual(d["coverage"], "POLYGON")
        self.assertIsNone(d["lat"])
        self.assertIsNone(d["collected_at"])
        self.assertEqual(d["research_period"], "2020-12-01 ~ 2020-12-31")

    def test_search_and_pick(self):
        hits = kpdc.parse_search(_SEARCH)
        self.assertEqual([h["entry_id"] for h in hits],
                         ["KOPRI-KPDC-00000510", "KOPRI-KPDC-00002544",
                          "KOPRI-KPDC-00001835"])
        # 둘이면 Entry ID 가 큰 것(다시 등록한 새 항목)
        self.assertEqual(kpdc.pick_hit(hits, "RS14-GC04")["entry_id"],
                         "KOPRI-KPDC-00002544")
        # 낱말 단위 — GC03 이 GC03B 를 줍지 않는다
        self.assertIsNone(kpdc.pick_hit(hits, "RS21-GC03"))
        self.assertEqual(kpdc.pick_hit(hits, "rs21-gc03b")["entry_id"],
                         "KOPRI-KPDC-00001835")
        self.assertIsNone(kpdc.pick_hit(hits, "AM22-GC10B"))


_DL_ROW = """
<tr class="files file selectable" data-file-id="913b08bc-add9-442d-affa-c677e4b69fee">
  <td class="file-category">Analysis Data</td>
  <td><span class="file-name">Downcore element concnetration_GC03-C1.xlsx</span></td>
  <td class="file-description"></td>
  <td class="file-size"><span class="file-size" title="51008">51.01 Kb</span></td>
  <td class="file-status">Download</td>
</tr>
"""


class DownloadTest(ForGIATestCase):
    """`Download` 인 첨부만 받고, 받은 자리를 meta 에 적는다. 네트워크는 흉내
    낸다 — `download()` 를 갈아 끼운다."""

    def test_file_id_only_on_downloadable(self):
        d = kpdc.parse_detail(_PAGE.replace("<tbody>", "<tbody>" + _DL_ROW, 1))
        self.assertEqual(len(d["files"]), 14)
        self.assertEqual(d["files"][0]["status"], "Download")
        self.assertEqual(d["files"][0]["file_id"],
                         "913b08bc-add9-442d-affa-c677e4b69fee")
        self.assertTrue(all(f["file_id"] == "" for f in d["files"][1:]))

    def test_save_downloads(self):
        import tempfile
        from unittest import mock
        d = kpdc.parse_detail(_PAGE.replace("<tbody>", "<tbody>" + _DL_ROW, 1))
        d["path"] = "/search/af2e48a7-17da-4735-82ae-59856a45b4b7"
        calls = []

        def fake(file_id, referer, *, purpose="x", timeout=0):
            calls.append((file_id, referer))
            return "Downcore element concnetration_GC03-C1.xlsx", b"x" * 51008

        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(kpdc, "download", fake):
            got = kpdc.save_downloads(d, td)
            self.assertEqual(got, ["Downcore element concnetration_GC03-C1.xlsx"])
            # Request required 는 안 부른다 · Referer 는 항목 페이지다
            self.assertEqual(calls, [("913b08bc-add9-442d-affa-c677e4b69fee",
                                      "/search/af2e48a7-17da-4735-82ae-59856a45b4b7")])
            self.assertTrue(d["files"][0]["saved"].endswith("GC03-C1.xlsx"))
            self.assertNotIn("saved", d["files"][1])
            # 같은 크기로 이미 있으면 다시 안 받는다
            got2 = kpdc.save_downloads(d, td)
            self.assertEqual(got2, [])
            self.assertEqual(len(calls), 1)
        self.assertEqual(kpdc.files_note(d), "첨부 14개 · 받아 둠 1 · 요청 필요 13")

    def test_files_note(self):
        self.assertEqual(kpdc.files_note(None), "")
        d = kpdc.parse_detail(_PAGE)
        self.assertEqual(kpdc.files_note(d), "첨부 13개 · 요청 필요 13")
        d = kpdc.parse_detail(_PAGE.replace("<tbody>", "<tbody>" + _DL_ROW, 1))
        self.assertEqual(kpdc.files_note(d), "첨부 14개 · 내려받을 수 있음 1 · 요청 필요 13")


class ApplyTest(ForGIATestCase):
    def test_fills_blanks(self):
        loc = _loc()
        meta = kpdc.parse_detail(_PAGE)
        changed = kpdc.apply(loc, meta)
        self.assertEqual(set(changed), {"lat", "lon", "water_depth_m",
                                        "collected_at", "collect_kind",
                                        "kpdc_id", "kpdc_meta"})
        loc.save(update_fields=changed)
        loc.refresh_from_db()
        self.assertEqual((loc.lat, loc.lon), (-77.399902, 176.299317))
        self.assertEqual(loc.water_depth_m, 625.0)
        self.assertEqual(loc.collected_at, dt.date(2020, 12, 13))
        self.assertEqual(loc.collect_kind, "gravity core")
        self.assertEqual(loc.kpdc_id, "KOPRI-KPDC-00001836")
        self.assertEqual(loc.kpdc_meta["core_length_m"], 2.17)
        self.assertEqual(loc.kpdc_url,
                         "https://doi.org/10.22663/KOPRI-KPDC-00001836")

    def test_never_overwrites_human_values(self):
        # WAP13-GC47 의 실제 모양 — 사람이 넣은 좌표가 KPDC 와 다르다
        loc = _loc(lat=-65.3676, lon=-64.455, water_depth_m=673.0,
                   collect_kind="piston core", note="사람이 적은 것")
        meta = kpdc.parse_detail(_PAGE)
        changed = kpdc.apply(loc, meta)
        self.assertEqual(set(changed), {"collected_at", "kpdc_id", "kpdc_meta"})
        self.assertEqual((loc.lat, loc.lon), (-65.3676, -64.455))
        self.assertEqual(loc.water_depth_m, 673.0)
        self.assertEqual(loc.collect_kind, "piston core")
        self.assertEqual(loc.note, "사람이 적은 것")

    def test_refetch_replaces_meta_only(self):
        loc = _loc()
        kpdc.apply(loc, kpdc.parse_detail(_PAGE))
        loc.lat = -70.0                                   # 사람이 고쳤다
        meta2 = dict(kpdc.parse_detail(_PAGE), lat=-1.0, views=9999)
        changed = kpdc.apply(loc, meta2)
        self.assertEqual(changed, ["kpdc_meta"])          # id 는 그대로라 안 들어간다
        self.assertEqual(loc.lat, -70.0)
        self.assertEqual(loc.kpdc_meta["views"], 9999)

    def test_unknown_instrument_leaves_collect_kind(self):
        loc = _loc()
        meta = kpdc.parse_detail(_PAGE)
        meta["fields"]["Instruments"] = "SEDIMENT CORERS"
        kpdc.apply(loc, meta)
        self.assertEqual(loc.collect_kind, "")


class ScriptTest(ForGIATestCase):
    """`ops/fetch_kpdc.py` 의 대상 고르기 — 네트워크는 안 부른다."""

    @classmethod
    def _load(cls):
        path = _ROOT / "ops" / "fetch_kpdc.py"
        spec = importlib.util.spec_from_file_location("fetch_kpdc_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_targets(self):
        mod = self._load()
        w = fx.make_world(slug="rs23", with_stack=False, with_files=False)
        Args = type("A", (), {"slide": None, "locality": None,
                              "missing": False, "all": False})

        a = Args(); a.slide = "rs23"
        self.assertEqual([str(x) for x in mod._targets(a)], ["RS23-GC03"])
        a = Args(); a.missing = True
        self.assertEqual([str(x) for x in mod._targets(a)], ["RS23-GC03"])
        w.locality.kpdc_id = "KOPRI-KPDC-00000001"
        w.locality.save(update_fields=["kpdc_id"])
        self.assertEqual(mod._targets(a), [])
        a = Args(); a.all = True
        self.assertEqual([str(x) for x in mod._targets(a)], ["RS23-GC03"])
        a = Args(); a.locality = ["RS23-GC03"]
        self.assertEqual([str(x) for x in mod._targets(a)], ["RS23-GC03"])


class PollerTest(ForGIATestCase):
    def test_poller_calls_after_grouping_and_does_not_stop(self):
        sh = (_ROOT / "deploy" / "poll_nas.sh").read_text()
        i_group = sh.index("group_focus_series.py")
        i_fetch = sh.index("fetch_kpdc.py --slide")
        i_stack = sh.index("focus_stack.py --slide")
        self.assertLess(i_group, i_fetch)
        self.assertLess(i_fetch, i_stack)
        # 실패 갈래가 `continue`·`exit` 가 아니다 — 반입이 서는 조건이 아니다
        block = re.search(r"if ! rundb [^\n]*fetch_kpdc\.py --slide.*?\n\s*fi\n",
                          sh, re.S).group(0)
        self.assertNotIn("continue", block)
        self.assertNotIn("exit", block)
        self.assertIn("say ", block)


class PageTest(ForGIATestCase):
    def test_core_page_shows_link(self):
        w = fx.make_world(slug="rs21_gc02_71cm", site_code="RS21", loc_code="GC02",
                          with_stack=False, with_files=False)
        url = reverse("core", args=["RS21", "GC02"])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("KPDC", r.content.decode())

        loc = w.locality
        kpdc.apply(loc, kpdc.parse_detail(_PAGE))
        loc.save()
        r = self.client.get(url)
        body = r.content.decode()
        self.assertIn('href="https://doi.org/10.22663/KOPRI-KPDC-00001836"', body)
        self.assertIn("첨부 13개 · 요청 필요 13", body)
        self.assertIn("길이 2.17 m", body)
