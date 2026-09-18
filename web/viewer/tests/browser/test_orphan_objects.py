"""DiaRUGA v0.29.0 `tests/browser/test_orphan_objects.py` 에서 왔다.

**후보 없이 교정만 남은 개체**가 화면에 나오는가 (DiaRUGA P09 2단계).

재검출에서 임자를 잃은 고아, 그리고 3단계부터는 사람이 그린 개체가 그렇다.
`Candidate` 가 없으므로 예전 `_apply_review` 는 **아예 안 그렸다.**

**안 그리면 다음 저장에 지워진다.** 화면은 자기가 아는 키만 보내고 `/review` 는
payload 에 없는 키를 지운다 — 사람이 아무것도 안 하고 **"검토 완료" 만 눌러도**
재생성 불가한 판단이 사라진다. 그 갈래는 3겹 시험이 재현해 둔다
(`OrphanReviewSurvivesTest`).

여기서 보는 것은 3겹이 못 보는 쪽이다.

- 실제로 **그려지는가** (`getComputedStyle` 로 — 요소가 있는 것과 보이는 것은
  다르다. `.tools` 가 그렇게 한 번 속였다)
- 엔진이 낸 것과 **달라 보이는가** — 같아 보이면 사람이 지표가 있는 줄 안다
- 눌러서 **고칠 수 있는가**, 그리고 그 저장이 옛 행을 지우지 않는가
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import ObjectReview

# 다른 개체와 안 겹치는 자리 (픽스처는 40,50 / 160,130 / 280,210 을 쓴다)
ORPHAN_BOX = [420, 300, 80, 60]
ORPHAN_KEY = "420_300_80_60"
BARE_BOX = [420, 60, 70, 50]
BARE_KEY = "420_60_70_50"


class OrphanOnScreenTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=3)
        det = self.w.detection()
        x, y, w, h = ORPHAN_BOX
        self.orphan = fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=det.batch,
            mask_key=ORPHAN_KEY, bind_method="orphan", label="other",
            note="엔진이 놓친 것",
            geom={"bbox": ORPHAN_BOX,
                  "polygon": [x, y, x + w, y, x + w, y + h, x, y + h]})
        # **표시가 `removed` 뿐인 고아.** 분류·코멘트가 붙은 것은 `labels`·
        # `notes` 를 타고 payload 에 실려 **안 그려도 살아남는다** — 그래서 위
        # 하나만으로는 이 갈래를 못 잡는다(실제로 못 잡았다). 지운 것은 `gone`
        # 목록에서 오는데 그 목록은 후보에서만 만들어지므로, 안 그리면 키가
        # payload 에서 통째로 빠진다.
        bx, by, bw, bh = BARE_BOX
        self.bare = fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=det.batch,
            mask_key=BARE_KEY, bind_method="orphan", removed=True,
            geom={"bbox": BARE_BOX,
                  "polygon": [bx, by, bx + bw, by, bx + bw, by + bh,
                              bx, by + bh]})

    def open_review(self):
        return self.open(reverse("group", args=[self.w.slug, self.w.vp.idx]))

    def orphan_box(self):
        return self.page.query_selector(".box.orphan")

    # --- 그려지는가 --------------------------------------------------------

    def test_고아_개체가_화면에_그려진다(self):
        page = self.open_review()
        el = self.orphan_box()
        self.assertIsNotNone(el, "고아 교정이 화면에 안 그려졌다")
        self.assertTrue(el.is_visible(), "요소는 있는데 안 보인다")

    def test_엔진이_낸_것과_달라_보인다(self):
        """**`getComputedStyle` 로 확인한다.** `.detview .box.<분류>` 가 특이도에서
        이겨 새 규칙이 한 번도 안 먹은 적이 있다(051).

        같아 보이면 사람이 **지표가 있는 개체로 읽는다** — 이 개체는 잰 적이 없다.
        """
        page = self.open_review()
        orphan = self.orphan_box()
        normal = page.query_selector(".box:not(.orphan):not(.gone)")
        self.assertIsNotNone(normal, "대조군이 없다 — 보통 개체가 안 그려졌다")

        style = "e => getComputedStyle(e).borderStyle"
        self.assertEqual(orphan.evaluate(style), "dashed")
        self.assertNotEqual(orphan.evaluate(style), normal.evaluate(style),
                            "고아가 보통 개체와 같아 보인다")

    def test_말풍선이_지표가_빈_이유를_적는다(self):
        """**빈칸은 고장처럼 보인다.** 왜 비었는지 적어야 한다."""
        page = self.open_review()
        x, y, w, h = ORPHAN_BOX
        px, py = self.image_point(x + w // 2, y + h // 2)
        page.mouse.move(px, py)
        page.wait_for_timeout(250)
        tip = page.query_selector("#tip-stack, .dettip")
        self.assertIsNotNone(tip, "말풍선이 안 떴다")
        self.assertIn("잰 적이 없어", tip.inner_text())

    # --- 사람이 아무것도 안 해도 살아남는가 --------------------------------

    def test_검토_완료만_눌러도_고아가_남는다(self):
        """**이 시험이 이 파일의 이유다.**

        화면이 자기가 아는 키만 보내므로, 안 그려지면 이 한 번의 클릭이
        재생성 불가한 판단을 지운다.
        """
        page = self.open_review()
        page.click("#done-stack")
        page.wait_for_timeout(900)

        self.assertTrue(ObjectReview.objects.filter(pk=self.orphan.pk).exists(),
                        "검토 완료를 눌렀더니 고아 교정이 사라졌다")
        o = ObjectReview.objects.get(pk=self.orphan.pk)
        self.assertEqual(o.label, "other", "분류가 바뀌었다")
        self.assertEqual(o.note, "엔진이 놓친 것", "코멘트가 사라졌다")
        # **표시가 `removed` 뿐인 것이 진짜 시험이다** (make_data 의 주석).
        self.assertTrue(ObjectReview.objects.filter(pk=self.bare.pk).exists(),
                        "표시가 removed 뿐인 고아가 사라졌다")
        self.assertTrue(ObjectReview.objects.get(pk=self.bare.pk).removed,
                        "지웠다는 표시가 풀렸다")

    def test_고아의_분류를_고칠_수_있다(self):
        """고칠 수 있어야 쓸모가 있다 — 보이기만 하면 읽기 전용과 같다."""
        page = self.open_review()
        x, y, w, h = ORPHAN_BOX
        menu = self.context_menu_at(x + w // 2, y + h // 2)
        self.assertIsNotNone(menu, "고아 개체 위에서 우클릭 메뉴가 안 떴다")
        self.menu_click("파손", exact=True)
        page.wait_for_timeout(900)

        o = ObjectReview.objects.get(pk=self.orphan.pk)
        self.assertEqual(o.label, "broken")
        self.assertEqual(o.mask_key, ORPHAN_KEY,
                         "키가 바뀌었다 — 옛 행이 지워지고 새 행이 생겼다")
        # 픽스처의 고아 둘. **행이 늘면** 키가 기하에서 다시 만들어져 같은 개체가
        # 둘이 된 것이고, **줄면** 다른 고아가 쓸려 간 것이다.
        self.assertEqual(ObjectReview.objects.count(), 2)
