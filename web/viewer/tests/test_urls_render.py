"""DiaRUGA v0.29.0 `tests/test_urls_render.py` 에서 왔다.

**모든 화면을 한 번 그린다.** 057 이 이 시험 하나로 잡혔을 것이다.

괄호가 든 슬러그 하나가 `urls.py` 의 `<slug:slug>` 를 어겨 **목록 템플릿이
링크를 만들다 죽었고 뷰어 전체가 500** 이었다. `/healthz` 는 그때도 `ok` 였다 —
**링크를 안 만들기 때문이다.** 그래서 여기서는 반드시 실제로 렌더한다.

"들어오는 값이 404 가 되는 것과 나가는 값을 못 만드는 것은 고장의 크기가
다르다"(CLAUDE.md). 이 파일이 보는 것은 **나가는 값** 쪽이다.
"""
from django.test import Client
from django.urls import NoReverseMatch, reverse

from .base import ForGIATestCase
from . import factories as fx
from .. import data
from ..models import Slide


class RenderAllPagesTest(ForGIATestCase):
    """자료가 있는 상태에서 모든 GET 화면이 그려지는가."""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        # 남극(시추코어)과 한국(노두) 둘 다. 지점 유형에 따라 갈리는 갈래가
        # 많아서(`depth_cm` 이냐 `sample_no` 냐, 노두 사진이 있느냐) 한쪽만
        # 세우면 절반을 안 본다.
        cls.ant = fx.make_world(slug="rs23", n_viewpoints=2, n_candidates=3)
        # ForGIA: 한국(노두) 갈래가 없다 — 다른 지역의 코어 하나로 대신한다
        cls.kr = fx.make_world(slug="am22-gc10_25cm", area="ant",
                               site_code="AM22", loc_code="GC10",
                               sample_code="25cm", depth_cm=25.0)
        # **합성본이 없는 싱글턴 시야도 세운다.** 검출이 프레임에 붙어 화면이
        # `_frames` 갈래로 간다. 이 자료가 없어서 크롭 화면이 v0.8.0 내내
        # `TypeError: _frames() takes 2 positional arguments` 로 500 이었는데도
        # **이 시험은 통과하고 있었다** — 세운 자료가 전부 합성본이었다.
        # 화면을 열어 보는 시험은 URL 이 아니라 **갈래**를 덮어야 한다.
        cls.singleton = fx.make_world(slug="wap13-gc47_116cm", site_code="WAP13",
                                      loc_code="GC47", sample_code="116cm",
                                      depth_cm=116.0, n_viewpoints=2,
                                      with_stack=False)

    def setUp(self):
        self.c = Client()

    def get(self, url, **kw):
        r = self.c.get(url, **kw)
        self.assertEqual(r.status_code, 200, f"{url} 이 {r.status_code} 다")
        return r

    # --- 인자 없는 화면 ----------------------------------------------------

    def test_인자_없는_화면들이_그려진다(self):
        # `offline` 은 5단계. ForGIA 의 것: 운영·학명·파이프라인 탭
        for name in ("index", "system_settings", "thresholds_all",
                     "system_settings_ops", "system_settings_taxa",
                     "system_settings_pipeline"):
            with self.subTest(name=name):
                self.get(reverse(name))

    def test_healthz_는_json_이고_ok_다(self):
        r = self.get(reverse("healthz"))
        d = r.json()
        self.assertEqual(d["status"], "ok", d)
        self.assertGreater(d["db"]["slide"], 0)

    # --- 슬라이드마다 ------------------------------------------------------

    def test_슬라이드_화면들이_전부_그려진다(self):
        for slide in Slide.objects.all():
            # `offline_slide` 는 5단계. `catalog` 가 3단계의 것
            for name in ("dataset", "dataset_edit", "detections", "crops",
                         "thresholds", "api_dataset", "catalog"):
                with self.subTest(slug=slide.slug, name=name):
                    self.get(reverse(name, args=[slide.slug]))

    def test_시야_화면이_전부_그려진다(self):
        for slide in Slide.objects.all():
            for vp in slide.viewpoints.all():
                with self.subTest(slug=slide.slug, gid=vp.idx):
                    self.get(reverse("group", args=[slide.slug, vp.idx]))

    def test_지점_화면이_그려진다(self):
        for w in (self.ant, self.kr):
            with self.subTest(loc=w.locality.code):
                self.get(reverse("core", args=[w.site.code, w.locality.code]))

    def test_옛_core_주소가_새_주소로_넘긴다(self):
        """"옛 주소. **지우지 않는다** — 적어 둔 링크와 브라우저 기록이 깨진다."""
        w = self.ant
        r = self.c.get(f"/core/{w.site.code}/{w.locality.code}/")
        self.assertIn(r.status_code, (301, 302), r.status_code)
        self.assertEqual(r["Location"],
                         reverse("core", args=[w.site.code, w.locality.code]))

    # --- 링크를 실제로 따라간다 (DiaRUGA 057) --------------------------------------

    def test_목록의_링크를_전부_따라간다(self):
        """**목록이 200 인 것과 그 링크가 사는 것은 다른 물음이다.**

        `smoke.sh` 5번이 배포된 것에 대고 하는 일을, 여기서는 코드에 대고 한다.
        거기는 링크 하나만 따라가지만 여기서는 전부 간다 — 싸기 때문이다.
        """
        import re

        from django.urls import Resolver404, resolve

        html = self.get(reverse("index")).content.decode()
        links = set(re.findall(r'href="([^"]+)"', html))
        followed = 0
        for href in sorted(links):
            if href.startswith(("http", "mailto:", "#", "javascript:")):
                continue
            # **이 앱의 주소만 따라간다.** 화면에는 Django 밖의 링크도 있다 —
            # 미리보기 갤러리(`/ForGIA-preview/`)는 nginx 가 정적으로 내주는
            # 자리라 여기서 404 가 나는 것이 정상이다. 목록을 손으로 적어 두면
            # 링크가 하나 늘 때 시험이 틀린 말을 하므로 URLconf 에 물어본다.
            try:
                resolve(href.split("?")[0].split("#")[0])
            except Resolver404:
                continue
            r = self.c.get(href)
            self.assertIn(r.status_code, (200, 302),
                          f"목록의 링크 {href} 가 {r.status_code} 다")
            followed += 1
        # 링크를 하나도 안 따라갔으면 이 시험은 아무것도 안 본 것이다.
        # 057 의 목록도 "링크가 없는 200" 이 될 수 있었다.
        self.assertGreater(followed, 0, "목록에 따라갈 링크가 하나도 없다")

    # --- 이미지 -----------------------------------------------------------

    def test_이미지와_크롭이_나온다(self):
        det = self.ant.detection()
        r = self.get(f"{reverse('image')}?p={det.image_path}")
        self.assertTrue(r["Content-Type"].startswith("image/"), r["Content-Type"])

        # `/crop` 은 마스크 키가 아니라 bbox 를 받는다 — 화면이 이미 알고 있는
        # 값이라 서버가 다시 찾지 않는다.
        cand = det.candidates.filter(passed=True).first()
        b = ",".join(str(v) for v in cand.bbox_xywh)
        r = self.c.get(f"{reverse('crop')}?p={det.image_path}&b={b}")
        # **`r.content` 를 쓰지 않는다** — 이미지 뷰는 `FileResponse` 라 거기에
        # `content` 가 없다. 실패 메시지에 적었다가 그 줄이 시험을 죽였다.
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r["Content-Type"].startswith("image/"), r["Content-Type"])

    def test_망가진_bbox_는_400_이다(self):
        det = self.ant.detection()
        for b in ("", "1,2", "1,2,0,0", "a,b,c,d", "1,2,-5,-5"):
            with self.subTest(b=b):
                r = self.c.get(f"{reverse('crop')}?p={det.image_path}&b={b}")
                self.assertEqual(r.status_code, 400, f"b={b!r} 이 통과했다")

    def test_DATA_ROOT_밖은_안_내준다(self):
        """이미지 서빙은 허용된 뿌리 안으로만 (settings.IMAGE_DIRS)."""
        for p in ("../../etc/passwd", "/etc/passwd", "photos/../../etc/passwd"):
            with self.subTest(p=p):
                r = self.c.get(f"{reverse('image')}?p={p}")
                self.assertNotEqual(r.status_code, 200, f"{p} 를 내줬다")


