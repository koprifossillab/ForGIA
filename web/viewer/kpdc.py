"""DiaRUGA v0.29.0 `web/viewer/kpdc.py` 에서 왔다. KPDC(극지 데이터 센터, kpdc.kopri.re.kr)에서 코어 지점의 메타데이터를 긁어
지점에 얹는다 (197).

새 슬라이드가 들어오면 `group_focus_series` 가 폴더 이름에서 지점을 만드는데
(`naming.parse_folder`), 그 행은 코드 하나뿐이다 — 좌표·수심·채취일이 비어
있고 사람이 관리 화면에서 채우기 전까지는 `—` 로 뜬다. 같은 코어가 KPDC 에
공개 항목으로 있으므로(2026-09-17 실측: 뷰어의 코어 지점 10개 전부) 거기서
읽어 온다. 폴러가 그룹핑 뒤에 한 번 부르고(`deploy/poll_nas.sh`), 손으로도
부른다(`ops/fetch_kpdc.py`).

## 자동값은 빈 칸만 채운다

**사람이 넣은 값은 안 덮는다** (063 과 같은 줄). `lat`·`lon`·`water_depth_m`·
`collected_at`·`collect_kind` 는 **비어 있을 때만** 쓴다. 실제로 `WAP13-GC47`
은 DB 의 좌표(사람이 넣은 것)와 KPDC 의 좌표가 다르다 — KPDC 쪽 항목이 코어
위치가 아니라 분석 자료 항목이라 그렇다. 덮었으면 옳은 값이 지워졌다.

`kpdc_id`·`kpdc_meta` 는 이 모듈의 것이라 다시 긁으면 갈아치운다.

## 페이지에 노출된 것만 읽는다

첨부 파일(코어 사진·X-ray·물성 xlsx)은 거의 다 "Request required" 라 로그인과
공개 요청을 거쳐야 받는다 — 그것은 **목록만** 적어 둔다(무엇이 몇 개 있는지).
`Download` 인 것은 `save_downloads()` 가 받아 둔다(`GC03-C1` 의 xlsx 하나가
그렇다). **받았다고 반입되는 것은 아니다** — 어느 열이 무엇인지는 사람이
`coredata/mapping.toml` 에 적고 P17 절차(`ops/import_coredata.py`)로 넣는다.

## 사내 DNS 가 이 호스트를 모른다

`kpdc.kopri.re.kr` 이 사내 리졸버에서 NXDOMAIN 이다(2026-09-17 · `kopri.re.kr`
만 사내 주소로 풀린다). 바깥 리졸버는 공인 IP 로 푸므로, **이름이 안 풀리면
IP 로 붙고 SNI·Host 는 이름으로 준다.** IP 는 `FORGIA_KPDC_IP` 로 바꾼다.

## 이 모듈은 Django 모델을 직접 부르지 않는다

`apply()` 가 받는 것은 지점 객체 하나이고 저장은 부르는 쪽이 한다 — 시험이
네트워크 없이 파서와 채우기 규칙을 따로 본다. 네트워크는 `fetch()` 하나만
지난다.
"""
from __future__ import annotations

import datetime as dt
import html as htmlmod
import http.client
import os
import re
import socket
import ssl
import urllib.parse

HOST = "kpdc.kopri.re.kr"
# 2026-09-17 에 8.8.8.8 이 준 값. 바뀌면 환경변수로 준다.
FALLBACK_IP = os.environ.get("FORGIA_KPDC_IP", "203.250.180.198")
DOI_PREFIX = "10.22663/"
TIMEOUT = 30

# 페이지의 `<dl><dt>이름</dt><dd>값</dd></dl>` 중 남길 것. 지도 레이어 목록도
# 같은 꼴이라(`Coastline`·`Base Layer`…) 이름으로 거른다.
FIELD_KEYS = ("Entry ID", "DOI", "Copyright", "Science Keyword", "ISO Topic",
              "Platforms", "Instruments", "Personnel", "Project",
              "Research period", "Create/Update Date", "Location", "Citation")


class KpdcError(Exception):
    pass


# ── 네트워크 ──────────────────────────────────────────────────────────────

class _Conn(http.client.HTTPSConnection):
    """IP 로 붙되 TLS SNI 는 호스트 이름으로 준다."""

    def __init__(self, ip: str, sni: str, **kw):
        super().__init__(ip, **kw)
        self._sni = sni

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self._sni)


def _resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443)
        return True
    except socket.gaierror:
        return False


