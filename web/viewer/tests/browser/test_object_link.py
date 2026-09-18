"""DiaRUGA v0.29.0 `tests/browser/test_object_link.py` 에서 왔다.

같은 개체 묶기 — **실제로 눌러서** 배선을 본다 (DiaRUGA P11 2단계).

3겹(endpoint 시험)은 서버 검증까지만 본다. 여기서 보는 것: 우클릭 메뉴에
항목이 나오는가 · 팝업이 뜨고 미리 고르기가 찍혀 있는가 · 저장이 DB 에
남는가 · 다시 열면 "묶음 열기" 가 되는가. 이벤트 배선 고장은 이것으로만
잡힌다 (CLAUDE.md).
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import (Candidate, Detection, Image, ForamObject,
                       ObjectReview, Run, RunBatch)


class ObjectLinkBrowserTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        # 합성본 + 프레임 3장. 검출은 합성본(팩토리)과 프레임 둘에 세운다 —
        # 캐러셀이 성립해야 묶을 상대가 있다.
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_frames=3, n_candidates=2)
        batch = RunBatch.objects.get(label="yolo-시험")
        run = Run.objects.create(kind="detect", batch=batch,
                                 slide=self.w.slide, status="done")
        self.frame_imgs = list(Image.objects.filter(
            viewpoint=self.w.vp, kind="frame").order_by("pk"))
        for img in self.frame_imgs[:2]:
            det = Detection.objects.create(
                viewpoint=self.w.vp, image=img, image_path=img.path,
                width=fx.IMG_W, height=fx.IMG_H, scale=1.0, um_per_pixel=0.1,
                run=run, is_current=True)
            # 합성본의 첫 후보(40,50,60,40)와 같은 자리 — IoU 1.0 이라
            # 미리 고르기 규칙(≥0.5 · 간격 ≥0.3)에 걸려 ✓ 가 찍혀 있어야 한다
            Candidate.objects.create(
                detection=det, raw_id=0, mask_key="40_50_60_40",
                bbox_x=40, bbox_y=50, bbox_w=60, bbox_h=40,
                center_x=70, center_y=70, area_px=1200, area_um2=6.0,
                major_um=6.0, minor_um=4.0, long_side_um=6.0,
                short_side_um=4.0, aspect_ratio=1.5, fill_ratio=0.6,
                shape_ok=True, circularity=0.8, convexity=0.9, solidity=0.9,
                elongation=1.5, ellipse_iou=0.85, texture=3000.0,
                predicted_iou=0.9, stability_score=0.9,
                polygon=[40, 50, 100, 50, 100, 90, 40, 90],
                passed=True, cls="broken")

    def open_group(self):
        return self.open(reverse("group", args=[self.w.slide.slug,
                                                self.w.vp.idx]))

    def menu_item(self, menu, text):
        for el in menu.query_selector_all("button, .mi"):
            if text in (el.inner_text() or ""):
                return el
        return None

    def test_묶고_다시_열면_묶음_열기다(self):
        page = self.open_group()
        # 합성본 첫 후보(중심 70,70) 위에서 우클릭
        menu = self.context_menu_at(70, 70)
        self.assertIsNotNone(menu, "우클릭 메뉴가 안 열렸다")
        item = self.menu_item(menu, "동일 개체 묶기")
        self.assertIsNotNone(item, "묶기 항목이 메뉴에 없다")
        item.click()
        page.wait_for_selector(".linkpanel", state="visible", timeout=3000)

        # 프레임 두 줄이 미리 골라져 있어야 한다 (IoU 1.0 · 간격 큼)
        picked = page.query_selector_all(".linkpanel .scell.on:not(.anchor)")
        self.assertEqual(len(picked), 2,
                         "미리 고르기가 안 찍혔다 (IoU 1.0 인데)")

        # 저장
        btn = page.query_selector(".linkpanel .lfoot .btn")
        self.assertIn("3장", btn.inner_text())
        btn.click()
        page.wait_for_selector(".linkpanel", state="detached", timeout=3000)

        link = fx.links().get()
        self.assertEqual(link.members.count(), 3)
        rep = link.members.get(is_rep=True)
        self.assertEqual(rep.image.kind, "stack", "기본 대표는 닻(합성본)이다")

        # **사슬 배지가 새로고침 없이 생긴다** (3단계) — 저장 성공 갈래가
        # build() 를 다시 부른다.
        self.assertIsNotNone(self.page.query_selector(".box.linked"),
                             "묶었는데 사슬 배지가 없다")

        # 같은 자리에서 다시 열면 "묶음 열기"
        menu = self.context_menu_at(70, 70)
        self.assertIsNotNone(self.menu_item(menu, "묶음 열기"),
                             "묶은 뒤에도 '묶기' 로 나온다")

    def test_없음을_고르면_그_판은_빠진다(self):
        page = self.open_group()
        menu = self.context_menu_at(70, 70)
        self.menu_item(menu, "동일 개체 묶기").click()
        page.wait_for_selector(".linkpanel", state="visible", timeout=3000)
        # 첫 프레임 줄의 (없음) 을 누른다
        page.query_selector_all(".linkpanel .lnone")[0].click()
        page.wait_for_timeout(150)
        btn = page.query_selector(".linkpanel .lfoot .btn")
        self.assertIn("2장", btn.inner_text())
        btn.click()
        page.wait_for_selector(".linkpanel", state="detached", timeout=3000)
        self.assertEqual(fx.links().get().members.count(), 2)

    def test_별로_대표를_옮긴다(self):
        page = self.open_group()
        menu = self.context_menu_at(70, 70)
        self.menu_item(menu, "동일 개체 묶기").click()
        page.wait_for_selector(".linkpanel", state="visible", timeout=3000)
        # 프레임 쪽(닻 아닌) 선택 칸의 별을 누른다
        star = page.query_selector(
            ".linkpanel .scell.on:not(.anchor) .lstar")
        self.assertIsNotNone(star, "선택 칸에 별이 없다")
        star.click()
        page.wait_for_timeout(150)
        page.query_selector(".linkpanel .lfoot .btn").click()
        page.wait_for_selector(".linkpanel", state="detached", timeout=3000)
        rep = fx.links().get().members.get(is_rep=True)
        self.assertEqual(rep.image.kind, "frame", "별을 옮겼는데 대표가 닻이다")

    def test_판이_하나면_메뉴에_항목이_없다(self):
        """묶을 상대가 없는 화면 — 죽은 항목을 내보이지 않는다."""
        w2 = fx.make_world(slug=f"solo-{self.uniq}", site_code=f"SO{self.uniq}",
                           n_frames=1, n_candidates=2)
        self.open(reverse("group", args=[w2.slide.slug, w2.vp.idx]))
        menu = self.context_menu_at(70, 70)
        self.assertIsNotNone(menu)
        self.assertIsNone(self.menu_item(menu, "동일 개체"),
                          "판이 하나인데 묶기 항목이 나온다")

    def test_저장이_한_번_미끄러진_그린_마스크도_묶인다(self):
        """실사용 g25 의 사고 (2026-08-10). 마스크를 그렸는데 /review 저장이
        한 번 실패하면 — 옛 코드는 `savePending` 을 이미 꺼 둔 채라 다시
        시도하지 않았고, 그 마스크는 **화면에는 있는데 DB 에는 영영 없었다.**
        묶기 POST 는 모르는 열쇠라며 "이 화면의 마스크가 아니다" 로 거절했다.

        고침 둘을 함께 본다: 실패가 `savePending` 을 되살리는 것, 그리고 묶기
        팝업이 열릴 때 그 밀린 저장을 먼저 밀어내는 것.
        """
        page = self.open_group()

        # /review 를 한 번만 떨어뜨린다 — 연결이 순간 끊긴 상황
        state = {"failed": 0}
        def wreck(route):
            if state["failed"] == 0:
                state["failed"] += 1
                route.abort()
            else:
                route.continue_()
        page.route("**/review", wreck)

        # 빈 자리에 마스크를 그린다 (draw 도구 → 점 셋 → 첫 점으로 닫기)
        page.click('#dv-stack button[data-act="draw"]')
        page.wait_for_timeout(150)
        for x, y in ((400, 300), (470, 300), (470, 360)):
            self.click_image(x, y)
        self.click_image(400, 300)
        page.wait_for_timeout(900)          # 지연 저장이 나가고 — 떨어진다
        self.assertEqual(state["failed"], 1, "저장 실패를 만들지 못했다")

        # 그린 마스크(중심께)에서 우클릭 → 묶기 → 프레임 후보는 없을 테니
        # 합성본 근처의 엔진 후보 하나를 프레임에서 고르는 대신, 그냥
        # 미리 골라진 것 없이 닻+없음이면 저장이 안 되므로 —
        # 엔진 후보(70,70) 위에서 여는 편이 확실하다: 닻은 엔진 마스크,
        # 다른 판 후보가 미리 골라져 있고, **팝업 열기가 밀린 저장을 먼저
        # 밀어낸다** 는 것이 이 시험의 요점이다.
        menu = self.context_menu_at(70, 70)
        self.menu_item(menu, "동일 개체 묶기").click()
        page.wait_for_selector(".linkpanel", state="visible", timeout=3000)
        page.wait_for_timeout(600)          # saveGate(재시도 저장)가 돌 시간
        page.query_selector(".linkpanel .lfoot .btn").click()
        page.wait_for_selector(".linkpanel", state="detached", timeout=3000)

        # 묶음이 섰고 — 그린 마스크도 (재시도 덕에) DB 에 있다
        #
        # **그린 마스크는 그 자체로 묶음을 하나 더 세운다** (106 2단계).
        # 같은 시야의 판마다 복제되면서 한 개체를 나눠 갖기 때문이다 — 이 시야는
        # 프레임 둘에 검출이 있어 멤버가 셋이 된다. 여기서 세려는 것은 **사람이
        # 팝업으로 만든 묶음**이라 엔진 쪽만 본다.
        made = [l for l in fx.links()
                if not l.members.filter(batch__isnull=True).exists()]
        self.assertEqual(len(made), 1, "팝업으로 만든 묶음이 하나여야 한다")
        drawn = ObjectReview.objects.filter(batch__isnull=True,
                                            source="manual")
        self.assertTrue(drawn.filter(image=self.w.detection().image).exists(),
                        "실패했던 저장이 재시도되지 않았다 — 그린 마스크가 DB 에 없다")

        # **일부러 떨어뜨린 요청의 콘솔 오류는 이 시험의 조건이지 고장이 아니다.**
        # 그 한 줄만 걸러 낸다 — 통째로 비우면 진짜 JS 오류까지 덮는다.
        self.errors = [e for e in self.errors
                       if "ERR_FAILED" not in e or "/review" not in e
                       if not e.startswith("console.error: Failed to load resource")]

    def test_탈락_후보가_팝업에_빨간_점선으로_나온다(self):
        """102 · 사용자 요청. **미리 고르기는 탈락을 안 집는다** — 자동으로
        골라 두면 사람이 확인하지 않은 채 되살아난다."""
        from ...models import Candidate
        # 프레임 판의 닻 자리에 탈락 후보 하나를 세운다
        det = Detection.objects.filter(image=self.frame_imgs[0],
                                       is_current=True).first()
        Candidate.objects.create(
            detection=det, raw_id=9, mask_key="44_54_58_38",
            bbox_x=44, bbox_y=54, bbox_w=58, bbox_h=38,
            center_x=73, center_y=73, area_px=1100, area_um2=5.5,
            major_um=5.8, minor_um=3.8, long_side_um=5.8, short_side_um=3.8,
            aspect_ratio=1.5, fill_ratio=0.6, shape_ok=True, circularity=0.8,
            convexity=0.9, solidity=0.9, elongation=1.5, ellipse_iou=0.8,
            texture=90.0, predicted_iou=0.9, stability_score=0.9,
            polygon=[44, 54, 102, 54, 102, 92, 44, 92],
            passed=False, reject="텍스처부족")

        page = self.open_group()
        menu = self.context_menu_at(70, 70)
        self.menu_item(menu, "동일 개체 묶기").click()
        page.wait_for_selector(".linkpanel", state="visible", timeout=3000)

        rej = page.query_selector_all(".linkpanel .scell.rej")
        self.assertTrue(rej, "탈락 후보가 팝업에 안 나온다")
        # 점선·빨강이 실제로 먹는가 (CSS 가 조각 안에 있으면 안 먹는다)
        st = rej[0].evaluate("""e => {
            const s = getComputedStyle(e);
            return [s.borderTopStyle, s.borderTopColor];
        }""")
        self.assertEqual(st[0], "dashed", "탈락 표시가 점선이 아니다")
        # 자동으로 골라져 있으면 안 된다
        self.assertNotIn("on", rej[0].get_attribute("class").split(),
                         "탈락 후보가 미리 골라져 있다 — 확인 없이 되살아난다")
        self.assertIsNotNone(page.query_selector(".linkpanel .ltag"),
                             "'탈락' 딱지가 없다")

    def test_탈락_후보를_골라도_칸이_늘지_않는다(self):
        """실사용 보고 (2026-08-10): "탈락 후보를 선택하면 옆에 동일한 탈락
        후보가 하나 더 나타나."

        `nearOf` 가 그릴 때마다 탈락 후보의 **사본**을 새로 만들어서, 고른
        것(옛 사본)이 새 목록에 없다고 판단해 앞에 하나 더 붙였다. 고르기
        상태가 바뀌면 다시 그리는 화면이라 **누르는 순간** 드러난다.
        """
        from ...models import Candidate
        det = Detection.objects.filter(image=self.frame_imgs[0],
                                       is_current=True).first()
        Candidate.objects.create(
            detection=det, raw_id=9, mask_key="44_54_58_38",
            bbox_x=44, bbox_y=54, bbox_w=58, bbox_h=38,
            center_x=73, center_y=73, area_px=1100, area_um2=5.5,
            major_um=5.8, minor_um=3.8, long_side_um=5.8, short_side_um=3.8,
            aspect_ratio=1.5, fill_ratio=0.6, shape_ok=True, circularity=0.8,
            convexity=0.9, solidity=0.9, elongation=1.5, ellipse_iou=0.8,
            texture=90.0, predicted_iou=0.9, stability_score=0.9,
            polygon=[44, 54, 102, 54, 102, 92, 44, 92],
            passed=False, reject="텍스처부족")

        page = self.open_group()
        menu = self.context_menu_at(70, 70)
        self.menu_item(menu, "동일 개체 묶기").click()
        page.wait_for_selector(".linkpanel", state="visible", timeout=3000)

        # 그 탈락 후보가 있는 줄의 칸 수를 센다
        row = page.query_selector(".linkpanel .scell.rej").evaluate_handle(
            "e => e.closest('.lrow')").as_element()
        before = len(row.query_selector_all(".scell"))

        page.query_selector(".linkpanel .scell.rej").click()
        page.wait_for_timeout(200)

        # 다시 그린 뒤에도 같은 줄의 칸 수가 그대로여야 한다
        row2 = page.query_selector(".linkpanel .scell.rej").evaluate_handle(
            "e => e.closest('.lrow')").as_element()
        after = len(row2.query_selector_all(".scell"))
        self.assertEqual(after, before,
                         f"고르니 칸이 {before} → {after} 로 늘었다 "
                         "— 같은 탈락 후보가 둘로 보인다")
        # 그리고 골라진 것은 하나다
        self.assertEqual(len(row2.query_selector_all(".scell.on")), 1)

    def test_되살린_탈락분이_판을_넘겨도_보인다(self):
        """실사용 보고 (2026-08-10): "묶기 완료한 시점에서 페이지에서는
        탈락했던 게 되살아났다는 정보가 없고, 프레임을 바꾸면 그 검출이
        여전히 안 보인다."

        서버는 `accepted` 를 세웠는데 화면의 `shotState` 가 그것을 몰라서
        **새로고침해야 맞게 나오는** 상태였다. 저장 성공 갈래가 탈락 펼침판과
        같은 규칙으로 상태를 옮겨야 한다.
        """
        from ...models import Candidate, ObjectReview
        det = Detection.objects.filter(image=self.frame_imgs[0],
                                       is_current=True).first()
        Candidate.objects.create(
            detection=det, raw_id=9, mask_key="44_54_58_38",
            bbox_x=44, bbox_y=54, bbox_w=58, bbox_h=38,
            center_x=73, center_y=73, area_px=1100, area_um2=5.5,
            major_um=5.8, minor_um=3.8, long_side_um=5.8, short_side_um=3.8,
            aspect_ratio=1.5, fill_ratio=0.6, shape_ok=True, circularity=0.8,
            convexity=0.9, solidity=0.9, elongation=1.5, ellipse_iou=0.8,
            texture=90.0, predicted_iou=0.9, stability_score=0.9,
            polygon=[44, 54, 102, 54, 102, 92, 44, 92],
            passed=False, reject="텍스처부족")

        page = self.open_group()
        menu = self.context_menu_at(70, 70)
        self.menu_item(menu, "동일 개체 묶기").click()
        page.wait_for_selector(".linkpanel", state="visible", timeout=3000)
        page.query_selector(".linkpanel .scell.rej").click()
        page.wait_for_timeout(150)
        page.query_selector(".linkpanel .lfoot .btn").click()
        page.wait_for_selector(".linkpanel", state="detached", timeout=3000)

        # 서버는 되살렸다
        self.assertTrue(
            ObjectReview.objects.filter(image=self.frame_imgs[0],
                                        mask_key="44_54_58_38",
                                        accepted=True).exists())

        # **새로고침 없이** 그 프레임으로 넘어가면 개체로 보여야 한다
        shot = page.query_selector(
            f'.shot[data-detkey="{self.frame_imgs[0].frame.name}"]')
        self.assertIsNotNone(shot, "캐러셀에 그 프레임이 없다")
        shot.click()
        page.wait_for_timeout(500)

        # 되살린 것은 개체 상자로 생긴다. **자리는 백분율이다** — 화소로 비교하면
        # 늘 어긋난다(그렇게 짰다가 고쳤다).
        boxes = page.evaluate(
            """() => [...document.querySelectorAll('.detview .box')]
                 .map(e => e.style.left + '/' + e.style.top)""")
        self.assertTrue(boxes, "그 판에 개체가 하나도 없다")
        want = f"{44 / fx.IMG_W * 100:.4g}%"
        self.assertTrue(
            any(b.startswith(want) for b in boxes),
            f"되살린 개체({want})가 안 보인다 — 새로고침해야 나오는 "
            f"상태다: {boxes}")
