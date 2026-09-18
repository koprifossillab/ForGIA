"""DiaRUGA v0.29.0 `tests/test_catalog_rows.py` 에서 왔다.

개체 카탈로그가 읽는 자료 (`data.catalog_rows`).

카드 한 장이 무엇을 알아야 하는가 — **번호 · 그림 · 동정 · 왜 번호가 없는가.**

여기서 지키는 것 넷.

1. **번호가 안 움직인다** — 묶어도, 기하를 고쳐도, 다시 읽어도
2. **못 만들 때는 이유를 말한다** — "번호 없음" 만 적으면 무엇을 채워야 하는지
   알 수 없다 (지우기 문턱을 버튼에 적는 것과 같은 이야기, 063)
3. **`M`(손그림)과 "코드를 아직 안 정했다" 를 안 섞는다** — 섞으면 엔진이 낸
   개체가 손그림으로 기록된다
4. **묶으면 그림만 바뀐다** — 번호는 합성본 기준으로 고정 (사용자 방침 2026-08-10)
"""
from . import factories as fx
from .base import ForGIATestCase
from .. import data
from ..models import ForamObject, ObjectReview, RunBatch


class CatalogNumberTest(ForGIATestCase):
    """번호가 붙는가 · 무엇으로 붙는가."""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def rows(self):
        return data.catalog_rows("rs23")

    def test_모든_개체가_번호를_받는다(self):
        rows = self.rows()
        self.assertTrue(rows)
        self.assertTrue(all(r["catalog_no"] for r in rows),
                        [r.get("catalog_why") for r in rows])

    def test_번호가_층에서_나온다(self):
        """픽스처는 RS23-GC03 71cm 관찰 0, 시야 0."""
        r = self.rows()[0]
        self.assertTrue(r["catalog_no"].startswith("RS23-GC03-071-g00-"),
                        r["catalog_no"])
        self.assertTrue(r["catalog_no"].endswith("-S1"), r["catalog_no"])

    def test_번호에서_개체를_되찾는다(self):
        """되찾지 못하면 번호가 이름표이기만 하고 열쇠가 아니게 된다."""
        from ..catalog import parse
        for r in self.rows():
            got = parse(r["catalog_no"])
            self.assertIsNotNone(got, r["catalog_no"])
            self.assertEqual(got["mask_key"], r["key"])
            self.assertEqual(got["viewpoint"], r["group_id"])

    def test_개체마다_다르다(self):
        nos = [r["catalog_no"] for r in self.rows()]
        self.assertEqual(len(nos), len(set(nos)))

    def test_다시_읽어도_같다(self):
        self.assertEqual([r["catalog_no"] for r in self.rows()],
                         [r["catalog_no"] for r in self.rows()])

    def test_합성본_개체는_f_가_안_붙는다(self):
        """`f` 가 붙어 있느냐가 곧 어느 이미지를 보고 잰 것이냐를 말한다."""
        for r in self.rows():
            self.assertIsNone(r["frame_seq"])
            self.assertNotIn("-f", r["catalog_no"])


