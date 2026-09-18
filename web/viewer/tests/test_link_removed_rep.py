"""DiaRUGA v0.29.0 `tests/test_link_removed_rep.py` 에서 왔다.

묶음에 지운 판이 섞여도 **얼굴은 산 판이어야 한다** (DiaRUGA 151).

## 규칙이 둘이었고, 하나를 골랐다

`check_db` 는 **묶음 안에 지운 마스크가 하나라도 있으면** 걸었는데, `/review` 는
그것을 일부러 허용한다(`test_object_link.test_삭제는_안_번진다` — *묶음은 정체에
대한 말이고 오검출 판정은 판마다 다르다*). 카탈로그 문만 거절하고 있었다.

**허용하는 쪽으로 정했다** (사용자 방침 2026-08-25). 한 프레임에서만 크게·흐리게
잘못 잡힌 것을 지울 수 있어야 한다. 그러면 남는 해악은 하나다 — **개체의 얼굴이
오검출이 되는 것.**

## 여기서 보는 것 셋

1. 카탈로그 카드가 **지운 마스크로 그려지지 않는가** (`link_mains`).
   운영에서 `obj#8094` 가 그 자리였다 — 지운 프레임이 189×258 로 멤버 중 가장
   커서, 카드가 계속 그 마스크를 그리고 있었다
2. 대표를 지우면 **얼굴이 산 판으로 옮겨 가는가** (`save_review`)
3. `check_db` 가 **그것만** 세는가 — 지운 판이 섞인 것 자체는 이제 문제가 아니다
"""
import importlib.util
import sys
from pathlib import Path

from django.test import Client
from django.urls import reverse

from . import factories as fx
from .base import ForGIATestCase
from .. import data
from ..models import ForamObject, Image, ObjectReview, RunBatch

