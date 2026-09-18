"""시스템 설정 · 파이프라인 탭 (098).

097(사흘) · 026(4시간 반) — 폴러가 죽어도 알려 주는 자리가 없어 늦었다.
이 화면이 그 자리다. 여기서 지키는 것:

1. **끝나지 않은 슬라이드가 상태·진행과 함께 나온다** — pending 이 "사진만 온
   상태" 로 읽히게 (097 의 증상 그대로)
2. **정찰이 오래됐으면 경고가 뜬다** — 사람이 숫자를 조합해 알아내게 두지 않는다
3. **밀린 것이 있는데 도는 실행이 없으면 경고가 뜬다** — 097 의 그 모양
4. 정찰 파일이 없으면(개발 서버) 그렇게 말하고 죽지 않는다
"""
import json
import os
import re
import time
from pathlib import Path

from django.conf import settings
from django.test import Client
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from ..models import Run, RunBatch, Slide


def write_scan(age_min=0.5, slides=()):
    """시험 DATA_ROOT 안에 정찰 파일을 만든다 — 운영 자리를 안 건드린다."""
    d = Path(settings.DATA_ROOT) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "last_scan.json"
    p.write_text(json.dumps({"slides": list(slides)}), encoding="utf-8")
    t = time.time() - age_min * 60
    os.utime(p, (t, t))
    return p


class PipelineTabTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)

    def setUp(self):
        self.c = Client()
        # 앞 시험이 쓴 정찰 파일을 치운다 — 파일 유무가 시험 조건이라
        # 남아 있으면 "없어도 죽지 않는다" 시험이 있는 파일을 본다.
        p = Path(settings.DATA_ROOT) / "logs" / "last_scan.json"
        if p.exists():
            p.unlink()

    def get(self):
        r = self.c.get(reverse("system_settings_pipeline"))
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_밀린_슬라이드가_상태와_진행으로_나온다(self):
        Slide.objects.create(
            name="RS23-GC03 999cm (7)", slug="s999", image_dir="photos/x/y",
            state="pending", state_note="그룹핑 대기")
        write_scan()
        html = self.get()
        self.assertIn("RS23-GC03 999cm (7)", html)
        self.assertIn("pending", html)
        self.assertIn("그룹핑 대기", html)

    def test_정찰이_오래되면_경고가_뜬다(self):
        write_scan(age_min=30)
        html = self.get()
        self.assertIn("폴러가 멈춰", html)

    def test_정찰이_신선하면_경고가_없다(self):
        write_scan(age_min=0.5)
        html = self.get()
        self.assertNotIn("폴러가 멈춰", html)

    def test_밀린_것이_있는데_실행이_없으면_경고가_뜬다(self):
        """097 의 모양 — pending 이 있는데 아무도 데리러 오지 않는다."""
        Slide.objects.create(name="x", slug="s1", image_dir="p/x",
                             state="pending", state_note="그룹핑 대기")
        write_scan(age_min=0.5)     # 정찰은 신선하다 — 097 이 정확히 이랬다
        html = self.get()
        self.assertIn("데리러 오지 않는", html)

    def test_도는_실행이_있으면_그_경고는_없다(self):
        Slide.objects.create(name="x", slug="s1", image_dir="p/x",
                             state="processing", state_note="")
        Run.objects.create(kind="stack", slide=Slide.objects.get(slug="s1"),
                           status="running")
        write_scan(age_min=0.5)
        html = self.get()
        self.assertNotIn("데리러 오지 않는", html)
        self.assertIn("도는 중", html)

    def test_정찰_파일이_없어도_죽지_않는다(self):
        html = self.get()
        self.assertIn("정찰 기록이 없다", html)

    def test_failed_는_사람이_볼_것으로_나온다(self):
        Slide.objects.create(name="y", slug="s2", image_dir="p/y",
                             state="failed", state_note="그룹핑 확인 필요")
        write_scan()
        html = self.get()
        self.assertIn("사람이 볼 것", html)
        self.assertIn("그룹핑 확인 필요", html)

    def test_새_NAS_폴더가_반입_대기로_나온다(self):
        write_scan(slides=[{"rel": "260811/NEW-1cm", "state": "new",
                            "jpgs": 42, "stable_min": 2.0}])
        html = self.get()
        self.assertIn("260811/NEW-1cm", html)
        self.assertIn("반입 대기", html)

    def test_긴_작업이_도는_중이면_정찰_경고를_접는다(self):
        """폴러는 flock 하나다 — 합성·검출(몇 시간)이 도는 동안 정찰이 늙는
        것은 정상이다. 실제로 이 화면을 처음 띄운 날 합성 도중이라 거짓
        경고부터 냈다."""
        Run.objects.create(kind="stack", slide=self.w.slide, status="running")
        write_scan(age_min=30)
        html = self.get()
        self.assertNotIn("폴러가 멈춰", html)
