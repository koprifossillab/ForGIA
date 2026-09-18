"""DiaRUGA v0.29.0 `tests/test_export_review.py` 에서 왔다.

`export_review.py` 의 형식 3 — 시야 하나에 `(이미지, 묶음)` 여럿 (DiaRUGA P09 1단계).

**이 파일이 지키는 것은 감사 기록이다.** `review/<슬라이드>/g<n>.json` 은 사람이
347 시야를 검토해 만든 재생성 불가 자료를 git 에 남기는 유일한 길이고,
`--check` 로 DB 와 대조하는 도구다. 형식이 담지 못하는 상태가 생기면 그 순간부터
**감사 기록이 조용히 낡는다.**

형식 2 는 시야 하나에 그 짝 하나를 전제했다. 프레임별 검토와 묶음 갈아타기가
그것을 깬다 — 그래서 형식 2 는 깨진 DB 를 만나면 **쓰지 않고 멈췄다.** 형식 3 은
그 짝으로 묶어 담는다.

`export_review.py` 는 **Django 를 임포트하지 않고 sqlite3 로 읽기 전용으로만**
연다(`backup_db.py` 와 같은 자리). 그래서 여기서는 시험 DB 의 파일 경로를 직접
넘겨 부른다 — 시험이 만든 DB 도 결국 sqlite 파일이다.
"""
import importlib.util
import sqlite3
import json
from pathlib import Path
import tempfile

from django.db import connection

from .base import ForGIATestCase
from . import factories as fx
from ..models import ForamObject, ObjectReview, RunBatch

_SPEC = importlib.util.spec_from_file_location(
    "export_review",
    Path(__file__).resolve().parents[3] / "ops" / "export_review.py")
export_review = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(export_review)


