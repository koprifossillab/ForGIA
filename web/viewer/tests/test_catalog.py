"""DiaRUGA v0.29.0 `tests/test_catalog.py` 에서 왔다.

카탈로그 번호 규칙 (`catalog.py`). **번호는 논문·표에 적히는 것이다.**

그래서 여기서 지키는 것이 두 가지다:

1. **같은 개체는 늘 같은 번호를 받는다** — 경계를 고쳐도, 지웠다 되살려도,
   재검출을 돌려도. 열쇠가 `mask_key` 라서 그렇다
2. **다른 개체가 같은 번호를 받는 길이 없다** — 짧게 줄이려는 후보들이 실측에서
   부딪혔고(머리말의 표), 이 시험이 그 자리를 지킨다

그리고 **번호를 못 만들 때는 말한다.** 조용히 이상한 번호를 내면 사람이 그것을
적어 두고, 나중에 그 번호로는 아무것도 못 찾는다.
"""
from django.test import SimpleTestCase

from ..catalog import (batch_code_seed, catalog_no, parse, part,
                       position_part, sample_part)


class ShapeTest(SimpleTestCase):
    """번호의 모양 — 두 체계(남극·육상)와 네 갈래(합성본·프레임·관찰·손그림)."""

    def test_남극_합성본(self):
        self.assertEqual(
            catalog_no(site="RS23", locality="GC03", sample="71cm",
                       viewpoint=3, mask_key="1204_856_132_97",
                       batch_code="S1"),
            "RS23-GC03-071-g03-1204_856_132_97-S1")

    def test_육상_단독프레임(self):
        """육상은 깊이가 없어 시료 코드가 `0901` 이다 (`naming.py` 의 두 체계).

        프레임에서 나온 개체는 `f` 가 붙는다 — **`f` 가 있느냐가 어느 이미지를
        보고 잰 것이냐를 말한다.**
        """
        self.assertEqual(
            catalog_no(site="BP", locality="BP09", sample="0901",
                       viewpoint=12, frame_seq=27,
                       mask_key="843_1502_96_210", batch_code="S1"),
            "BP-BP09-0901-g12-f27-843_1502_96_210-S1")

    def test_관찰번호는_0_이면_생략한다(self):
        """12개 슬라이드 중 10개가 `obs_no=0` 이다 — 늘 붙어 있으면 정보가 없다."""
        base = dict(site="RS23", locality="GC03", sample="369cm", viewpoint=7,
                    mask_key="410_222_88_140", batch_code="S1")
        self.assertEqual(catalog_no(**base, obs_no=0),
                         "RS23-GC03-369-g07-410_222_88_140-S1")
        self.assertEqual(catalog_no(**base, obs_no=100),
                         "RS23-GC03-369-100-g07-410_222_88_140-S1")

    def test_손그림_개체는_꼬리가_M_이다(self):
        """어느 묶음에도 안 속하는 것이 그 개체의 성질이다 (`ObjectReview.batch`
        가 NULL). 엔진 코드를 붙일 자리가 없다."""
        self.assertEqual(
            catalog_no(site="RS23", locality="GC03", sample="71cm",
                       viewpoint=3, mask_key="m1a2b3c4d"),
            "RS23-GC03-071-g03-m1a2b3c4d-M")

    def test_꼬리가_어느_검출인지_말한다(self):
        """앞쪽 `관찰-시야-위치` 는 엔진끼리 같은 모양이고 꼬리만 다르다 —
        나란히 놓고 비교할 때 읽히라고 꼬리에 뒀다 (사용자 판단 2026-08-10)."""
        base = dict(site="RS23", locality="GC03", sample="71cm", viewpoint=3,
                    mask_key="1204_856_132_97")
        sam = catalog_no(**base, batch_code="S1")
        yolo = catalog_no(**base, batch_code="Y3")
        self.assertTrue(sam.endswith("-S1"))
        self.assertTrue(yolo.endswith("-Y3"))
        self.assertEqual(sam[:-3], yolo[:-3])


