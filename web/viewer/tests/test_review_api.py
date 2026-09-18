"""DiaRUGA v0.29.0 `tests/test_review_api.py` 에서 왔다.

`/review` 의 계약. **이 저장소에서 자료를 잃은 두 사고가 전부 여기다.**

`/review` POST 는 **그 시야의 교정 전체를 갈아치운다.** "뷰어는 늘 전체를
보낸다" 는 전제이고, 깨지면 나머지를 지운다. 실제로 두 번 당했다.

    027  빈 키 목록을 운영 DB 로 보내 교정 14건
    051  "읽기 전용" 이라 적어 놓고 CSS 로 버튼만 감춘 화면에서 37건
    053  싱글턴 시야 12개가 남의 슬라이드를 열고 있었고, "검토 완료" 만 누른
         빈 payload 하나가 남의 교정 7건을 지웠다

**그러니 "전체를 갈아치운다" 는 것 자체는 시험으로 못 박아야 할 정상 동작이다** —
그것이 "교정 전체 초기화" 의 유일한 경로다. 시험이 지키는 것은 그 동작이 아니라
**그것이 엉뚱한 시야에 걸리지 않는다**는 쪽이다.
"""
import json

from django.test import Client
from django.urls import reverse

from .base import ForGIATestCase
from . import factories as fx
from ..models import ObjectReview, ViewpointReview


class ReviewContractTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)

    def setUp(self):
        self.c = Client()

    def post(self, payload, expect=200):
        r = self.c.post(reverse("save_review"), data=json.dumps(payload),
                        content_type="application/json")
        self.assertEqual(r.status_code, expect, r.content[:300])
        return r

    def full(self, **over):
        """화면이 보내는 것과 같은 모양 — **늘 전체를 보낸다.**"""
        p = {"stem": self.w.stem(), "slug": self.w.slug, "gid": self.w.vp.idx,
             "done": False, "removed": [], "accepted": [],
             "labels": {}, "notes": {}}
        p.update(over)
        return p

    # --- 정상 -------------------------------------------------------------

    def test_교정을_저장한다(self):
        k = self.w.keys()[0]
        self.post(self.full(removed=[k], labels={k: "broken"}, done=True))

        obj = ObjectReview.objects.get(mask_key=k)
        self.assertTrue(obj.removed)
        self.assertEqual(obj.label, "broken")
        # **열쇠는 `(image, mask_key)` 다** — 현재 검출의 이미지에 붙어야 한다.
        self.assertEqual(obj.image_id, self.w.detection().image_id)
        self.assertTrue(ViewpointReview.objects.get(viewpoint=self.w.vp).done)

    def test_교정_없이_검토_완료만_켤_수_있다(self):
        """고칠 것이 없어서 교정이 비어도 검토는 끝났을 수 있다.

        **109 에서 뜻이 하나 붙었다.** 완료를 누르면 남은 통과분에 서명이
        생긴다(`auto_confirmed`) — 그래서 행 수는 0 이 아니다. 지킬 것은 *사람이 안
        한 판단이 안 생긴다*는 쪽이다: 지움·되살림·분류·코멘트가 전부 비어야 한다.
        """
        self.post(self.full(done=True))
        self.assertTrue(ViewpointReview.objects.get(viewpoint=self.w.vp).done)
        rows = list(ObjectReview.objects.all())
        self.assertTrue(rows, "완료를 눌렀는데 서명이 안 생겼다")
        for o in rows:
            self.assertTrue(o.auto_confirmed)
            self.assertEqual((o.removed, o.accepted, o.label, o.note),
                             (False, False, "", ""),
                             "완료만 눌렀는데 사람이 안 한 판단이 생겼다")

    def test_빈_목록은_그_시야의_교정을_전부_지운다(self):
        """**이것이 정상 동작이다** — "교정 전체 초기화" 의 유일한 경로다.

        027 이 사고였던 것은 이 동작이 틀려서가 아니라 **빈 payload 가 나갈 수
        있는 화면**이 있었기 때문이다. 동작 쪽을 고치면 초기화를 못 하게 된다.
        """
        for k in self.w.keys()[:2]:
            fx.add_review(self.w.vp, k, removed=True)
        self.assertEqual(ObjectReview.objects.count(), 2)

        self.post(self.full())
        self.assertEqual(ObjectReview.objects.count(), 0)

    def test_표시가_사라진_행은_남지_않는다(self):
        k = self.w.keys()[0]
        self.post(self.full(removed=[k]))
        self.assertEqual(ObjectReview.objects.count(), 1)
        # 같은 키를 아무 표시 없이 다시 보내면 행이 지워진다.
        self.post(self.full())
        self.assertEqual(ObjectReview.objects.count(), 0)

    # --- 남의 교정을 지우지 않는다 (051 · 053) ----------------------------

    def test_현재_검출에_없는_키가_섞이면_409_이고_아무것도_안_바뀐다(self):
        """051 — `/engine/` 이 YOLO 검출을 그리고 있었고, 마스크 클릭 하나가
        그 키만 담은 POST 를 보내 369cm g32 의 교정 37건을 지웠다.

        **409 를 내는 것만으로는 모자란다** — 그 전에 있던 교정이 그대로 있어야
        한다. 반쯤 지우고 거절하면 사고가 그대로다.
        """
        keep = self.w.keys()[0]
        fx.add_review(self.w.vp, keep, removed=True)
        before = ObjectReview.objects.count()

        r = self.post(self.full(removed=["9999_9999_10_10"]), expect=409)
        self.assertFalse(r.json()["ok"])
        self.assertEqual(ObjectReview.objects.count(), before)
        self.assertTrue(ObjectReview.objects.get(mask_key=keep).removed)

    def test_다른_슬라이드의_시야를_건드리지_않는다(self):
        """053 — 프레임 이름은 슬라이드끼리 겹친다.

        같은 이름의 프레임을 가진 싱글턴 시야 둘을 세우고, `stem` 만 보내는
        옛 화면의 요청이 **아무거나 집지 않고 거절하는지**를 본다.
        """
        a = fx.make_world(slug="aa", site_code="AA", loc_code="GC01",
                          with_stack=False, frame_name="Snap-99999")
        b = fx.make_world(slug="bb", site_code="BB", loc_code="GC02",
                          with_stack=False, frame_name="Snap-99999")
        fx.add_review(b.vp, b.keys()[0], removed=True)
        before = ObjectReview.objects.filter(viewpoint=b.vp).count()

        # slug·gid 없이 stem 만. 이름이 겹치므로 어느 것인지 알 수 없다.
        r = self.post({"stem": "Snap-99999", "done": True, "removed": [],
                       "accepted": [], "labels": {}, "notes": {}}, expect=409)
        self.assertIn("어느 것인지", r.json()["error"])
        self.assertEqual(ObjectReview.objects.filter(viewpoint=b.vp).count(),
                         before)
        self.assertFalse(ViewpointReview.objects.get(viewpoint=a.vp).done)
        self.assertFalse(ViewpointReview.objects.get(viewpoint=b.vp).done)

    def test_화면과_저장_대상이_어긋나면_409_다(self):
        """`(slug, gid)` 가 정답이고 `stem` 은 검증용이다."""
        r = self.post(self.full(stem="Snap-00000"), expect=409)
        self.assertIn("어긋난다", r.json()["error"])

    def test_모르는_시야는_409_다(self):
        self.post(self.full(slug="없는슬라이드", gid=0), expect=409)
        self.post(self.full(gid=999), expect=409)

    # --- 자동 처리가 안 끝난 슬라이드 -------------------------------------

    def test_처리_중인_슬라이드는_저장을_안_받는다(self):
        """반쯤 처리된 슬라이드를 검토하면 아직 안 돌아간 시야의 검출이 뒤늦게
        들어오면서 이미 본 화면이 바뀐다 (DiaRUGA P01 §1)."""
        p = fx.make_world(slug="pending", site_code="PP", loc_code="GC09",
                          state="processing")
        r = self.c.post(
            reverse("save_review"),
            data=json.dumps({"stem": p.stem(), "slug": p.slug, "gid": 0,
                             "done": True, "removed": [], "accepted": [],
                             "labels": {}, "notes": {}}),
            content_type="application/json")
        self.assertEqual(r.status_code, 409, r.content[:200])
        self.assertFalse(ViewpointReview.objects.get(viewpoint=p.vp).done)

    # --- 망가진 입력 -------------------------------------------------------

    def test_망가진_입력은_400_이다(self):
        bad = [
            ({"stem": "../../etc/passwd"}, "경로가 든 stem"),
            ({"stem": self.w.stem(), "slug": self.w.slug, "gid": "abc"}, "gid"),
            (self.full(removed="전부"), "목록이 아닌 removed"),
            (self.full(labels={"없는키!!": "broken"}), "키 규칙 위반"),
            (self.full(labels="broken"), "dict 가 아닌 labels"),
        ]
        for payload, why in bad:
            with self.subTest(why=why):
                r = self.c.post(reverse("save_review"),
                                data=json.dumps(payload),
                                content_type="application/json")
                self.assertEqual(r.status_code, 400, f"{why} 가 통과했다")

    def test_모르는_분류는_조용히_버린다(self):
        """분류표에 없는 값은 저장하지 않는다 — 400 이 아니라 무시다.

        분류를 `active=False` 로 끈 뒤에도 옛 탭이 그 값을 보낼 수 있어서,
        여기서 400 을 내면 그 화면이 아무것도 저장하지 못하게 된다.
        """
        k = self.w.keys()[0]
        self.post(self.full(removed=[k], labels={k: "없는분류"}))
        self.assertEqual(ObjectReview.objects.get(mask_key=k).label, "")

    def test_json_이_아니면_400_이다(self):
        r = self.c.post(reverse("save_review"), data="이건 JSON 이 아니다",
                        content_type="application/json")
        self.assertEqual(r.status_code, 400)


