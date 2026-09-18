"""DiaRUGA v0.29.0 `tests/test_batch_shapes.py` 에서 왔다.

두 엔진이 **다른 모양**을 낸다 — 묶음을 갈아타면 화면이 그 모양을 따라간다.

실측한 비대칭이다.

| 묶음 | 검출이 붙는 자리 |
|---|---|
| `yolo-시험` | **합성본에만** (시야마다 하나) |
| `yolo-3차` | **합성본 + 프레임마다** (시야 452개에 프레임 검출 1,310개) |

P10 전에는 `?batch=` 로 읽기 전용으로 넘겨 보며 이 두 모양이 오가는 것을 확인했다.
P10 이 바꾼 것은 **무엇을 현재로 볼지 정하는 방식**이다 — 검출 행의 `is_current`
를 뒤집는 대신 `RunBatch.for_review` 깃발 하나를 옮긴다. 화면이 보는 것은
**두 깃발이 다 켜진 검출**이다.

여기서 지키는 것은 그 갈아타기가 **모양까지 따라가는가**다. 따라가지 않으면
고장이 조용하다 — 프레임을 넘겨 보는데 마스크가 안 그려지거나, 더 나쁘게는
**합성본의 마스크가 프레임 위에 그려진다.** 둘 다 예외가 안 난다.

그리고 **교정은 갈아타도 섞이지 않는다.** 열쇠가 `(image, batch, mask_key)` 라
SAM 을 보며 지운 것이 YOLO 화면에 지워진 채로 나오면 안 된다 — 다른 개체다.
"""
from .base import ForGIATestCase
from . import factories as fx
from .. import data, manage_data
from ..models import Detection, Image, ObjectReview, RunBatch


