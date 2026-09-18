"""DiaRUGA v0.29.0 `tests/test_object_cards.py` 에서 왔다.

카탈로그 카드가 **개체 단위**인가 (DiaRUGA P18).

사용자가 둘을 지적했다 — 메인 그림은 묶음의 대표여야 하고(152 에서 했다),
**특정 판에만 있는 유공충도 카탈로그에 나와야 한다**. 뒤엣것이 여기다.

## 말부터

카드 하나 = **개체**(`ForamObject`) 하나다. 후보가 아니다. 그래서

- **아직 아무도 안 본 후보는 카드가 없다** (사용자 방침 2026-08-25).
  검토 완료가 통과분에 개체를 세운다(`confirm_kept`) — 그 뒤에 찬다
- **합성본에서 안 잡히고 프레임에서만 잡힌 개체도 카드가 된다** (DiaRUGA 106)
- 한 개체이 판 넷에 잡혀 있고 사람이 **묶었으면** 카드가 하나다.
  안 묶었으면 넷이다 — 묶는 것은 사람의 판단이라(DiaRUGA P11) 기계가 접지 않는다

## 번호는 앵커에서 나온다

`catalog.py` 의 "저장하지 않는다 — 늘 계산해서 낸다" 는 그대로다. 저장하는 것은
**번호가 아니라 어느 판정에서 계산할까**(`ForamObject.anchor`)이고, 그래서

- **묶어도 · 대표를 ★ 로 옮겨도 번호가 안 움직인다**
- 앵커가 지워지면 남은 멤버로 넘어간다
- **멤버 아무 번호로나 찾아진다** — 논문에 적힌 번호가 앵커의 것이 아닐 수 있다
"""
from django.test import Client
from django.urls import reverse

from . import factories as fx
from .base import ForGIATestCase
from .. import data
from ..models import ForamObject, Image, ObjectReview, RunBatch


class ObjectCardTest(ForGIATestCase):

    def setUp(self):
        super().setUp()
        fx.make_classes()
        self.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        self.batch = RunBatch.objects.get(for_review=True)
        self.stack_img = Image.objects.get(viewpoint=self.w.vp, kind="stack")

    def rows(self, **kw):
        return data.catalog_rows("rs23", **kw)

    def keys(self, **kw):
        return sorted(r["key"] for r in self.rows(**kw))

    # --- 1. 사람이 손대기 전까지는 카드가 없다 -----------------------------

    def test_안_본_시야는_카드가_없다(self):
        """**개체는 사람이 손대기 전까지 없는 것이 맞다** (사용자 방침).

        예전에는 후보가 곧 카드라, 아무도 안 본 검출이 동정할 것처럼 카드로
        나왔다.
        """
        self.assertEqual(self.rows(), [])

    def test_검토_완료를_누르면_찬다(self):
        """완료는 *남는 마스크를 확인하는 것*이라 그때 개체가 선다
        (`confirm_kept`). 그것이 이 방침이 성립하는 이유다."""
        fx.review_done(self.w.vp, self.batch)
        self.assertEqual(len(self.rows()), 2)

    def test_왜_이만큼인지_화면이_말한다(self):
        """**조용히 줄면 자료가 사라진 것으로 읽힌다** (DiaRUGA P18).

        카드가 개체 단위가 되면서 검토 안 한 시야는 카드가 없다 — 파편을 감출
        때 몇 개를 감췄는지 적는 것과 같은 줄이다.
        """
        # **글자로 세면 안 된다** — `vpdone` 은 `<style>` 에도 있어서 결과와
        # 무관하게 늘 걸린다(`catcard` 로 한 번 당한 그 자리다). 속성으로 짚는다.
        MARK = 'class="vpdone"'
        html = Client().get(reverse("catalog", args=["rs23"])).content.decode()
        self.assertIn(MARK, html, "덜 봤는데 그 말을 안 한다")
        fx.review_done(self.w.vp, self.batch)
        html = Client().get(reverse("catalog", args=["rs23"])).content.decode()
        self.assertNotIn(MARK, html, "다 봤는데도 그 말을 한다")

    # --- 2. 프레임에만 있는 개체 -----------------------------------------

    def test_프레임에만_있는_개체도_카드가_된다(self):
        """**106 이 여기서 닫힌다.** 합성본에서 안 잡히고 어떤 프레임에서만
        잡힌 개체이 카탈로그에 아예 없었다 — 실측 5,123건이다.

        예전 `candidate_rows` 는 시야마다 **판 하나**(합성본이 있으면 합성본)만
        훑었다. 그래서 이 개체는 어느 화면에도 안 나왔다.
        """
        extra = fx.add_frame_detections(self.w.vp)
        frame_img = extra[0][1]
        row = fx.add_review(self.w.vp, self.w.keys()[0], image=frame_img,
                            label="broken")
        # 합성본에는 이 개체가 없다 — 프레임에만 판정이 있다
        ObjectReview.objects.filter(image=self.stack_img).delete()
        keys = [(r["image_id"], r["key"]) for r in self.rows()]
        self.assertIn((frame_img.pk, row.mask_key), keys,
                      "프레임에만 있는 개체가 카드에 없다")

    # --- 3. 묶으면 카드가 하나다 -------------------------------------------

    def test_묶으면_카드가_하나다(self):
        extra = fx.add_frame_detections(self.w.vp)
        frame_img = extra[0][1]
        key = self.w.keys()[0]
        a = fx.add_review(self.w.vp, key, image=self.stack_img)
        b = fx.add_review(self.w.vp, key, image=frame_img)
        self.assertEqual(len([r for r in self.rows() if r["key"] == key]), 2,
                         "묶기 전인데 카드가 둘이 아니다 — 앞의 전제가 깨졌다")
        fx.link_reviews([a, b], rep=0)
        hit = [r for r in self.rows() if r["key"] == key]
        self.assertEqual(len(hit), 1, "묶었는데 카드가 하나로 안 모였다")
        self.assertEqual(hit[0]["linked_n"], 2)