class PostOnlyTest(ForGIATestCase):
    """**주소를 누르는 것만으로 판단이 뒤집히면 안 된다.**

    쓰는 길은 전부 POST 전용이다. GET 으로 열어 두면 링크 하나·프리페치 하나가
    슬라이드 하나의 검토 상태를 갈아치운다.
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23")

    def test_쓰는_주소는_GET_을_거절한다(self):
        w = self.w
        urls = [
            reverse("save_review"),
            reverse("mark_all", args=[w.slug]),
            reverse("split_group", args=[w.slug, w.vp.idx]),
            # `outcrop_edit` 은 ForGIA 에 없다 (노두 갈래 없음)
        ]
        for url in urls:
            with self.subTest(url=url):
                r = Client().get(url)
                self.assertEqual(r.status_code, 405, f"{url} 이 GET 을 받았다")


class NoteIsCatalogOnlyTest(ForGIATestCase):
    """**개체 코멘트는 개체 카탈로그에서만 적는다** (0036, 사용자 2026-08-12).

    코멘트가 개체(`ForamObject`)로 옮겨 오면서 *적는 자리*도 하나로 모았다.
    적는 자리가 둘이면 사람이 **어디에 적었는지를 기억해야** 하고, 번지기(DiaRUGA 106)
    뒤로는 한 개체이 판 넷에 걸쳐 있어 더 그렇다.

    지킬 것이 둘이고 **층이 다르다** — 051 이 가르친 그 규칙이다.

    1. **서버가 안 받는다.** 옛 탭(배포 중에 열려 있던 화면)이 `notes` 를 실어
       보내도 개체의 글은 안 바뀐다. 오류로 물리지 않는 것은, 그 저장에 함께
       실린 삭제·되살림까지 잃기 때문이다
    2. **화면이 되는 것처럼 보이지도 않는다.** 목록·팝업·우클릭 항목을 전부
       걷었다 — 저장만 잠그면 사람이 적어 놓고 사라진 것을 나중에 안다
    """

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)

    def setUp(self):
        self.c = Client()

    def test_옛_탭이_보낸_코멘트를_흘린다(self):
        k = self.w.keys()[0]
        r = self.c.post(reverse("save_review"), data=json.dumps(
            {"stem": self.w.stem(), "slug": self.w.slug, "gid": self.w.vp.idx,
             "done": False, "removed": [], "accepted": [],
             "labels": {k: "broken"}, "notes": {k: "옛 탭이 보냈다"}}),
            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content[:300])
        obj = ObjectReview.objects.get(mask_key=k)
        self.assertEqual(obj.label, "broken", "함께 온 분류까지 물렸다")
        self.assertEqual(obj.note, "", "이 화면이 개체 코멘트를 적었다")

    def test_화면에_개체_코멘트를_적는_자리가_없다(self):
        html = self.c.get(reverse("group", args=[self.w.slug, self.w.vp.idx])
                          ).content.decode()
        # **실패할 수 있는 것만 고른다** (DiaRUGA 064). 넷 다 옛 판의 이 화면에 실제로
        # 있던 글자다 — 되돌리면 잡힌다. 주석에 적힌 이름(`.noteedit` 를 걷었다는
        # 기록)까지 걸면 시험이 문서를 막고, 그때는 실패할 수도 없다.
        for mark in ('id="nlist-', "noteEdit.className", "openNoteEdit(",
                     "📝 코멘트 달기"):
            self.assertNotIn(mark, html, f"걷어낸 자리가 남아 있다: {mark}")
        # **시야 코멘트 상자는 183 에서 걷었다** (사용자 2026-09-04). 570 시야에
        # 6건이었고, 그 자리는 개체 카탈로그 칸이 쓴다 — **DB 의 기록과 서버의
        # 문(`{"only": "note"}`)은 그대로 둔다.** 지운 것은 화면뿐이다.
        self.assertNotIn('id="gnote-', html, "걷어낸 시야 메모 상자가 남아 있다")
        # 그 자리에 무엇이 들어왔는가 — 없으면 오른쪽 칸이 그냥 빈 것이다
        self.assertIn('id="catbox-', html, "개체 카탈로그 칸이 안 놓였다")