def fetch(path: str, *, timeout: int = TIMEOUT, retries: int = 1) -> str:
    """`https://kpdc.kopri.re.kr<path>` 를 받는다. 같은 호스트 안의 리다이렉트는
    따라간다(세 번까지). **읽기 시간 초과는 한 번 더 해 본다** — 실측에서 열
    페이지 중 하나가 30초를 넘겼다(2026-09-17). 한 번의 실패로 그 지점이
    `--missing` 에 남는 것보다 싸다."""
    for attempt in range(retries + 1):
        try:
            _, _, body = _request("GET", path, timeout=timeout)
            return body.decode("utf-8", "replace")
        except KpdcError as e:
            if attempt >= retries or "timed out" not in str(e):
                raise
    raise AssertionError("unreachable")


def download(file_id: str, referer_path: str, *, purpose: str = "ForGIA",
             timeout: int = 120) -> tuple[str, bytes]:
    """상태가 `Download` 인 첨부 하나를 받는다 → (파일 이름, 내용).

    화면의 「Download」 버튼이 하는 것을 그대로 한다 — `POST /rawdata/<id>/` 에
    용도(`purpose`)를 실으면 302 로 일회용 주소를 주고 그것을 GET 하면 파일이다.
    **Referer 가 항목 페이지가 아니면 500** 이고, **302 를 POST 로 따라가면
    400** 이다(curl `-X POST -L` 이 그렇게 한다 — 실측). 쿠키·로그인은 없다.
    """
    body = urllib.parse.urlencode({"purpose": purpose}).encode()
    hdr = {"Referer": f"https://{HOST}{referer_path}",
           "Content-Type": "application/x-www-form-urlencoded"}
    _, headers, data = _request("POST", f"/rawdata/{file_id}/", body=body,
                                headers=hdr, timeout=timeout)
    cd = headers.get("Content-Disposition") or ""
    m = re.search(r"filename\*=UTF-?8''([^;]+)", cd, re.I)
    name = urllib.parse.unquote(m.group(1)) if m else ""
    if not name:
        m = re.search(r'filename="?([^";]+)', cd)
        name = m.group(1) if m else ""
    if not name or data[:6] == b"<html>" or data.lstrip()[:9].lower() == b"<!doctype":
        raise KpdcError(f"첨부가 아니라 HTML 이 왔다: /rawdata/{file_id}/")
    return name, data


def _connect(timeout: int):
    ctx = ssl.create_default_context()
    if _resolves(HOST):
        return http.client.HTTPSConnection(HOST, timeout=timeout, context=ctx)
    return _Conn(FALLBACK_IP, HOST, timeout=timeout, context=ctx)


def _request(method: str, path: str, *, body: bytes | None = None,
             headers: dict | None = None, timeout: int = TIMEOUT,
             _hops: int = 0) -> tuple[int, dict, bytes]:
    """한 번의 요청. 리다이렉트는 **GET 으로** 따라간다 — 브라우저가 그렇게
    하고 KPDC 의 내려받기 주소가 그것을 전제한다."""
    conn = _connect(timeout)
    try:
        h = {"Host": HOST, "User-Agent": "ForGIA/kpdc"}
        h.update(headers or {})
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        hdrs = {k: v for k, v in r.getheaders()}
        if r.status in (301, 302, 303, 307, 308):
            loc = r.getheader("Location") or ""
            u = urllib.parse.urlsplit(loc)
            if _hops >= 3 or (u.netloc and u.netloc != HOST):
                raise KpdcError(f"redirect 를 못 따라간다: {loc}")
            nxt = urllib.parse.urlunsplit(("", "", u.path, u.query, ""))
            keep = {k: v for k, v in (headers or {}).items() if k == "Referer"}
            return _request("GET", nxt, headers=keep, timeout=timeout,
                            _hops=_hops + 1)
        if r.status != 200:
            raise KpdcError(f"HTTP {r.status} {path}")
    except (OSError, http.client.HTTPException) as e:
        raise KpdcError(f"{HOST} 에 닿지 못했다: {e}") from e
    finally:
        conn.close()
    return r.status, hdrs, data


# ── 파서 ──────────────────────────────────────────────────────────────────

def _text(s: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", "", s,
               flags=re.S)
    t = htmlmod.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def parse_search(page: str) -> list[dict]:
    """키워드 검색 결과의 `[Entry ID] 제목` 링크들. 페이지 순서대로."""
    out = []
    for m in re.finditer(r'href="(/search/[0-9a-f-]{36})"[^>]*>(.*?)</a>',
                         page, re.S):
        title = _text(m.group(2))
        eid = re.match(r"\[(KOPRI-KPDC-\d+)\]\s*(.*)", title)
        out.append({"path": m.group(1),
                    "entry_id": eid.group(1) if eid else "",
                    "title": eid.group(2) if eid else title})
    return out


