"""DiaRUGA v0.29.0 `tests/test_fold_object_cls.py` 에서 왔다.

묶인 개체는 판마다 다른 유형으로 보이지 않는다 (사용자 지적 2026-09-04).

유형(`ForamObject.label`)은 개체에 살아서 **사람이 지정한 것은 어긋날 수가
없다** — 판정 행의 `label` 은 개체를 비추는 읽기 전용 통로다(DiaRUGA P12). 그래서
"묶인 개체의 유형이 따로 논다" 는 자리는 DB 가 아니라 **화면의 대체값**이다:
유형이 비면 화면이 판마다 *엔진이 그 판에서 본 값*으로 떨어지는데, 그 값이
판마다 다르다.

실측(09-04 백업)으로 판 둘 이상인 개체 1,629개 중 갈리는 것이 19개였고 전부
유형이 빈 개체였다. 갈래가 둘이다.

- **엔진 값이 프레임마다 다르다** (7건). 같은 개체인데 초점면마다
  `elongation` 이 문턱을 넘나든다. 되살린 판은 `_guess_cls` 가 다시 재는데
  거기는 1.4~2.0 이 무분류라, 옆 판이 봉상인데 혼자 무분류가 된다
- **번지기(DiaRUGA 106)가 앉힌 손그림 판이 섞인다** (12건). 손그림은 `Candidate` 가
  없어 유형이 아예 없고, 한 판만 엔진 후보에 붙어 있으면 그 판만 색이 있다

**저장에까지 번지면 안 된다.** 접은 값은 엔진의 추측이지 사람의 지정이 아니라
`cls_folded` 로 표시해 화면이 payload 에서 뺀다 — 손그림의 `drawn` 은 `cls` 를
그대로 `ForamObject.label` 에 적으므로(`_save_drawn`), 실려 가면 **추측이
사람이 지정한 유형 자리에 눌러앉는다.** 그 갈래는 화면 쪽이라
`browser/test_fold_object_cls.py` 가 본다.
"""
from .base import ForGIATestCase
from . import factories as fx
from .. import data
from ..models import Candidate, ForamObject, Image, RunBatch


