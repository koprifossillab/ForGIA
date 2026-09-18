#!/usr/bin/env python3
"""WoRMS 의 유공충 전체를 `Taxon` 표로 반입한다 (P01 5절 · 3단계).

DiaRUGA 의 `tools/harvest_worms.py` 는 도감 색인의 학명을 **이름으로** 물었다 —
목록이 먼저 있고 판정을 얻는 도구였다. ForGIA 는 반대다: **목록이 없다.** 그래서
WoRMS 의 계층을 유공충 문(AphiaID 1410)부터 **아래로 훑어** 통째로 가져오고,
쓰는 것만 `active` 로 켠다(자동완성은 켠 것만 낸다 · `data.resolve_taxon`).

두 걸음이다 — **긁기와 넣기를 가른다.** 긁는 데 수천 번의 요청이 들고(수만 행 ·
한 쪽에 50행), 끊기면 처음부터 다시 하지 않게 원시 레코드를 파일에 쌓는다.
넣기는 그 파일만 읽어 몇 초에 끝나고, 몇 번을 돌려도 같다(`aphia_id` 로 갱신).

    python migrate/import_worms.py harvest                 # /data3/ForGIA/worms/records.jsonl
    python migrate/import_worms.py harvest --root 744104   # 한 강(Globothalamea)만 — 시험 삼아
    python migrate/import_worms.py load                    # 파일 → Taxon
    python migrate/import_worms.py load --dry-run
    python migrate/import_worms.py habit --root 22528 --habit planktonic   # 아래로 물려준다

## 함정

- **`extant_only=false` 를 빠뜨리면 화석이 통째로 빠진다** (DiaRUGA 119 가 겪은
  자리). 남극 코어는 화석도 본다. `marine_only=false` 도 같이 준다
- **한 쪽은 50행이고 끝은 204 다.** 200 에 빈 배열이 아니라 **내용 없는 204** 라
  JSON 을 읽으려 들면 죽는다
- **이명은 자기 자리(부모 아래)에 그대로 온다** — `status=unaccepted` 이고
  `valid_AphiaID` 가 유효명을 가리킨다. 지우지 않는다: 옛 문헌이 그 이름으로
  적혀 있어 사람이 그것으로 찾는다. 화면이 옆에 유효명을 보여준다
- **부모·유효명 FK 는 둘째 판에 맺는다.** 아래로 훑으면 부모가 먼저 오지만
  유효명은 다른 가지에 있을 수 있다 — 전부 넣은 뒤에 잇는다
- **행을 지우지 않는다.** `ForamObject.taxon` 이 PROTECT 로 잡고 있고, WoRMS 가
  이름을 없앤 것은 `status` 로 드러난다
- **속도.** WoRMS 가 초당 몇 번을 받는지 못 박혀 있지 않다 — 요청 사이 `--sleep`
  (기본 0.2초)을 둔다. 5만 행이면 천 번 남짓이라 몇 분이면 끝난다
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://www.marinespecies.org/rest"
FORAMINIFERA = 1410
PAGE = 50
DEFAULT_OUT = Path(os.environ.get("FORGIA_DATA_ROOT", "/data3/ForGIA")) / "worms" / "records.jsonl"

# WoRMS 의 `rank` 이름 → `Taxon.rank` 선택지. 없는 급은 그대로 적는다(빈 값 아님)
RANKS = {"Phylum", "Subphylum", "Class", "Subclass", "Order", "Suborder",
         "Superfamily", "Family", "Subfamily", "Genus", "Subgenus",
         "Species", "Subspecies", "Variety", "Forma"}


def get(url, tries=4):
    """JSON 을 받는다. 204 는 `[]`. 잠깐의 오류는 몇 번 다시 묻는다."""
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                if r.status == 204:
                    return []
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 204:
                return []
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(2.0 * (i + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < tries - 1:
                time.sleep(2.0 * (i + 1))
                continue
            raise


def children(aphia_id, sleep):
    """한 부모의 자식 전부 — 쪽을 넘기며."""
    offset = 1
    while True:
        q = urllib.parse.urlencode({"marine_only": "false", "extant_only": "false",
                                    "offset": offset})
        rows = get(f"{API}/AphiaChildrenByAphiaID/{aphia_id}?{q}")
        if not rows:
            return
        yield from rows
        if len(rows) < PAGE:
            return
        offset += PAGE
        time.sleep(sleep)


def harvest(root, out: Path, sleep, limit):
    """`root` 아래를 전부 파일에 쌓는다. **이미 있는 것은 다시 안 묻는다** —
    파일의 `AphiaID` 를 읽어 그 자식을 이미 다 받은 부모는 건너뛴다."""
    out.parent.mkdir(parents=True, exist_ok=True)
    seen, done_parents = set(), set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            seen.add(r["AphiaID"])
            if r.get("_children_done"):
                done_parents.add(r["AphiaID"])
    # 뿌리 자신
    if root not in seen:
        rec = get(f"{API}/AphiaRecordByAphiaID/{root}")
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        seen.add(root)
    stack = [root]
    n_req = 0
    with out.open("a", encoding="utf-8") as fh:
        while stack:
            pid = stack.pop()
            if pid in done_parents:
                # 자식은 이미 받았다 — 그 자식들의 자식만 이어 간다
                continue
            kids = list(children(pid, sleep))
            n_req += 1 + len(kids) // PAGE
            for r in kids:
                if r["AphiaID"] not in seen:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                    seen.add(r["AphiaID"])
                # 종 아래(아종·변종)도 자식이 있을 수 있다 — 전부 내려간다
                stack.append(r["AphiaID"])
            fh.write(json.dumps({"AphiaID": pid, "_children_done": True}) + "\n")
            fh.flush()
            done_parents.add(pid)
            if n_req % 20 == 0:
                print(f"  {len(seen)}행 · 요청 {n_req}회 · 남은 부모 {len(stack)}", file=sys.stderr)
            if limit and len(seen) >= limit:
                print(f"--limit {limit} 에 닿아 멈춘다", file=sys.stderr)
                break
            time.sleep(sleep)
    print(f"{len(seen)}행이 {out} 에 있다")


def load(path: Path, dry_run):
    """파일 → `Taxon`. 둘째 판에서 부모·유효명을 잇는다."""
    APP = Path(os.environ.get("FORGIA_APP") or Path(__file__).resolve().parent.parent)
    sys.path.insert(0, str(APP / "web"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "forgiaweb.settings")
    import django
    django.setup()
    from django.db import transaction
    from viewer.models import Taxon

    recs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("_children_done") or not r.get("scientificname"):
            continue
        recs[r["AphiaID"]] = r
    print(f"레코드 {len(recs)}개")
    if dry_run:
        from collections import Counter
        print(Counter(r.get("rank") for r in recs.values()).most_common())
        print(Counter(r.get("status") for r in recs.values()).most_common())
        return

    made = updated = 0
    with transaction.atomic():
        by_aphia = {t.aphia_id: t for t in Taxon.objects.exclude(aphia_id=None)}
        for aid, r in recs.items():
            fields = {
                "name": r["scientificname"].strip(),
                "rank": r.get("rank") or "",
                "status": (r.get("status") or "accepted")[:40],
                "authority": (r.get("authority") or "").strip()[:160],
                "extinct": bool(r.get("isExtinct")),
            }
            t = by_aphia.get(aid)
            if t is None:
                # 반입 전에 사람이 손으로 넣은 같은 이름이 있으면 그 행에 aphia 를 붙인다
                t = Taxon.objects.filter(aphia_id=None, name=fields["name"],
                                         authority=fields["authority"]).first()
                if t is None:
                    t = Taxon(aphia_id=aid, **fields)
                    t.save()
                    by_aphia[aid] = t
                    made += 1
                    continue
                t.aphia_id = aid
            changed = False
            for k, v in fields.items():
                if getattr(t, k) != v:
                    setattr(t, k, v)
                    changed = True
            if changed or t.aphia_id != aid:
                t.save()
                updated += 1
                by_aphia[aid] = t
        # 둘째 판 — 부모·유효명
        linked = 0
        for aid, r in recs.items():
            t = by_aphia[aid]
            parent = by_aphia.get(r.get("parentNameUsageID"))
            valid = r.get("valid_AphiaID")
            acc = by_aphia.get(valid) if valid and valid != aid else None
            if t.parent_id != (parent.pk if parent else None) or \
               t.accepted_id != (acc.pk if acc else None):
                t.parent = parent
                t.accepted = acc
                t.save(update_fields=["parent", "accepted"])
                linked += 1
    print(f"새로 {made} · 고침 {updated} · 부모/유효명 이음 {linked} · 표 전체 {Taxon.objects.count()}")


def habit(root, value):
    """`root` 와 그 아래 전부의 생활형을 바꾼다 (부유성/저서성). WoRMS 에 없는 칸이라
    사람이 과·목 수준에서 붙인다 — 화면(시스템 설정 · 학명)도 같은 일을 한다."""
    APP = Path(os.environ.get("FORGIA_APP") or Path(__file__).resolve().parent.parent)
    sys.path.insert(0, str(APP / "web"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "forgiaweb.settings")
    import django
    django.setup()
    from viewer.models import Taxon
    top = Taxon.objects.filter(aphia_id=root).first()
    if top is None:
        raise SystemExit(f"AphiaID {root} 가 표에 없다 — 먼저 load 할 것")
    ids, frontier = [top.pk], [top.pk]
    while frontier:
        kids = list(Taxon.objects.filter(parent_id__in=frontier).values_list("pk", flat=True))
        ids += kids
        frontier = kids
    n = Taxon.objects.filter(pk__in=ids).update(habit=value)
    print(f"{top.name} 아래 {n}행 → {value or '(없음)'}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("harvest", help="WoRMS 에서 긁어 파일에 쌓는다")
    h.add_argument("--root", type=int, default=FORAMINIFERA)
    h.add_argument("--out", type=Path, default=DEFAULT_OUT)
    h.add_argument("--sleep", type=float, default=0.2)
    h.add_argument("--limit", type=int, default=0, help="이만큼 모이면 멈춘다 (시험용)")
    l = sub.add_parser("load", help="파일 → Taxon")
    l.add_argument("--path", type=Path, default=DEFAULT_OUT)
    l.add_argument("--dry-run", action="store_true")
    b = sub.add_parser("habit", help="생활형을 아래로 물려준다")
    b.add_argument("--root", type=int, required=True, help="AphiaID")
    b.add_argument("--habit", choices=["", "planktonic", "benthic"], required=True)
    a = ap.parse_args()
    if a.cmd == "harvest":
        harvest(a.root, a.out, a.sleep, a.limit)
    elif a.cmd == "load":
        load(a.path, a.dry_run)
    else:
        habit(a.root, a.habit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