def pick_hit(hits: list[dict], core: str) -> dict | None:
    """제목에 코어 이름이 있는 것 중 **Entry ID 가 가장 큰 것**(가장 최근 등록).

    `RS14-GC04` 는 항목이 둘이다 — 2014년 항차 직후 것("Old ID" 가 붙어 있다)과
    2024년에 다시 등록한 것. 새것이 좌표·수심·길이를 갖고 있다.
    """
    # 낱말 단위로 본다 — `RS21-GC03` 이 `RS21-GC03B` 를 줍지 않게
    pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(core) + r"(?![A-Za-z0-9])",
                     re.IGNORECASE)
    cands = [h for h in hits if pat.search(h["title"])]
    if not cands:
        return None
    return max(cands, key=lambda h: int(h["entry_id"].rsplit("-", 1)[-1] or 0)
               if h["entry_id"] else -1)


def parse_detail(page: str) -> dict:
    """상세 페이지 하나 → dict. 네트워크를 안 쓴다."""
    d: dict = {"title": "", "abstract": "", "fields": {}, "files": [],
               "lat": None, "lon": None, "coverage": "", "views": None,
               "versions": []}
    m = re.search(r'<h2 class="page_secondary">(.*?)</h2>', page, re.S)
    if m:
        d["title"] = _text(m.group(1))
    m = re.search(r'<p class="description">(.*?)</p>', page, re.S)
    if m:
        d["abstract"] = htmlmod.unescape(m.group(1)).strip()

    for m in re.finditer(r"<dl[^>]*>\s*<dt>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>\s*</dl>",
                         page, re.S):
        k, v = _text(m.group(1)), m.group(2)
        if k == "Copyright":
            link = re.search(r'href="([^"]+)"', v)
            v = link.group(1) if link else v
        if k in FIELD_KEYS:
            d["fields"][k] = _text(v)

    m = re.search(r"<dt>Spatial Coverage</dt>(.*?)</dl>", page, re.S)
    if m:
        cov = m.group(1)
        kind = re.search(r'<p class="point-header">(\w+)</p>', cov)
        d["coverage"] = kind.group(1) if kind else ""
        pts = re.findall(r"lat:</span>\s*([-\d.]+),\s*<span[^>]*>lon:</span>\s*([-\d.]+)",
                         cov)
        d["points"] = [(float(a), float(b)) for a, b in pts]
        # 점 하나일 때만 좌표로 쓴다 — 다각형의 꼭짓점 하나를 코어 위치라
        # 할 수는 없다
        if d["coverage"] == "POINT" and len(d["points"]) == 1:
            d["lat"], d["lon"] = d["points"][0]

    for m in re.finditer(r'<tr class="files[^"]*"([^>]*)>(.*?)</tr>', page, re.S):
        attrs, row = m.group(1), m.group(2)
        fid = re.search(r'data-file-id="([0-9a-f-]{36})"', attrs)
        cat = re.search(r'class="file-category">(.*?)</td>', row, re.S)
        name = re.search(r'class="file-name">(.*?)</span>', row, re.S)
        desc = re.search(r'class="file-description">(.*?)</td>', row, re.S)
        size = re.search(r'<span class="file-size" title="(\d+)">', row)
        status = re.search(r'class="file-status">(.*?)</td>', row, re.S)
        d["files"].append({
            "category": _text(cat.group(1)) if cat else "",
            "name": _text(name.group(1)) if name else "",
            "description": _text(desc.group(1)) if desc else "",
            "bytes": int(size.group(1)) if size else None,
            "status": _text(status.group(1)) if status else "",
            # 내려받을 수 있는 것(`Download`)만 id 가 붙어 있다
            "file_id": fid.group(1) if fid else ""})

    m = re.search(r'<span class="count">([\d,]+)</span>\s*Views', page)
    if m:
        d["views"] = int(m.group(1).replace(",", ""))

    m = re.search(r"<h4[^>]*>Version History</h4>(.*?)</table>", page, re.S)
    if m:
        for row in re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S):
            cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) >= 4 and cells[0].isdigit():
                d["versions"].append({"version": int(cells[0]),
                                      "date": cells[1], "submitter": cells[2],
                                      "summary": cells[3]})

    # 요약문에 적힌 수심·길이. 형식이 사람마다 달라서(`625m` · `1249` · `288Core
    # Length : 2.88m`) 숫자 앞의 이름표만 믿는다
    m = re.search(r"Water Depth\s*:\s*([\d.]+)", d["abstract"])
    d["water_depth_m"] = float(m.group(1)) if m else None
    m = re.search(r"Core Length\s*:\s*([\d.]+)\s*m", d["abstract"])
    d["core_length_m"] = float(m.group(1)) if m else None

    # 채취일 — 하루짜리 기간일 때만. 한 달 범위(`2014-02-01 ~ 2014-02-28`)를
    # 첫날로 적으면 그날 떴다는 말이 된다
    m = re.match(r"(\d{4}-\d{2}-\d{2}) ~ (\d{4}-\d{2}-\d{2})",
                 d["fields"].get("Research period", ""))
    d["collected_at"] = m.group(1) if m and m.group(1) == m.group(2) else None
    d["research_period"] = d["fields"].get("Research period", "")

    d["entry_id"] = re.sub(r"\s.*", "", d["fields"].get("Entry ID", ""))
    d["doi"] = f"{DOI_PREFIX}{d['entry_id']}" if d["entry_id"] else ""
    return d