class FoldObjectClsTest(ForGIATestCase):

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        # **판이 넷인 시야여야 성립한다** — 합성본 하나 + 프레임 셋.
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=2)
        cls.extra = fx.add_frame_detections(cls.w.vp)

    def setUp(self):
        self.batch = RunBatch.objects.get(label="yolo-시험")
        self.stack_img = Image.objects.get(viewpoint=self.w.vp, kind="stack")
        self.frame_imgs = [img for _f, img, _d in self.extra]
        self.key = self.w.keys()[0]

    # --- 거들 --------------------------------------------------------------

    def set_cls(self, img, cls, key=None):
        """그 판의 엔진 판정을 갈아 끼운다 — 초점면마다 달리 본 상태다."""
        n = Candidate.objects.filter(
            detection__image=img, detection__is_current=True,
            detection__run__batch=self.batch,
            mask_key=key or self.key).update(cls=cls)
        self.assertEqual(n, 1, f"{img.pk} 에 {key or self.key} 후보가 없다")

    def link(self, imgs, rep=0, label="") -> ForamObject:
        rows = [fx.new_review(viewpoint=self.w.vp, image=img,
                              batch=self.batch, mask_key=self.key,
                              bind_method="exact", label=label,
                              geom={"bbox_xywh": [40, 50, 60, 40]})
                for img in imgs]
        return fx.link_reviews(rows, rep=rep)

    def _screen(self, imgs, field, key=None) -> dict:
        """검토 화면이 판마다 그 마스크에 쓰는 값. `{이미지 pk: 값}`.

        **판을 골라서 본다** — 시야에는 안 묶인 판도 함께 있고, 그것들은
        서로 다른 개체이라 접히지 않는 것이 맞다.
        """
        want = {getattr(i, "pk", i) for i in imgs}
        ctx = data.group_detail(self.w.slug, self.w.vp.idx)
        out = {}
        for shot in (ctx["shot_dets"] or {}).values():
            if shot["image"] not in want:
                continue
            for c in shot["candidates"]:
                if c["key"] == (key or self.key):
                    out[shot["image"]] = c.get(field)
        self.assertEqual(set(out), want, "판이 화면에서 빠졌다")
        return out

    def shown(self, imgs, key=None) -> dict:
        return self._screen(imgs, "cls", key)

    def folded(self, imgs, key=None) -> dict:
        return {k: bool(v) for k, v in
                self._screen(imgs, "cls_folded", key).items()}

    # --- 1. 엔진 값이 판마다 다를 때 -----------------------------------------

    def test_판마다_다른_엔진_값이_하나로_접힌다(self):
        """되살린 판이 무분류로 떨어지는 그 모양이다(실측 7건)."""
        self.set_cls(self.stack_img, "foram")
        self.set_cls(self.frame_imgs[0], "broken")
        self.set_cls(self.frame_imgs[1], "broken")
        imgs = [self.stack_img, self.frame_imgs[0], self.frame_imgs[1]]
        self.link(imgs)

        shown = self.shown(imgs)
        self.assertEqual(len(set(shown.values())), 1,
                         f"판마다 유형이 다르다: {shown}")
        # 다수결 — 셋 중 둘이 봉상이다.
        self.assertEqual(set(shown.values()), {"broken"})

    def test_유형이_없는_판도_아는_값을_받는다(self):
        """엔진이 한 판에서만 분류했다. 나머지가 무분류로 남으면 안 된다."""
        self.set_cls(self.stack_img, "")
        self.set_cls(self.frame_imgs[0], "other")
        imgs = [self.stack_img, self.frame_imgs[0]]
        self.link(imgs)

        self.assertEqual(set(self.shown(imgs).values()), {"other"})

    def test_아는_판이_하나도_없으면_그대로_둔다(self):
        """접을 근거가 없다. 없는 값을 지어내지 않는다."""
        self.set_cls(self.stack_img, "")
        self.set_cls(self.frame_imgs[0], "")
        imgs = [self.stack_img, self.frame_imgs[0]]
        self.link(imgs)

        self.assertEqual(set(self.shown(imgs).values()), {None})

    def test_같은_값이_갈릴_때는_면적이_큰_판이_이긴다(self):
        """다수결이 안 갈리는 자리다 — 가장 잘 보이는 판의 것으로 정한다."""
        self.set_cls(self.stack_img, "foram")
        self.set_cls(self.frame_imgs[0], "broken")
        Candidate.objects.filter(detection__image=self.frame_imgs[0],
                                 mask_key=self.key).update(area_px=999999)
        imgs = [self.stack_img, self.frame_imgs[0]]
        self.link(imgs)

        self.assertEqual(set(self.shown(imgs).values()), {"broken"})

    # --- 2. 접은 것은 사람의 지정이 아니다 ------------------------------------

    def test_접은_판에_표시가_남는다(self):
        """**화면이 이것으로 저장에서 뺀다.** 없으면 엔진의 추측이 사람이
        지정한 유형 자리에 눌러앉는다 (`_save_drawn`)."""
        self.set_cls(self.stack_img, "foram")
        self.set_cls(self.frame_imgs[0], "broken")
        self.set_cls(self.frame_imgs[1], "broken")
        imgs = [self.stack_img, self.frame_imgs[0], self.frame_imgs[1]]
        self.link(imgs)

        folded = self.folded(imgs)
        # 접힌 것은 합성본뿐이다 — 나머지는 제 값이 곧 개체의 값이다.
        self.assertEqual(sum(folded.values()), 1, folded)

    def test_접어도_DB_는_안_바뀐다(self):
        """읽는 자리의 대체값이다. 사람이 지정하지 않은 유형은 계속 비어 있다."""
        self.set_cls(self.stack_img, "foram")
        self.set_cls(self.frame_imgs[0], "broken")
        imgs = [self.stack_img, self.frame_imgs[0]]
        obj = self.link(imgs)

        self.shown(imgs)
        self.assertEqual(ForamObject.objects.get(pk=obj.pk).label, "")

    def test_사람이_지정한_개체는_안_건드린다(self):
        """진실이 하나라 접을 것이 없다 — 판 전부가 이미 그 값을 본다."""
        self.set_cls(self.stack_img, "foram")
        self.set_cls(self.frame_imgs[0], "broken")
        imgs = [self.stack_img, self.frame_imgs[0]]
        self.link(imgs, label="broken")

        self.assertEqual(set(self.shown(imgs).values()), {"broken"})
        self.assertEqual(set(self.folded(imgs).values()), {False})

    # --- 3. 개체가 아닌 것은 안 접는다 ----------------------------------------

    def test_묶이지_않은_개체는_안_접는다(self):
        """판마다 개체가 따로 서 있으면 그것들은 서로 다른 개체이다."""
        self.set_cls(self.stack_img, "foram")
        self.set_cls(self.frame_imgs[0], "broken")
        fx.new_review(viewpoint=self.w.vp, image=self.stack_img,
                      batch=self.batch, mask_key=self.key,
                      bind_method="exact")
        fx.new_review(viewpoint=self.w.vp, image=self.frame_imgs[0],
                      batch=self.batch, mask_key=self.key,
                      bind_method="exact")

        shown = self.shown([self.stack_img, self.frame_imgs[0]])
        self.assertEqual(shown[self.stack_img.pk], "foram")
        self.assertEqual(shown[self.frame_imgs[0].pk], "broken")

    # --- 4. 카탈로그도 같은 것을 본다 -----------------------------------------

    def test_카탈로그_줄도_접힌_값을_본다(self):
        """카드는 개체 하나다 (DiaRUGA P18) — 어느 줄을 집었느냐로 유형이 달라지면
        같은 카드가 판을 바꿀 때마다 다른 유공충로 읽힌다."""
        self.set_cls(self.stack_img, "foram")
        self.set_cls(self.frame_imgs[0], "broken")
        self.set_cls(self.frame_imgs[1], "broken")
        obj = self.link([self.stack_img, self.frame_imgs[0],
                         self.frame_imgs[1]])

        rows = [r for r in data.candidate_rows(self.w.slug, all_images=True)
                if r.get("obj_id") == obj.pk]
        self.assertEqual(len(rows), 3, "판 셋이 다 나와야 한다")
        self.assertEqual({r["cls"] for r in rows}, {"broken"})