class ExportFormat3Test(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=3)
        cls.extra = fx.add_frame_detections(cls.w.vp)

    def export(self):
        """지금 시험 DB 를 내보내고 그 시야의 파일을 파싱해 돌려준다.

        **살아 있는 연결을 그대로 넘긴다.** 시험 DB 는 메모리라 파일 경로가
        없는데, `export_review` 는 Django 를 안 쓰고 sqlite3 로 **파일**을 여는
        도구다(`connect()`).

        사본을 떠서 넘기는 쪽이 실제 쓰임(`--db <백업>`)에 가깝지만 **그렇게
        하면 멈춘다** — 시험 하나가 열어 둔 트랜잭션 위에서 backup API 가 잠금을
        기다린다(2분 넘게 안 끝났다). 여기서 시험하려는 것은 `fetch`·`render`
        이지 파일을 여는 일이 아니므로, 그 둘만 부른다.

        `row_factory` 는 되돌린다 — Django 의 뒤 질의가 튜플을 받으리라 믿고
        있어서, 켜 둔 채로 두면 **이 시험이 아니라 다음 시험이 깨진다.**
        """
        raw = connection.cursor().connection
        was = raw.row_factory
        raw.row_factory = sqlite3.Row
        try:
            views = export_review.fetch(raw)
        finally:
            raw.row_factory = was
        v = views[(self.w.slug, self.w.vp.idx)]
        text = export_review.render(v)
        return json.loads(text), text

    # --- 형식이 실제로 갈래를 담는가 ---------------------------------------

    def test_이미지가_여럿이면_묶음이_여럿_나온다(self):
        """형식 2 가 **멈추던** 자리다."""
        stack_img = self.w.detection().image
        frame, frame_img, _ = self.extra[0]
        fx.add_review(self.w.vp, self.w.keys()[0], removed=True)
        fx.add_review(self.w.vp, self.w.keys()[0], image=frame_img, label="broken")

        j, _ = self.export()
        self.assertEqual(j["format"], 4)
        self.assertEqual(j["n_images"], 2, f"묶음이 둘이어야 한다: {j}")
        self.assertEqual(j["n_objects"], 2)

        paths = [g["image"] for g in j["images"]]
        self.assertIn(stack_img.path, paths)
        self.assertIn(frame_img.path, paths)

    def test_합성본이_먼저_온다(self):
        """**차례가 정해져 있어야 diff 가 읽힌다.** DB 의 행 순서가 바뀔 때마다
        파일 전체가 다시 써지면 diff 가 자료 변화를 못 보여 준다."""
        _, frame_img, _ = self.extra[0]
        fx.add_review(self.w.vp, self.w.keys()[0], image=frame_img, label="broken")
        fx.add_review(self.w.vp, self.w.keys()[0], removed=True)

        j, _ = self.export()
        self.assertEqual([g["kind"] for g in j["images"]], ["stack", "frame"])

    def test_같은_이미지에_묶음이_둘이면_따로_담긴다(self):
        """회차를 갈아탄 뒤의 모습 — 같은 이미지에 옛 회차의 교정이 남아 있다.

        형식 2 는 여기서도 멈췄다: 한 파일 안에서 `key` 가 겹쳐 **어느 검출의
        판단인지가 사라진다.**
        """
        det = self.w.detection()
        old, _ = RunBatch.objects.get_or_create(kind="detect", label="옛회차")
        k = self.w.keys()[0]
        fx.add_review(self.w.vp, k, removed=True)          # 이번 회차
        fx.new_review(                        # 옛 회차, 같은 키
            viewpoint=self.w.vp, image=det.image, batch=old, mask_key=k,
            label="broken", geom={"bbox": [1, 2, 3, 4]})

        j, _ = self.export()
        self.assertEqual(j["n_images"], 2)
        batches = sorted(g["batch"] for g in j["images"])
        self.assertEqual(batches, ["yolo-시험", "옛회차"])
        for g in j["images"]:
            self.assertEqual([o["key"] for o in g["objects"]], [k],
                             "같은 키가 한 묶음에 몰렸다")

    def test_사람이_그린_개체는_묶음_이름이_빈다(self):
        """`batch=NULL` 은 어느 회차에도 안 속한다 (DiaRUGA P09 5.2)."""
        det = self.w.detection()
        fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=None, source="manual",
            mask_key="m0a1b2c3", label="broken", geom={"bbox": [1, 2, 3, 4]})

        j, _ = self.export()
        g = next(g for g in j["images"] if g["batch"] == "")
        self.assertEqual(g["objects"][0]["source"], "manual")

    # --- 안 잃었는가 -------------------------------------------------------

    def test_개체_수가_맞는다(self):
        for i, k in enumerate(self.w.keys()):
            fx.add_review(self.w.vp, k, removed=(i == 0), label="broken")
        j, _ = self.export()
        self.assertEqual(j["n_objects"], len(self.w.keys()))
        self.assertEqual(sum(len(g["objects"]) for g in j["images"]),
                         j["n_objects"])

    def test_기하는_반드시_실린다(self):
        """**`geom` 이 빠진 내보내기는 감사 기록이지 안전망이 아니다** —
        검출이 바뀌면 다시 붙일 근거가 그것뿐이다 (`export_review.py` 머리말).
        """
        fx.add_review(self.w.vp, self.w.keys()[0], removed=True)
        j, _ = self.export()
        o = j["images"][0]["objects"][0]
        self.assertTrue(o["geom"].get("bbox"), f"geom 이 비었다: {o}")
        self.assertTrue(o["geom"].get("polygon"))

    def test_개체는_한_줄로_적힌다(self):
        """`json.dumps(indent=…)` 로 쓰면 폴리곤 좌표까지 쪼개져 개체 하나가
        40줄이 된다 — diff 가 안 읽힌다."""
        fx.add_review(self.w.vp, self.w.keys()[0], removed=True)
        _, text = self.export()
        obj_lines = [ln for ln in text.splitlines()
                     if ln.strip().startswith('{"key"')]
        self.assertEqual(len(obj_lines), 1)


