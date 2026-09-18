#!/usr/bin/env python3
"""DiaRUGA v0.29.0 `ops/export_review.py` 에서 왔다 — 종명이 `Taxon` 조인이고, 옛 판(ObjectLink · 판정에 붙은 종명) 갈래는 ForGIA 에 그런 DB 가 없어 남겨만 둔다.

사람의 교정을 `review/` 로 내보낸다 — git 에 남길 감사 기록 (DiaRUGA P02 5단계 · P06).

    python export_review.py                        # 저장소 review/ 로
    python export_review.py --check                # 파일 ↔ DB 대조 (안 쓴다)
    python export_review.py --slide 260731_am22-gc10b_25cm
    python export_review.py --db backup/DiaRUGA_20260804_114433.db --out /tmp/before

교정(삭제·되살림·분류·코멘트 6,700여 건)은 사람이 시야를 하나씩 보며 만든 것이고
**다시 만들 수 없다.** 지금 안전망은 `backup_db.py` 하나인데, 백업은 "몇 행이었나"
는 알려 주지만 **"무엇이 달라졌나" 는 못 알려 준다** — 027 을 알아챈 것도 숫자였지
내용이 아니었다. 이 스크립트가 그 자리를 맡는다.

세 가지를 다 해야 한다. 하나라도 빠지면 다른 물건이 된다.

1. **감사 기록** — git 에 남아 `diff` 로 언제 무엇이 달라졌는지 보인다
2. **안전망** — DB 가 상해도 사람의 판단을 되살릴 수 있다
3. **대조 도구** — 두 시점(백업 파일 포함)의 교정을 견줄 수 있다

## Django 를 임포트하지 않는다

`backup_db.py` 와 같은 자리다. 규약("DB 는 컨테이너 문 하나로만")이 막으려는 것은
**같은 파일을 두 벌의 환경이 만지는 것**인데, 여기는 Django 를 안 쓰고 원본을
**읽기 전용**으로만 열어서 그 상황이 성립하지 않는다.

그래서 얻는 것이 셋이다.

- **저장소에 바로 쓴다.** 컨테이너는 `/srv/DiaRUGA/db` 와 `/data3` 만 물고
  저장소를 안 문다. 내보낸 뒤 사람이 옮겨야 하는 감사 기록은 아무도 안 돌린다
- **`models.py` 가 흔들려도 돈다.** 감사 기록이 코드 판에 매이면 안 된다 —
  특히 `Image` 정규화(DiaRUGA P06 2~5단계)로 **스키마가 바뀌는 동안에도 같은 도구로
  견줘야 한다**
- **백업 파일을 그대로 읽는다.** 두 시점을 비교하는 일이 이것 하나로 된다

## 파일 이름은 `(슬라이드, 시야 번호)` 다

예전 형식은 `review/<stem>_review.json` 이었다. **쓸 수 없다** — 싱글턴 시야는
stem 이 곧 프레임 이름이고 **프레임 이름은 슬라이드끼리 겹친다**(143종). 053 에서
저장이 남의 시야로 가던 것과 같은 원인이고, 여기서는 **파일이 서로를 덮어쓰는**
모습으로 나타난다.

`(슬라이드 슬러그, 시야 번호)` 는 겹치지 않는다 — `Viewpoint` 에 `(slide, idx)`
유일 제약이 있고, 주소(`/d/<slug>/g/<n>/`)와도 같은 열쇠다.

## `geom` 을 반드시 넣는다

`removed`·`accepted` 만 있던 옛 형식으로는 **되살릴 수 없다.** 키(`mask_key`)는
bbox 문자열이라 검출이 바뀌면 안 맞고, 그때 다시 붙이는 근거가 `geom` 이다.
**`geom` 이 빠진 내보내기는 감사 기록이지 안전망이 아니다.** 크기는 문제가 안
된다 — 6,732행에 1.4 MB 다(폴리곤이 `approxPolyDP` 로 단순화돼 점이 10~20개).

## diff 가 읽히게 쓴다

감사 기록의 값은 `git diff` 가 읽히는가에 달렸다. **개체 하나가 한 줄**이라야
"이 개체의 분류가 바뀌었다" 가 한 줄로 보인다. `mask_key` 로 정렬하고(dict 순서가
아니라), 한글 코멘트가 그대로 보이게 `ensure_ascii=False` 로 쓴다.
"""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