class UniquenessTest(SimpleTestCase):
    """**다른 개체가 같은 번호를 받는 길이 없어야 한다.**

    짧게 줄이려던 후보가 전부 실측에서 부딪혔다 (2026-08-10, 이미지 한 장 안에서):
    x,y 는 SAM 90·YOLO 270, x,y,너비는 10·30, 중심 좌표는 0·30, 해시 4자는 2·0.
    이미지 한 장에 개체가 최대 106개라 좌표를 자르면 붙어 있는 것들이 뭉개진다.
    """

    def test_원점이_같고_크기가_다른_둘을_가른다(self):
        """`x,y` 로 잘랐을 때 부딪히던 자리다 — 자료에 90건 있었다."""
        base = dict(site="RS23", locality="GC03", sample="71cm", viewpoint=3,
                    batch_code="S1")
        a = catalog_no(**base, mask_key="1204_856_132_97")
        b = catalog_no(**base, mask_key="1204_856_80_60")
        self.assertNotEqual(a, b)

    def test_같은_위치라도_시야가_다르면_다르다(self):
        """`mask_key` 는 이미지 안에서만 유일하다 — 시야끼리는 흔하게 겹친다."""
        base = dict(site="RS23", locality="GC03", sample="71cm",
                    mask_key="1204_856_132_97", batch_code="S1")
        self.assertNotEqual(catalog_no(**base, viewpoint=3),
                            catalog_no(**base, viewpoint=4))

    def test_같은_시야라도_프레임이_다르면_다르다(self):
        """프레임끼리 `mask_key` 가 45% 겹친다 (DiaRUGA P06 머리말의 실측)."""
        base = dict(site="RS23", locality="GC03", sample="71cm", viewpoint=3,
                    mask_key="1198_861_130_99", batch_code="Y3")
        self.assertNotEqual(catalog_no(**base, frame_seq=27),
                            catalog_no(**base, frame_seq=28))

    def test_합성본과_프레임이_안_섞인다(self):
        """같은 시야의 합성본과 프레임에서 같은 좌표가 나올 수 있다."""
        base = dict(site="RS23", locality="GC03", sample="71cm", viewpoint=3,
                    mask_key="1198_861_130_99", batch_code="Y3")
        self.assertNotEqual(catalog_no(**base),
                            catalog_no(**base, frame_seq=28))

    def test_시료가_다르면_다르다(self):
        base = dict(site="RS23", locality="GC03", viewpoint=3,
                    mask_key="1204_856_132_97", batch_code="S1")
        self.assertNotEqual(catalog_no(**base, sample="71cm"),
                            catalog_no(**base, sample="231cm"))


class StabilityTest(SimpleTestCase):
    """**같은 개체는 늘 같은 번호를 받는다.** 열쇠가 `mask_key` 라서 그렇다."""

    def test_사람이_경계를_고쳐도_안_바뀐다(self):
        """`mask_key` 는 사람이 기하를 고쳐도 그대로다 — 그래서 화면이 키를 늘
        실어 보낸다 (`_cand_dict` 머리말, P09 4단계). 번호가 그 위에 얹혀 있으므로
        `geom_edited` 인 개체도 번호가 안 움직인다.

        **기하에서 번호를 만들면 고치는 순간 다른 개체가 된다** — 실제로 교정
        저장이 그 갈래에서 옛 행을 지웠던 적이 있다.
        """
        key = "1204_856_132_97"
        no = catalog_no(site="RS23", locality="GC03", sample="71cm",
                        viewpoint=3, mask_key=key, batch_code="S1")
        # 사람이 경계를 고쳐 bbox 가 [1200, 850, 140, 105] 가 됐다고 해도 키는 그대로다
        self.assertEqual(
            catalog_no(site="RS23", locality="GC03", sample="71cm",
                       viewpoint=3, mask_key=key, batch_code="S1"), no)

    def test_묶기가_번호를_움직이지_않는다(self):
        """묶으면 카드에 보이는 그림이 가장 큰 프레임으로 바뀌지만 **번호는 합성본
        기준으로 고정한다** (사용자 방침 2026-08-10). 번호가 묶는 행위에 따라
        움직이면 이미 적어 둔 번호가 무효가 된다 — 그래서 `frame_seq` 는 개체가
        실제로 프레임에서 나왔을 때만 준다.
        """
        stacked = catalog_no(site="RS23", locality="GC03", sample="71cm",
                             viewpoint=3, mask_key="1204_856_132_97",
                             batch_code="Y3")
        self.assertNotIn("-f", stacked)