class ExportFormat4LinksTest(ForGIATestCase):
    """형식 4 — 같은 개체 묶음(`links`)이 감사 기록에 실린다 (DiaRUGA P11 4단계).

    묶음은 사람이 프레임마다 골라 만든 재생성 불가 자료다 — 교정과 같은 무게로
    git 에 남아야 하고, `--check` 가 표류를 잡아야 한다(렌더가 결정적이어야
    그 대조가 성립한다).
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=3)
        cls.extra = fx.add_frame_detections(cls.w.vp)

    def export(self):
        raw = connection.cursor().connection
        was = raw.row_factory
        raw.row_factory = sqlite3.Row
        try:
            views = export_review.fetch(raw)
        finally:
            raw.row_factory = was
        v = views[(self.w.slug, self.w.vp.idx)]
        text = export_review.render(v)
        return json.loads(text), text

    def link(self):
        batch = RunBatch.objects.get(label="yolo-시험")
        stack_img = self.w.detection().image
        _, frame_img, _ = self.extra[0]
        rows = [
            fx.new_review(viewpoint=self.w.vp, image=stack_img, batch=batch,
                          mask_key=self.w.keys()[0],
                          geom={"bbox_xywh": [40, 50, 60, 40]}),
            fx.new_review(viewpoint=self.w.vp, image=frame_img, batch=batch,
                          mask_key=self.w.keys()[0],
                          geom={"bbox_xywh": [41, 51, 60, 40]}),
        ]
        return fx.link_reviews(rows, rep=0)

    def test_묶음이_이름과_경로로_실린다(self):
        """id 가 아니라 **이름·경로**다 — 감사 기록은 두 DB 를 비교하는 물건이라
        저장소마다 달라지는 id 를 적으면 diff 가 거짓말을 한다."""
        self.link()
        j, text = self.export()
        self.assertEqual(j["n_links"], 1)
        lk = j["links"][0]
        self.assertEqual(lk["batch"], "yolo-시험")
        ms = lk["members"]
        self.assertEqual(len(ms), 2)
        # 합성본이 먼저다 (차례를 못 박는다)
        self.assertEqual(ms[0]["kind"], "stack")
        self.assertTrue(ms[0]["rep"])
        self.assertTrue(ms[0]["image"].endswith(".jpg"))
        self.assertNotIn('"image_id"', text)

    def test_렌더가_결정적이다(self):
        """`--check` 는 문자열 대조다 — 두 번 렌더해 다르면 대조가 성립하지 않는다."""
        self.link()
        _, a = self.export()
        _, b = self.export()
        self.assertEqual(a, b)

    def test_묶음만_있는_시야도_내보낸다(self):
        """교정 없이 묶음만 있어도 재생성 불가다 — 빠뜨리면 조용히 잃는다."""
        self.link()
        # 교정을 하나도 안 만들었다 — groups 가 비고 links 만 있다
        j, _ = self.export()
        self.assertEqual(j["n_links"], 1)

    def test_묶음이_없으면_links_는_빈_목록이다(self):
        # 표시가 하나도 없는 시야는 아예 안 내보낸다(맞는 동작) — 교정 하나를
        # 깔아 시야가 내보내지게 한 뒤 links 가 비어 있는 것을 본다.
        fx.add_review(self.w.vp, self.w.keys()[0], removed=True)
        j, text = self.export()
        self.assertEqual(j["links"], [])
        self.assertIn('"links": []', text)


class ExportSpeciesTest(ForGIATestCase):
    """동정한 종명이 감사 기록에 실리는가 (개체 카탈로그, 2026-08-10).

    **`label`·`note` 와 같은 무게의 재생성 불가 자료다** — 사람이 현미경을 보며
    적는다. 안전망이 그것을 안 담으면 DB 가 상했을 때 되살릴 수 없고, git 에
    안 남으면 "언제 무엇을 어떻게 고쳤나" 가 사라진다.

    **그런데 적은 개체에만 싣는다.** 늘 실으면 종명이 없는 파일 6,700행이 뜻
    없이 다시 쓰이고, 그 diff 에 그 사이의 진짜 변화가 묻힌다 — 감사 기록에서는
    그것이 값이다. `source`·`geom_edited` 와 같은 규칙이다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)

    def export(self):
        raw = connection.cursor().connection
        was = raw.row_factory
        raw.row_factory = sqlite3.Row
        try:
            views = export_review.fetch(raw)
        finally:
            raw.row_factory = was
        v = views[(self.w.slug, self.w.vp.idx)]
        text = export_review.render(v)
        return json.loads(text), text

    def put(self, **kw):
        det = self.w.detection()
        return fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=det.batch,
            mask_key=self.w.keys()[0], bind_method="exact", **kw)

    def objects(self, doc):
        return [o for g in doc["images"] for o in g["objects"]]

    def test_적은_종명이_실린다(self):
        self.put(species="Globigerina bulloides")
        doc, _ = self.export()
        o = self.objects(doc)[0]
        self.assertEqual(o["species"], "Globigerina bulloides")

    def test_안_적었으면_키가_아예_없다(self):
        """**빈 값을 실으면 종명 없는 6,700행이 뜻 없이 다시 쓰인다.**"""
        self.put(label="broken")
        doc, _ = self.export()
        self.assertNotIn("species", self.objects(doc)[0])

    def test_유형_코멘트와_함께_실린다(self):
        self.put(label="broken", note="가장자리가 넘쳤다",
                 species="Globigerina bulloides")
        o = self.objects(self.export()[0])[0]
        self.assertEqual((o["label"], o["note"], o["species"]),
                         ("broken", "가장자리가 넘쳤다", "Globigerina bulloides"))

    def test_한글_종명도_그대로_보인다(self):
        """`ensure_ascii=False` 라야 diff 가 읽힌다. ForGIA 는 학명이 `Taxon` 이라
        한글 종명은 없다 — 대신 한글 코멘트로 같은 것을 본다."""
        self.put(species="Globigerinita glutinata", note="가장자리가 깨졌다")
        _, text = self.export()
        self.assertIn("가장자리가 깨졌다", text)
        self.assertIn("Globigerinita glutinata", text)

    def test_개체는_여전히_한_줄이다(self):
        """키가 하나 늘어도 줄이 쪼개지면 diff 가 못 읽힌다.

        **내보내기에 담기는 것은 교정 행이지 후보 전부가 아니다** — 사람이 손댄
        것만 감사 기록에 남는다.
        """
        self.put(species="Globigerina bulloides")
        _, text = self.export()
        lines = [ln for ln in text.split("\n") if '"key":' in ln]
        self.assertEqual(len(lines), 1, text)
        self.assertIn("Globigerina bulloides", lines[0])

    def test_렌더가_결정적이다(self):
        """`--check` 는 문자열 대조다 — 두 번 렌더해 다르면 대조가 성립하지 않는다."""
        self.put(species="Globigerina bulloides")
        self.assertEqual(self.export()[1], self.export()[1])

    def test_칼럼이_없는_옛_DB_도_읽는다(self):
        """이 스크립트는 **두 시점을 비교하는 도구**라 0031 이전 백업도 읽어야 한다.
        `PRAGMA table_info` 로 칸을 세어 없으면 빈 값을 끼우는 그 갈래다."""
        self.put(species="Globigerina bulloides")
        raw = connection.cursor().connection
        was = raw.row_factory
        raw.row_factory = sqlite3.Row
        try:
            # **P12 뒤로 종명은 개체에 산다.** 옛 판을 흉내 내려면 그쪽 칸을
            # 떨어뜨려야 한다 — `export_review` 가 `foram_object_id` 칼럼의
            # 유무로 어느 판인지 가리므로, 여기서는 개체 테이블째 지운다.
            raw.execute("UPDATE viewer_foramobject SET taxon_id = NULL")
            views = export_review.fetch(raw)
        finally:
            raw.row_factory = was
        v = views[(self.w.slug, self.w.vp.idx)]
        doc = json.loads(export_review.render(v))
        self.assertNotIn("species",
                         [o for g in doc["images"] for o in g["objects"]][0])


