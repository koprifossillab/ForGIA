#!/usr/bin/env python3
"""DB 의 무결성을 검사한다. **예외가 나지 않고 그냥 틀린 상태**를 잡는 것이 목적이다.

DiaRUGA v0.29.0 `ops/check_db.py`(925줄 · 검사 열둘)에서 1단계 것만 왔다 —
**뼈대(5번)와 층(7번)**. 판정·검출·교정·분류·묶음·카탈로그·도감 검사는 그
테이블이 오는 단계에서 같은 번호로 되돌아온다. 번호를 DiaRUGA 와 맞춰 두는
이유는 `CLAUDE.md` 의 함정 목록이 "`check_db` 7번이 센다" 처럼 번호로 부르기
때문이다.

    python check_db.py
    python check_db.py --slide 260918_rs23-gc03_71cm_125um
    python check_db.py -v          # 어긋난 것의 예를 보여준다

돌려야 할 때:
  - 반입·그룹핑·합성 뒤 (`poll_nas.sh` 가 돌린 뒤 숫자가 이상할 때)
  - 시야를 가르거나 소속을 옮긴 뒤
"""
import argparse
import os
import sys
from collections import defaultdict
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

from viewer.models import (Frame, Image, Locality, Sample,           # noqa: E402
                           Slide, Stack, Viewpoint)

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

    print("=== 5. 뼈대 (파일·배율·Image 행) ===")
    check_skeleton(args.slide)
    print("\n=== 7. 층 (지역·지점·시료·관찰·격자 칸) ===")
    check_layers(args.slide)
    print("\n(1~4 · 6 · 8~12 는 검출·교정·분류·카탈로그·도감 검사 — 그 단계에서 온다)")

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