class SamplePartTest(SimpleTestCase):
    """시료 토막 — `Sample.code` 만 본다 (`depth_cm` 을 쓰면 규칙이 둘이 된다)."""

    def test_cm_를_떼고_세_자리로_채운다(self):
        """`816` 이 `71` 앞에 오는 문자열 정렬을 막는다."""
        self.assertEqual(sample_part("71cm"), "071")
        self.assertEqual(sample_part("231cm"), "231")
        self.assertEqual(sample_part("816cm"), "816")
        self.assertEqual(sorted(["816", "071", "231"]), ["071", "231", "816"])

    def test_대문자_cm_와_공백도_뗀다(self):
        self.assertEqual(sample_part("71 CM"), "071")
        self.assertEqual(sample_part(" 25cm "), "025")

    def test_육상은_그대로_둔다(self):
        """`0901` 은 이미 네 자리다 — 채우지도 깎지도 않는다."""
        self.assertEqual(sample_part("0901"), "0901")

    def test_네_자리_이상_깊이도_안_깎는다(self):
        self.assertEqual(sample_part("1234cm"), "1234")


class RefusalTest(SimpleTestCase):
    """**번호를 못 만들 때는 말한다.** 조용히 이상한 번호를 내면 사람이 그것을
    적어 두고, 나중에 그 번호로는 아무것도 못 찾는다."""

    def test_층이_비면_거절한다(self):
        """소속을 잃은 슬라이드가 실제로 있었다 (DiaRUGA 063). 그때 `RS23--071-…` 같은
        번호를 내면 되읽을 수도 없다."""
        for bad in ({"site": ""}, {"locality": ""}, {"sample": ""},
                    {"site": None}, {"locality": "   "}):
            with self.assertRaises(ValueError):
                catalog_no(**{**dict(site="RS23", locality="GC03",
                                     sample="71cm", viewpoint=3,
                                     mask_key="1204_856_132_97",
                                     batch_code="S1"), **bad})

    def test_음수_좌표를_거절한다(self):
        """`data.CAND_KEY` 는 음수를 받지만 여기서는 안 받는다 — `-` 가 토막을
        가르는 글자라 번호를 되돌릴 수 없어진다. 지금 자료에 음수 키는 0건이다."""
        with self.assertRaises(ValueError):
            position_part("-12_856_132_97")
        with self.assertRaises(ValueError):
            catalog_no(site="RS23", locality="GC03", sample="71cm",
                       viewpoint=3, mask_key="-12_856_132_97",
                       batch_code="S1")

    def test_규칙에_안_맞는_키를_거절한다(self):
        for bad in ("", "1204_856", "1204_856_132_97_5", "abc", "m1a2b",
                    "M1A2B3C4D", "1204,856,132,97"):
            with self.assertRaises(ValueError):
                position_part(bad)

    def test_시야가_숫자가_아니면_거절한다(self):
        with self.assertRaises(ValueError):
            catalog_no(site="RS23", locality="GC03", sample="71cm",
                       viewpoint="g3", mask_key="1204_856_132_97",
                       batch_code="S1")