class AnchorTest(ForGIATestCase):
    """번호가 앵커에서 나오고, 사람이 무엇을 해도 안 움직이는가."""

    def setUp(self):
        super().setUp()
        fx.make_classes()
        self.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        self.extra = fx.add_frame_detections(self.w.vp)
        self.frame_img = self.extra[0][1]
        self.stack_img = Image.objects.get(viewpoint=self.w.vp, kind="stack")
        self.key = self.w.keys()[0]
        self.a = fx.add_review(self.w.vp, self.key, image=self.stack_img)
        self.b = fx.add_review(self.w.vp, self.key, image=self.frame_img)

    def card(self):
        hit = [r for r in data.catalog_rows("rs23") if r["key"] == self.key]
        self.assertEqual(len(hit), 1, f"카드가 하나가 아니다: {len(hit)}")
        return hit[0]

    def test_개체가_설_때_앵커가_선다(self):
        # **DB 에서 다시 읽는다** — 앵커는 판정을 세운 뒤에 `update` 로 붙어
        # 손에 든 인스턴스는 그것을 모른다.
        for row in (self.a, self.b):
            obj = ForamObject.objects.get(pk=row.foram_object_id)
            self.assertEqual(obj.anchor_id, row.pk)

    def test_묶어도_번호가_안_움직인다(self):
        """**이 시험이 이 갈래의 요점이다.** 번호는 논문·표에 적히는 것이라
        묶는 행위에 따라 움직이면 이미 적어 둔 것이 무효가 된다."""
        before = [r for r in data.catalog_rows("rs23")
                  if r["image_id"] == self.a.image_id
                  and r["key"] == self.key][0]["catalog_no"]
        fx.link_reviews([self.a, self.b], rep=0)
        self.assertEqual(self.card()["catalog_no"], before)

    def test_대표를_옮겨도_번호가_안_움직인다(self):
        """얼굴은 대표, 이름은 앵커 — 두 축을 갈라 둔 이유가 이것이다."""
        obj = fx.link_reviews([self.a, self.b], rep=0)
        before = self.card()["catalog_no"]
        # ★ 로 대표를 프레임으로 옮긴 것과 같다
        ObjectReview.objects.filter(foram_object=obj).update(is_rep=False)
        ObjectReview.objects.filter(pk=self.b.pk).update(is_rep=True)
        after = self.card()
        self.assertEqual(after["catalog_no"], before,
                         "대표를 옮겼더니 번호가 따라갔다")
        # 그림은 따라간다 — 얼굴은 대표다 (DiaRUGA 152)
        self.assertTrue(after["image_id"] == self.b.image_id
                        or (after.get("view") or {}).get("rel"),
                        "얼굴이 대표를 안 따라갔다")

    def test_앵커가_지워지면_남은_멤버로_넘어간다(self):
        obj = fx.link_reviews([self.a, self.b], rep=1)
        # 앵커는 대표와 무관하다 — 가장 오래된 멤버다 (DiaRUGA P18)
        self.assertEqual(obj.anchor_id, self.a.pk)
        ObjectReview.objects.filter(pk=self.a.pk).delete()
        data.reanchor([obj.pk])
        obj.refresh_from_db()
        self.assertEqual(obj.anchor_id, self.b.pk,
                         "앵커를 잃은 채로 남았다 — 번호를 만들 재료가 없다")

    def test_번호가_어느_판_기준인지_적는다(self):
        """카드는 개체인데 번호는 판정 하나의 이름이다 — 안 적으면 `f03` 이
        카드 전체를 설명하는 것처럼 읽힌다."""
        obj = fx.link_reviews([self.a, self.b], rep=1)   # 대표는 프레임
        r = self.card()
        self.assertFalse(r["no_here"], "번호가 이 줄의 것이라고 말한다")
        html = Client().get(reverse("catalog", args=["rs23"])).content.decode()
        self.assertIn("기준", html)

    def test_멤버_아무_번호로나_찾아진다(self):
        """**논문에 적힌 번호가 앵커의 것이 아닐 수 있다** (DiaRUGA P18 "공개·인용").

        묶기 전에 적어 둔 번호가 묶은 뒤에도 열려야 한다.
        """
        rows = data.catalog_rows("rs23")
        other = [r for r in rows if r["image_id"] == self.b.image_id
                 and r["key"] == self.key][0]["catalog_no"]
        fx.link_reviews([self.a, self.b], rep=0)
        self.assertNotEqual(self.card()["catalog_no"], other,
                            "앵커가 아닌 번호를 카드가 내보이고 있다 — 전제가 깨졌다")
        r = Client().get(reverse("catalog", args=["rs23"]), {"q": other})
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        # **`catcard` 로 세면 안 된다** — 그 글자가 `<style>` 에 늘 있어서
        # 결과가 0건이어도 통과한다(처음에 그렇게 짰다). **카드가 실제로
        # 그려졌는지**는 그 개체의 열쇠로 본다.
        self.assertIn(f'data-key="{self.key}"', html,
                      "묶기 전에 적어 둔 번호로 찾으니 그 카드가 안 나온다")
        self.assertIn(self.card()["catalog_no"], html,
                      "찾긴 했는데 앵커의 번호를 안 내보인다")
