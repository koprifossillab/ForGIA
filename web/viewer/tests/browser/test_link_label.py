"""DiaRUGA v0.29.0 `tests/browser/test_link_label.py` 에서 왔다.

묶인 개체는 분류를 함께 받는다 — 화면 쪽 (사용자 요청 2026-08-10).

서버가 번지게 하는 것은 `test_object_link.LinkLabelSpreadTest` 가 본다. 여기서
보는 것은 **화면이 그 사실을 받아 들이는가**이고, 그건 3겹에서 안 보인다.

두 가지인데 **뒤엣것이 더 무섭다.**

1. 판을 넘기면 번진 분류가 보인다 — 새로고침을 안 기다린다 (102 덧 2 와 같은 자리)
2. **그 판의 다음 저장이 도로 지우지 않는다** — 화면의 `labels` 는 열릴 때 받은
   것이라 번진 분류를 모른다. 뷰어는 늘 전체를 보내므로, 모르는 채로 저장이
   나가면 서버가 "표시가 사라진 행" 으로 보고 지운다. 예외도 경고도 없이,
   **다른 판에서 뭔가를 지운 순간** 앞서 정한 분류가 사라진다
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import Image, ObjectReview, RunBatch


class LinkLabelSpreadBrowserTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_frames=3, n_candidates=2)
        self.extra = fx.add_frame_detections(self.w.vp)
        self.batch = RunBatch.objects.get(label="yolo-시험")
        self.stack_img = Image.objects.get(viewpoint=self.w.vp, kind="stack")
        self.frame, self.frame_img, _ = self.extra[0]

        # 합성본과 첫 프레임의 같은 자리를 한 개체로 묶어 둔다 — 픽스처가 판마다
        # 같은 자리에 후보를 세우므로 `mask_key` 가 그대로 맞는다.
        self.key = self.w.keys()[0]
        rows = [fx.new_review(viewpoint=self.w.vp, image=img, batch=self.batch,
                              mask_key=self.key, bind_method="exact",
                              geom={"bbox_xywh": [40, 50, 60, 40]})
                for img in (self.stack_img, self.frame_img)]
        fx.link_reviews(rows, rep=0)

    def open_review(self):
        page = self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))
        page.wait_for_selector(".detview .box")
        return page

    def shot(self, detkey):
        el = self.page.query_selector(f'.shot[data-detkey="{detkey}"]')
        self.assertIsNotNone(el, f"캐러셀에 {detkey} 판이 없다")
        el.scroll_into_view_if_needed()
        return el

    def pick(self, detkey):
        el = self.shot(detkey)
        el.hover()
        self.page.wait_for_timeout(350)
        el.click()
        self.page.wait_for_timeout(400)
        # **사진을 화면 안으로 되돌린다.** 캐러셀을 누르느라 스크롤이 내려가
        # 있으면 이미지 좌표로 짚은 클릭이 창 밖으로 떨어진다 — 우클릭 메뉴가
        # 안 열려서 "메뉴가 없다" 로 읽힌다 (test_carousel_preview 와 같다).
        self.masks_svg().scroll_into_view_if_needed()
        self.page.wait_for_timeout(150)

    def label_first_as_rod(self):
        """합성본에서 첫 개체를 고르고 봉상(w)으로 지정한다."""
        self.click_image(40 + 60 // 2, 50 + 40 // 2)
        self.page.keyboard.press("w")
        self.page.wait_for_timeout(900)          # 지연 저장 + 응답

    def classes_of_boxes(self):
        return self.page.evaluate(
            """() => [...document.querySelectorAll('.detview .box')]
                 .map(e => e.className)""")

    def test_판을_넘기면_번진_분류가_보인다(self):
        self.open_review()
        self.label_first_as_rod()
        self.assertEqual(
            ObjectReview.objects.get(image=self.frame_img,
                                     mask_key=self.key).label, "broken",
            "서버가 안 번지게 했다 — 이 시험의 전제가 틀렸다")

        self.pick(self.frame.name)
        rods = [c for c in self.classes_of_boxes() if "broken" in c]
        self.assertTrue(rods,
                        "묶인 판으로 넘어왔는데 봉상으로 안 보인다 — "
                        f"새로고침해야 나오는 상태다: {self.classes_of_boxes()}")

    def test_그_판에서_저장해도_번진_분류가_안_지워진다(self):
        """**이것이 진짜 위험한 쪽이다.** 화면이 모르는 채로 전체를 보낸다."""
        self.open_review()
        self.label_first_as_rod()

        # 묶인 판으로 넘어가 **다른** 개체를 지운다 → 그 판의 저장이 나간다
        self.pick(self.frame.name)
        self.context_menu_at(160 + 70 // 2, 130 + 50 // 2)   # 둘째 후보
        self.page.get_by_text("오검출로 삭제", exact=False).first.click()
        self.page.wait_for_timeout(1000)

        row = ObjectReview.objects.filter(image=self.frame_img,
                                          mask_key=self.key).first()
        self.assertIsNotNone(
            row, "그 판에서 뭔가를 지웠더니 번진 분류 행이 통째로 사라졌다")
        self.assertEqual(row.label, "broken",
                         "그 판의 다음 저장이 번진 분류를 도로 지웠다")



class LinkUnifyBrowserTest(BrowserTestCase):
    """묶기 팝업이 **분류·종명 충돌을 알린다** (사용자 요청 2026-08-10).

    묶기 전에 판마다 따로 적어 둔 분류·종명이 다를 수 있다. 조용히 대표 것으로
    덮으면 남의 동정을 잃는다 — 종명은 현미경을 보며 적는 것이라 재생성 불가다.

    그래서 **알리고 고르게 한다.** 기본은 `그대로` 라서 아무것도 안 누르면
    아무것도 안 덮는다. 이 갈래는 3겹에서 안 보인다 — 팝업이 값을 모아 세고
    칩을 그리는 일 전체가 화면 쪽이다.
    """

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}",
                               n_frames=3, n_candidates=2)
        self.extra = fx.add_frame_detections(self.w.vp)
        self.batch = RunBatch.objects.get(label="yolo-시험")
        self.stack_img = Image.objects.get(viewpoint=self.w.vp, kind="stack")
        self.frame, self.frame_img, _ = self.extra[0]
        self.key = self.w.keys()[0]
        # **판마다 다른 분류를 미리 적어 둔다** — 묶기 전에 벌어지는 상황 그대로
        fx.new_review(
            viewpoint=self.w.vp, image=self.stack_img, batch=self.batch,
            mask_key=self.key, label="broken")
        fx.new_review(
            viewpoint=self.w.vp, image=self.frame_img, batch=self.batch,
            mask_key=self.key, label="foram")

    def open_panel(self):
        page = self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))
        page.wait_for_selector(".detview .box")
        menu = self.context_menu_at(40 + 60 // 2, 50 + 40 // 2)
        self.assertIsNotNone(menu, "우클릭 메뉴가 안 열렸다")
        for el in menu.query_selector_all("button, .mi"):
            if "동일 개체 묶기" in (el.inner_text() or ""):
                el.click()
                break
        else:
            self.fail("묶기 항목이 메뉴에 없다")
        page.wait_for_selector(".linkpanel", state="visible", timeout=3000)
        return page

    def labels(self):
        return sorted(ObjectReview.objects.filter(mask_key=self.key)
                      .values_list("foram_object__label", flat=True))

    def test_엇갈리면_알린다(self):
        page = self.open_panel()
        clash = page.query_selector(".linkpanel .urow.clash")
        self.assertIsNotNone(clash, "분류가 엇갈리는데 아무 말도 없다")
        text = clash.inner_text()
        self.assertIn("분류가 엇갈립니다", text)
        self.assertIn("파손", text)
        self.assertIn("온전", text)
        # **기본은 `그대로`** — 아무것도 안 눌렀는데 덮을 것을 미리 골라 두면
        # 사람이 못 보고 저장한다
        on = page.query_selector(".linkpanel .urow.clash .uchip.on")
        self.assertIsNotNone(on, "고른 것이 하나도 없다")
        self.assertIn("그대로", on.inner_text())

    def test_고르면_묶인_판이_모두_그_분류가_된다(self):
        page = self.open_panel()
        for b in page.query_selector_all(".linkpanel .urow.clash .uchip"):
            if "파손" in (b.inner_text() or ""):
                b.click()
                break
        else:
            self.fail("파손 칩이 없다")
        page.wait_for_timeout(150)
        page.query_selector(".linkpanel .lfoot .btn").click()
        page.wait_for_selector(".linkpanel", state="detached", timeout=3000)

        link = fx.links().get()
        n = link.members.count()
        self.assertGreaterEqual(n, 2)
        # 미리 고르기가 프레임을 여럿 잡으므로 멤버 수는 자료가 정한다 —
        # 보는 것은 **전부 같은 분류가 됐는가**다
        self.assertEqual(set(self.labels()), {"broken"}, "고른 분류로 안 맞춰졌다")
        self.assertEqual(len(self.labels()), n,
                         "멤버 수만큼 안 앉았다 (행이 없던 판을 빠뜨렸다)")

    def test_안_고르면_묶기가_거절된다(self):
        """**P12 에서 뜻이 옮겨 갔다.** 예전에는 분류가 판마다 살아서 "묶되
        아무것도 안 덮는다" 가 가능했다. 지금은 개체가 값을 하나만 들 수 있어
        **"그대로" 가 성립하지 않는다** — 그릇의 값이 남의 동정을 덮는다.

        그래서 서버가 거절한다. 지키는 것은 같다: **사람이 적은 것을 자동으로
        덮지 않는다.** 화면은 팝업이 물어보게 돼 있고, 여기 걸리는 것은 그
        팝업을 안 지난 요청이다.
        """
        self.expect_http_error(409)          # 서버가 거절하는 것이 이 시험이다
        page = self.open_panel()
        page.query_selector(".linkpanel .lfoot .btn").click()
        page.wait_for_timeout(500)
        self.assertEqual(fx.links().count(), 0, "덮으면서 묶였다")
        self.assertEqual(self.labels(), ["broken", "foram"],
                         "고르지도 않았는데 분류가 덮였다")