class PartTest(SimpleTestCase):
    """토막 정규화 — `-` 는 토막을 가르는 글자라 안에 들어갈 수 없다."""

    def test_붙임표를_지운다(self):
        """화면이 서는 것보다는 낫지만 **두 지점이 같은 토막으로 누울 수 있다** —
        그것은 `check_db.py` 가 센다."""
        self.assertEqual(part("GC-03"), "GC03")

    def test_소문자를_올린다(self):
        self.assertEqual(part("gc03"), "GC03")

    def test_공백과_기호를_지운다(self):
        self.assertEqual(part("GC 03 (a)"), "GC03A")

    def test_비면_거절한다(self):
        for bad in ("", None, "   ", "---", "()"):
            with self.assertRaises(ValueError):
                part(bad)


class RoundTripTest(SimpleTestCase):
    """**번호에서 개체를 되찾을 수 있어야 한다.** 못 되찾으면 번호가 이름표이기만
    하고 열쇠가 아니게 되고, 검색이 규칙 하나로 안 된다."""

    CASES = [
        dict(site="RS23", locality="GC03", sample="071", viewpoint=3,
             obs_no=0, frame_seq=None, mask_key="1204_856_132_97",
             batch_code="S1"),
        dict(site="BP", locality="BP09", sample="0901", viewpoint=12,
             obs_no=0, frame_seq=27, mask_key="843_1502_96_210",
             batch_code="S1"),
        dict(site="RS23", locality="GC03", sample="369", viewpoint=7,
             obs_no=100, frame_seq=None, mask_key="410_222_88_140",
             batch_code="Y3"),
        dict(site="RS23", locality="GC03", sample="071", viewpoint=3,
             obs_no=0, frame_seq=None, mask_key="m1a2b3c4d",
             batch_code="M"),
        dict(site="GC03", locality="C1", sample="072", viewpoint=5,
             obs_no=0, frame_seq=None, mask_key="410_222_88_140",
             batch_code="S1"),
    ]

    def test_되읽은_것으로_다시_만들면_같다(self):
        for want in self.CASES:
            no = catalog_no(**want)
            got = parse(no)
            self.assertIsNotNone(got, no)
            self.assertEqual(got, want, no)
            self.assertEqual(catalog_no(**got), no)

    def test_손그림_키는_소문자로_돌려준다(self):
        """대문자로 두면 그 키로 교정 행을 못 찾는다 (`data.MANUAL_KEY` 가
        소문자 16진수다)."""
        got = parse("RS23-GC03-071-g03-M1A2B3C4D-M")
        self.assertEqual(got["mask_key"], "m1a2b3c4d")

    def test_소문자로_쳐도_찾는다(self):
        """사람이 검색창에 소문자로 칠 수 있다 — 그때 안 찾아지는 것은 규칙이
        틀린 것이 아니라 화면이 쓸모없어지는 것이다."""
        got = parse("rs23-gc03-071-g03-1204_856_132_97-s1")
        self.assertEqual(got["site"], "RS23")
        self.assertEqual(got["batch_code"], "S1")
        self.assertEqual(got["mask_key"], "1204_856_132_97")

    def test_규칙에_안_맞는_것은_None_이다(self):
        for bad in ("", None, "RS23-GC03-071", "RS23-GC03-071-g03-S1",
                    "RS23-GC03-071-1204_856_132_97-S1",
                    "RS23-GC03-071-g03-1204_856_132_97",
                    "RS23-GC03-071-g03-1204_856-S1",
                    "그냥 글자"):
            self.assertIsNone(parse(bad), bad)


class BatchCodeSeedTest(SimpleTestCase):
    """묶음 코드의 **첫 제안**. 번호에는 안 쓴다 — 자동값이 번호로 새면 라벨을
    고치는 순간 이미 적어 둔 번호가 바뀐다."""

    def test_라벨에서_글자를_뽑는다(self):
        self.assertEqual(batch_code_seed("yolo-시험"), "YOLO")
        self.assertEqual(batch_code_seed("yolo-3차"), "YOLO3")

    def test_회차가_다르면_다른_씨앗이_나온다(self):
        """`yolo-3차`·`yolo-4차` 가 같은 글자로 누우면 두 회차의 번호가 겹친다."""
        self.assertNotEqual(batch_code_seed("yolo-3차"),
                            batch_code_seed("yolo-4차"))
