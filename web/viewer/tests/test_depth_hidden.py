"""DiaRUGA v0.29.0 `tests/test_depth_hidden.py` 에서 왔다.

검토 화면의 캐러셀에 **깊이 맵이 안 놓인다** (123 · 사용자 2026-08-14).

검토에 쓸 일이 없는 판이다 — Z 좌표가 없어 상대값이고, **검출이 붙지 않아**
골라도 볼 것이 없다. 116 에서 저장이 대표 이미지로 가던 그 판이 이것이다.

**자료를 지우는 것이 아니다.** `Stack.depth_path` 도 `Image` 행(`kind="depth"`)도
그대로 있고 `data` 도 계속 싣는다 — 화면에서 안 내는 것뿐이다. 그래서 시험이
둘을 함께 본다: **화면에 없다**와 **자료는 그대로다**.

**깊이 맵이 있는 시야로 세운다.** 픽스처는 `depth_path` 를 안 채우므로, 안 채운
채로 "안 보인다" 를 확인하면 **실패할 수 없는 시험**이 된다 (DiaRUGA 064).
"""
from django.urls import reverse

from . import factories as fx
from .base import ForGIATestCase
from .. import data
from ..models import Stack


class DepthHiddenTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_viewpoints=1, n_candidates=3)
        st = Stack.objects.get(viewpoint=cls.w.vp)
        # 실제 파일을 가리키게 둔다 — 누가 조각을 되살리면 `{% thumb %}` 이
        # 파일을 열어야 하고, 그때 이 시험은 **없는 파일 오류가 아니라
        # "깊이 맵이 보인다" 로** 실패해야 한다.
        st.depth_path = st.focused_path
        st.save(update_fields=["depth_path"])
        cls.stack = st

    def review_html(self):
        r = self.client.get(reverse("group", args=["rs23", self.w.vp.idx]))
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.content.decode()

    def test_깊이_맵이_있는_시야다(self):
        """**이 시험 묶음이 뜻을 갖는 전제다** — 없으면 아래가 늘 통과한다."""
        self.assertTrue(self.stack.depth_path)
        self.assertTrue(
            data.stack_for(self.w.vp.tag)["depth_rel"],
            "자료 쪽에서 깊이 맵이 안 잡히면 화면 시험도 아무것도 안 본다")

    def test_캐러셀에_깊이_맵이_없다(self):
        """**표시가 아니라 요소를 짚는다.**

        "깊이 맵" 이라는 글자만 세면 **JS 주석에 걸린다** — 그 주석은 왜 뺐는지를
        적어 둔 것이고 브라우저로 그대로 나간다. 글자로 세는 시험은 주석을
        고칠 때마다 깨지고, 정작 단추가 되살아나도 그것을 못 가른다.
        """
        html = self.review_html()
        self.assertNotIn('data-title="깊이 맵"', html)
        self.assertNotIn('alt="깊이 맵"', html)
        self.assertNotIn('<span class="cap">깊이 맵</span>', html)

    def test_합성본과_프레임은_그대로다(self):
        """**빼는 것이 캐러셀을 비우는 것이 아니다.**"""
        html = self.review_html()
        self.assertIn('data-title="합성본"', html)
        self.assertIn('class="shot', html)

    def test_자료는_그대로다(self):
        """화면에서 안 내는 것과 지우는 것은 다르다."""
        self.stack.refresh_from_db()
        self.assertTrue(self.stack.depth_path)
        # `data` 가 계속 싣는다 — 화면 조각에 되붙이면 그날 바로 그려진다
        self.assertEqual(data.stack_for(self.w.vp.tag)["depth_rel"],
                         self.stack.depth_path)