class SingletonFrameTest(ForGIATestCase):
    """합성본이 없는 시야 — 프레임이 곧 검출 대상이다 (053 이 난 자리).

    **`make_world(with_stack=False)` 가 그 반대쪽 갈래다** (DiaRUGA 086). 자료를 전부
    합성본으로 세우면 프레임 갈래를 한 번도 안 밟는다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2, with_stack=False)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def test_프레임_개체는_f_가_붙는다(self):
        rows = data.catalog_rows("rs23")
        self.assertTrue(rows)
        for r in rows:
            self.assertIsNotNone(r["frame_seq"])
            self.assertIn(f"-f{r['frame_seq']:02d}-", r["catalog_no"])


class NoNumberTest(ForGIATestCase):
    """**번호를 못 만들 때는 이유를 말한다.**"""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def test_묶음_코드가_없으면_번호도_없다(self):
        """**`M` 을 대신 붙이면 안 된다** — 엔진이 낸 개체가 손그림으로 기록된다."""
        RunBatch.objects.filter(for_review=True).update(code="")
        r = data.catalog_rows("rs23")[0]
        self.assertEqual(r["catalog_no"], "")
        self.assertIn("코드", r["catalog_why"])

    def test_소속을_잃으면_번호도_없다(self):
        """소속을 잃은 관찰이 실제로 있었다 (DiaRUGA 063). `RS23--071-…` 은 되읽을 수 없다."""
        RunBatch.objects.filter(for_review=True).update(code="S1")
        self.w.slide.sample = None
        self.w.slide.save(update_fields=["sample"])
        r = data.catalog_rows("rs23")[0]
        self.assertEqual(r["catalog_no"], "")
        self.assertIn("소속", r["catalog_why"])

    def test_번호가_있으면_이유는_비어_있다(self):
        RunBatch.objects.filter(for_review=True).update(code="S1")
        for r in data.catalog_rows("rs23"):
            self.assertEqual(r["catalog_why"], "")


class ManualObjectTest(ForGIATestCase):
    """사람이 그린 개체 — 꼬리가 `M` 이고 묶음을 안 탄다."""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def test_손그림은_꼬리가_M_이다(self):
        det = self.w.detection()
        fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=None,
            mask_key="m1a2b3c4d", source="manual", bind_method="manual",
            geom={"bbox": [10, 20, 30, 40],
                  "polygon": [10, 20, 40, 20, 40, 60, 10, 60]})
        r = next(x for x in data.catalog_rows("rs23") if x["key"] == "m1a2b3c4d")
        self.assertTrue(r["catalog_no"].endswith("-m1a2b3c4d-M"),
                        r["catalog_no"])

    def test_엔진_개체는_M_이_안_붙는다(self):
        for r in data.catalog_rows("rs23"):
            if r.get("source") != "manual":
                self.assertFalse(r["catalog_no"].endswith("-M"),
                                 r["catalog_no"])


class SpeciesOnRowTest(ForGIATestCase):
    """동정이 카드까지 오는가."""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def test_적은_종명이_카드에_온다(self):
        det = self.w.detection()
        key = self.w.keys()[0]
        fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=det.batch,
            mask_key=key, bind_method="exact",
            species="Globigerina bulloides")
        r = next(x for x in data.catalog_rows("rs23") if x["key"] == key)
        self.assertEqual(r["species"], "Globigerina bulloides")

    def test_안_적은_것은_빈_문자열이다(self):
        """`None` 이면 템플릿이 "None" 을 찍는다."""
        for r in data.catalog_rows("rs23"):
            self.assertEqual(r["species"], "")

    def test_유형과_따로_온다(self):
        det = self.w.detection()
        key = self.w.keys()[0]
        fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=det.batch,
            mask_key=key, bind_method="exact", label="broken",
            species="Globigerina bulloides", note="가장자리가 넘쳤다")
        r = next(x for x in data.catalog_rows("rs23") if x["key"] == key)
        self.assertEqual((r["cls"], r["species"], r["note"]),
                         ("broken", "Globigerina bulloides", "가장자리가 넘쳤다"))


class LinkedViewTest(ForGIATestCase):
    """묶으면 **그림만** 가장 큰 프레임으로 바뀐다 (DiaRUGA P11 · 사용자 방침).

    **번호는 안 움직인다.** 번호가 묶는 행위에 따라 바뀌면 이미 적어 둔 번호가
    무효가 된다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=2)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def setUp(self):
        self.det = self.w.detection()
        self.key = self.w.keys()[0]
        self.batch = self.det.batch
        self.frame_img = self.w.vp.images.filter(kind="frame").first()

    def link(self, *sizes, box_key="bbox_xywh", rep=0):
        """합성본 멤버 + 프레임 멤버 하나. `sizes` 는 각 멤버의 (w, h).

        `rep` 은 **대표로 세울 멤버**다 (0 합성본 · 1 프레임). 152 부터 카드가
        그리는 것은 크기가 아니라 대표라, 이 인자가 곧 시험의 조건이다.

        **`box_key` 가 기본으로 `bbox_xywh` 인 것이 요점이다.** 실제 저장
        (`views.save_object_link`)이 그 키를 쓰는데 이 픽스처가 `bbox` 를 쓰고
        있었고, 그래서 **시험은 통과하는데 운영에서는 크롭 상자가 비어 원래
        상자로 되돌아갔다**(2026-08-10). 사람이 그린 멤버는 `ObjectReview.geom`
        을 그대로 실어 `bbox` 라서, 한 칸에 두 모양이 섞인다 — 둘 다 밟는다.
        """
        (sw, sh), (fw, fh) = sizes

        def geom(x, y, w, h):
            return {box_key: [x, y, w, h],
                    "polygon": [x, y, x + w, y, x + w, y + h, x, y + h]}

        rows = [
            fx.new_review(viewpoint=self.w.vp, image=self.det.image,
                          batch=self.batch, mask_key=self.key,
                          geom=geom(10, 10, sw, sh)),
            fx.new_review(viewpoint=self.w.vp, image=self.frame_img,
                          batch=self.batch, mask_key="500_500_20_20",
                          geom=geom(20, 20, fw, fh)),
        ]
        return fx.link_reviews(rows, rep=rep)

    def row(self):
        return next(x for x in data.catalog_rows("rs23") if x["key"] == self.key)

    def test_안_묶었으면_view_가_없다(self):
        r = self.row()
        self.assertNotIn("view", r)
        self.assertNotIn("linked_n", r)

    def test_대표가_프레임이면_그것을_그린다(self):
        """**카드가 그리는 것은 대표다** (152 · 사용자 방침 2026-08-25).

        묶기 패널이 *사람이 묶기를 누른 판*을 대표로 세우므로(`repKey = curKey`),
        이것은 **사람이 고른 판을 그대로 그린다**는 말이다.

        **대표 판에 현재 검출이 없으면 `view` 로 그린다.** 판정이 `geom` 을
        스스로 들고 있어 검출 없이도 그릴 수 있다 — 이 픽스처의 프레임이 그
        자리다. 검출이 있으면 카드 줄 자체가 대표의 줄이 된다.
        """
        self.link((30, 30), (90, 90), rep=1)
        r = self.row()
        self.assertEqual(r["view"]["rel"], self.frame_img.path)
        self.assertEqual(r["linked_n"], 2)

    def test_크기는_이제_안_고른다(self):
        """**예전에는 가장 큰 멤버를 그렸다** (2026-08-10 ~ 2026-08-25).

        대표가 이미 따로 있었고, 그래서 **같은 개체를 카드와 학습 자료가 다른
        판으로 말하고 있었다** — 실측으로 묶음 680개 중 410개가 그랬다.
        여기서는 프레임이 세 배 큰데도 대표(합성본)를 그려야 한다.
        """
        self.link((30, 30), (90, 90), rep=0)
        self.assertNotIn("view", self.row(),
                         "크기가 대표를 이겼다 — 152 이전으로 돌아갔다")

    def test_보여줄_상자가_그_멤버의_것이다(self):
        """**상자가 비면 예외가 안 나고 원래 상자로 되돌아간다** — 운영에서
        그랬다. 그러면 다른 판의 그림을 이 개체의 옛 자리로 잘라 낸다.

        """
        self.link((30, 30), (90, 90), rep=1)
        self.assertEqual(self.row()["view"]["bbox_xywh"], [20, 20, 90, 90])

    def test_두_기하_모양을_다_읽는다(self):
        """`ObjectLinkMember.geom` 은 `bbox_xywh`, 사람이 그린 멤버는 `bbox` 다."""
        for key in ("bbox_xywh", "bbox"):
            with self.subTest(키=key):
                ObjectReview.objects.all().delete(); ForamObject.objects.all().delete()
                self.link((30, 30), (90, 90), box_key=key, rep=1)
                self.assertEqual(self.row()["view"]["bbox_xywh"],
                                 [20, 20, 90, 90])

    def test_대표가_합성본이면_안_바꾼다(self):
        self.link((90, 90), (30, 30), rep=0)
        r = self.row()
        self.assertNotIn("view", r)
        self.assertEqual(r["linked_n"], 2)

    def test_묶어도_번호가_그대로다(self):
        """**이 시험이 이 갈래의 요점이다.**"""
        before = self.row()["catalog_no"]
        self.link((30, 30), (90, 90))
        self.assertEqual(self.row()["catalog_no"], before)

    def test_멤버가_하나면_건너뛴다(self):
        """묶은 것이 아니라 만들다 만 것이다 — 그림을 바꿀 이유가 없다."""
        fx.new_review(viewpoint=self.w.vp, image=self.det.image,
                      batch=self.batch, mask_key=self.key,
                      geom={"bbox": [10, 10, 30, 30],
                            "polygon": [10, 10, 40, 10, 40, 40, 10, 40]})
        r = self.row()
        self.assertNotIn("view", r)
        self.assertNotIn("linked_n", r)


class OrderTest(ForGIATestCase):
    """**번호 차례로 늘어놓는다** — 시야, 그 안에서 위아래·좌우 순.

    크롭 화면은 크기 내림차순인데(의심스러운 것을 뒤로 모으려고) 카탈로그는
    번호를 따라 읽는 표라 차례가 달라야 한다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_viewpoints=3, n_candidates=3)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        # **카드가 개체 단위다** (DiaRUGA P18) — 판정이 없는 후보는 카드가 없다.
        # 검토 완료가 그 자리에서 개체를 세운다(`confirm_kept`).
        for _vp in cls.w.viewpoints:
            fx.review_done(_vp)

    def test_시야_순이다(self):
        gids = [r["group_id"] for r in data.catalog_rows("rs23")]
        self.assertEqual(gids, sorted(gids))

    def test_시야_안에서는_위에서_아래로(self):
        rows = [r for r in data.catalog_rows("rs23") if r["group_id"] == 0]
        ys = [r["bbox_xywh"][1] for r in rows]
        self.assertEqual(ys, sorted(ys))