class EmptyDatabaseTest(ForGIATestCase):
    """**자료가 하나도 없어도 화면이 살아야 한다.**

    새 체크아웃·빈 마운트에서 500 이 나면 무엇이 잘못됐는지 알 수 없는 상태로
    시작한다. `/healthz` 는 이때 `unhealthy` 가 **맞다**(빈 DB 함정) — 화면과
    건강 상태의 답이 다른 것이 정상이라는 것까지 못 박아 둔다.
    """

    def test_빈_DB_에서도_목록이_그려진다(self):
        fx.make_classes()
        r = Client().get(reverse("index"))
        self.assertEqual(r.status_code, 200)

    def test_빈_DB_는_healthz_가_unhealthy_다(self):
        r = Client().get(reverse("healthz"))
        self.assertEqual(r.status_code, 503, r.content[:200])
        self.assertEqual(r.json()["status"], "unhealthy")


class NoEngineScreenTest(ForGIATestCase):
    """`/engine/` 을 지웠다 (DiaRUGA 075) — **남은 길이 없어야 한다.**

    `reverse()` 로 지운 이름을 부르는 자리가 하나라도 남으면 그 화면이 통째로
    500 이 된다. 들어오는 값이 404 가 되는 것과 **나가는 값을 못 만드는 것**은
    고장의 크기가 다르다 (DiaRUGA 057).
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        # **시야가 둘이어야 한다.** `link` 는 이웃 시야의 주소를 만드는 함수라
        # 시야가 하나면 한 번도 안 불린다 — 지운 이름을 부르는 코드를 그대로
        # 두고도 시험이 통과했다.
        cls.w = fx.make_world(slug="rs23", n_viewpoints=2, n_candidates=2)
        cls.other = fx.add_other_engine(cls.w.vp)

    def test_이름이_사라졌다(self):
        for name in ("engine_index", "engine_run", "engine_view"):
            with self.subTest(name=name), self.assertRaises(NoReverseMatch):
                reverse(name, args=[1, "rs23", 0][:3])

    def test_주소가_404_다(self):
        c = Client()
        for path in ("/engine/", f"/engine/{self.other.pk}/",
                     f"/engine/{self.other.pk}/rs23/0/"):
            with self.subTest(path=path):
                self.assertEqual(c.get(path).status_code, 404)

    def test_목록에_링크가_없다(self):
        body = Client().get(reverse("index")).content.decode()
        self.assertNotIn("엔진 비교", body)

    def test_비교_화면은_그대로_돈다(self):
        """지운 것은 별도 화면이고, **라디오로 하는 비교는 남는다.**"""
        c = Client()
        url = reverse("group", args=["rs23", self.w.vp.idx])
        r = c.get(url + f"?batch={self.other.pk}")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("Read only", body)
        # 옆 시야로 가는 길이 `?batch=` 를 들고 간다 — 지운 이름을 안 부른다
        d = data.group_detail("rs23", self.w.vp.idx, run_id=self.other.pk)
        for key in ("prev_url", "next_url"):
            if d.get(key):
                self.assertIn("?batch=", d[key])

    def test_링크를_안_주면_검토_화면으로_만든다(self):
        """`engine_viewpoint` 의 기본 `link` — 여기가 지운 이름을 부르고 있었다.

        `group_detail` 은 늘 `link` 를 주므로 이 갈래는 아무도 안 지난다.
        **그래서 더 위험하다** — 다음에 부르는 사람이 500 을 만난다.
        """
        d = data.engine_viewpoint("rs23", self.w.vp.idx, self.other.pk)
        self.assertIsNotNone(d)
        self.assertTrue(d.get("next_url") or d.get("prev_url"),
                        "이웃 시야가 없어 link 가 한 번도 안 불렸다")
        for key in ("prev_url", "next_url"):
            if d.get(key):
                self.assertIn("?batch=", d[key])
