"""DiaRUGA v0.29.0 `tests/test_check_db_grade_pose.py` 에서 왔다.

`check_db.py` 10번 — 등급·자세가 매길 수 있는 자리에만 붙어 있는가.

**세 번째 자리를 지키는 시험이다.** 화면이 파편에 칸을 안 보여주고
(`catalog.html`), 서버가 다시 받지 않고(`data.check_grade_pose`), 그 둘을 안
지나는 길을 이 검사가 본다 — 마이그레이션·일회성 스크립트·손으로 고친 SQL 은
예외를 안 내고 값을 앉힌다.

**그래서 시험이 옆문으로 심는다.** `data` 를 지나 심으면 서버가 막아 세우므로
검사가 잡을 자료를 못 만든다 — 여기서는 `update()` 로 곧장 넣는다. 그것이
이 검사가 상대하는 바로 그 길이다.

**되살려서 잡히는 것을 본다** — 심어 놓고 걸리는지, 치우면 풀리는지 둘 다 본다.
실패할 수 없는 검사는 없는 것보다 나쁘다(덮은 줄 알게 한다).
"""
import importlib.util
import sys
from pathlib import Path

from .base import ForGIATestCase
from . import factories as fx
from ..models import ForamObject, ObjectReview

_PATH = Path(__file__).resolve().parents[3] / "ops" / "check_db.py"


def _load():
    """`check_db.py` 를 모듈로 들인다.

    이 스크립트는 `django.setup()` 을 임포트 때 부르는데, 시험 안에서는 이미
    선 뒤라 두 번째 호출이 그냥 돌아온다. `sys.path` 를 만지는 것도 이미 그
    자리들이 들어 있어 더 하는 일이 없다.
    """
    spec = importlib.util.spec_from_file_location("check_db_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class CheckGradePoseTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_candidates=3)

    def setUp(self):
        self.check_db = _load()

    def run_check(self):
        """검사를 돌리고 **올라온 문제의 이름들**을 돌려준다."""
        self.check_db.problems = []
        self.check_db.check_grade_pose()
        return [name for name, _n, _why in self.check_db.problems]

    def put(self, *, label="foram", grade="", pose="", removed=False):
        det = self.w.detection()
        row = fx.new_review(
            viewpoint=self.w.vp, image=det.image, batch=det.batch,
            mask_key=self.w.keys()[0], bind_method="exact",
            removed=removed, label=label)
        # **옆문으로 심는다** — `data` 를 지나면 서버가 막아 세운다(머리말).
        # 0035 뒤로 등급도 개체에 산다.
        if grade or pose:
            ForamObject.objects.filter(pk=row.foram_object_id).update(
                grade=grade, pose=pose)
        return row

    # --- 잡는가 -----------------------------------------------------------

    def test_파편에_등급이_붙으면_잡는다(self):
        self.put(label="fragment", grade="A")
        self.assertIn("파편에 등급이 안 붙어 있다", self.run_check())

    def test_파편에_자세가_붙으면_잡는다(self):
        self.put(label="other", pose="umbilical")
        self.assertIn("파편에 자세가 안 붙어 있다", self.run_check())

    def test_판이_모두_지워진_개체에_등급이_남으면_잡는다(self):
        """"이 개체은 오검출이면서 A 다" 가 되면 **학습 자료가 모순이 된다** —
        등급으로 무엇을 먼저 학습시킬지 고르기 때문이다.

        0035 뒤로 등급이 개체에 살아 **판 하나가 아니라 개체 전체**를 본다.
        """
        self.put(grade="A", removed=True)
        self.assertIn("등급·자세가 살아 있는 개체에만 붙어 있다",
                      self.run_check())

    def test_판이_모두_지워진_개체에_자세가_남으면_잡는다(self):
        self.put(pose="spiral", removed=True)
        self.assertIn("등급·자세가 살아 있는 개체에만 붙어 있다",
                      self.run_check())

    # --- 멀쩡한 것을 잡지 않는가 -------------------------------------------

    def test_완형에_제대로_매긴_것은_안_잡는다(self):
        self.put(label="foram", grade="A", pose="umbilical")
        self.assertEqual(self.run_check(), [])

    def test_분류를_아직_안_정한_개체는_안_잡는다(self):
        """분류를 안 정한 개체가 흔하다 — 매기는 순서를 강제하지 않는다
        (`data.check_grade_pose` 와 같은 규칙)."""
        self.put(label="", grade="B", pose="other")
        self.assertEqual(self.run_check(), [])

    def test_치우면_풀린다(self):
        row = self.put(label="fragment", grade="A", pose="umbilical")
        self.assertNotEqual(self.run_check(), [])
        ForamObject.objects.filter(pk=row.foram_object_id).update(
            grade="", pose="")
        self.assertEqual(self.run_check(), [])

    def test_아무도_안_매겼으면_아무_말도_안_한다(self):
        """배포 직후의 모습이다 — 값이 0건이라고 문제가 되면 안 된다."""
        self.put(label="foram")
        self.assertEqual(self.run_check(), [])

    # --- 기준이지 제약이 아닌 것 -------------------------------------------

    def test_A_인데_종명이_비어도_문제가_아니다(self):
        """등급을 먼저 매기고 종명은 문헌을 찾아 나중에 적는 것이 실제 순서다
        (2026-08-11 사용자). 막으면 그 순서를 막는다 — **세되 문제로 안 올린다.**
        """
        self.put(label="foram", grade="A")   # 종명이 비어 있다
        self.assertEqual(self.run_check(), [])
