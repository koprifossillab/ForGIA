"""DiaRUGA v0.29.0 `tests/browser/test_manage_batch.py` 에서 왔다.

관리 화면의 묶음 셀렉터가 **실제로 보이고 눌리는가** (DiaRUGA P10 3단계).

3겹은 "200 이 뜨고 이런 HTML 이 나온다" 까지만 본다. 여기서 보는 것은 그 밖이다.

- **CSS 가 먹는가** — `.mng`·`.tag` 라고 적었다가 있는 클래스가 아니어서 한 번도
  안 먹을 뻔했다. `.tools` 가 그렇게 세 화면을 속인 적이 있다(051)
- **누르기 전에 무엇이 달라지는지 보이는가** — 숫자가 화면에 있어야 한다
- **눌러서 바뀌는가**

ForGIA 에서 달라진 것: 줄마다 카탈로그 코드를 적는 단추(적기)가 하나 더 있다
(3단계). 그래서 "바꾸는 단추" 는 `button.pick` 으로 짚는다 — 맨 `button` 으로
잡으면 코드 칸의 것이 먼저 걸려 헛눌린다.
"""
from django.urls import reverse

from .base import BrowserTestCase
from .. import factories as fx
from ...models import Detection, RunBatch


class ManageBatchPickerTest(BrowserTestCase):

    def make_data(self):
        fx.make_classes()
        self.w = fx.make_world(slug=f"rs23-{self.uniq}",
                               site_code=f"RS{self.uniq}", n_candidates=3)
        self.other = fx.add_other_engine(self.w.vp)
        Detection.objects.filter(run=self.other).update(is_current=True)
        # 이 묶음이 안 덮는 시야 — "빈 화면이 될 시야" 가 0 이 아니어야 한다
        fx.make_world(slug=f"다른-{self.uniq}", site_code=f"XX{self.uniq}",
                      n_viewpoints=2)

    def open_manage(self):
        # 묶음 고르기는 **운영 화면**으로 옮겼다 (083)
        return self.open(reverse("system_settings_ops"))

    def rows(self):
        return self.page.query_selector_all("table.mtab tr")

    def test_셀렉터가_보인다(self):
        page = self.open_manage()
        self.assertIn("검토할 묶음", page.inner_text("body"))
        labels = page.inner_text("body")
        self.assertIn("yolo-시험", labels)
        self.assertIn(self.other.batch.label, labels)

    def test_지금_검토_중인_줄이_달라_보인다(self):
        """**`getComputedStyle` 로 확인한다.** 클래스 이름을 잘못 적으면 규칙이
        한 번도 안 먹는데 예외도 경고도 없다."""
        page = self.open_manage()
        now = page.query_selector("tr.nowrow")
        self.assertIsNotNone(now, "검토 중인 줄에 표시가 없다")
        self.assertTrue(page.is_visible(".nowtag"), "'검토 중' 딱지가 안 보인다")

        bg = now.query_selector("td").evaluate(
            "e => getComputedStyle(e).backgroundColor")
        plain = page.query_selector("tr:not(.nowrow) td")
        self.assertNotEqual(bg, plain.evaluate(
            "e => getComputedStyle(e).backgroundColor"),
            "검토 중인 줄이 보통 줄과 같아 보인다")

    def test_누르기_전에_숫자가_보인다(self):
        """**바꾸고 나서 아는 것은 늦다.** 몇 시야를 덮고 몇이 비는지."""
        page = self.open_manage()
        # 다른 엔진 묶음의 줄 — 시야 1개를 덮고 2개가 빈다
        row = None
        for tr in self.rows():
            if self.other.batch.label in (tr.inner_text() or ""):
                row = tr
                break
        self.assertIsNotNone(row, "그 묶음의 줄이 없다")
        nums = [td.inner_text().strip()
                for td in row.query_selector_all("td.num")]
        self.assertEqual(nums[0], "1", f"덮는 시야 수가 틀리다: {nums}")
        self.assertEqual(nums[1], "2", f"빈 화면이 될 시야 수가 틀리다: {nums}")

    def test_눌러서_바꾼다(self):
        page = self.open_manage()
        page.on("dialog", lambda d: d.accept())        # 확인 창을 받는다

        for tr in self.rows():
            if self.other.batch.label in (tr.inner_text() or ""):
                btn = tr.query_selector("button.pick")
                self.assertIsNotNone(btn, "바꾸는 단추가 없다")
                btn.click()
                break
        page.wait_for_timeout(900)

        self.assertEqual(
            list(RunBatch.objects.filter(for_review=True)
                 .values_list("label", flat=True)), [self.other.batch.label])
        self.assertIn("바꿨습니다", page.inner_text("body"))

    def test_검토_중인_줄에는_단추가_없다(self):
        """이미 보고 있는 것을 다시 고를 이유가 없다 — 누르면 거절만 나온다."""
        page = self.open_manage()
        now = page.query_selector("tr.nowrow")
        self.assertIsNone(now.query_selector("button.pick"),
                          "검토 중인 줄에 바꾸는 단추가 있다")