class BatchShapeTest(ForGIATestCase):
    """SAM(합성본만) ↔ YOLO(프레임+합성본) 를 오간다."""

    @classmethod
    def setUpTestData(cls):
        fx.make_classes()
        cls.w = fx.make_world(slug="rs23", n_frames=3, n_candidates=3)
        cls.vp = cls.w.vp

        # YOLO 모양 — 합성본 + 프레임 3장. 운영에서는 0027 이 묶음마다 정규화해
        # 두므로 `is_current` 는 켜져 있다. 갈아타기 전이라 화면은 아직 SAM 이다.
        cls.yolo = fx.add_other_engine(cls.vp, label="yolo-재학습", frames=True)
        Detection.objects.filter(run=cls.yolo).update(is_current=True)

    def sam(self):
        return RunBatch.objects.get(label="yolo-시험")

    def switch(self, batch):
        ok, msg = manage_data.set_review_batch(batch.pk)
        self.assertTrue(ok, msg)

    # --- 자료가 정말 비대칭인가 --------------------------------------------

    def test_시험_자료가_두_모양을_갖고_있다(self):
        """**전제부터 확인한다.** 비대칭이 없으면 아래가 전부 헛통과한다."""
        self.assertEqual(
            Detection.objects.filter(viewpoint=self.vp,
                                     run__batch=self.sam()).count(), 1)
        self.assertEqual(
            Detection.objects.filter(viewpoint=self.vp,
                                     run=self.yolo).count(), 4)

    # --- 갈아타면 판의 수가 따라가는가 --------------------------------------

    def test_SAM_은_합성본_하나만_현재다(self):
        dets = data.current_detections(self.vp)
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0].image.kind, "stack")

    def test_YOLO_로_갈아타면_프레임까지_현재가_된다(self):
        self.switch(self.yolo.batch)
        dets = data.current_detections(self.vp)
        self.assertEqual(len(dets), 4)
        self.assertEqual(
            sorted(d.image.kind for d in dets),
            ["frame", "frame", "frame", "stack"])

    def test_되돌리면_다시_하나다(self):
        self.switch(self.yolo.batch)
        self.switch(self.sam())
        dets = data.current_detections(self.vp)
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0].image.kind, "stack")

    def test_대표는_양쪽_다_합성본이다(self):
        """**처음 열리는 판이 바뀌면 안 된다.** 갈아탈 때마다 다른 사진이 열리면
        같은 시야를 보고 있다는 감각을 잃는다."""
        a = data.representative_detection(self.vp)
        self.switch(self.yolo.batch)
        b = data.representative_detection(self.vp)
        self.assertEqual(a.image.kind, "stack")
        self.assertEqual(b.image.kind, "stack")
        self.assertNotEqual(a.pk, b.pk, "같은 검출을 돌려준다 — 안 갈아탔다")

    # --- 화면이 그 모양대로 그리는가 ---------------------------------------

    def test_SAM_에서는_프레임에_마스크가_없다(self):
        d = data.group_detail("rs23", self.vp.idx)
        self.assertIsNone(d["shot_dets"], "판이 하나인데 갈아 끼울 자료가 있다")
        self.assertTrue(all(f["detection"] is None for f in d["frames"]),
                        "합성본 검출이 프레임에 붙어 있다")

    def test_YOLO_에서는_프레임마다_제_마스크가_있다(self):
        self.switch(self.yolo.batch)
        d = data.group_detail("rs23", self.vp.idx)

        self.assertIsNotNone(d["shot_dets"], "판이 넷인데 갈아 끼울 자료가 없다")
        self.assertEqual(len(d["shot_dets"]), 4)
        for f in d["frames"]:
            self.assertIsNotNone(f["detection"], f"{f['name']} 에 검출이 없다")

        # **판마다 저장이 제 이미지로 간다.** 여기가 어긋나면 사람이 보고 있는
        # 것과 다른 자리에 판단이 쌓인다 — 예외도 경고도 없다 (DiaRUGA P09 1단계)
        by_img = {s["image"] for s in d["shot_dets"].values()}
        self.assertEqual(len(by_img), 4)
        self.assertNotIn(None, by_img)

        # 판마다 개체가 저마다 다르다 (팩토리가 자리를 어긋나게 둔다)
        keys = [tuple(sorted(c["key"] for c in s["candidates"]))
                for s in d["shot_dets"].values()]
        self.assertEqual(len(set(keys)), 4, "판들이 같은 개체를 들고 있다")

    def test_갈아타면_합성본_마스크도_바뀐다(self):
        """같은 합성본인데 **어느 엔진이 낸 마스크인가**가 달라야 한다."""
        before = {c["key"] for c in
                  data.group_detail("rs23", self.vp.idx)["base_det"]["candidates"]}
        self.switch(self.yolo.batch)
        after = {c["key"] for c in
                 data.group_detail("rs23", self.vp.idx)["base_det"]["candidates"]}
        self.assertTrue(before and after)
        self.assertFalse(before & after, "두 엔진이 같은 개체를 내고 있다")

    # --- 교정이 섞이지 않는가 ----------------------------------------------

    def test_교정은_묶음을_안_넘어간다(self):
        """열쇠가 `(image, batch, mask_key)` 다. 넘어가면 **다른 개체를 지운다.**"""
        sam_key = data.group_detail(
            "rs23", self.vp.idx)["base_det"]["candidates"][0]["key"]
        fx.add_review(self.vp, sam_key, removed=True)

        self.switch(self.yolo.batch)
        d = data.group_detail("rs23", self.vp.idx)
        self.assertEqual(d["base_det"]["removed_candidates"], [],
                         "SAM 에서 지운 것이 YOLO 화면에 지워져 있다")

        self.switch(self.sam())
        d = data.group_detail("rs23", self.vp.idx)
        self.assertEqual([c["key"] for c in d["base_det"]["removed_candidates"]],
                         [sam_key], "돌아왔더니 지운 것이 사라졌다")

    def test_프레임_교정이_합성본으로_새지_않는다(self):
        """`(image, batch)` 의 `image` 쪽 — 판이 여럿일 때만 눌리는 자리다."""
        self.switch(self.yolo.batch)
        d = data.group_detail("rs23", self.vp.idx)

        frame = next(f for f in d["frames"] if f["detection"])
        key = frame["detection"]["candidates"][0]["key"]
        img = Image.objects.get(pk=frame["image_id"])
        fx.new_review(
            viewpoint=self.vp, image=img, batch=self.yolo.batch,
            mask_key=key, removed=True, bind_method="exact")

        d = data.group_detail("rs23", self.vp.idx)
        self.assertEqual(d["base_det"]["removed_candidates"], [],
                         "프레임에서 지운 것이 합성본에도 지워져 있다")
        shot = d["shot_dets"][frame["name"]]     # 판의 열쇠는 프레임 이름이다
        self.assertEqual([c["key"] for c in shot["gone"]], [key])
