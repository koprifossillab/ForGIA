"""반입 넷이 끝까지 돈다 — 그룹핑 → 합성 → `Viewpoint`·`Frame`·`Stack`·`Image`.

합성 사진(P01 5.1)의 한 시야를 흐림을 달리해 세 장으로 만들어 초점 시리즈를
흉내 낸다. **`cv2` 가 없으면 건너뛴다** — 뷰어 시험은 Django 와 PIL 만 쓴다는
경계를 지킨다 (CI 의 web 러너에는 cv2 가 없다).
"""
import importlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from PIL import Image, ImageFilter

from ..models import Frame, Image as ImageRow, Run, Slide, Stack, Viewpoint
from .base import ForGIATestCase, assert_sandboxed_root

try:
    import cv2  # noqa: F401
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


def _synth(path: Path, seed: int, blur: float, exif_um: float | None):
    """합성 사진 하나 — 바탕 + 원 하나. 자리는 `seed` 가 정한다 (시야가 갈리게)."""
    from PIL import ImageDraw
    im = Image.new("RGB", (256, 192), (210, 210, 205))
    d = ImageDraw.Draw(im)
    x, y = 60 + seed * 50, 70 + (seed % 2) * 40
    d.ellipse([x, y, x + 34, y + 30], fill=(110, 100, 90), outline=(60, 55, 50))
    for k in range(6):                       # 구멍 무늬 — 선명도가 갈리게
        d.ellipse([x + 5 + k * 4, y + 8, x + 7 + k * 4, y + 10], fill=(40, 40, 40))
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    if exif_um is None:
        im.save(path, quality=92)
    else:
        ex = Image.Exif(); ex[0x010E] = json.dumps({"microscope": {"um_per_pixel": exif_um}})
        im.save(path, quality=92, exif=ex.tobytes())


@unittest.skipUnless(HAS_CV2, "cv2 가 없다 — 파이프라인 시험은 호스트 venv 에서 돈다")
class IngestPipelineTest(ForGIATestCase):
    @classmethod
    def setUpTestData(cls):
        assert_sandboxed_root()
        cls.folder = Path(settings.DATA_ROOT) / "photos" / "260918" / "RS23-GC03 71cm >125um (1)"
        cls.folder.mkdir(parents=True)
        n = 0
        for fi in range(2):
            for blur in (2.5, 0.0, 2.5):
                n += 1
                _synth(cls.folder / f"Snap-{n:05d}.jpg", fi, blur,
                       exif_um=(1.5625 if fi == 0 else None))
        (cls.folder / "scale.toml").write_text("um_per_pixel = 3.125\n", encoding="utf-8")

    def _mod(self, name):
        sys.path.insert(0, str(Path(settings.BASE_DIR).parent / "pipeline"))
        return importlib.import_module(name)

    def test_group_then_stack(self):
        g = self._mod("group_focus_series")
        # main() 은 argparse 라 함수들을 직접 엮는다 — 시험이 셸 인자를 흉내 내지 않게
        files = sorted(p for p in self.folder.iterdir() if p.suffix == ".jpg")
        fps = [g.fingerprint(f, 2.0) for f in files]
        corrs = [g.ncc(fps[i], fps[i + 1]) for i in range(len(files) - 1)]
        groups, cur = [], [0]
        for i, c in enumerate(corrs):
            if c < 0.8:
                groups.append(cur); cur = []
            cur.append(i + 1)
        groups.append(cur)
        self.assertEqual([len(x) for x in groups], [3, 3], corrs)
        sharps = {f.stem: round(g.sharpness(f), 1) for f in files}
        times = [g.read_timestamp(f) for f in files]
        sep = g.separability(corrs, groups, 0.8)
        run = Run.objects.create(kind="group", status="running")
        slide = g.save_grouping(self.folder, files, groups, sharps, times,
                                SimpleNamespace(corr_thresh=0.8), sep, run)

        # 층·분획·관찰 번호가 폴더 이름에서 왔다
        self.assertEqual(str(slide.sample), "RS23-GC03-71cm")
        self.assertEqual((slide.fraction_um, slide.obs_no), (125.0, 1))
        # 시야 = 격자 칸, 촬영 순서대로 1 부터
        vps = list(Viewpoint.objects.filter(slide=slide).order_by("idx"))
        self.assertEqual([v.cell for v in vps], [1, 2])
        # 가장 선명한 장(흐림 0)이 대표다
        self.assertEqual([v.sharpest_frame.name for v in vps], ["Snap-00002", "Snap-00005"])
        # 스케일 출처 — 첫 시야는 EXIF, 둘째는 폴더의 toml
        src = {f.name: (f.um_per_pixel, f.um_per_pixel_source)
               for f in Frame.objects.filter(slide=slide)}
        self.assertEqual(src["Snap-00001"], (1.5625, "exif"))
        self.assertEqual(src["Snap-00004"], (3.125, "toml"))
        self.assertEqual(ImageRow.objects.filter(kind="frame").count(), 6)

        # 합성
        fs = self._mod("focus_stack")
        out_dir = Path(settings.DATA_ROOT) / "stacked"
        out_dir.mkdir()
        run2 = Run.objects.create(kind="stack", status="running", slide=slide)
        for vp in vps:
            paths = [Path(settings.DATA_ROOT) / f.path for f in vp.frames.order_by("seq")]
            r = fs.stack_group(paths, 1.0, True, True, 90.0, out_dir, vp.tag)
            fs.save_stack(vp, out_dir, r, run2)
        self.assertEqual(Stack.objects.count(), 2)
        st = {s.viewpoint.cell: s for s in Stack.objects.select_related("viewpoint")}
        self.assertEqual((st[1].um_per_pixel, st[1].um_per_pixel_source), (1.5625, "exif"))
        self.assertEqual((st[2].um_per_pixel, st[2].um_per_pixel_source), (3.125, "toml"))
        self.assertTrue((Path(settings.DATA_ROOT) / st[1].focused_path).exists())
        self.assertEqual(ImageRow.objects.filter(kind="stack").count(), 2)
        self.assertEqual(ImageRow.objects.filter(kind="depth").count(), 2)

        # 시야 목록·시야 화면이 그것을 그린다
        r = self.client.get(f"/d/{slide.slug}/")
        self.assertContains(r, "칸 1 ·")
        self.assertContains(r, "합성됨")
        r = self.client.get(f"/d/{slide.slug}/g/1/")
        self.assertContains(r, 'data-title="합성본"')
        self.assertContains(r, "3.1250 µm/px")
        self.assertContains(r, "(toml)")

    def test_regroup_refused_without_force_when_downstream_exists(self):
        """검출·교정 테이블이 아직 없어도 `--force` 갈래는 죽지 않아야 한다 —
        `hasattr` 로 0 을 센다 (2·3단계에서 그 테이블이 붙는다)."""
        g = self._mod("group_focus_series")
        Slide.objects.create(name="x", slug="x", image_dir=g.rel(self.folder))
        vp = Viewpoint.objects.filter(slide__slug="x")
        self.assertFalse(hasattr(vp, "object_reviews"))