def _find_root():
    """`.env` 가 어디 있는지 스스로 찾는다.

    이 스크립트는 **두 자리에서 돈다** — 저장소의 `ops/` 와 배포의
    `/srv/DiaRUGA/scripts/`. 둘 다 `.env` 는 **한 칸 위**에 있다(저장소 루트 ·
    `/srv/DiaRUGA`). 옛날에는 스크립트가 저장소 루트에 있어 `parent` 가 맞았고,
    100 에서 옮기면서 그 전제가 깨졌다 — **시간별 백업이 두 시간 죽었다**.
    자리를 박아 두지 말고 찾는다.
    """
    here = Path(__file__).resolve().parent
    for d in (here, here.parent):
        if (d / ".env").exists():
            return d
    return here.parent


ROOT = _find_root()


def _env(key, default):
    """환경변수 → `.env` → 기본값.

    `backup_db.py` 에도 같은 것이 있다. 일부러 나눠 뒀다 — 두 스크립트 다
    **다른 것이 전부 망가졌을 때 쓰는 물건**이라 혼자 돌아야 한다.
    """
    if key in os.environ:
        return os.environ[key]
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip()
    except OSError:
        pass
    return default


DB = Path(_env("FORGIA_DB", str(ROOT / "ForGIA.db")))
OUT = ROOT / "review"

# 내보내는 형식의 판. **올릴 때는 읽는 쪽을 함께 본다** — 감사 기록이라
# 옛 파일이 계속 남아 있고, 판이 없으면 어느 형식인지 알 수 없다.
#
# **3 — 시야 하나에 `(이미지, 묶음)` 여럿** (DiaRUGA P09 1단계). 형식 2 는 그 짝이 시야마다
# 하나라는 전제였고, 깨지면 한 파일 안에서 `key` 가 겹쳐 어느 검출의 판단인지가
# 사라졌다. 그래서 2 는 깨진 DB 를 만나면 **쓰지 않고 멈췄다.**
#
# 프레임별 검토와 묶음 갈아타기가 그 전제를 깬다: 프레임 검출을 올리면 시야마다
# 이미지가 3.6장이 되고, 회차를 돌리면 같은 이미지에 묶음이 여럿 앉는다.
#
# 형식 4 (DiaRUGA P11): **같은 개체 묶음(`links`)이 실린다.** 사람이 프레임마다 골라
# 묶은 것이라 교정과 같은 무게의 재생성 불가 자료다 — 같은 감사 기록으로 간다.
# 묶음이 없는 시야는 `"links": []` 다. 옛 DB(0030 이전 백업)에는 표가 없어
# 조용히 빈 목록이 된다 — 이 스크립트는 두 시점을 비교하는 도구라 옛 판도 읽는다.
#
# **형식 번호를 안 올리고 `species` 를 더했다** (개체 카탈로그, 2026-08-10).
# 동정한 종명이고 `label`·`note` 와 같은 무게의 재생성 불가 자료다 — 사람이
# 현미경을 보며 적는다.
#
# 번호를 올리지 않은 이유가 둘이다. **적힌 개체에만 키를 싣는다**(`source`·
# `geom_edited` 와 같은 규칙) — 그래서 종명이 없는 파일은 **한 글자도 안 바뀐다.**
# 형식 3→4 는 `links` 라는 새 층이 생겨 모든 파일이 바뀌었지만 이번은 아니다.
# 그리고 읽는 쪽이 모르는 키를 만나도 깨지지 않는다(`--check` 는 문자열 대조다).
#
# **6,700행을 뜻 없이 다시 쓰지 않는 것이 감사 기록에서는 값이다** — 그 diff 가
# 한 번 지나가면 그 사이에 실제로 달라진 판단이 그 안에 묻힌다.
#
# **`grade`·`pose` 도 같은 규칙으로 더했다** (등급·자세, 2026-08-12 · 0034).
# 사람이 현미경을 보며 매기는 것이라 `species` 와 같은 무게의 재생성 불가
# 자료이고, **매긴 것에만 실어** 번호를 안 올렸다. 값이 쌓이기 전에 넣는 것이
# 싸다 — 안 넣고 수백 건을 매기면 그동안의 판단이 감사 기록에 없다.
FORMAT = 4