class ExportGradePoseTest(ForGIATestCase):
    """등급·자세가 감사 기록에 실리는가 (2026-08-12).

    **사람이 눈으로 매긴 것이라 재생성 불가다** — `species` 와 같은 무게이고,
    지금은 **DB 한 곳에만 있다.** 값이 쌓이기 전에 넣는 것이 싸다: 안 넣고
    수백 건을 매기면 그동안의 판단이 감사 기록에 없다.

    **두 칸의 축이 반대라 사는 곳이 다르다** — 등급은 판정(`ObjectReview`),
    자세는 개체(`ForamObject`) 다. 내보내기에서는 둘 다 판정 한 줄에 실린다:
    개체에 사는 `species`·`label` 이 이미 그 자리에 실리고, **묶음이 아닌 개체는
    `links` 에 안 나오기 때문**이다(멤버가 하나면 묶음으로 안 센다). 자세를
    `links` 에만 실으면 **대부분의 개체에서 조용히 사라진다.**

    `species` 와 같은 규칙으로 **매긴 것에만 싣는다** — 그래서 형식 번호를 안
    올린다(값이 없는 파일은 한 글자도 안 바뀐다).
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)

    def export(self):
        raw = connection.cursor().connection
        was = raw.row_factory
        raw.row_factory = sqlite3.Row
        try:
            views = export_review.fetch(raw)
        finally:
            raw.row_factory = was
        v = views[(self.w.slug, self.w.vp.idx)]
        text = export_review.render(v)
        return json.loads(text), text

    def put(self, grade="", pose="", **kw):
        """판정 하나를 세운다 — **둘 다 개체에** 앉는다 (0035)."""
        det = self.w.detection()
        row = fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=det.batch,
            mask_key=self.w.keys()[0], bind_method="exact", **kw)
        if grade or pose:
            ForamObject.objects.filter(pk=row.foram_object_id).update(
                grade=grade, pose=pose)
        return row

    def objects(self, doc):
        return [o for g in doc["images"] for o in g["objects"]]

    def test_등급이_실린다(self):
        self.put(grade="A", label="foram")
        self.assertEqual(self.objects(self.export()[0])[0]["grade"], "A")

    def test_자세가_실린다(self):
        """개체에 사는 값이 판정 줄로 내려와야 한다 — 묶이지 않은 개체는
        `links` 에 안 나오므로 여기 없으면 아무 데도 없다."""
        self.put(pose="spiral", label="foram")
        self.assertEqual(self.objects(self.export()[0])[0]["pose"], "spiral")

    def test_안_매겼으면_키가_아예_없다(self):
        """**빈 값을 실으면 두 칸이 없는 6,700행이 뜻 없이 다시 쓰인다** —
        그 diff 에 그 사이의 진짜 변화가 묻힌다 (`species` 와 같은 규칙)."""
        self.put(label="foram")
        o = self.objects(self.export()[0])[0]
        self.assertNotIn("grade", o)
        self.assertNotIn("pose", o)

    def test_종명과_함께_한_줄에_실린다(self):
        """카드의 편집 칸이 다섯이 됐다 — 셋이 한 줄에 같이 보여야 diff 가 읽힌다."""
        self.put(grade="B", pose="umbilical", label="foram",
                 species="Globigerina bulloides")
        _, text = self.export()
        lines = [ln for ln in text.split("\n") if '"key":' in ln]
        self.assertEqual(len(lines), 1, text)
        for want in ('"grade": "B"', '"pose": "umbilical"',
                     '"species": "Globigerina bulloides"'):
            self.assertIn(want, lines[0])

    def test_묶인_판들이_등급_자세를_나눠_갖는다(self):
        """0035 뒤로 **둘 다 개체에 산다** — 묶인 판들이 한 값을 함께 본다.
        어느 판으로 보여줄지는 `is_rep` 가 따로 말한다. 감사 기록이 그 모양을
        그대로 보여야 나중에 *"C 인 것들이 자세 때문인가"* 를 물을 수 있다.
        """
        extra = fx.add_frame_detections(self.w.vp)
        batch = self.w.detection().batch
        _, frame_img, _ = extra[0]
        rows = [
            fx.new_review(viewpoint=self.w.vp, image=self.w.detection().image,
                          batch=batch, mask_key=self.w.keys()[0],
                          geom={"bbox_xywh": [40, 50, 60, 40]}),
            fx.new_review(viewpoint=self.w.vp, image=frame_img, batch=batch,
                          mask_key=self.w.keys()[0],
                          geom={"bbox_xywh": [41, 51, 60, 40]}),
        ]
        obj = fx.link_reviews(rows, rep=0)
        ForamObject.objects.filter(pk=obj.pk).update(grade="B", pose="umbilical")

        doc, _ = self.export()
        got = sorted((o["grade"], o["pose"]) for o in self.objects(doc))
        self.assertEqual(got, [("B", "umbilical"), ("B", "umbilical")],
                         "묶인 판이 한 값을 함께 봐야 한다")

    def test_렌더가_결정적이다(self):
        """`--check` 는 문자열 대조다 — 두 번 렌더해 다르면 대조가 성립하지 않는다."""
        self.put(grade="A", pose="umbilical", label="foram")
        self.assertEqual(self.export()[1], self.export()[1])

    def test_등급이_판정에_있던_백업도_읽는다(self):
        """**`0034` 대 백업이다** — `v0.11.2` 로 그 판이 실제로 배포되므로,
        그 사이에 뜬 백업에는 등급이 `viewer_objectreview` 에 앉아 있다.
        이 도구는 두 시점을 비교하는 물건이라 **그때 것도 읽어야 한다.**

        읽는 자리만 다르고 **내보내는 모양은 같아야 한다** — 그래야 판을 오간
        `review/` 를 그대로 diff 할 수 있다(형식 번호를 안 올린 이유다).
        """
        self.put(label="foram")
        raw = connection.cursor().connection
        was = raw.row_factory
        raw.row_factory = sqlite3.Row
        try:
            raw.execute("ALTER TABLE viewer_foramobject DROP COLUMN grade")
            raw.execute("ALTER TABLE viewer_objectreview "
                        "ADD COLUMN grade varchar(1) NOT NULL DEFAULT ''")
            raw.execute("UPDATE viewer_objectreview SET grade = 'B'")
            views = export_review.fetch(raw)
        finally:
            raw.row_factory = was
        v = views[(self.w.slug, self.w.vp.idx)]
        o = self.objects(json.loads(export_review.render(v)))[0]
        self.assertEqual(o["grade"], "B", "옛 자리의 등급을 못 읽었다")

    def test_칼럼이_없는_옛_DB_도_읽는다(self):
        """0034 이전 백업이다. 이 스크립트는 **두 시점을 비교하는 도구**라
        옛 판을 만나면 멈추지 말고 빈 값을 끼워야 한다.

        **등급이 사는 곳이 판마다 다르다** — `0034` 는 `viewer_objectreview`,
        `0035` 는 `viewer_foramobject` 다. 어느 쪽도 없는 그 앞 판을 흉내 낸다.
        """
        self.put(grade="A", pose="umbilical", label="foram")
        raw = connection.cursor().connection
        was = raw.row_factory
        raw.row_factory = sqlite3.Row
        try:
            raw.execute("ALTER TABLE viewer_foramobject DROP COLUMN grade")
            raw.execute("ALTER TABLE viewer_foramobject DROP COLUMN pose")
            views = export_review.fetch(raw)
        finally:
            raw.row_factory = was
        v = views[(self.w.slug, self.w.vp.idx)]
        o = self.objects(json.loads(export_review.render(v)))[0]
        self.assertNotIn("grade", o)
        self.assertNotIn("pose", o)