_PATH = Path(__file__).resolve().parents[3] / "ops" / "check_db.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_db_link_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class LinkRemovedRepTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)
        RunBatch.objects.filter(for_review=True).update(code="S1")
        cls.batch = RunBatch.objects.get(label="yolo-시험")
        # **프레임에도 검출을 세운다.** 안 세우면 멤버의 마스크가 실재하지 않아
        # `check_links` 의 다른 검사가 걸리고, 이 시험이 보려는 것과 섞인다.
        cls.extra = fx.add_frame_detections(cls.w.vp)
        cls.stack_img = Image.objects.get(viewpoint=cls.w.vp, kind="stack")
        cls.frame_img = cls.extra[0][1]

    def setUp(self):
        super().setUp()
        self.check_db = _load()
        self.key = self.w.keys()[0]

        # 합성본과 프레임 하나를 한 개체로 묶는다. **프레임 쪽을 더 크게** 만든다
        # — 운영에서 걸린 자리가 그 모양이다(`obj#8094` 는 지운 것이 가장 컸다).
        self.stack_row = fx.new_review(
            viewpoint=self.w.vp, image=self.stack_img, batch=self.batch,
            mask_key=self.key, bind_method="exact",
            geom={"bbox": [10, 10, 100, 100]})
        self.frame_row = fx.new_review(
            viewpoint=self.w.vp, image=self.frame_img, batch=self.batch,
            mask_key=self.key, bind_method="exact",
            geom={"bbox": [10, 10, 400, 400]})
        self.obj = fx.link_reviews([self.stack_row, self.frame_row], rep=0)

    def problems(self):
        self.check_db.problems = []
        self.check_db.check_links()
        return [name for name, _n, _why in self.check_db.problems]

    def face_of(self):
        """카탈로그가 그 개체를 그릴 때 쓰는 판."""
        mains = data.link_mains(self.w.slide)
        best, _n = mains[(self.stack_row.image_id, self.stack_row.batch_id,
                          self.key)]
        return best

    # --- 1. 얼굴 고르기 -----------------------------------------------------

    def test_지운_판은_얼굴이_안_된다(self):
        """**대표가 지운 판이면 산 멤버로 물러난다** (151 · 자리는 152).

        152 부터 얼굴은 대표다. 그런데 대표가 지워져 있으면 그대로 쓸 수 없어
        산 것 중 가장 큰 것으로 물러나는데, **그 물러남이 실제로 도는지**를 본다
        — 안 물러나면 카드가 사람이 오검출이라고 지운 마스크를 그린다.
        """
        # 대표를 프레임(더 큰 쪽)으로 옮겨 놓는다 — 그것이 얼굴이어야 한다
        ObjectReview.objects.filter(foram_object=self.obj).update(is_rep=False)
        ObjectReview.objects.filter(pk=self.frame_row.pk).update(is_rep=True)
        self.assertEqual(self.face_of().pk, self.frame_row.pk,
                         "대표를 얼굴로 안 골랐다 — 앞의 전제가 깨졌다")
        ObjectReview.objects.filter(pk=self.frame_row.pk).update(removed=True)
        self.assertEqual(self.face_of().pk, self.stack_row.pk,
                         "지운 마스크를 개체의 얼굴로 그리고 있다")

    def test_대표를_그대로_쓴다(self):
        """**크기가 대표를 이기면 안 된다** (DiaRUGA 152). 프레임이 네 배 크다."""
        self.assertEqual(self.face_of().pk, self.stack_row.pk,
                         "크기가 대표를 이겼다")

    def test_전부_지웠으면_가장_큰_것으로_물러난다(self):
        """「지운 것」 화면이 그 카드를 그려야 한다 — 거기서는 지운 것이 볼 것이다.

        대표까지 지워졌고 물러날 산 멤버도 없다. **빈 얼굴을 내놓지 않는다.**
        """
        ObjectReview.objects.filter(foram_object=self.obj).update(removed=True)
        self.assertEqual(self.face_of().pk, self.frame_row.pk)

    # --- 2. 대표 옮기기 -----------------------------------------------------

    def test_대표를_지우면_산_판으로_옮긴다(self):
        # 대표를 프레임 쪽으로 옮겨 놓고, 그 판을 검토 화면에서 지운다
        ObjectReview.objects.filter(foram_object=self.obj).update(is_rep=False)
        ObjectReview.objects.filter(pk=self.frame_row.pk).update(is_rep=True)

        import json as _json
        r = Client().post(reverse("save_review"), _json.dumps({
            "slug": "rs23", "gid": self.w.vp.idx, "stem": self.w.stem(),
            "image": self.frame_img.pk,
            "removed": [self.key], "accepted": [], "labels": {},
            "note": "", "done": False,
        }), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content[:300])

        self.frame_row.refresh_from_db()
        self.stack_row.refresh_from_db()
        self.assertTrue(self.frame_row.removed, "지우지 못했다 — 앞의 전제가 깨졌다")
        self.assertFalse(self.frame_row.is_rep,
                         "지운 판이 그대로 개체의 얼굴이다")
        self.assertTrue(self.stack_row.is_rep,
                        "얼굴을 산 판으로 안 옮겼다")

    def test_살아_있는_판이_없으면_안_옮긴다(self):
        """전부 오검출인 개체는 얼굴을 고를 자리가 없다 — 억지로 세우지 않는다.

        **대표까지 지워진 상태를 만들어야 한다.** 대표가 성하면 이 함수는 애초에
        그 개체를 안 집으므로, 무엇을 빼도 0 이 나와 **시험이 아무것도 안 본다**
        — 처음에 그렇게 짰다가 되살려 보고 알았다.
        """
        ObjectReview.objects.filter(foram_object=self.obj).update(is_rep=False,
                                                                  removed=True)
        ObjectReview.objects.filter(pk=self.frame_row.pk).update(is_rep=True)
        n = data._reelect_removed_reps([self.obj.pk])
        self.assertEqual(n, 0, "얼굴을 고를 자리가 없는데 옮겼다")
        self.frame_row.refresh_from_db()
        self.assertTrue(self.frame_row.is_rep, "대표가 사라졌다")

    # --- 3. 검사가 무엇을 세는가 -------------------------------------------

    def test_지운_판이_섞인_것만으로는_안_잡는다(self):
        """**허용하기로 한 상태다** — 여기서 걸면 정상 자료가 매번 경고를 낸다."""
        ObjectReview.objects.filter(foram_object=self.obj).update(is_rep=False)
        ObjectReview.objects.filter(pk=self.stack_row.pk).update(is_rep=True)
        ObjectReview.objects.filter(pk=self.frame_row.pk).update(removed=True)
        self.assertNotIn("묶음의 대표가 지운 판이 아니다", self.problems())

    def test_대표가_지운_판이면_잡는다(self):
        ObjectReview.objects.filter(foram_object=self.obj).update(is_rep=False)
        ObjectReview.objects.filter(pk=self.frame_row.pk).update(is_rep=True,
                                                                removed=True)
        self.assertIn("묶음의 대표가 지운 판이 아니다", self.problems())

    def test_전부_지운_개체는_이_검사가_안_센다(self):
        """얼굴을 고를 자리가 없다 — 묶음의 문제가 아니라 그 개체가 통째로
        오검출이라는 말이고, 등급·자세 검사가 그쪽을 본다."""
        ObjectReview.objects.filter(foram_object=self.obj).update(removed=True,
                                                                  is_rep=False)
        ObjectReview.objects.filter(pk=self.frame_row.pk).update(is_rep=True)
        self.assertNotIn("묶음의 대표가 지운 판이 아니다", self.problems())
