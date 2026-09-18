#!/usr/bin/env python3
"""DB 의 무결성을 검사한다. **예외가 나지 않고 그냥 틀린 상태**를 잡는 것이 목적이다.

DiaRUGA v0.29.0 `ops/check_db.py`(925줄 · 검사 열둘)에서 왔다 — 1단계: 뼈대(5)·
층(7). 2단계: 판정 캐시(1)·현재 검출(2)·분류(4)·문턱(6). 3단계: 교정(3)·묶음(8)·
카탈로그(9)·등급·자세(10). 도감·출현 기록(11·12)은 5단계다. 번호를 DiaRUGA 와
맞춰 두는 이유는 `CLAUDE.md` 의 함정 목록이 "`check_db` 7번이 센다" 처럼 번호로
부르기 때문이다.

    python check_db.py
    python check_db.py --slide 260918_rs23-gc03_71cm_125um
    python check_db.py -v          # 어긋난 것의 예를 보여준다

돌려야 할 때:
  - 반입·그룹핑·합성·검출 뒤 (`poll_nas.sh` 가 돌린 뒤 숫자가 이상할 때)
  - `refilter`·`segment_forams` 를 돌린 뒤 · `judge.py` 의 규칙을 고친 뒤
  - 시야를 가르거나 소속을 옮긴 뒤
"""
import argparse
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import django

# 이 스크립트는 저장소 밖(/srv/ForGIA/scripts)에 복사해 두고 컨테이너 안에서
# 돌릴 수도 있다. 그때 Django 코드가 어디 있는지는 FORGIA_APP 이 알려 준다 —
# 이미지 안의 /app 이고, 뷰어 컨테이너가 쓰는 바로 그 코드다. 저장소에서 그냥
# 돌리면 자기 옆의 web/ 을 본다. (DiaRUGA 100)
APP = Path(os.environ.get("FORGIA_APP")
          or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(APP / "web"))