# ── 긁기 ──────────────────────────────────────────────────────────────────

def scrape(core: str) -> dict | None:
    """`<지역>-<지점>` 하나를 검색해 상세를 읽는다. 없으면 `None`."""
    hits = parse_search(fetch("/search/?q=" + urllib.parse.quote(core)))
    hit = pick_hit(hits, core)
    if hit is None:
        return None
    d = parse_detail(fetch(hit["path"]))
    d["url"] = f"https://{HOST}{hit['path']}"
    d["path"] = hit["path"]
    d["hits"] = [{"entry_id": h["entry_id"], "title": h["title"]} for h in hits]
    d["fetched_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    d.pop("points", None)
    return d


def save_downloads(meta: dict, dest_dir, *, purpose: str = "ForGIA") -> list[str]:
    """`Download` 상태인 첨부를 `dest_dir` 에 받아 두고 `meta["files"][i]["saved"]`
    에 자리를 적는다. 받은 파일 이름을 돌려준다.

    **이미 같은 크기로 있으면 다시 안 받는다.** "Request required" 는 건드리지
    않는다 — 로그인·공개 요청이 필요한 것이라 여기서 할 수 없다.
    """
    from pathlib import Path
    dest = Path(dest_dir)
    got = []
    for f in meta.get("files", []):
        if f.get("status") != "Download" or not f.get("file_id"):
            continue
        name = (f.get("name") or "").replace("/", "_")
        target = dest / name if name else None
        if target and target.exists() and f.get("bytes") and target.stat().st_size == f["bytes"]:
            f["saved"] = str(target)
            continue
        fname, data = download(f["file_id"], meta["path"], purpose=purpose)
        target = dest / fname.replace("/", "_")
        dest.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        f["saved"] = str(target)
        f["saved_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        got.append(target.name)
    return got


# ── 지점에 얹기 ───────────────────────────────────────────────────────────

# 장비 이름 → 채취 방식. 단정할 수 있는 것만 — 그 밖은 비워 두고 사람이 적는다
_INSTRUMENT_KIND = {"gravity corer": "gravity core", "box corer": "box core",
                    "piston corer": "piston core"}


def apply(loc, meta: dict) -> list[str]:
    """지점 객체에 얹는다. **빈 칸만 채운다.** 바꾼 칸의 이름을 돌려준다 —
    저장은 부르는 쪽이 한다(`loc.save(update_fields=…)`)."""
    changed = []

    def fill(field, value):
        if value in (None, "") or getattr(loc, field) not in (None, ""):
            return
        setattr(loc, field, value)
        changed.append(field)

    fill("lat", meta.get("lat"))
    fill("lon", meta.get("lon"))
    fill("water_depth_m", meta.get("water_depth_m"))
    if meta.get("collected_at"):
        fill("collected_at", dt.date.fromisoformat(meta["collected_at"]))
    inst = (meta.get("fields") or {}).get("Instruments", "").strip().lower()
    fill("collect_kind", _INSTRUMENT_KIND.get(inst))

    if loc.kpdc_id != meta.get("entry_id", ""):
        loc.kpdc_id = meta.get("entry_id", "")
        changed.append("kpdc_id")
    loc.kpdc_meta = meta
    changed.append("kpdc_meta")
    return changed


def files_note(meta: dict | None) -> str:
    """지점 카드에 적는 첨부 한 줄 — `첨부 13개 · 요청 필요 13` ·
    `첨부 1개 · 받아 둠 1`."""
    files = (meta or {}).get("files") or []
    if not files:
        return ""
    saved = sum(1 for f in files if f.get("saved"))
    dl = sum(1 for f in files if f.get("status") == "Download")
    req = sum(1 for f in files if f.get("status") == "Request required")
    parts = [f"첨부 {len(files)}개"]
    if saved:
        parts.append(f"받아 둠 {saved}")
    elif dl:
        parts.append(f"내려받을 수 있음 {dl}")
    if req:
        parts.append(f"요청 필요 {req}")
    return " · ".join(parts)


def doi_url(entry_id: str) -> str:
    return f"https://doi.org/{DOI_PREFIX}{entry_id}" if entry_id else ""