# 묶음(그룹) 정렬 — 합성본이 먼저다. **차례가 정해져 있어야 diff 가 읽힌다.**
_KIND_ORDER = {"stack": 0, "frame": 1, "depth": 2}


def connect(path: Path) -> sqlite3.Connection:
    """**읽기 전용으로만 연다.** `immutable=1` 은 주지 않는다 — 운영 DB 는 WAL 로
    계속 쓰이고 있어서, 그렇게 열면 `-wal` 에 있는 최근 쓰기를 못 본다.
    """
    if not path.exists():
        sys.exit(f"DB 가 없다: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch(conn, slide_slug=None) -> dict:
    """시야마다의 교정을 `{(슬러그, idx): payload}` 로.

    한 번의 질의로 다 읽어 파이썬에서 묶는다. 시야 452개에 교정 6,732행이라
    시야마다 질의를 던질 이유가 없다.
    """
    where, args = "", []
    if slide_slug:
        where = "WHERE s.slug = ?"
        args = [slide_slug]

    views = {}
    for r in conn.execute(f"""
        SELECT v.id, v.idx, v.tag, s.slug
          FROM viewer_viewpoint v JOIN viewer_slide s ON s.id = v.slide_id
          {where}
    """, args):
        views[r["id"]] = {"slide": r["slug"], "gid": r["idx"], "tag": r["tag"],
                          "done": False, "note": "",
                          # `(이미지 경로, 묶음 이름)` → 그 짝의 교정들.
                          # 형식 3 이 담는 것이 이 갈래다 (DiaRUGA P09 1단계).
                          "groups": {}}

    for r in conn.execute("SELECT viewpoint_id, done, note FROM viewer_viewpointreview"):
        v = views.get(r["viewpoint_id"])
        if v is not None:
            v["done"] = bool(r["done"])
            v["note"] = r["note"] or ""

    # `image_id` 는 P06 2단계(0019)에서, `batch_id`·`source`·`geom_edited` 는
    # P09 0단계(0025)에서 생겼다. **옛 DB(백업 파일)에는 없다** — 이 스크립트는
    # 두 시점을 비교하는 도구라 옛 판도 읽어야 한다.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(viewer_objectreview)")}

    def col(name, default="NULL"):
        # **테이블 이름을 붙여 낸다** — P12 뒤로는 개체 테이블과 조인하므로
        # 맨 이름이면 어느 쪽 칸인지 모호해진다.
        return f"r.{name}" if name in cols else f"{default} AS {name}"

    img_col = col("image_id")
    batch_col = col("batch_id")
    src_col = col("source", "'engine'")
    edit_col = col("geom_edited", "0")
    # **분류·종명이 어디 사는가가 판마다 다르다** (DiaRUGA P12, 0032). 새 판은
    # `ForamObject` 에 있고 옛 판(백업 파일)은 `ObjectReview` 에 있다 — 이
    # 스크립트는 두 시점을 비교하는 도구라 **양쪽을 다 읽어야 한다.**
    #
    # 내보내는 모양은 안 바뀐다. 감사 기록의 형식 번호를 올리지 않는 이유이고,
    # 그래야 P12 전후의 `review/` 를 그대로 diff 할 수 있다.
    # 검토 완료가 통과분에 남긴 서명 (0033). **옛 DB 에는 없다** — 위와 같은 갈래.
    conf_col = col("auto_confirmed", "0")
    new_home = "foram_object_id" in cols
    if new_home:
        # ForGIA: 종은 `Taxon` FK 다 — 이름을 조인해서 DiaRUGA 와 같은 모양으로 낸다
        label_col = "o.label"
        species_col = "COALESCE((SELECT t.name FROM viewer_taxon t WHERE t.id = o.taxon_id), '')"
        obj_join = " LEFT JOIN viewer_foramobject o ON o.id = r.foram_object_id"
    else:
        label_col = "r.label"
        species_col = "r.species" if "species" in cols else "'' AS species"
        obj_join = ""

    # **등급이 판마다 다른 테이블에 산다** — `0034` 는 `ObjectReview` 에 넣었고
    # `0035` 가 `ForamObject` 로 옮겼다(하루 만에 뒤집었다 · 111). 이 스크립트는
    # **두 시점을 비교하는 도구**라 양쪽을 다 읽어야 한다. 자세는 처음부터 개체다.
    #
    # 그래서 갈래가 셋이다: 개체에 있다(지금) · 판정에 있다(`0034` 대) · 없다
    # (그 앞). **어느 쪽에서 읽든 내보내는 모양은 같다** — 그래야 판을 오간
    # `review/` 를 그대로 diff 할 수 있고, 형식 번호를 안 올린 이유도 그것이다.
    #
    # **둘 다 판정 한 줄에 싣는다.** 개체에 사는 `label`·`species` 가 이미 그
    # 자리에 실리는 것과 같고, 무엇보다 **묶음이 아닌 개체는 `links` 에 안
    # 나온다**(멤버가 하나면 묶음으로 안 센다) — 묶음 머리에만 실으면 대부분의
    # 개체에서 조용히 사라진다.
    obj_cols = set()
    if new_home:
        obj_cols = {r[1] for r in
                    conn.execute("PRAGMA table_info(viewer_foramobject)")}
    pose_col = "o.pose" if "pose" in obj_cols else "'' AS pose"
    # **코멘트도 판마다 다른 테이블에 산다** — `0036` 이 개체로 옮겼다(등급과
    # 같은 이야기의 반쪽이다 · 112). 옛 판은 판정에 있다. 등급과 달리 갈래가
    # 둘뿐인 것은 **처음부터 있던 칸**이라 "없다" 가 없어서다.
    #
    # **내보내는 모양은 안 바뀐다** — 판정 한 줄에 그대로 싣는다. 묶인 개체는
    # 판마다 같은 글이 실리는데, 그것이 지금의 사실이다(개체 하나가 든 값이다).
    note_col = col("note", "o.note")
    if "grade" in obj_cols:
        grade_col = "o.grade"
    else:
        grade_col = col("grade", "''")

    # **묶음은 id 가 아니라 이름으로 적는다.** 감사 기록은 사람이 읽고 두 DB 를
    # 비교하는 물건이라, 저장소마다 달라지는 id 를 적으면 diff 가 거짓말을 한다.
    batch_name = {}
    if "batch_id" in cols:
        try:
            batch_name = {r[0]: r[1] for r in
                          conn.execute("SELECT id, label FROM viewer_runbatch")}
        except sqlite3.Error:
            pass

    # **이미지도 id 가 아니라 경로로 적는다.** 같은 이유다 — 감사 기록은 두 DB 를
    # 비교하는 물건이라 저장소마다 달라지는 id 를 적으면 diff 가 거짓말을 한다.
    # 그리고 `Image` 의 열쇠가 원래 `path` 다 (DiaRUGA P06 §"열쇠는 path 다").
    images = {}
    if "image_id" in cols:
        try:
            images = {r[0]: (r[1], r[2]) for r in
                      conn.execute("SELECT id, path, kind FROM viewer_image")}
        except sqlite3.Error:
            pass

    for r in conn.execute(f"""
        SELECT r.viewpoint_id, r.mask_key, r.removed, r.accepted,
               {label_col} AS label, {note_col},
               r.geom, r.bind_method, r.bind_score,
               {img_col}, {batch_col}, {src_col}, {edit_col}, {conf_col},
               {species_col} AS species, {grade_col}, {pose_col}
          FROM viewer_objectreview r{obj_join}
    """):
        v = views.get(r["viewpoint_id"])
        if v is None:
            continue
        # **키가 먼저 오게 짓는다.** 한 줄로 쓰므로 줄 앞머리가 무엇에 대한
        # 기록인지를 바로 말해야 diff 가 읽힌다.
        obj = {
            "key": r["mask_key"],
            "removed": bool(r["removed"]),
            "accepted": bool(r["accepted"]),
            "label": r["label"] or "",
            "note": r["note"] or "",
            "bind": r["bind_method"] or "",
        }
        # **적은 개체에만 싣는다.** 늘 실으면 종명이 없는 파일 6,700행이 뜻
        # 없이 다시 쓰이고, 그 diff 에 그 사이의 진짜 변화가 묻힌다.
        # `label` 은 늘 실리는데 이쪽이 다른 것은 그 이유다(형식 머리말).
        if r["species"]:
            obj["species"] = r["species"]
        # 등급·자세도 같은 규칙이다 — **매긴 것에만.** 빈 값을 실으면 두 칸이
        # 없는 6,700행이 뜻 없이 다시 쓰이고 그 diff 에 진짜 변화가 묻힌다.
        # 종명 옆에 둔다: 셋이 사람이 개체를 보고 적는 판단이라 한 줄 안에서
        # 붙어 있어야 "이 개체를 어떻게 봤나" 가 한눈에 읽힌다.
        if r["grade"]:
            obj["grade"] = r["grade"]
        if r["pose"]:
            obj["pose"] = r["pose"]
        if (r["source"] or "engine") != "engine":
            obj["source"] = r["source"]
        if r["geom_edited"]:
            obj["geom_edited"] = True
        # **적힌 것에만 싣는다** (`species`·`geom_edited` 와 같은 규칙). 늘
        # 실으면 서명 없는 행 수천 개가 뜻 없이 다시 쓰이고, 그 diff 에 그
        # 사이의 진짜 변화가 묻힌다.
        if r["auto_confirmed"]:
            obj["auto_confirmed"] = True
        if r["bind_score"] is not None:
            obj["bind_score"] = round(r["bind_score"], 4)
        # 기하는 마지막이다 — 길고, 사람이 눈으로 읽는 것이 아니다.
        try:
            obj["geom"] = json.loads(r["geom"]) if r["geom"] else {}
        except (TypeError, ValueError):
            obj["geom"] = {}
        # **열쇠가 `(image, batch, mask_key)` 라 그 짝으로 묶는다** (DiaRUGA P09 5.1).
        # 묶음 이름은 개체마다가 아니라 **그룹 머리에 한 번** 적는다 — 같은
        # 그룹의 모든 개체가 같은 값이라 개체마다 실으면 diff 만 시끄러워진다.
        # 사람이 그린 개체는 어느 묶음에도 안 속해 빈 문자열이다.
        path, kind = images.get(r["image_id"], ("", ""))
        label = batch_name.get(r["batch_id"], "") if r["batch_id"] else ""
        g = v["groups"].setdefault((path, label),
                                   {"image": path, "kind": kind,
                                    "batch": label, "objects": []})
        g["objects"].append(obj)

    # 같은 개체 묶음 (DiaRUGA P11 · 형식 4). 표가 없는 옛 DB 면 조용히 빈 목록 —
    # 위의 `image_id` 칸 처리와 같은 갈래다.
    for v in views.values():
        v["links"] = []
    # **테이블이 판마다 다르다** (DiaRUGA P12, 0032). 새 판은 `viewer_foramobject` 에
    # 멤버가 `viewer_objectreview` 로 흡수돼 있고, 옛 판(백업 파일)은
    # `viewer_objectlink` + `viewer_objectlinkmember` 다.
    #
    # **묶음이 아닌 것은 안 낸다.** 새 판에서는 판정마다 개체가 하나씩 서므로
    # 거르지 않으면 감사 기록에 개체 7,900개가 "묶음" 으로 실린다 — 형식이
    # 말하는 묶음은 여전히 *여러 판이 한 개체* 인 것이다.
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "viewer_foramobject" in tables:
        link_sql = """
            SELECT l.id, l.viewpoint_id, l.batch_id, l.note,
                   m.image_id, m.batch_id AS mbatch, m.mask_key, m.is_rep,
                   m.geom
              FROM viewer_foramobject l
              JOIN viewer_objectreview m ON m.foram_object_id = l.id
             WHERE l.id IN (SELECT foram_object_id FROM viewer_objectreview
                             GROUP BY foram_object_id HAVING COUNT(*) > 1)
        """
    elif "viewer_objectlink" in tables:
        link_sql = """
            SELECT l.id, l.viewpoint_id, l.batch_id, l.note,
                   m.image_id, m.batch_id AS mbatch, m.mask_key, m.is_rep,
                   m.geom
              FROM viewer_objectlink l
              JOIN viewer_objectlinkmember m ON m.link_id = l.id
        """
    else:
        link_sql = ""
    if link_sql:
        link_rows = {}
        for r in conn.execute(link_sql):
            v = views.get(r["viewpoint_id"])
            if v is None:
                continue
            lk = link_rows.setdefault(r["id"], {
                "viewpoint": r["viewpoint_id"],
                "batch": batch_name.get(r["batch_id"], "") if r["batch_id"] else "",
                "note": r["note"] or "", "members": []})
            path, kind = images.get(r["image_id"], ("", ""))
            try:
                geom = json.loads(r["geom"]) if r["geom"] else {}
            except (TypeError, ValueError):
                geom = {}
            lk["members"].append({
                "image": path, "kind": kind,
                "batch": batch_name.get(r["mbatch"], "") if r["mbatch"] else "",
                "key": r["mask_key"], "rep": bool(r["is_rep"]), "geom": geom})
        # **차례를 못 박는다** — 멤버는 합성본 먼저 경로순, 묶음은 첫 멤버의
        # (경로, 키) 순. DB 마다 달라지는 id 로 늘어놓으면 diff 가 거짓말을 한다.
        for lk in link_rows.values():
            lk["members"].sort(key=lambda m: (_KIND_ORDER.get(m["kind"], 9),
                                              m["image"], m["key"]))
        for lk in sorted(link_rows.values(),
                         key=lambda l: (l["members"][0]["image"],
                                        l["members"][0]["key"])):
            views[lk.pop("viewpoint")]["links"].append(lk)

    # **표시가 하나도 없는 시야는 내보내지 않는다.** 452개 중 432개만 자료가
    # 있는데, 빈 파일 20개를 두면 "아직 안 본 것" 과 "봤는데 고칠 게 없던 것" 이
    # 파일 있음/없음으로 구분되지 않는다. 후자는 `done` 이 켜져 있다.
    out = {}
    for v in views.values():
        if (not v["groups"] and not v["done"] and not v["note"]
                and not v["links"]):
            continue
        # **차례를 못 박는다** — 합성본 먼저, 그다음 프레임을 경로순으로.
        # dict 가 넣은 순서를 기억한다고 기대면 DB 의 행 순서가 바뀔 때마다
        # 파일 전체가 다시 써져 **diff 가 자료 변화를 못 보여 준다.**
        v["groups"] = sorted(
            v["groups"].values(),
            key=lambda g: (_KIND_ORDER.get(g["kind"], 9), g["image"], g["batch"]))
        for g in v["groups"]:
            g["objects"].sort(key=lambda o: o["key"])
        out[(v["slide"], v["gid"])] = v

    # 형식 2 는 여기서 **쓰지 않고 멈췄다** — 시야 하나에 `(이미지, 묶음)` 하나를
    # 전제했고, 깨지면 한 파일 안에서 `key` 가 겹쳐(프레임끼리 45%) 어느 검출의
    # 판단인지가 사라졌다. 형식 3 은 그 짝으로 묶어 담으므로 멈출 이유가 없다.
    #
    # **`done`·`note` 는 여전히 시야당 하나다.** 검토 완료의 단위를 시야로 두기로
    # 했기 때문이다(DiaRUGA P06 §8) — 그 시야의 이미지를 다 봐야 완료다.
    return out


def render(v: dict) -> str:
    """개체 하나가 한 줄인 JSON. `json.dumps(indent=…)` 로는 안 된다 —
    폴리곤 좌표까지 한 줄씩 쪼개져 개체 하나가 40줄이 된다.

    형식 3 은 그 위에 **`(이미지, 묶음)` 묶음 한 겹**이 더 있다. 묶음 머리도 한
    줄로 적어 — 그래야 `git diff` 에서 "어느 판의 교정이 달라졌나" 가 한 줄로
    보인다. 개체를 들여쓰기로만 가르면 어느 판에 속하는지 찾으려고 위로 거슬러
    올라가야 한다.
    """
    n_obj = sum(len(g["objects"]) for g in v["groups"])
    head = {"format": FORMAT, "slide": v["slide"], "gid": v["gid"],
            "tag": v["tag"], "done": v["done"], "note": v["note"],
            "n_images": len(v["groups"]), "n_objects": n_obj,
            "n_links": len(v.get("links", []))}
    lines = ["{"]
    for k, val in head.items():
        lines.append(f"  {json.dumps(k)}: {json.dumps(val, ensure_ascii=False)},")
    lines.append('  "images": [')
    for gi, g in enumerate(v["groups"]):
        gcomma = "" if gi == len(v["groups"]) - 1 else ","
        lines.append("    {")
        for k in ("image", "kind", "batch"):
            lines.append(f"      {json.dumps(k)}: "
                         f"{json.dumps(g[k], ensure_ascii=False)},")
        lines.append(f'      "n_objects": {len(g["objects"])},')
        lines.append('      "objects": [')
        for i, o in enumerate(g["objects"]):
            comma = "" if i == len(g["objects"]) - 1 else ","
            lines.append("        " + json.dumps(o, ensure_ascii=False,
                                                 separators=(", ", ": ")) + comma)
        lines.append("      ]")
        lines.append("    }" + gcomma)
    lines.append("  ],")
    # 같은 개체 묶음 (형식 4). 멤버 하나가 한 줄 — 개체와 같은 규칙이다.
    # 비어 있으면 한 줄이다 — 묶음이 없는 파일(대부분)에 두 줄을 보탤 이유가 없다.
    lks = v.get("links", [])
    if not lks:
        lines.append('  "links": []')
        lines.append("}")
        return "\n".join(lines) + "\n"
    lines.append('  "links": [')
    for li, lk in enumerate(lks):
        lcomma = "" if li == len(lks) - 1 else ","
        lines.append("    {")
        lines.append(f'      "batch": {json.dumps(lk["batch"], ensure_ascii=False)},')
        lines.append(f'      "note": {json.dumps(lk["note"], ensure_ascii=False)},')
        lines.append('      "members": [')
        for mi, m in enumerate(lk["members"]):
            mcomma = "" if mi == len(lk["members"]) - 1 else ","
            lines.append("        " + json.dumps(m, ensure_ascii=False,
                                                 separators=(", ", ": ")) + mcomma)
        lines.append("      ]")
        lines.append("    }" + lcomma)
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def path_for(out_dir: Path, slug: str, gid: int) -> Path:
    return out_dir / slug / f"g{gid:03d}.json"


def main():
    ap = argparse.ArgumentParser(description="교정을 review/ 로 내보낸다")
    ap.add_argument("--db", default=str(DB), help=f"기본 {DB}")
    ap.add_argument("--out", default=str(OUT), help=f"기본 {OUT}")
    ap.add_argument("--slide", help="슬라이드 슬러그 하나만")
    ap.add_argument("--check", action="store_true",
                    help="쓰지 않고 파일과 DB 를 대조한다")
    args = ap.parse_args()

    out_dir = Path(args.out)
    conn = connect(Path(args.db))
    try:
        views = fetch(conn, args.slide)
    finally:
        conn.close()

    n_obj = sum(len(g["objects"]) for v in views.values() for g in v["groups"])
    n_lk = sum(len(v.get("links", [])) for v in views.values())
    print(f"{args.db}\n  시야 {len(views)} · 교정 {n_obj} · 묶음 {n_lk}")

    want = {path_for(out_dir, slug, gid): render(v)
            for (slug, gid), v in views.items()}

    # 지금 있는 파일. **슬라이드 하나만 볼 때는 그 아래만 본다** — 안 그러면
    # 나머지 슬라이드의 파일이 전부 "남는 것" 으로 잡힌다.
    #
    # **`<슬라이드>/g*.json` 만 본다.** `rglob` 로 훑으면 옛 평면 형식
    # (`review/g000_…_focused_review.json`)까지 걸리는데, 그 이름이 `g` 로
    # 시작하는 것은 우연이다. 우연에 기대 지우면 형식을 또 바꿀 때 조용히
    # 다르게 군다 — 옛것은 아래에서 이름으로 따로 처리한다.
    slides = [out_dir / args.slide] if args.slide else \
        [d for d in sorted(out_dir.glob("*")) if d.is_dir()]
    have = {p for d in slides if d.exists() for p in d.glob("g*.json")}

    # 옛 평면 형식. stem 이 슬라이드끼리 겹쳐 파일이 서로를 덮어쓰므로 버렸다
    # (머리말). 새 형식과 섞여 있으면 어느 쪽이 진짜인지 알 수 없다.
    legacy = sorted(out_dir.glob("*_review.json")) if not args.slide else []

    added = [p for p in want if p not in have]
    gone = sorted(have - set(want))
    changed, same = [], 0
    for p in sorted(set(want) & have):
        if p.read_text(encoding="utf-8") == want[p]:
            same += 1
        else:
            changed.append(p)

    def rel(p):
        try:
            return p.relative_to(out_dir)
        except ValueError:
            return p

    if args.check:
        # **대조는 아무것도 쓰지 않는다.** 이것이 P06 2~5단계의 안전장치다 —
        # 마이그레이션 전후로 돌려 같은지 본다.
        for label, items in (("새로 생김", sorted(added)), ("달라짐", changed),
                             ("파일만 남음", gone), ("옛 형식", legacy)):
            for p in items[:20]:
                print(f"  {label}: {rel(p)}")
            if len(items) > 20:
                print(f"  {label}: … 외 {len(items) - 20}개")
        ok = not (added or changed or gone or legacy)
        print(f"  같음 {same} · 새로 생김 {len(added)} · 달라짐 {len(changed)} "
              f"· 파일만 남음 {len(gone)} · 옛 형식 {len(legacy)}")
        print("대조 통과 — 다른 것 없음" if ok else "대조 실패 — 위를 볼 것")
        return 0 if ok else 1

    for p, text in want.items():
        if p in have and p.read_text(encoding="utf-8") == text:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    # **DB 에 없는 시야의 파일은 지운다.** 남겨 두면 "지금 DB 의 모습" 이 아니라
    # "언젠가 있었던 것들의 합집합" 이 되어 감사 기록으로 못 쓴다. 지운 것은
    # git 이 기억한다.
    for p in gone + legacy:
        p.unlink()
    for d in sorted({p.parent for p in gone}):
        if d.exists() and not any(d.iterdir()):
            d.rmdir()

    print(f"  그대로 {same} · 새로 씀 {len(added)} · 고쳐 씀 {len(changed)} "
          f"· 지움 {len(gone)}"
          + (f" · 옛 형식 걷음 {len(legacy)}" if legacy else ""))
    print(f"  -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