sys.path.append(str(APP))
sys.path.insert(0, str(APP / "pipeline"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "forgiaweb.settings")
django.setup()

from django.db.models import Count, Q                               # noqa: E402

import judge                                                        # noqa: E402
from viewer import catalog                                          # noqa: E402
from viewer.models import (Candidate, ClassDef, Detection,          # noqa: E402
                           ForamObject, Frame, Image, Locality,
                           ObjectReview, RunBatch, Sample, Slide,
                           Stack, Viewpoint, ViewpointReview)

VERBOSE = False
problems = []


def report(name, bad, total, why="", examples=()):
    """검사 하나의 결과. bad 가 0 이어야 한다."""
    ok = bad == 0
    mark = "  " if ok else "!!"
    print(f"{mark} {name:46s} {'OK' if ok else f'{bad}건'}"
          f"{'' if ok else '  <-- ' + why}")
    if not ok:
        problems.append((name, bad, why))
        if VERBOSE:
            for e in list(examples)[:5]:
                print(f"       {e}")
    return ok


def dets(slug=None):
    """**뷰어가 보여줄 검출** — 검토 대상 묶음의, 그 묶음 안 최신 것 (DiaRUGA P10)."""
    qs = (Detection.objects.reviewing()
          .select_related("thresholds", "viewpoint", "viewpoint__slide"))
    if slug:
        qs = qs.filter(viewpoint__slide__slug=slug)
    return qs


# --- 1. 판정이 (지표 + 문턱) 과 맞는가 --------------------------------------
def check_verdicts(slug=None):
    """`passed`·`cls`·`reject` 는 저장된 사실이 아니라 **순수 함수의 캐시**다.

    지표(불변) + 문턱(파라미터) 이면 판정이 결정된다. 다시 계산해서 저장된 값과
    같아야 한다. 어긋나는 경우 — refilter 가 도는 중에 끊겼다 · `Detection.thresholds`
    만 바꾸고 `Candidate` 를 안 고쳤다 · `judge.py` 의 규칙을 고치고 재판정을 잊었다.
    """
    bad = total = 0
    ex = []
    for det in dets(slug).prefetch_related("candidates"):
        th = judge.Thresholds(**(det.thresholds.as_dict() if det.thresholds
                                 else judge.DEFAULTS))
        cands = list(det.candidates.all())
        total += len(cands)
        recs = [{"_pk": c.pk, "bbox_xywh": c.bbox_xywh, "area_px": c.area_px,
                 "shape_ok": c.shape_ok, "major_um": c.major_um,
                 "long_side_um": c.long_side_um, "predicted_iou": c.predicted_iou}
                for c in cands]
        kept, rejected = judge.apply(recs, th)
        want = {r["_pk"]: (True, r["cls"] or "", "") for r in kept}
        for r in rejected:
            want[r["_pk"]] = (False, r.get("cls") or "", r["reject"])
        for c in cands:
            w = want.get(c.pk)
            if w is None:
                continue
            got = (c.passed, c.cls or "", c.reject or "")
            if got != w:
                bad += 1
                if len(ex) < 5:
                    ex.append(f"{det.viewpoint} {c.mask_key}: 저장 {got} != 계산 {w}")
    report("판정 == 지표 + 문턱 (캐시가 맞는가)", bad, total,
           "재판정이 필요하다: python refilter.py", ex)


# --- 2. 이미지마다 보여줄 검출이 하나 이하인가 ----------------------------------
def check_current(slug=None):
    """검토 대상 묶음 안에서만 센다 — 다른 묶음의 검출은 같은 이미지에 있는 것이 정상."""
    qs = Viewpoint.objects.all()
    if slug:
        qs = qs.filter(slide__slug=slug)
    counts = dict(Detection.objects.reviewing().filter(viewpoint__in=qs)
                  .values_list("image").annotate(n=Count("id")))
    many = {i: n for i, n in counts.items() if n > 1}
    report("이미지마다 보여줄 검출이 하나 이하", len(many), len(counts) or 1,
           "한 이미지에 둘 이상이다 — 어느 것을 보여줄지 알 수 없다",
           [f"image #{i}: {n}개" for i, n in list(many.items())[:5]])
    # **검토 대상이 정해져 있는가.** 없으면 뷰어가 아무것도 안 보여준다 — 500 도
    # 404 도 아니고 그냥 빈 화면이다. 검출이 하나도 없는 DB(1단계)에서는 묶음도
    # 없는 것이 정상이라 그때는 알리기만 한다
    n_rev = RunBatch.objects.filter(for_review=True).count()
    if Detection.objects.exists():
        report("검토 대상 묶음이 하나 정해져 있다", 0 if n_rev == 1 else 1, 1,
               f"검토 대상이 {n_rev}개다 — 0이면 화면이 비고, 둘이면 제약이 막았어야 한다",
               [] if n_rev == 1 else [f"for_review={n_rev}"])
    else:
        print("     (검출이 아직 없다 — 검토 대상 묶음도 없는 것이 정상)")
    # **검토 완료 줄이 제 자리에 있는가** (DiaRUGA 073). 줄이 두 종류다 —
    # `batch` 가 있는 줄은 "그 묶음을 다 봤다", `batch=NULL` 인 줄은 시야
    # 코멘트다. 섞이면 예외는 안 나고 **완료 표시가 묶음을 넘어 새거나
    # 코멘트가 묶음마다 갈라진다.**
    misplaced = list(ViewpointReview.objects
                     .filter(batch__isnull=True, done=True)[:5])
    report("코멘트 줄에 완료 표시가 없다",
           ViewpointReview.objects.filter(batch__isnull=True, done=True).count(),
           ViewpointReview.objects.count(),
           "묶음 없는 줄이 done=True 다 — 어느 묶음을 다 봤다는 말인지 알 수 없다",
           [f"vp #{r.viewpoint_id}" for r in misplaced])
    noted = list(ViewpointReview.objects
                 .filter(batch__isnull=False).exclude(note="")[:5])
    report("완료 줄에 코멘트가 없다",
           ViewpointReview.objects.filter(batch__isnull=False).exclude(note="").count(),
           ViewpointReview.objects.count(),
           "묶음에 달린 줄이 코멘트를 들고 있다 — 묶음을 갈면 사람이 쓴 글이 사라진다",
           [f"vp #{r.viewpoint_id}" for r in noted])
    with_det = set(Detection.objects.reviewing().filter(viewpoint__in=qs)
                   .values_list("viewpoint_id", flat=True))
    none = [vp for vp in qs if vp.id not in with_det]
    if none:
        print(f"     (검출이 없는 시야 {len(none)}개 — 아직 돌리지 않았다면 정상)")


# --- 4. 분류 -------------------------------------------------------------------
def check_classes(slug=None):
    """`ClassDef` 에 없는 분류가 붙어 있으면 화면에서 이름도 색도 없이 나온다."""
    known = set(ClassDef.objects.values_list("key", flat=True))
    qs = Candidate.objects.filter(detection__in=Detection.objects.reviewing()).exclude(cls="")
    if slug:
        qs = qs.filter(detection__viewpoint__slide__slug=slug)
    used = Counter(qs.values_list("cls", flat=True))
    bad = {k: v for k, v in used.items() if k not in known}
    report("개체 분류가 ClassDef 에 있다", len(bad), len(used),
           f"정의되지 않은 분류: {list(bad)}", [f"{k}: {v}개" for k, v in bad.items()])
    lab = Counter(ForamObject.objects.exclude(label="").values_list("label", flat=True))
    bad2 = {k: v for k, v in lab.items() if k not in known}
    report("사람이 지정한 분류가 ClassDef 에 있다", len(bad2), len(lab),
           f"정의되지 않은 분류: {list(bad2)}")
    # 판정이 내는 분류(`judge.PASS_CLS`)가 표에 있어야 한다 — 없으면 통과분이
    # 이름 없는 분류가 된다
    report("판정 분류(judge.PASS_CLS)가 ClassDef 에 있다",
           0 if judge.PASS_CLS in known else 1, 1, f"'{judge.PASS_CLS}' 행이 없다")
    active = list(ClassDef.objects.filter(active=True).values("key", "hotkey", "color"))
    nokey = [c["key"] for c in active if not (c["hotkey"] or "").strip()]
    report("활성 분류에 단축키가 있다", len(nokey), len(active), f"단축키 없음: {nokey}")
    nocolor = [c["key"] for c in active if not (c["color"] or "").strip()]
    report("활성 분류에 색이 있다", len(nocolor), len(active), f"색 없음: {nocolor}")


# --- 6. 문턱이 갈라져 있는가 ----------------------------------------------------
def check_thresholds(slug=None):
    """문턱은 **슬라이드 안에서** 하나여야 한다 — 갈라지면 시야 간 개수를 비교할 수 없다."""
    per = defaultdict(Counter)
    for d in dets(slug):
        per[d.viewpoint.slide.slug][d.thresholds_id] += 1
    mixed = {s: c for s, c in per.items() if len(c) > 1}
    if not mixed:
        sets = {tid for c in per.values() for tid in c}
        print(f"   슬라이드마다 문턱이 하나다 ({len(per)}개 슬라이드 · 문턱 조합 {len(sets)}가지)")
        return
    print(f"!! 슬라이드 안에서 문턱이 갈라졌다 — {len(mixed)}개  <-- 시야 간 개수를 비교할 수 없다")
    for s, c in sorted(mixed.items()):
        print(f"       {s}: " + " · ".join(f"#{t}:{n}" for t, n in c.most_common()))
    problems.append(("슬라이드 내 문턱 혼재", len(mixed), "refilter.py --slide 로 맞출 것"))


# --- 3. 교정이 실제 개체에 붙어 있는가 ---------------------------------------
def check_reviews(slug=None):
    """교정이 현재 검출의 개체를 가리키고 있는가.

    **`mask_key` 가 맞는지로 보면 안 된다.** 검출을 다시 돌리면 SAM2 가 미세하게
    다른 마스크를 내서 키가 어긋나는데, 그때 `rebind.py` 가 IoU 로 다시 맺어 준다
    (실측: 재검출 한 번에 67건 중 exact 26 · iou 40 · 고아 1). 키만 보면 정상적으로
    맺힌 40건이 전부 고아로 잡힌다.

    진짜 고아는 **가리키는 개체가 아예 없는 것**이다. 지우지 않는다 —
    재생성 불가한 자료이고 `geom` 에 기하를 스스로 들고 있다.

    ## 무엇을 세지 않는가 (DiaRUGA P09 0단계)

    - **사람이 그린 개체**(`source="manual"`)는 후보가 없는 것이 정상이다.
      세면 마스크 편집을 쓰는 만큼 이 검사가 빨개진다
    - **지금 안 보고 있는 묶음의 교정**도 고아가 아니다. YOLO 로 갈아타면 SAM2
      시절 교정이 통째로 "현재 검출 밖" 이 되는데 그것은 **보존이지 고장이
      아니다**(DiaRUGA P09 5.1). 현재 검출이 속한 묶음의 교정만 본다
    """
    qs = ObjectReview.objects.select_related("viewpoint")
    if slug:
        qs = qs.filter(viewpoint__slide__slug=slug)
    all_reviews = list(qs)

    # 현재 검출에 속한 개체 id 집합. 교정이 이 밖을 가리키면 옛 검출에 남은 것이다.
    cur_det = Detection.objects.reviewing().filter(
        **({"viewpoint__slide__slug": slug} if slug else {}))
    current = set(Candidate.objects.filter(detection__in=cur_det)
                  .values_list("id", flat=True))
    # 지금 화면이 보고 있는 묶음들. 이 밖의 교정은 다른 회차의 기록이다.
    cur_batches = {b for b in cur_det.select_related("run")
                   .values_list("run__batch_id", flat=True) if b}

    reviews = [o for o in all_reviews
               if o.source != "manual" and o.batch_id in cur_batches]
    other = len(all_reviews) - len(reviews)

    orphan, mismatch, nogeom = [], [], []
    for o in reviews:
        if o.candidate_id is None:
            orphan.append(o)
        elif o.candidate_id not in current:
            mismatch.append(o)
    for o in all_reviews:            # geom 은 모든 행이 들고 있어야 한다
        if not o.geom:
            nogeom.append(o)

    report("교정이 현재 검출에 붙어 있다", len(orphan), len(reviews),
           "고아 교정 — 지우지 말 것. 8단계(고아 화면)에서 다시 맺는다",
           [f"{o.viewpoint} {o.mask_key}" for o in orphan])
    report("교정의 candidate 링크가 맞다", len(mismatch), len(reviews),
           "옛 검출의 개체를 가리킨다 — 재바인딩이 중간에 끊겼는가",
           [f"{o.viewpoint} {o.mask_key}" for o in mismatch])
    report("교정이 기하(geom)를 갖고 있다", len(nogeom), len(all_reviews),
           "검출기가 바뀌면 그릴 것이 없어진다 (DiaRUGA P02 §2.7)",
           [f"{o.viewpoint} {o.mask_key}" for o in nogeom])
    if other:
        print(f"     (위 셋에서 뺀 교정 {other}건 — 사람이 그린 것과 지금 안 "
              f"보고 있는 묶음의 것. 고장이 아니다)")

    # --- batch·source 가 서로 맞는가 (DiaRUGA P09 0단계) -----------------------------
    # 두 칸을 따로 둔 값이 여기서 나온다 — 한쪽만 맞는 행은 어딘가에서 잘못
    # 만든 것이고, **예외는 안 나고 그냥 틀린 상태**로 남는다.
    bad_src = [o for o in all_reviews
               if (o.source == "manual") != (o.batch_id is None)]
    report("사람이 그린 교정만 묶음이 비어 있다", len(bad_src), len(all_reviews),
           "`source` 와 `batch` 가 어긋난다 — 엔진 교정이 사람이 그린 자리에 "
           "앉았거나 그 반대다 (DiaRUGA P09 5.2)",
           [f"{o.viewpoint} {o.mask_key} source={o.source} batch={o.batch_id}"
            for o in bad_src])

    # bind_method 분포는 정보로만
    dist = Counter(o.bind_method for o in reviews)
    print(f"     바인딩: {dict(dist)}")

def check_links(slug=None):
    """같은 개체 묶음이 앞뒤가 맞는가 (DiaRUGA P11).

    묶음은 사람이 프레임마다 골라 만든 것이라 재생성 불가다 — 어긋나면
    교정과 같은 무게로 잃는다. **여기서 잡는 것은 예외가 안 나고 그냥 틀린
    상태다**: 대표가 없는 묶음은 학습 자료로 뽑을 때 얼굴이 없고, 남의 시야
    이미지를 문 멤버는 화면에 안 그려져 조용히 사라진다.
    """
    # **묶음은 멤버가 둘 이상인 개체다** (DiaRUGA P12). 판정마다 개체가 하나씩 서므로
    # 거르지 않으면 개체 수천 개를 훑고, "혼자인 묶음" 검사가 전부 걸린다.
    qs = (ForamObject.objects.annotate(n_members=Count("members"))
          .filter(n_members__gte=2))
    if slug:
        qs = qs.filter(viewpoint__slide__slug=slug)
    links = list(qs.prefetch_related("members__image"))
    if not links:
        if VERBOSE:
            print("   (묶음이 아직 없다)")
        return

    # 대표가 정확히 하나인가. "둘 이상" 은 DB 제약이 막지만 **0개는 못 막는다** —
    # 저장 쪽이 지키는 약속이고, 여기가 그것을 센다.
    norep = [l for l in links if sum(1 for m in l.members.all() if m.is_rep) != 1]
    report("묶음마다 대표가 하나다", len(norep), len(links),
           "대표가 없으면 학습 자료로 뽑을 때 얼굴이 없다",
           [f"obj#{l.pk}" for l in norep])

    # **멤버가 하나도 없는 개체** (DiaRUGA P12). 판정을 지우면 개체가 유령으로 남는데
    # 예외는 안 나고 개체를 세는 자리마다 하나씩 는다 — `data.prune_objects`
    # 를 안 지난 삭제 경로가 있다는 뜻이다.
    ghosts = list(ForamObject.objects.filter(members__isnull=True)
                  .values_list("pk", flat=True)[:20])
    n_ghost = ForamObject.objects.filter(members__isnull=True).count()
    report("개체마다 판정이 하나 이상 있다", n_ghost,
           ForamObject.objects.count(),
           "멤버 없는 개체는 유령이다 — prune_objects 를 안 지난 삭제가 있다",
           [f"obj#{p}" for p in ghosts])

    # **묶음의 얼굴이 지운 판이면 안 된다** (151 · 사용자 방침 2026-08-25).
    #
    # 예전에는 **묶음 안에 지운 마스크가 하나라도 있으면** 걸었다. 그런데 그것은
    # 이 저장소의 다른 결정과 어긋난 규칙이었다 — 묶음은 *정체*(같은 개체)에
    # 대한 말이고 오검출 판정은 **판마다 다르다.** 한 프레임에서만 흐릿하게·
    # 크게 잘못 잡힌 것을 지울 수 있어야 하고, `/review` 는 그것을 일부러
    # 허용한다(`test_삭제는_안_번진다`). 카탈로그 문만 거절하고 있었다.
    #
    # 남는 해악은 하나다 — **개체의 얼굴이 오검출이 되는 것.** `is_rep` 은
    # "학습 자료로 뽑을 때, 목록에 보일 때 이 판을 쓴다" 이므로(DiaRUGA 116) 그 자리에
    # 지운 판이 앉으면 크롭도 학습 자료도 사람이 아니라고 한 마스크를 쓴다.
    # 제약은 "대표가 둘" 만 막고 이 상태는 통과시킨다.
    #
    # **전부 지운 개체는 여기서 안 센다** — 얼굴을 고를 자리가 없고, 그것은
    # 묶음의 문제가 아니라 그 개체가 통째로 오검출이라는 말이다.
    bad_rep = [f"obj#{l.pk}" for l in links
               if any(m.is_rep and m.removed for m in l.members.all())
               and any(not m.removed for m in l.members.all())]
    report("묶음의 대표가 지운 판이 아니다", len(bad_rep), len(links),
           "개체의 얼굴이 오검출이다 — 크롭과 학습 자료가 그것을 쓴다",
           bad_rep)

    # **앵커가 성한가** (DiaRUGA P18). 카탈로그 번호를 어느 판정에서 뽑을지가 이 칸이고,
    # 비어 있으면 **그 개체의 번호를 만들 재료가 없다.** 남의 판정을 가리키면
    # 더 나쁘다 — 예외는 안 나고 **다른 개체의 이름이 이 카드에 적힌다.**
    #
    # **묶음만 보지 않는다.** 앵커는 혼자인 개체에도 있어야 하므로 여기서만
    # 시야가 넓다(`links` 는 멤버가 둘 이상인 것뿐이다).
    objs = ForamObject.objects.all()
    if slug:
        objs = objs.filter(viewpoint__slide__slug=slug)
    objs = list(objs.select_related("anchor").prefetch_related("members"))
    live = [o for o in objs if o.members.all()]
    no_anchor = [f"obj#{o.pk}" for o in live if o.anchor_id is None]
    report("개체마다 앵커가 있다", len(no_anchor), len(live),
           "카탈로그 번호를 만들 재료가 없다 (data.reanchor 가 세운다)",
           no_anchor)
    stray = [f"obj#{o.pk}" for o in live
             if o.anchor_id is not None
             and o.anchor.foram_object_id != o.pk]
    report("앵커가 그 개체의 판정이다", len(stray), len(live),
           "남의 판정으로 번호를 만든다 — 다른 개체의 이름이 카드에 적힌다",
           stray)

    # 멤버의 이미지가 묶음의 시야에 속하는가. 어긋나면 화면이 못 그린다.
    stray = [l for l in links
             if any(m.image.viewpoint_id != l.viewpoint_id
                    for m in l.members.all())]
    report("멤버가 묶음의 시야 안에 있다", len(stray), len(links),
           "남의 시야 이미지를 문 멤버는 화면에 안 그려진다",
           [f"obj#{l.pk}" for l in stray])

    # 멤버가 실재하는 마스크를 가리키는가 — 그 (image, batch) 현재 검출의
    # 통과 후보이거나, 사람이 그린 교정(geom)이거나.
    dangling = []
    for l in links:
        for m in l.members.all():
            if m.batch_id is None:
                ok = ObjectReview.objects.filter(
                    image_id=m.image_id, batch__isnull=True,
                    mask_key=m.mask_key).exists()
            else:
                ok = Candidate.objects.filter(
                    detection__image_id=m.image_id,
                    detection__is_current=True,
                    detection__run__batch_id=m.batch_id,
                    mask_key=m.mask_key).exists()
            if not ok:
                dangling.append(f"obj#{l.pk}/{m.mask_key}")
    report("멤버의 마스크가 실재한다", len(dangling),
           sum(l.members.count() for l in links),
           "재검출로 사라진 마스크다 — geom 스냅샷으로만 남아 있다",
           dangling)

def check_catalog(slug=None):
    """카탈로그 번호가 **날 수 있는가, 그리고 겹치지 않는가** (개체 카탈로그).

    번호는 저장하지 않고 층·시야·`mask_key`·묶음 코드로 그때그때 만든다. 그래서
    어긋날 수가 없는 대신 **재료가 빠지면 번호가 아예 안 난다** — 화면은 "번호
    없음" 이라고 적지만, 그 상태로 며칠이 지나면 그 관찰만 동정을 못 한 채 남는다.
    여기서 세는 것이 그것이다.

    그리고 **겹치는 번호는 논문에 실린 뒤에는 못 고친다.** 규칙상 겹칠 수 없지만
    (`mask_key` 가 `(detection, mask_key)` 유일 제약을 타고, 묶음 코드에 유일
    제약이 있다) 층 코드를 정규화하면서 두 지점이 한 토막으로 누울 수는 있다
    (`GC-03` 과 `GC03` 이 다 `GC03` 이다) — 그 갈래를 기계로 본다.
    """
    # 1) 묶음 코드. 비면 그 묶음의 개체는 번호가 하나도 안 난다.
    batches = list(RunBatch.objects.filter(kind="detect")
                   .values("id", "label", "code", "for_review"))
    if not batches:
        if VERBOSE:
            print("   (검출 묶음이 아직 없다)")
        return

    used = [b for b in batches
            if Detection.objects.filter(is_current=True,
                                        run__batch_id=b["id"]).exists()]
    nocode = [b["label"] for b in used if not (b["code"] or "").strip()]
    report("검출이 있는 묶음에 카탈로그 코드가 있다", len(nocode), len(used),
           "코드가 없으면 그 묶음의 개체는 번호가 하나도 안 난다", nocode)

    # `M` 은 손그림 자리다 (`catalog.MANUAL_CODE`). DB 제약이 막지만 옛 판으로
    # 들어온 행이 있을 수 있어 함께 센다 — 막는 것과 확인하는 것은 다른 일이다.
    manual = [b["label"] for b in batches
              if (b["code"] or "").upper() == catalog.MANUAL_CODE]
    report("묶음 코드가 M 이 아니다", len(manual), len(batches),
           "M 은 사람이 그린 개체 자리다 — 섞이면 한 번호 아래 둘이 된다", manual)

    # 2) 층 코드가 정규화되면서 뭉개지는가. `GC-03` 과 `GC03` 이 한 토막이 된다.
    seen = defaultdict(set)
    for loc in Locality.objects.select_related("site"):
        try:
            key = (catalog.part(loc.site.code), catalog.part(loc.code))
        except ValueError:
            key = None
        if key:
            seen[key].add(f"{loc.site.code}-{loc.code}")
    clash = {k: v for k, v in seen.items() if len(v) > 1}
    report("지역·지점 코드가 번호에서 안 뭉개진다", len(clash), len(seen),
           "두 지점이 한 토막으로 누우면 서로 다른 개체가 같은 번호를 받는다",
           [f"{'-'.join(k)} <- {sorted(v)}" for k, v in clash.items()])

    # 3) 실제로 번호가 나는가. **검토 대상 묶음만** 본다 — 화면이 그것을 연다.
    slides = Slide.objects.select_related("sample__locality__site")
    if slug:
        slides = slides.filter(slug=slug)
    codes = {b["id"]: (b["code"] or "") for b in batches}
    # **행은 안 훑는다.** 번호가 나는지는 층이 있는지로 갈린다 — 슬라이드 12개에
    # 개체 3만이라, 세려고 자료를 물질화하지 말라는 것과 같은 이야기다.
    noloc = [sl.slug for sl in slides
             if not (sl.sample and sl.sample.locality
                     and sl.sample.locality.site)]
    report("관찰이 카탈로그 번호를 만들 층을 갖고 있다", len(noloc),
           slides.count(),
           "소속을 잃은 관찰은 번호가 안 난다 — 화면에서 동정을 못 적는다",
           noloc)

    # 4) 종명이 붙은 교정 행이 묶음에 들어 있는가. 손그림(NULL)은 제 자리다.
    named = ObjectReview.objects.exclude(foram_object__taxon__isnull=True)
    if slug:
        named = named.filter(viewpoint__slide__slug=slug)
    n_named = named.count()
    if n_named:
        orphan = [o.mask_key for o in
                  named.filter(batch__isnull=True).exclude(source="manual")[:20]]
        report("종명이 붙은 교정이 묶음에 들어 있다", len(orphan), n_named,
               "묶음 없는 엔진 교정은 어느 판의 동정인지 알 수 없다", orphan)
        nocode2 = [o.mask_key for o in named.select_related("batch")
                   if o.batch_id and not codes.get(o.batch_id, "")]
        report("동정한 개체의 묶음에 코드가 있다", len(nocode2), n_named,
               "코드가 없으면 그 동정을 부를 번호가 없다", nocode2[:20])
    elif VERBOSE:
        print("   (아직 동정한 개체가 없다)")

def check_grade_pose(slug=None):
    """등급·자세가 **매길 수 있는 자리에만** 붙어 있는가 (0034·0035).

    두 칸은 **완형에만 매긴다.** 파편(`ClassDef.counted=0`)은 완형을 유추할 수
    있어도 확실하지 않고, 무엇보다 *한 개체로 인정하는 규칙을 만족하지 못한 것*
    이라 우수성을 물을 자리가 아니다.

    **이것이 세 번째 자리다.** 화면이 파편에 칸을 안 보여주고(카탈로그 카드),
    서버가 다시 받지 않는다(`data.check_grade_pose`). 여기는 **그 둘을 안 지나는
    길**을 보는 곳이다 — 마이그레이션·일회성 스크립트·손으로 고친 SQL 은 예외를
    안 내고, 옆문으로 들어온 값은 **예외가 안 나고 그냥 틀린 상태**로 앉는다.

    잘못 붙은 값을 여기서 지우지 않는다. 사람이 눈으로 매긴 것이라 재생성
    불가이고, 어느 쪽이 틀렸는지(등급인가 분류인가)는 사람이 안다.

    ## `0035` 로 검사가 둘에서 하나의 모양이 됐다

    등급이 판정에서 개체로 오면서 **두 칸이 같은 행에 산다.** 그래서 "판마다
    어긋나는가" 를 물을 자리가 아예 없어졌다 — 묶인 판들은 한 값을 함께 본다.
    남는 것은 *그 개체가 매길 수 있는 것인가* 뿐이고, 그것이 아래 셋이다.
    묶을 때 값이 엇갈리면 거절하는 쪽이 나머지를 맡는다(DiaRUGA 108).

    **늘 0 인 검사는 두지 않는다** — 덮은 줄 알게 한다.

    ## `0035` 이전 DB 에는 못 돌린다 — 그리고 그것을 여기서 막지 않는다

    막아 봐야 소용이 없다. Django 는 모델의 **모든 칼럼을 SELECT 하므로**
    칸이 어긋난 DB 에서는 3번(교정)이 먼저 죽는다 — 이 스크립트 전체가 그렇다.
    여기에만 갈래를 두면 *"등급·자세만 건너뛰고 나머지는 돈다"* 고 믿게 된다.
    `check_db.py` 는 뷰어 코드를 그대로 쓰는 도구라 **판을 함께 올리는 축**에
    있다(DiaRUGA 057) — 카탈로그 검사가 `viewer/catalog.py` 를 기다리는 것과 같다.
    """
    # 파편은 `counted=0` 이다. `ClassDef` 를 읽어 온다 — 목록을 여기 박아 두면
    # 분류를 더할 때 이 검사만 조용히 낡는다.
    frag = set(ClassDef.objects.filter(counted=False)
               .values_list("key", flat=True))

    objs = ForamObject.objects.exclude(grade="", pose="")
    if slug:
        objs = objs.filter(viewpoint__slide__slug=slug)
    n = objs.count()
    if not n:
        if VERBOSE:
            print("   (아직 등급·자세를 매긴 개체가 없다)")
        return

    if frag:
        bad_g = objs.exclude(grade="").filter(label__in=frag)
        report("파편에 등급이 안 붙어 있다", bad_g.count(), n,
               "파편은 우수성을 물을 자리가 아니다 — 화면·서버를 안 지난 값이다",
               [f"obj#{o.pk} ({o.label} {o.grade})" for o in bad_g[:20]])

        bad_p = objs.exclude(pose="").filter(label__in=frag)
        report("파편에 자세가 안 붙어 있다", bad_p.count(), n,
               "위와 같다 — 두 칸은 완형에만 매긴다",
               [f"obj#{o.pk} ({o.label} {o.pose})" for o in bad_p[:20]])

    # **판이 모두 오검출인 개체.** "이 개체은 오검출이면서 A 다" 가 되어 학습
    # 자료가 모순이 된다 — 등급으로 무엇을 먼저 학습시킬지 고르기 때문에 그
    # 모순이 그대로 자료 선택으로 간다. 개체에 사는 값이라 판 하나가 아니라
    # **개체 전체가 지워졌는가**를 본다.
    dead = objs.annotate(
        n_live=Count("members", filter=Q(members__removed=False))
    ).filter(n_live=0)
    report("등급·자세가 살아 있는 개체에만 붙어 있다", dead.count(), n,
           "판이 모두 오검출인 개체가 등급·자세를 들고 있다",
           [f"obj#{o.pk} ({o.grade}{o.pose})" for o in dead[:20]])

    # **`A` 인데 종명이 빈 것은 문제가 아니다.** 등급을 먼저 매기고 종명은
    # 문헌을 찾아 나중에 적는 것이 실제 순서라, 저장을 막으면 그 순서를 막는다
    # (2026-08-11 사용자). 그래서 **세되 문제로 올리지 않는다** — 매기는 사람이
    # 지키는 기준이지 제약이 아니다.
    n_a = objs.filter(grade="A", taxon__isnull=True).count()
    if n_a:
        print(f"   A 인데 종명이 빈 것 {n_a}건 "
              f"(문제가 아니다 — 나중에 적는 순서를 막지 않는다)")


# --- 5. 뼈대 — 경로가 실제 파일을 가리키는가, 배율이 있는가 --------------------
def check_skeleton(slug=None):
    """파일이 없으면 화면이 깨진 이미지를 보여줄 뿐 예외가 나지 않는다.
    배율이 없으면 계측값이 통째로 뜻을 잃는다."""
    from django.conf import settings
    root = Path(settings.DATA_ROOT)

    fq = Frame.objects.select_related("slide")
    if slug:
        fq = fq.filter(slide__slug=slug)
    fl = list(fq)
    fm = [f for f in fl if not (root / f.path).exists()]
    report("원본 프레임 파일이 있다", len(fm), len(fl), "", [f.path for f in fm])

    noscale = [f for f in fl if not f.um_per_pixel]
    report("프레임에 배율(µm/px)이 있다", len(noscale), len(fl),
           "계측값이 뜻을 잃는다 — 폴더에 scale.toml 을 둘 것",
           [f.path for f in noscale])
    # 기본값으로 떨어진 것은 틀린 것은 아니지만 사람이 채워야 할 자리다
    dflt = [f for f in fl if f.um_per_pixel_source == "default"]
    report("프레임 배율이 기본값이 아니다", len(dflt), len(fl),
           "메타도 scale.toml 도 없어 기본값을 썼다 (pipeline/scale.py)",
           [f.path for f in dflt])

    sq = Stack.objects.select_related("viewpoint__slide")
    if slug:
        sq = sq.filter(viewpoint__slide__slug=slug)
    st = list(sq)
    sm = [s for s in st if not (root / s.focused_path).exists()]
    report("합성본 파일이 있다", len(sm), len(st), "", [s.focused_path for s in sm])

    # `Image` 테이블은 디스크의 파일을 비추는 것이다 (DiaRUGA P06) — 프레임·합성본
    # 마다 행이 하나씩 있어야 한다
    iq = Image.objects.all()
    if slug:
        iq = iq.filter(viewpoint__slide__slug=slug)
    paths = set(iq.values_list("path", flat=True))
    no_img = [f.path for f in fl if f.path not in paths]
    report("프레임마다 Image 행이 있다", len(no_img), len(fl),
           "images.ensure_frame_image 를 안 지난 반입이 있다", no_img)
    no_img = [s.focused_path for s in st if s.focused_path not in paths]
    report("합성본마다 Image 행이 있다", len(no_img), len(st),
           "images.ensure_stack_images 를 안 지난 합성이 있다", no_img)

    # 시야의 `n_frames` 와 실제 프레임 수
    vq = Viewpoint.objects.select_related("slide")
    if slug:
        vq = vq.filter(slide__slug=slug)
    vl = list(vq)
    counts = defaultdict(int)
    for f in fl:
        if f.viewpoint_id:
            counts[f.viewpoint_id] += 1
    off = [v for v in vl if counts.get(v.id, 0) != v.n_frames]
    report("시야의 n_frames 가 실제 프레임 수와 같다", len(off), len(vl),
           "시야를 가르거나 프레임을 지우고 수를 안 맞췄다",
           [f"{v.slide.slug} g{v.idx}: {v.n_frames} != {counts.get(v.id, 0)}" for v in off])

    # 배율은 **슬라이드 안에서** 하나여야 한다. 슬라이드끼리 다른 것은 정상이다
    # (배율을 바꿔 찍는다). 한 슬라이드 안이 갈라지는 것이 사고다.
    by_slide = defaultdict(set)
    for f in fl:
        if f.um_per_pixel:
            by_slide[f.slide.slug].add(round(f.um_per_pixel, 9))
    mixed = {s: v for s, v in by_slide.items() if len(v) > 1}
    if mixed:
        print(f"!!   슬라이드 안에서 배율이 섞였다 — {len(mixed)}개")
        for s, v in sorted(mixed.items()):
            print(f"       {s}: {sorted(v)}")
        print("       <-- 한 슬라이드는 한 배율로 찍힌다. EXIF·scale.toml 을 볼 것")
        problems.append(("슬라이드 내 배율 혼재", len(mixed), ""))
    else:
        print(f"   슬라이드마다 배율이 하나다 ({len(by_slide)}개 슬라이드)")
    if VERBOSE or len(by_slide) > 1:
        for s, v in sorted(by_slide.items()):
            print(f"     {s:<36} {list(v)[0]:.6f} µm/px")


# --- 7. 층 — 지역 → 지점 → 시료 → 관찰 → 시야(격자 칸) --------------------------
def check_layers(slug=None):
    """**여기서 잡는 것은 예외가 안 나고 그냥 틀린 상태다.** 소속을 잃은 관찰은
    어느 권역 탭에도 안 나와 화면에서 통째로 사라진다 (DiaRUGA 063)."""
    qs = Slide.objects.all()
    if slug:
        qs = qs.filter(slug=slug)
    sl = list(qs.select_related("sample__locality__site"))

    orphan = [s for s in sl if not s.sample_id]
    report("관찰에 시료가 붙어 있다", len(orphan), len(sl),
           "어느 권역 탭에도 안 나와 화면에서 사라진다 — 시스템 설정에서 붙일 것",
           [s.slug for s in orphan])

    sm = Sample.objects.select_related("locality")
    if slug:
        sm = sm.filter(slides__slug=slug).distinct()
    sm = list(sm)
    nopos = [x for x in sm if x.depth_cm is None]
    report("시료에 깊이가 있다", len(nopos), len(sm),
           "축에 안 놓이고 정렬에서 뒤로 밀린다",
           [f"{x.locality.code}/{x.code}" for x in nopos])

    # 분획이 빈 관찰. 폴더 이름에 `>125um` 이 없었고 사람도 안 채운 것 — 계수표가
    # 그 슬라이드를 분획 없이 세게 된다
    nofrac = [s for s in sl if s.fraction_um is None]
    report("관찰에 분획이 있다", len(nofrac), len(sl),
           "폴더 이름에 >125um 이 없다 — 정보 편집에서 채울 것",
           [s.slug for s in nofrac])

    # 같은 시료·분획에 관찰이 여럿인데 집계 제외가 하나도 없으면 합계가 두 배다
    # (DiaRUGA 056). 세는 것은 틀린 것이 아니라 사람이 정해야 할 자리다
    groups = defaultdict(list)
    for s in sl:
        if s.sample_id:
            groups[(s.sample_id, s.fraction_um)].append(s)
    dup = [g for g in groups.values()
           if len(g) > 1 and not any(x.exclude_from_totals for x in g)]
    report("같은 시료·분획의 관찰 여럿에 집계 제외가 있다", len(dup), len(groups),
           "합계가 조용히 두 배가 된다 — 정보 편집에서 하나만 남길 것",
           [" · ".join(x.slug for x in g) for g in dup])

    # 격자 칸 번호 — 한 슬라이드 안에서 겹치면 안 되고, 칸 수보다 클 수 없다
    vq = Viewpoint.objects.select_related("slide")
    if slug:
        vq = vq.filter(slide__slug=slug)
    cells = defaultdict(list)
    for v in vq:
        if v.cell is not None:
            cells[v.slide_id].append((v.cell, v.idx))
    dupc = []
    for sid, lst in cells.items():
        seen = defaultdict(list)
        for c, i in lst:
            seen[c].append(i)
        dupc += [f"slide#{sid} 칸 {c}: g{','.join(map(str, ii))}"
                 for c, ii in seen.items() if len(ii) > 1]
    report("격자 칸 번호가 슬라이드 안에서 겹치지 않는다", len(dupc), len(cells),
           "두 시야가 같은 칸이라고 말한다 — 하나는 잘못 매겨졌다", dupc)
    over = [f"{s.slug}: 칸 {c} > {s.cells}"
            for s in sl if s.cells
            for c, _ in cells.get(s.id, []) if c > s.cells]
    report("격자 칸 번호가 슬라이드의 칸 수 안에 있다", len(over), len(sl),
           "칸 수(Slide.cells)보다 큰 번호가 있다", over)

    if not slug:
        empty = [c for c in Locality.objects.select_related("site")
                 if not c.samples.exists()]
        report("지점에 시료가 있다", len(empty), Locality.objects.count(),
               "빈 지점이 목록·지도에 자리만 차지한다",
               [f"{c.site.code}/{c.code}" for c in empty])


def main():
    global VERBOSE
    ap = argparse.ArgumentParser()
    ap.add_argument("--slide", help="이 슬러그만")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="어긋난 것의 예를 보여준다")
    args = ap.parse_args()
    VERBOSE = args.verbose

    if args.slide and not Slide.objects.filter(slug=args.slide).exists():
        raise SystemExit(f"슬라이드를 찾지 못했다: {args.slide}")

    print("=== 1. 판정 캐시 ===")
    check_verdicts(args.slide)
    print("\n=== 2. 현재 검출 ===")
    check_current(args.slide)
    print("\n=== 3. 교정 ===")
    check_reviews(args.slide)
    print("\n=== 4. 분류 ===")
    check_classes(args.slide)
    print("\n=== 5. 뼈대 (파일·배율·Image 행) ===")
    check_skeleton(args.slide)
    print("\n=== 6. 문턱 ===")
    check_thresholds(args.slide)
    print("\n=== 7. 층 (지역·지점·시료·관찰·격자 칸) ===")
    check_layers(args.slide)
    print("\n=== 8. 같은 개체 묶음 ===")
    check_links(args.slide)
    print("\n=== 9. 개체 카탈로그 (번호·동정) ===")
    check_catalog(args.slide)
    print("\n=== 10. 등급·자세 ===")
    check_grade_pose(args.slide)
    print("\n(11·12 는 도감·출현 기록 검사 — 5단계에서 온다)")

    print()
    if problems:
        print(f"문제 {len(problems)}건:")
        for name, n, why in problems:
            print(f"  {name}: {n}건 {why}")
        return 1
    print("DB 가 앞뒤가 맞는다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
