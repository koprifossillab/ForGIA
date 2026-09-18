"""DiaRUGA v0.29.0 `tests/browser/test_hover_and_overlay.py` 에서 왔다.

커서와 클릭이 **같은 개체를 가리키는가**, 그리고 합성본 마스크 겹쳐 보기 (072).

## 커서와 클릭

예전에는 판정이 둘이었다.

| | 무엇으로 골랐나 |
|---|---|
| 커서 | 상자마다 `mouseenter` — **DOM 에서 맨 위에 있는 상자** |
| 클릭 | `topHitAt` — **마스크 안에 든 것 중 가장 작은 것** |

서버가 개체를 면적 내림차순으로 내려 주는 동안에만 우연히 같았다. 겹친 자리에서
어긋났고(운영 자료 실측 2~4%), **말풍선이 A 를 설명하는데 클릭은 B 를 고른다.**
말풍선의 크기·분류를 보고 판단한 뒤 누르면 **다른 개체에 그 분류가 앉는다.**

여기서 만드는 자료가 그 어긋남 그대로다 — 작은 개체의 **상자**가 큰 개체를
덮는데 그 **마스크**는 커서 자리를 안 덮는다. 세모난 개체에서 늘 나는 모양이다.

## 합성본 마스크 겹쳐 보기

`yolo-시험` 는 시야마다 검출이 하나이고 그것이 합성본에 붙는다. 프레임은 같은
시야를 다른 초점면에서 본 것이라 마스크가 그대로 맞는데, 판을 넘기면 **"이 판에는
검출이 없습니다" 라며 비웠다.** 엔진이 못 본 것으로 읽힌다.
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ... import data
from ...models import Candidate, Detection


def _cand(det, raw_id, bbox, polygon, area, cls="broken"):
    x, y, w, h = bbox
    return Candidate.objects.create(
        detection=det, raw_id=raw_id,
        mask_key=data.cand_key({"bbox_xywh": list(bbox)}),
        bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h,
        center_x=x + w // 2, center_y=y + h // 2,
        area_px=area, area_um2=area / 100.0,
        major_um=float(w) / 10, minor_um=float(h) / 10,
        long_side_um=float(w) / 10, short_side_um=float(h) / 10,
        aspect_ratio=w / h, fill_ratio=0.6, shape_ok=True,
        circularity=0.8, convexity=0.9, solidity=0.9, elongation=1.2,
        ellipse_iou=0.86, texture=2800.0,
        polygon=list(polygon), passed=True, cls=cls)


class HoverMatchesClickTest(BrowserTestCase):
    """겹친 자리에서 커서와 클릭이 같은 개체를 가리킨다."""

    # 큰 개체 — 네모 마스크가 이 점을 덮는다
    BIG = (40, 40, 400, 400)
    # 작은 개체 — **상자**는 이 점을 품지만 **마스크**(왼쪽 위 세모)는 안 덮는다
    SMALL = (150, 150, 120, 120)
    POINT = (250, 250)          # 작은 것의 상자 안 · 마스크 밖 · 큰 것의 마스크 안

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=2)
        det = Detection.objects.get(viewpoint=self.w.vp, is_current=True)
        det.candidates.all().delete()

        bx, by, bw, bh = self.BIG
        _cand(det, 0, self.BIG,
              [bx, by, bx + bw, by, bx + bw, by + bh, bx, by + bh],
              area=bw * bh)
        sx, sy, sw, sh = self.SMALL
        # 왼쪽 위 세모 — 오른쪽 아래 구석(POINT)은 마스크 밖이다
        _cand(det, 1, self.SMALL, [sx, sy, sx + sw, sy, sx, sy + sh],
              area=sw * sh // 2, cls="foram")

    def open_view(self, mask_only=True):
        """**마스크만 보는 상태로 연다.** 판정이 마스크로 바뀌는 자리가 여기다 —
        상자를 켜 둔 채로는 둘 다 상자로 재서 어긋남이 안 드러난다. 마스크를
        고치며 검토할 때 실제로 쓰는 화면이기도 하다."""
        page = self.open(reverse("group", args=[self.w.slide.slug,
                                                self.w.vp.idx]))
        page.wait_for_selector(".detview .box")
        if mask_only:
            page.click('#layers-stack input[data-layer="box"]')
            page.wait_for_timeout(80)
            self.assertTrue(page.evaluate(
                "() => document.querySelector('#dv-stack').classList.contains('hide-box')"),
                "상자를 껐는데 화면이 마스크 모드가 아니다")
        return page

    def hot(self):
        el = self.page.query_selector(".detview .box.hot")
        return el.get_attribute("data-i") if el else None

    def test_자료가_정말_어긋난_모양이다(self):
        """**전제부터 확인한다.** 작은 것이 나중에(위에) 그려져야 한다."""
        page = self.open_view()
        order = [b.get_attribute("data-i")
                 for b in page.query_selector_all(".detview .box")]
        self.assertEqual(order, ["0", "1"], "면적 내림차순이 아니다")
        # 큰 것이 0 (먼저·아래), 작은 것이 1 (나중·위)
        rects = page.evaluate("""() => [...document.querySelectorAll('.detview .box')]
            .map(e => e.getBoundingClientRect()).map(r => [r.width, r.height])""")
        self.assertGreater(rects[0][0], rects[1][0])

    def test_커서와_클릭이_같은_개체를_가리킨다(self):
        """**여기가 고장 났던 자리다.** 커서는 작은 것의 상자에 잡혀 아무것도
        못 띄웠고, 클릭은 큰 것을 골랐다."""
        page = self.open_view()
        x, y = self.image_point(*self.POINT)
        page.mouse.move(x, y)
        page.wait_for_timeout(120)
        hot = self.hot()
        self.assertIsNotNone(hot, "커서를 올렸는데 아무 말풍선도 안 뜬다")

        page.keyboard.down("Shift")
        page.mouse.click(x, y)
        page.keyboard.up("Shift")
        page.wait_for_timeout(120)
        sel = [e.get_attribute("data-i")
               for e in page.query_selector_all(".detview .box.sel")]
        self.assertEqual(sel, [hot],
                         f"커서는 {hot} 을 가리키는데 클릭은 {sel} 을 골랐다")
        self.assertEqual(hot, "0", "마스크가 덮는 개체가 아니라 상자로 골랐다")

    def test_말풍선의_내용이_그_개체의_것이다(self):
        """`.hot` 만 맞고 글이 다른 개체 것이면 사람은 여전히 오독한다."""
        page = self.open_view()
        x, y = self.image_point(*self.POINT)
        page.mouse.move(x, y)
        page.wait_for_timeout(120)
        self.assertTrue(page.is_visible(".dettip"))
        txt = page.inner_text(".dettip")
        self.assertIn("파손", txt, f"큰 개체(파손)의 말풍선이 아니다: {txt[:80]}")

    def test_빈_자리에서는_안_뜬다(self):
        page = self.open_view()
        x, y = self.image_point(1200, 900)
        page.mouse.move(x, y)
        page.wait_for_timeout(120)
        self.assertIsNone(self.hot())
        self.assertFalse(page.is_visible(".dettip"))


class StackMaskOverlayTest(BrowserTestCase):
    """합성본에만 검출이 있는 묶음을 볼 때, 프레임에도 그 마스크가 남는다."""

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_frames=3, n_candidates=3)
        # 검토 대상은 프레임까지 있는 묶음(YOLO 모양), 비교로 보는 것이 합성본만
        # 있는 묶음(SAM 모양) — 실제 화면이 그 조합이다
        self.sam_run = Detection.objects.get(
            viewpoint=self.w.vp, is_current=True).run
        self.yolo = fx.add_other_engine(self.w.vp, label=f"yolo-{self.uniq}",
                                        frames=True)
        Detection.objects.filter(run=self.yolo).update(is_current=True)
        from ...models import RunBatch
        RunBatch.objects.filter(pk=self.sam_run.batch_id).update(for_review=False)
        RunBatch.objects.filter(pk=self.yolo.batch_id).update(for_review=True)

    def open_sam(self):
        page = self.open(reverse("group", args=[self.w.slide.slug,
                                                self.w.vp.idx])
                         + f"?batch={self.sam_run.pk}")
        page.wait_for_selector(".detview .box")
        return page

    def counts(self):
        return self.page.evaluate("""() => ({
            box: document.querySelectorAll('.detview .box').length,
            meta: ((document.querySelector('#tabs-stack .tabmeta')||{}).textContent||'').trim()
        })""")

    def test_프레임으로_넘겨도_마스크가_남는다(self):
        page = self.open_sam()
        first = self.counts()["box"]
        self.assertGreater(first, 0, "합성본 판에 마스크가 없다")

        moved = 0
        for s in page.query_selector_all("#strip-stack .shot"):
            title = s.get_attribute("data-title") or ""
            if title in ("합성본", "깊이 맵"):
                continue
            s.scroll_into_view_if_needed()
            s.click()
            page.wait_for_timeout(250)
            got = self.counts()
            moved += 1
            self.assertEqual(got["box"], first,
                             f"{title} 에서 마스크가 사라졌다")
            self.assertIn("겹쳐 봅니다", got["meta"],
                          f"{title} 에서 겹쳐 보는 중이라고 안 적는다")
        self.assertGreater(moved, 0, "넘겨 볼 프레임이 없었다")

    def test_판마다_검출이_있으면_안_겹친다(self):
        """**YOLO 에서는 하면 안 된다.** 그 프레임에 없는 것이 실제로 없는
        것이고, 합성본 것을 얹으면 그 자리에 없는 개체를 있다고 읽는다."""
        page = self.open(reverse("group", args=[self.w.slide.slug,
                                                self.w.vp.idx]))
        page.wait_for_selector(".detview .box")
        seen = set()
        for s in page.query_selector_all("#strip-stack .shot"):
            if (s.get_attribute("data-title") or "") in ("합성본", "깊이 맵"):
                continue
            s.scroll_into_view_if_needed()
            s.click()
            page.wait_for_timeout(250)
            meta = self.counts()["meta"]
            self.assertNotIn("겹쳐 봅니다", meta, "판마다 검출이 있는데 겹쳤다")
            keys = page.evaluate(
                """() => [...document.querySelectorAll('.detview .box')]
                     .map(e => e.style.left + ',' + e.style.top).join('|')""")
            seen.add(keys)
        self.assertGreater(len(seen), 1, "판을 바꿔도 같은 마스크만 보인다")
