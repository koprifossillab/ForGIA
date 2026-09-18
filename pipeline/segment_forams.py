#!/usr/bin/env python3
"""
픽킹 슬라이드 사진에서 유공충 개체를 찾아낸다. DiaRUGA v0.29.0 `segment_diatoms.py`
(1,404줄)에서 왔다 — **SAM2 백엔드·텍스처 지표를 뺐고** 판정은 새 `judge.py`
(크기·확신도)다 (P01 2절 ②). YOLO 백엔드·DB 저장·잠금·OOM 재시도·`Run`·묶음은
그대로다.

    python segment_forams.py --slide <slug> --weights models/11n-synth-v2-1024.pt --batch yolo-seed
    python segment_forams.py "/data3/ForGIA/stacked/g000_Snap-00001-00003_focused.jpg" --no-db

**교정 재바인딩(`rebind.py`)은 3단계에서 온다.** 그때까지 `_promote_and_rebind` 는
`is_current` 만 옮기고 교정은 안 맺는다 — 아직 교정 표가 없다.

DiaRUGA 에서 물려받은 규칙:

- **검출을 덮어쓰지 않고 쌓는다.** 돌릴 때마다 새 `Detection` 행이고 `is_current`
  가 뷰어가 볼 것을 가리킨다. 덮어쓰면 엔진 교체 전후를 비교할 수 없다
- 실행이 `Run(kind=detect)` 에 남는다. `--batch` 로 여러 슬라이드를 한 묶음으로
- `out/*_candidates.json` 은 **`--export-json` 을 줄 때만** 쓴다. 원본은 DB 다
"""
import argparse
import contextlib
import fcntl
import gc
import hashlib
import json
import math
import os
import socket
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import torch
import django
from PIL import Image

# 이 스크립트는 저장소 밖(/srv/ForGIA/scripts)에 복사해 두고 컨테이너 안에서
# 돌릴 수도 있다. 그때 Django 코드가 어디 있는지는 FORGIA_APP 이 알려 준다 —
# 이미지 안의 /app 이고, 뷰어 컨테이너가 쓰는 바로 그 코드다. 저장소에서 그냥
# 돌리면 예전처럼 자기 옆의 web/ 을 본다.
# **저장소에서는 한 단계 위가 뿌리다** (스크립트가 pipeline/·ops/·migrate/
# 안에 있다). `/srv/ForGIA/scripts` 처럼 저장소 밖에서 돌 때는 그 짐작이
# 안 맞으므로 `FORGIA_APP` 이 알려 준다 — 컨테이너에서는 이미지 안의 /app 이다.
APP = Path(os.environ.get("FORGIA_APP")
          or Path(__file__).resolve().parent.parent)
# **`APP` 은 Django 코드를 찾는 자리일 뿐이다** (DiaRUGA 100). `sys.path` 앞에 통째로
# 밀어 넣으면 **이미지 안의 옛 `judge.py`·`zen_meta.py` 가 자기 옆의 것을 가린다**
# — `/srv/ForGIA/scripts` 로 밀어 넣은 새 규칙이 안 먹는 채로 돌았다(실측).
# 그래서 **뒤에 붙인다**: 스크립트 자신의 디렉토리(파이썬이 `sys.path[0]` 에
# 놓는다)가 먼저이고, Django 는 그 뒤에서 찾힌다.
sys.path.insert(0, str(APP / "web"))
sys.path.append(str(APP))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "forgiaweb.settings")
django.setup()
# **모델 코드와 DB 의 판이 같은가** — DB 를 만지기 전에 본다 (DiaRUGA 198). 이미지의
# 모델이 걷힌 칼럼을 알고 있으면 SELECT 한 번에 죽는데, 저장 도중이면 앞
# 트랜잭션이 이미 커밋된 뒤라 실패마다 행이 남는다. 어긋나면 3 으로 끝낸다.
# 스크립트로 돌 때만이다 — 시험이 임포트할 때는 시험 DB 가 아직 없다.
import schema_guard                                                 # noqa: E402
if __name__ == "__main__":
    schema_guard.check_or_exit("segment_forams")

from django.conf import settings                                    # noqa: E402
from django.db import transaction                                   # noqa: E402
from django.utils import timezone                                   # noqa: E402

import batch_scope                                                  # noqa: E402
import runlog                                                       # noqa: E402
# 교정 재바인딩은 3단계(`migrate/rebind.py`)에서 온다. 그때까지는 없다 —
# `mask_key` 규칙만 여기 한 벌 둔다(그쪽과 같아야 한다: bbox 네 수를 `_` 로)
try:
    import rebind                                                   # noqa: E402
except ImportError:
    rebind = None
import judge                                                        # noqa: E402
from viewer.images import ensure_image
from viewer.models import (Candidate, Detection, Frame, Run,        # noqa: E402
                           ThresholdSet, Viewpoint)
from judge import classify, dedupe                 # noqa: F401,E402 (외부에서 쓴다)
from judge import collapse_boxes as judge_collapse  # noqa: E402
from judge import DEFAULTS as JUDGE_DEFAULTS       # noqa: E402
from scale import DEFAULT_UM_PER_PIXEL, ScaleLog, scaling_for    # noqa: E402

# 판정 규칙은 judge.py 에 있다 — refilter.py 와 같은 함수를 써야 하고,
# 문턱 재조정은 GPU 가 필요 없는 일이라 torch 에 기대지 않아야 한다.

# µm/픽셀은 `scale.py` 가 사진 곁에서 읽는다 (scale.toml · EXIF · 사이드카).
# 상수로 박아 두면 촬영 조건이 바뀌었을 때 조용히 전부 틀리기 때문이다.


def autocast_dtype():
    """GPU 세대에 맞는 autocast dtype. Pascal(6.x)은 bf16 미지원 + fp16이 느려서 fp32."""
    if not torch.cuda.is_available():
        return None
    major, _ = torch.cuda.get_device_capability()
    if major >= 8:
        return torch.bfloat16
    if major == 7:
        return torch.float16
    return None


def load_generator(backend: str, device: str, args):
    if backend == "yolo":
        return YoloGenerator(weights_path(args.weights), device,
                             conf=args.yolo_conf, imgsz=args.yolo_imgsz,
                             iou=args.yolo_nms_iou)
    raise SystemExit(f"unknown backend: {backend}")


def weights_path(weights: str) -> Path:
    """가중치의 실제 자리. 상대경로면 **자료 뿌리 아래**에서 찾는다.

    컨테이너 안팎의 경로가 같아(`/data3/ForGIA`) 명령을 그대로 옮겨 쓸 수 있지만,
    작업 디렉토리는 `/app` 이라 상대경로가 저장소 안을 가리킨다 — 거기엔 가중치가
    없다. 절대경로는 그대로 쓴다.
    """
    p = Path(weights)
    if p.is_absolute():
        return p
    here = Path(settings.DATA_ROOT) / weights
    return here if here.exists() else p


def weights_stamp(path: Path) -> dict:
    """**어느 가중치가 이 회차를 냈는가** (DiaRUGA 075). `Run.params` 에 남는다.

    회차를 돌리면 가중치가 여럿이 된다. 이름만 남기면 같은 이름으로 다시 학습한
    파일을 못 가르므로 **크기와 내용 해시**를 함께 적는다 — 파일이 사라진 뒤에도
    "그 묶음이 무엇으로 나왔는지" 를 되짚을 수 있어야 한다.

    이름 규칙은 `<모형>-<자료판>-<입력크기>[-<날짜>].pt` 다. 새로 학습할 때마다
    날짜를 붙여 **덮어쓰지 않는다** — 덮어쓰면 옛 묶음의 근거가 사라진다.
    """
    if not path.exists():
        return {"path": str(path), "missing": True}
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return {"path": str(path), "bytes": path.stat().st_size,
            "mtime": int(path.stat().st_mtime), "sha256": h.hexdigest()[:16]}


class YoloGenerator:
    """학습한 YOLO-seg 를 검출기 공통 모양(마스크 dict 목록)으로 감싼다.

    `generate(arr)` 가 dict 목록을 내면 뒤의 파이프라인(`masks_to_records` →
    `filter_records` → `judge`)이 그대로 돈다. **엔진을 갈아도 지표 계산과
    판정이 한 곳에 남는다.**

    `predicted_iou`·`stability_score` 자리에 YOLO 의 `conf` 를 넣는다 (DiaRUGA 의
    SAM2 자리 그대로). **`judge.conf_min` 이 `predicted_iou` 를 본다** — 낮게
    뽑아 DB 에 두고 문턱은 `refilter.py` 로 나중에 고른다.

    **`conf` 를 낮게 두고 뽑는 것이 기본이다.** 운영점은 슬라이드마다 다르고
    (DiaRUGA 025 §8), 낮게 뽑아 두면 DB 에서 다시 고를 수 있다.
    """

    def __init__(self, weights: str, device: str, conf: float, imgsz: int,
                 iou: float):
        from ultralytics import YOLO                          # noqa: PLC0415

        if not Path(weights).exists():
            raise SystemExit(f"가중치가 없다: {weights}")
        self.model = YOLO(weights)
        self.device = 0 if device == "cuda" else "cpu"
        self.conf, self.imgsz, self.iou = conf, imgsz, iou

    # 마스크를 잘라 올 때 상자 둘레에 두는 여유(화소). 마스크는 상자보다 조금
    # 넘칠 수 있다 — 상자와 마스크가 따로 예측되기 때문이다. 넉넉히 잡고,
    # 그래도 잘린 낌새가 보이면 전체로 물러난다.
    PAD = 24

    def generate(self, arr):
        r = self.model.predict(arr, imgsz=self.imgsz, conf=self.conf,
                               iou=self.iou, device=self.device,
                               verbose=False, retina_masks=True)[0]
        if r.masks is None:
            return []
        h, w = arr.shape[:2]
        out = []
        confs = r.boxes.conf.tolist()
        # **상자만큼만 CPU 로 가져온다.** 마스크는 (N, 2208, 2752) 로 오는데
        # 개체는 그 안의 한 조각이다. 통째로 numpy 로 옮겨 `> 0.5`·`nonzero` 를
        # 하면 개체마다 6백만 화소를 세 번 훑는다 — 실측 15 ms/개체였다.
        boxes = r.boxes.xyxy.round().to(int).tolist()
        data = r.masks.data
        for i in range(len(data)):
            bx0, by0, bx1, by1 = boxes[i]
            cx0, cy0 = max(0, bx0 - self.PAD), max(0, by0 - self.PAD)
            cx1, cy1 = min(w - 1, bx1 + self.PAD), min(h - 1, by1 + self.PAD)
            mt = data[i]
            if mt.shape != (h, w):
                # retina_masks=True 면 원본 크기지만, 확인 없이 믿지 않는다
                m = cv2.resize(mt.cpu().numpy().astype(np.float32), (w, h),
                               interpolation=cv2.INTER_NEAREST)
                sub = m[cy0:cy1 + 1, cx0:cx1 + 1] > 0.5
            else:
                sub = (mt[cy0:cy1 + 1, cx0:cx1 + 1] > 0.5).cpu().numpy()
            if not sub.any():
                continue
            sy, sx = np.nonzero(sub)
            # 잘린 창의 테두리에 닿으면 마스크가 더 뻗어 있을 수 있다.
            # 그때만 전체를 본다 — 답이 달라지면 안 되므로 물러나는 쪽을 고른다.
            touches = (sx.min() == 0 or sy.min() == 0
                       or sx.max() == sub.shape[1] - 1
                       or sy.max() == sub.shape[0] - 1)
            if touches and (cx0 > 0 or cy0 > 0 or cx1 < w - 1 or cy1 < h - 1):
                m = (mt.cpu().numpy() if mt.shape == (h, w)
                     else cv2.resize(mt.cpu().numpy().astype(np.float32), (w, h),
                                     interpolation=cv2.INTER_NEAREST))
                seg = m > 0.5
                ys, xs = np.nonzero(seg)
                if len(xs) == 0:
                    continue
                x0, x1 = int(xs.min()), int(xs.max())
                y0, y1 = int(ys.min()), int(ys.max())
                area = int(seg.sum())
            else:
                seg = np.zeros((h, w), dtype=bool)
                seg[cy0:cy1 + 1, cx0:cx1 + 1] = sub
                x0, x1 = int(sx.min()) + cx0, int(sx.max()) + cx0
                y0, y1 = int(sy.min()) + cy0, int(sy.max()) + cy0
                area = int(sub.sum())
            if area == 0:
                continue
            c = round(float(confs[i]), 4)
            out.append({
                "segmentation": seg,
                # **상자를 마스크에서 다시 뽑는다.** YOLO 의 상자와 마스크는
                # 따로 예측되어 미세하게 어긋난다. 마스크가 곧 상자여야 `mask_key`
                # 가 마스크를 가리킨다
                "bbox": [x0, y0, x1 - x0 + 1, y1 - y0 + 1],
                "area": area,
                "predicted_iou": c,
                "stability_score": c,
            })
        return out


def shape_metrics(seg, bbox=None):
    """
    마스크 하나의 형태 지표. 측정 불가면 None.

    원형도(circularity)는 신장비에 지배되므로 '테두리 매끈함' 으로 쓸 수 없다.
    신장비 6인 봉상은 윤곽이 완벽해도 0.37 이다. 매끈함은 convexity 로 잰다.

    **개체가 있는 자리만 훑는다.** 마스크는 이미지 전체 크기(2752×2208, 6백만
    화소)로 오는데 개체는 중앙값 169 px 다. 예전에는 개체 하나마다 전체 배열을
    네댓 번 훑어서 프레임 시간의 대부분이 여기 들었다.

    **그러면서 값은 한 자리도 달라지지 않아야 한다.** `judge` 가 이 값들로
    판정하므로 달라지면 지금까지의 검출·문턱 이력과 어긋난다. 그래서 윤곽만
    잘라서 찾고, **찾은 뒤 곧바로 원래 좌표로 되돌려** 나머지 계산을 옛날과
    똑같은 좌표에서 한다 — `fitEllipse` 는 float32 라 좌표가 조금만 달라도
    결과가 미세하게 바뀌고, `int(round(cx))` 가 그 차이를 1 픽셀로 증폭한다
    (실측: 3,000개 중 1,011개의 `ellipse_iou` 가 중앙 1.6% 달라졌다).

    `bbox` 를 주면 그것으로 자른다. 안 주면 마스크를 훑어 찾는데, 그 훑기가
    아끼려던 비용만큼 든다 — 부르는 쪽은 이미 bbox 를 갖고 있다.
    """
    H, W = seg.shape[:2]
    if bbox is not None:
        bx, by, bw, bh = (int(v) for v in bbox)
        x0, y0, x1, y1 = bx, by, bx + bw - 1, by + bh - 1
    else:
        ys, xs = np.nonzero(seg)
        if len(xs) == 0:
            return None
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W - 1, x1), min(H - 1, y1)
    if x1 < x0 or y1 < y0:
        return None

    # 윤곽을 찾을 창. 1 화소 여유를 두되 이미지 밖으로는 안 나간다 — 옛 코드는
    # 이미지 전체를 봤으므로 가장자리에 닿은 개체의 윤곽도 그때와 같아야 한다.
    cx0, cy0 = max(0, x0 - 1), max(0, y0 - 1)
    cx1, cy1 = min(W - 1, x1 + 1), min(H - 1, y1 + 1)
    m = np.ascontiguousarray(seg[cy0:cy1 + 1, cx0:cx1 + 1].astype(np.uint8))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    # 여기서 원래 좌표로 되돌린다. 이 아래는 옛 코드와 글자 그대로 같다.
    c = c + np.array([[[cx0, cy0]]], dtype=c.dtype)

    area = cv2.contourArea(c)
    if area < 20 or len(c) < 5:
        return None

    peri = cv2.arcLength(c, True)
    hull = cv2.convexHull(c)
    hull_area = cv2.contourArea(hull)

    (cx, cy), (d1, d2), ang = cv2.fitEllipse(c)
    major, minor = max(d1, d2), min(d1, d2)

    # 외곽선을 단순화해 저장한다. 마스크 전체(RLE)를 넣으면 JSON 이 수십 배로
    # 불어나는데, 뷰어는 윤곽만 있으면 SVG 로 그릴 수 있다.
    # epsilon 을 둘레에 비례시켜 크기와 무관하게 같은 정도로 단순화한다.
    poly = cv2.approxPolyDP(c, 0.004 * peri, True).reshape(-1, 2)

    # 적합 타원과의 IoU — 봉상(길쭉한 타원)과 원형(이심률 0)을 한 지표로 묶는다.
    # OpenCV 5 는 RotatedRect 축약형을 안 받아서 인자를 풀어 쓴다.
    #
    # 캔버스는 **마스크와 타원을 모두 덮는 만큼만** 잡고 이미지 밖으로는 안
    # 나간다. 그 바깥은 마스크도 타원도 0 이라 교집합·합집합에 아무것도 더하지
    # 않는다 — 이미지 전체에 그리던 옛 결과와 정확히 같다. 휜 봉상에 맞춘
    # 타원은 마스크를 크게 벗어나므로 그 몫을 축정렬 외접 상자로 정확히 구한다.
    ra, rb = d1 / 2, d2 / 2
    th = math.radians(ang)
    ex = math.hypot(ra * math.cos(th), rb * math.sin(th))
    ey = math.hypot(ra * math.sin(th), rb * math.cos(th))
    ix0 = max(0, min(x0, int(math.floor(cx - ex)) - 2))
    iy0 = max(0, min(y0, int(math.floor(cy - ey)) - 2))
    ix1 = min(W - 1, max(x1, int(math.ceil(cx + ex)) + 2))
    iy1 = min(H - 1, max(y1, int(math.ceil(cy + ey)) + 2))
    sub = np.ascontiguousarray(seg[iy0:iy1 + 1, ix0:ix1 + 1].astype(np.uint8))
    canvas = np.zeros(sub.shape, dtype=np.uint8)
    cv2.ellipse(canvas, (int(round(cx)) - ix0, int(round(cy)) - iy0),
                (int(round(d1 / 2)), int(round(d2 / 2))),
                float(ang), 0.0, 360.0, 1, -1)
    inter = int(np.logical_and(sub, canvas).sum())
    union = int(np.logical_or(sub, canvas).sum())

    return {
        "circularity": round(float(4 * np.pi * area / max(peri**2, 1e-9)), 3),
        # 볼록껍질 둘레 / 실제 둘레. 울퉁불퉁하면 분모만 길어진다. 신장비와 무관.
        "convexity": round(float(cv2.arcLength(hull, True) / max(peri, 1e-9)), 3),
        "solidity": round(float(area / max(hull_area, 1e-9)), 3),
        "elongation": round(float(major / max(minor, 1e-6)), 2),
        "ellipse_iou": round(float(inter / max(union, 1)), 3),
        # [x0,y0,x1,y1,...] 평탄 배열 — 좌표쌍 리스트보다 JSON 이 작다.
        "polygon": [int(v) for v in poly.ravel()],
        "_major_px": float(major),
        "_minor_px": float(minor),
    }



def largest_body(seg, bbox=None):
    """마스크에서 **가장 큰 연결 성분 하나만** 남긴다.

    후처리를 껐으므로(`apply_postprocessing=False`, `min_mask_region_area=0`)
    SAM 마스크에는 떨어져 나온 작은 조각이 남는다. 005 §5 실측으로 한 사진에서
    **마스크의 21%(24/117)가 연결 성분 2개 이상**이었고, 떠돌이 픽셀 하나가
    bbox 를 24 px 넓힌 사례가 있었다.

    이것이 조용히 문제가 되는 이유는 **두 갈래가 다른 것을 보기 때문**이다 —
    bbox·area 는 SAM 이 준 마스크 전체에서 오고, 폴리곤과 형태 지표는
    `shape_metrics()` 가 가장 큰 윤곽 하나에서 낸다. 그래서 통과분의 27.5%
    (694/2,522)가 bbox 가 본체보다 10 px 이상 컸고, bbox 로 재는 중복 정리가
    무력화됐다(실측: cover 0.794 로 문턱을 아슬아슬하게 피해 둘 다 남았는데,
    본체 bbox 였다면 0.925 로 정리됐을 것이다).

    여기서 본체만 남기면 두 갈래가 같은 것을 보게 된다.

    `bbox` 를 주면 그 안에서만 찾는다. 안 주면 마스크를 훑어 찾는데, **그 훑기가
    아끼려던 비용만큼 든다** — 부르는 쪽은 이미 bbox 를 갖고 있다.

    돌려주는 값: (본체 마스크, (x,y,w,h), 면적, 성분 수). 빈 마스크면 None.
    """
    # **개체가 있는 자리에서만 찾는다.** 마스크는 이미지 전체 크기(2752×2208,
    # 6백만 화소)로 오는데 개체는 그 안의 0.3% 다. 전체에서 연결 성분을 찾으면
    # 개체당 수십 ms 가 든다 — 실측으로 지표 계산 37 ms/개체의 대부분이 여기였다.
    #
    # 잘라도 답은 같다. 연결 성분은 **국소적**이라 bbox 밖의 배경이 결과에
    # 영향을 주지 않는다. 다만 돌려주는 마스크는 **전체 좌표**여야 한다 —
    # 부르는 쪽이 `seg[y:y+h, x:x+w]` 처럼 이미지 좌표로 자른다.
    if bbox is not None:
        bx, by, bw, bh = (int(v) for v in bbox)
        x0, y0 = max(0, bx), max(0, by)
        x1 = min(seg.shape[1] - 1, bx + bw - 1)
        y1 = min(seg.shape[0] - 1, by + bh - 1)
        if x1 < x0 or y1 < y0:
            return None
    else:
        # bbox 를 안 주면 여기서 찾는데, **그 훑기가 아끼려던 비용만큼 든다.**
        # 실측으로 이 한 줄이 개체당 35 ms 였다 — 부르는 쪽은 이미 갖고 있다.
        ys, xs = np.nonzero(seg)
        if len(xs) == 0:
            return None
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
    sub = np.ascontiguousarray(seg[y0:y1 + 1, x0:x1 + 1].astype(np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(sub, connectivity=8)
    if n <= 1:                      # 0 번은 배경이다
        return None
    idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    box = (int(stats[idx, cv2.CC_STAT_LEFT]) + x0,
           int(stats[idx, cv2.CC_STAT_TOP]) + y0,
           int(stats[idx, cv2.CC_STAT_WIDTH]), int(stats[idx, cv2.CC_STAT_HEIGHT]))
    body = np.zeros(seg.shape, dtype=bool)
    body[y0:y1 + 1, x0:x1 + 1] = (labels == idx)
    return body, box, int(stats[idx, cv2.CC_STAT_AREA]), n - 1


def masks_to_records(masks, um_per_px=DEFAULT_UM_PER_PIXEL, gray=None,
                     keep_largest_body=True):
    """검출기 마스크 dict 리스트를 정리된 후보 레코드로.

    `keep_largest_body` 가 기본이다 (DiaRUGA 005 §5) — 떨어져 나온 조각이 bbox 를
    넓히면 `mask_key` 가 흔들린다.
    """
    records = []
    n_split = 0
    for i, m in enumerate(masks):
        x, y, w, h = m["bbox"]
        area_px = int(m["area"])
        seg = m["segmentation"]

        if keep_largest_body:
            # bbox 를 넘겨 준다 — 안 주면 마스크를 다시 훑는다
            body = largest_body(seg, (x, y, w, h))
            if body is None:
                continue                       # 빈 마스크
            seg, (x, y, w, h), area_px, n_bodies = body
            if n_bodies > 1:
                n_split += 1
        # 형태 지표: 채움율, 종횡비
        fill = area_px / max(w * h, 1)
        records.append(
            {
                "id": i,
                "bbox_xywh": [int(x), int(y), int(w), int(h)],
                "center_xy": [int(x + w / 2), int(y + h / 2)],
                "area_px": area_px,
                "area_um2": round(area_px * um_per_px**2, 2),
                "long_side_um": round(max(w, h) * um_per_px, 2),
                "short_side_um": round(min(w, h) * um_per_px, 2),
                "aspect_ratio": round(max(w, h) / max(min(w, h), 1), 2),
                "fill_ratio": round(fill, 3),
                "predicted_iou": round(float(m["predicted_iou"]), 3),
                "stability_score": round(float(m["stability_score"]), 3),
                "rle": None,  # 필요하면 별도 저장
            }
        )
        rec = records[-1]
        rec["_seg"] = seg

        # 형태 지표를 여기서 전부 계산해 남긴다 — 문턱을 바꿀 때 검출기를 다시
        # 돌리지 않고 refilter.py 로 끝난다.
        # bbox 를 넘겨 준다 — 안 주면 마스크를 다시 훑느라 아끼려던
        # 비용이 그대로 든다
        sm = shape_metrics(seg, (x, y, w, h))
        if sm is None:
            rec["shape_ok"] = False
            continue
        rec["shape_ok"] = True
        rec["major_um"] = round(sm.pop("_major_px") * um_per_px, 2)
        rec["minor_um"] = round(sm.pop("_minor_px") * um_per_px, 2)
        rec.update(sm)
        # 텍스처는 안 잰다 (P01 2절 ②) — 칸은 DiaRUGA 와 같게 두고 늘 None 이다
        rec["texture"] = None
    if n_split:
        print(f"  연결 성분이 여럿인 마스크 {n_split}/{len(masks)}개 — "
              f"본체만 남겼다", file=sys.stderr)
    return records


def filter_records(records, min_um, max_um, drop_background_frac=0.5, img_area=None):
    """너무 작은 debris와 배경 전체를 덮는 마스크를 제거.

    **크기는 타원 장축으로 잰다.** README 가 그렇게 정해 뒀고 `judge.classify()`
    도 `major_um` 을 본다. 여기만 bbox 긴 변으로 재고 있었는데, 지금까지는 bbox 가
    떠돌이 조각 때문에 부풀어 있어서 이 관문이 거의 안 걸렸다. 본체만 남기면서
    bbox 가 조여지자 **타원 장축은 범위 안인데 bbox 로는 밖인 개체**가 생긴다
    (005 §5 실측 33개, 장축 중앙값 11.2 µm — 관문 한가운데다).

    형태를 못 낸 마스크(`shape_ok=False`)만 bbox 긴 변으로 대신 잰다.
    """
    out = []
    for r in records:
        size_um = r.get("major_um") if r.get("shape_ok") else r["long_side_um"]
        if size_um is None:
            size_um = r["long_side_um"]
        if size_um < min_um or size_um > max_um:
            continue
        if img_area and r["area_px"] > drop_background_frac * img_area:
            continue
        out.append(r)
    return out


# Candidate 에 그대로 들어가는 수치 칸들 (import_json.py 와 같아야 한다)
NUM = ("area_um2", "major_um", "minor_um", "long_side_um", "short_side_um",
       "aspect_ratio", "fill_ratio", "circularity", "convexity", "solidity",
       "elongation", "ellipse_iou", "texture", "predicted_iou",
       "stability_score")


def git_version():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5,
                             cwd=Path(__file__).resolve().parent)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def rel(p) -> str:
    """DATA_ROOT 기준 상대경로. DB 는 이 형태로만 경로를 담는다."""
    p = Path(p)
    if not p.is_absolute():
        return str(p)
    try:
        return str(p.relative_to(Path(settings.DATA_ROOT)))
    except ValueError:
        return str(p)


def find_viewpoint(stem: str, slide=None):
    """파일 이름으로 시야를 찾는다.

    합성본은 `<tag>_focused`, 싱글턴은 프레임 이름(`Snap-21171`)이다.

    **`slide` 를 반드시 함께 준다.** 프레임 이름은 카메라가 매기는 일련번호라
    슬라이드 사이에서 겹친다 — 같은 날 이어서 찍으면 번호대가 이어지기 때문이다.
    실제로 `260803` 의 두 슬라이드에서 **143종이 겹쳤고**, 이름만으로 찾다가
    한 슬라이드의 싱글턴 12개가 전부 다른 슬라이드의 시야로 풀렸다. 그쪽은 이미
    검출이 끝나 있어 "이미 검출함" 으로 건너뛰었고, **파이프라인이 매분 헛돌며
    슬라이드가 영영 `done` 이 되지 않았다.**

    순서가 반대였다면 더 나빴다 — 검출이 엉뚱한 슬라이드의 시야에 저장됐을 것이다.
    """
    if stem.endswith("_focused"):
        tag = stem[: -len("_focused")]
        q = Viewpoint.objects.filter(tag=tag)
        if slide is not None:
            q = q.filter(slide=slide)
        return q.first(), "stack", None
    q = Frame.objects.filter(name=stem).select_related("viewpoint")
    if slide is not None:
        q = q.filter(slide=slide)
    elif q.count() > 1:
        # 슬라이드를 모르는데 이름이 겹치면 아무거나 고르지 않는다.
        # 조용히 틀리는 것보다 안 하는 것이 낫다.
        print(f"  {stem}: 이름이 여러 슬라이드에 걸쳐 있다 — 어느 것인지 "
              f"정할 수 없어 건너뛴다 (--slide 를 줄 것)", file=sys.stderr)
        return None, None, None
    fr = q.first()
    if fr and fr.viewpoint:
        return fr.viewpoint, "frame", fr
    return None, None, None


def mask_key(bbox) -> str:
    """교정이 붙는 열쇠 — bbox 네 수를 `_` 로 이은 문자열. **`rebind.mask_key` 와
    같아야 한다** (3단계에서 그쪽이 오면 이것을 그쪽으로 바꾼다)."""
    return "_".join(str(int(v)) for v in bbox)


def threshold_set_for(values: dict) -> ThresholdSet:
    """같은 문턱 조합이면 한 행을 공유한다. refilter.py 와 같은 규칙."""
    found = ThresholdSet.objects.filter(**values).first()
    if found:
        return found
    name = (f"conf {values['conf_min']:g} · "
            f"{values['min_um']:g}~{values['max_um']:g} µm")
    return ThresholdSet.objects.create(name=name, note="segment 가 만들었다",
                                       **values)


@contextlib.contextmanager
def gpu_lock(timeout: float = 7200.0):
    """GPU 를 쓰는 작업이 한 번에 하나만 돌게 한다.

    **부르는 쪽이 기억해야 하는 일로 두지 않는다.** 폴러는 `flock` 으로 자기들끼리
    막지만, 사람이 손으로 돌릴 때 그것을 잊으면 겹친다. 이 장비는 8 GB 라 두
    작업이 겹치면 둘 다 느려지거나 하나가 죽는다.

    잠금 파일은 호스트와 컨테이너가 같은 경로로 보는 자리에 둔다. 컨테이너가
    죽어도 파일 잠금은 커널이 풀어 준다 — 남은 파일이 막지 않는다.

    **DiaRUGA 와 같은 카드를 쓴다** (P01 5절). `FORGIA_GPU_LOCK` 을 DiaRUGA 의 잠금
    파일(`/data3/DiaRUGA/locks/gpu.lock`)로 주고 그 디렉토리를 컨테이너에 물리면
    `flock` 이 같은 inode 를 잡아 **두 프로젝트가 서로 기다린다** — DiaRUGA 쪽
    코드를 고치지 않아도 된다(그쪽 기본값이 그 파일이다). `deploy/srv/docker-compose.yml`
    이 그렇게 물린다.
    """
    path = Path(os.environ.get("FORGIA_GPU_LOCK",
                               str(Path(settings.DATA_ROOT) / "locks" / "gpu.lock")))
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    waited = 0.0
    try:
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if waited >= timeout:
                    raise SystemExit(
                        f"다른 GPU 작업이 {timeout / 60:.0f}분 넘게 돌고 있다: {path}")
                if waited == 0:
                    print("다른 GPU 작업이 끝나기를 기다린다…", file=sys.stderr)
                time.sleep(5.0)
                waited += 5.0
        if waited:
            print(f"  {waited:.0f}초 기다렸다", file=sys.stderr)
        yield
    finally:
        fh.close()          # 닫으면 잠금도 풀린다


def mark_done_if_complete(slide) -> bool:
    """자동 처리가 다 끝났으면 슬라이드를 `done` 으로 연다.

    **`done` 이 되기 전에는 뷰어가 검토를 막는다** (DiaRUGA P01 §1). 반쯤 처리된 슬라이드를
    사람이 검토하면, 아직 안 돌아간 시야의 검출이 뒤늦게 들어오면서 이미 본 화면이
    바뀐다. 최상위 디렉토리 하나를 단위로 열고 닫는다.

    `failed`(그룹핑이 미심쩍다고 표시한 것)는 열지 않는다 — 사람이 봐야 한다.
    """
    if slide.state == "failed":
        return False
    vps = list(slide.viewpoints.all())
    if not vps:
        return False
    missing = [vp for vp in vps
               if not vp.detections.filter(is_current=True).exists()]
    if missing:
        slide.state = "processing"
        slide.state_note = f"검출 대기 시야 {len(missing)}개"
        slide.save(update_fields=["state", "state_note"])
        return False
    slide.state = "done"
    slide.state_note = ""
    slide.processed_at = timezone.now()
    slide.save(update_fields=["state", "state_note", "processed_at"])
    return True


def with_db_retry(fn, tries=6, base=0.7):
    """SQLite 가 잠겨 있으면 잠깐 쉬었다 다시 한다.

    **뷰어와 파이프라인이 같은 DB 를 쓴다.** WAL 이라 읽기는 여럿이 동시에 되지만
    **쓰기는 한 번에 하나**다. 파이프라인이 개체 수천 개를 한 트랜잭션으로 넣는
    동안 사람이 검토를 저장하면 뒤엣것이 `timeout`(20초)을 기다리다 죽는다.

    실제로 그렇게 잃었다 — 2차 실행 중에 검토 저장 31건이 겹쳐 `bp09-0901`
    프레임 229장이 통째로 날아갔다. 사람이 검토하는 중에 파이프라인을 돌리는
    일이 앞으로 늘어날 것이므로 재시도로 받아 낸다.

    **잠금이 아닌 오류는 그대로 올려보낸다.** 무엇이든 다시 해 보는 것은
    고장을 숨기는 짓이다.
    """
    from django.db import OperationalError                      # noqa: PLC0415

    for i in range(tries):
        try:
            return fn()
        except OperationalError as e:
            if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                raise
            if i == tries - 1:
                raise
            wait = base * (2 ** i)
            print(f"  DB 가 잠겨 있다 — {wait:.1f}초 뒤 다시 ({i + 1}/{tries})",
                  file=sys.stderr)
            time.sleep(wait)


def save_detection(payload: dict, img_path: Path, run: Run, iou_min: float,
                   keep_current: bool = False, slide=None):
    """검출 결과를 새 Detection 으로 쌓고 교정을 다시 맺는다.

    **트랜잭션을 둘로 나눈다.**

    1. 검출 행 + 개체 수천 개를 넣는다 (`is_current=False`)
    2. `is_current` 를 옮기고 교정을 다시 맺는다

    2번은 **반드시 한 덩어리여야 한다** — 갈라지면 그 사이에 뷰어를 연 사람이
    "교정이 하나도 안 붙은 새 검출"을 본다. 1번은 그럴 필요가 없다.
    `is_current=False` 라 뷰어에 아예 안 보이기 때문이다.

    **나눈 이유는 잠금이다.** 뷰어와 파이프라인이 같은 SQLite 를 쓴다. WAL 이라
    읽기는 여럿이 동시에 되지만 **쓰기는 한 번에 하나**다. 둘을 한 덩어리로 묶으면
    개체 수천 개를 넣는 내내 쓰기 잠금을 쥐고 있어, 그동안 사람이 검토를 저장하면
    기다리다 죽는다 — 실제로 그렇게 `bp09-0901` 프레임 229장을 잃었다.

    나누면 잠금을 쥐는 시간이 **각 조각의 길이**로 줄어든다. 1번이 실패하면
    `is_current=False` 인 검출이 남는데, 화면에 안 보이고 다음 실행이 새로
    쌓으므로 해가 없다.

    `keep_current=True` 면 **`is_current` 를 옮기지 않고 재바인딩도 하지 않는다.**
    새 검출은 `is_current=False` 로 쌓이기만 한다. 뷰어(`data.py`)는 current 인
    것만 보므로 화면도 교정도 그대로다 — 다른 엔진을 같은 시야에서 나란히
    재 보려고 둔 길이다(DiaRUGA P01 §3).

    **이것이 없으면 실험 한 번이 교정 2,400건을 흔든다.** 엔진이 다르면
    `mask_key`(bbox 문자열)가 거의 전부 어긋나 재바인딩이 orphan 을 쏟아내고,
    되돌리려면 또 한 번 뒤집어야 한다.

    돌려주는 값: (Detection, 개체 수, 재바인딩 방법별 개수)
    """
    stem = img_path.stem
    vp, target, frame = find_viewpoint(stem, slide)
    if vp is None:
        return None, 0, None

    with transaction.atomic():
        ts = threshold_set_for(payload["thresholds"])
        # 어느 이미지에 대한 검출인가 (DiaRUGA P06). `target`·`frame` 은 아직 함께
        # 쓴다 — 조이기(5단계) 전까지는 그쪽이 원본이다.
        image = ensure_image(rel(img_path),
                             "frame" if target == "frame" else "stack",
                             viewpoint=vp, frame=frame,
                             stack=getattr(vp, "stack", None) if target == "stack" else None,
                             width=payload["size"][0], height=payload["size"][1])
        det = Detection.objects.create(
            viewpoint=vp, image=image,
            image_path=rel(img_path),
            width=payload["size"][0], height=payload["size"][1],
            scale=payload["scale"],
            um_per_pixel=payload["um_per_pixel"],
            um_per_pixel_native=payload["um_per_pixel_native"],
            um_per_pixel_source=payload["um_per_pixel_source"] or "",
            n_raw_masks=payload["n_raw_masks"], n_sized=payload["n_sized"],
            thresholds=ts, run=run, is_current=False)   # 아직 아니다

        rows, seen = [], set()
        for passed, pool in ((True, payload["candidates"]),
                             (False, payload["rejected"])):
            for c in pool:
                b = c["bbox_xywh"]
                key = mask_key(b)
                # 같은 bbox 가 두 번 나오면 unique 에 걸린다 — 첫 것만 둔다
                if key in seen:
                    continue
                seen.add(key)
                ctr = c.get("center_xy") or [None, None]
                rows.append(Candidate(
                    detection=det, mask_key=key, raw_id=c.get("id"),
                    bbox_x=int(b[0]), bbox_y=int(b[1]),
                    bbox_w=int(b[2]), bbox_h=int(b[3]),
                    center_x=ctr[0], center_y=ctr[1],
                    area_px=c.get("area_px") or 0,
                    shape_ok=bool(c.get("shape_ok")),
                    polygon=c.get("polygon") or [],
                    passed=passed, cls=c.get("cls") or "",
                    reject=(c.get("reject") or "") if not passed else "",
                    **{f: c.get(f) for f in NUM}))
        Candidate.objects.bulk_create(rows, batch_size=2000)
    # ── 첫 트랜잭션 끝. 여기서 쓰기 잠금을 놓는다 ──

    if keep_current:
        # 쌓아만 둔다. 지금 것을 건드리지 않으므로 재바인딩도 하지 않는다.
        return det, len(rows), None

    # 두 번째 — 짧고, 반드시 한 덩어리여야 하는 부분
    #
    # **여기서 죽으면 첫 트랜잭션이 남긴 것을 거둔다** (DiaRUGA 198). 앞 트랜잭션은
    # 이미 커밋된 뒤라 그냥 올려보내면 `is_current=False` 인 검출과 후보
    # 수백 행이 실패마다 남는다 — 폴러가 매분 다시 부르므로 이틀에 4,200번
    # 쌓여 DB 가 여섯 배가 됐다. 잠금 재시도(`with_db_retry`)도 같은 길이다:
    # 다시 돌면 첫 트랜잭션을 또 지나므로 앞엣것은 지워져 있어야 한다.
    try:
        stat = _promote_and_rebind(vp, det, iou_min)
    except BaseException:
        Detection.objects.filter(pk=det.pk).delete()    # 후보는 CASCADE
        raise
    return det, len(rows), stat


def _promote_and_rebind(vp, det, iou_min):
    """새 검출을 현재로 올리고 교정을 다시 맺는다 — 한 트랜잭션. 재바인딩 통계를 돌려준다."""
    with transaction.atomic():
        # **같은 묶음 안에서만 인계한다** (DiaRUGA P10). `is_current` 의 뜻이 좁아졌다 —
        # 예전에는 "뷰어가 볼 것" 이라 시야에 하나였고, 이제는 **그 묶음 안에서
        # 이 이미지의 최신**이다. 묶음을 안 가리고 끄면 나란히 쌓아 둔 다른
        # 회차(yolo-3차 1,799개)의 현재 표시가 통째로 꺼지고 `superseded_by` 가
        # 묶음을 가로질러 걸린다 — 그 묶음으로 갈아타는 날 화면이 빈다.
        #
        # 뷰어가 무엇을 보는지는 `RunBatch.for_review` 가 정하므로, 여기서 다른
        # 묶음을 건드릴 이유가 애초에 없다.
        # `Detection.batch` 는 `run.batch` 를 짚는 속성이라 `batch_id` 가 없다 —
        # 실행을 타고 물어본다.
        new_batch = det.run.batch_id if det.run_id else None
        old = [d for d in vp.detections.filter(is_current=True)
               .select_related("run")
               if d.image_id == det.image_id
               and (d.run.batch_id if d.run_id else None) == new_batch]
        Detection.objects.filter(pk__in=[d.pk for d in old]).update(
            is_current=False, superseded_by=det)
        det.is_current = True
        det.save(update_fields=["is_current"])

        det.refresh_from_db()
        # 교정 재바인딩은 3단계(`migrate/rebind.py` · 교정 표)에서 온다. 그때까지
        # 옮길 교정이 없으므로 통계는 빈 Counter 다 — 부르는 쪽은 그대로 돈다
        if rebind is None:
            return Counter()
        return rebind.rebind_viewpoint(vp, det, iou_min=iou_min)


def process(img_path: Path, gen, args, out_dir: Path, scale_log=None):
    # 계측의 기준이 되는 픽셀 크기. `scale.py` 가 사진 곁에서 읽고(scale.toml ·
    # EXIF), 합성본은 focus_stack.py 가 남긴 사이드카에서 온다. --um-per-pixel 이
    # 있으면 그것이 우선한다 (메타데이터가 틀렸다고 판단한 사람의 지시이므로).
    if args.um_per_pixel:
        scaling = {"um_per_pixel": args.um_per_pixel, "source": "cli", "path": None}
    else:
        scaling = scaling_for(img_path)
    if scale_log is not None:
        scale_log.add(img_path.name, scaling["um_per_pixel"])

    img = Image.open(img_path).convert("RGB")
    if args.scale != 1.0:
        img = img.resize((int(img.width * args.scale), int(img.height * args.scale)),
                         Image.LANCZOS)
    arr = np.array(img)

    with torch.inference_mode():
        dtype = autocast_dtype()
        if dtype is None:
            masks = gen.generate(arr)
        else:
            with torch.autocast("cuda", dtype=dtype):
                masks = gen.generate(arr)

    # --scale 로 리사이즈했으면 픽셀이 그만큼 커진 셈이다
    um_per_px = scaling["um_per_pixel"] / args.scale
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    recs = masks_to_records(masks, um_per_px, gray=gray,
                            keep_largest_body=not args.all_bodies)
    sized = filter_records(recs, args.min_um, args.max_um,
                           img_area=arr.shape[0] * arr.shape[1])
    # **판정 전에 같은 bbox 를 접는다** (DiaRUGA 077). DB 의 열쇠가 bbox 라 같은 자리는
    # 한 행밖에 못 들어가는데, 넣을 때 버리면 **판정이 본 집합과 저장된 집합이
    # 달라진다** — 그러면 저장된 판정을 다시 계산해도 안 맞는다(`judge.collapse_boxes`).
    sized, n_dup = judge_collapse(sized)
    if n_dup:
        print(f"  bbox 가 같은 마스크 {n_dup}개를 접었다 (판정 전)", file=sys.stderr)

    if args.no_shape_filter:
        kept, rejected = sized, []
        for r in kept:
            r["cls"] = None
    else:
        passed, rejected = [], []
        for r in sized:
            cls, why = classify(r, args)
            if cls:
                r["cls"] = cls
                passed.append(r)
            else:
                r["reject"] = why
                rejected.append(r)
        kept = dedupe(passed)
        dropped = [r for r in passed if r not in kept]
        for r in dropped:
            r["reject"] = "중첩정리"
        rejected += dropped

    kept.sort(key=lambda r: -r["area_px"])
    for i, r in enumerate(kept):
        r["id"] = i

    def clean(rs):
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rs]

    stem = img_path.stem
    payload = {
        "image": str(img_path),
        "size": [img.width, img.height],
        "scale": args.scale,
        "um_per_pixel": um_per_px,
        # 계측값을 나중에 의심할 때 어디서 온 배율인지 알 수 있게 남긴다
        "um_per_pixel_native": scaling["um_per_pixel"],
        "um_per_pixel_source": scaling["source"],
        "n_raw_masks": len(recs),
        "n_sized": len(sized),
        "n_candidates": len(kept),
        # 문턱을 바꿔 다시 거를 때 필요한 값들. **`refilter.py` 는 이제
        # DB 를 읽는다**(P02 6단계) — 이 칸은 내보내기용으로만 남는다.
        "thresholds": {f: getattr(args, f) for f in judge.FIELDS},
        "counts": {
            judge.PASS_CLS: sum(1 for r in kept if r.get("cls") == judge.PASS_CLS),
        },
        "candidates": clean(kept),
        # 탈락분도 지표째 남긴다 — 문턱 재조정이 검출기 재실행 없이 끝난다.
        "rejected": clean(rejected),
    }
    # **기본은 안 쓴다** (DiaRUGA P02 7단계). 원본은 DB 이고, 이 파일은 내보내기
    # 형식일 뿐인데 늘 나오면 "이것이 결과인가" 로 읽힌다. 게다가 경로가
    # 엔진·묶음을 안 갈라서 **나중에 돈 엔진이 앞의 것을 덮어썼다** —
    # 파일 이름만으로는 어느 엔진 결과인지 알 수 없었다.
    if getattr(args, "export_json", False):
        (out_dir / f"{stem}_candidates.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
    print(f"{stem}: raw={len(recs)} 크기통과={len(sized)} 최종={len(kept)}")
    return payload


def slide_area(slide) -> str:
    """이 슬라이드의 **권역**(`Site.area`). 소속이 끊겨 있으면 빈 문자열.

    소속은 `Slide` 에 없다 — `sample → locality → site` 로 올라간다
    (CLAUDE.md · `slide.locality` 는 지름길 property 다). 중간이 비어 있는
    행이 실제로 생긴다(`check_db.py` 7번이 세는 그 상태), 그래서 예외로
    죽지 않고 **빈 값으로 말한다** — 무엇을 할지는 `batch_scope` 가 정한다.
    """
    site = getattr(getattr(getattr(slide, "sample", None),
                           "locality", None), "site", None)
    return getattr(site, "area", "") or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", help="이미지 파일 또는 디렉토리")
    ap.add_argument("--slide",
                    help="슬라이드 slug. 검출 대상을 DB 에서 고른다 — 합성본이 "
                         "있으면 그것을, 싱글턴 시야는 그 한 장을 쓴다")
    ap.add_argument("-o", "--out", default=None, help="기본값은 DATA_ROOT/out")
    ap.add_argument("--export-json", action="store_true",
                    help="out/<stem>_candidates.json 을 함께 남긴다. "
                         "**기본은 안 남긴다** — 원본은 DB 다")
    ap.add_argument("--no-db", action="store_true",
                    help="JSON 만 쓰고 DB 는 건드리지 않는다 (시험용)")
    ap.add_argument("--rebind-iou", type=float, default=0.5,
                    help="mask_key 가 안 맞을 때 교정을 다시 맺는 IoU 하한")
    ap.add_argument("--force", action="store_true",
                    help="사람이 교정한 시야도 다시 검출한다 (아래 설명을 읽을 것)")
    ap.add_argument("--all-bodies", action="store_true",
                    help="떨어진 조각까지 마스크로 인정한다 (2026-07-31 이전 방식)")
    ap.add_argument("--backend", default="yolo", choices=["yolo"],
                    help="검출기. SAM2 는 안 가져왔다 (P01 5절) — 씨앗 가중치를 합성 자료로 굽는다")
    # **시야마다 이미지 한 장** 이 기본이다 — SAM2 는 합성본만 보면 되고,
    # 폴러가 그렇게 돈다. YOLO 회차는 프레임까지 봐야 하므로 이 표를 준다.
    # 없을 때는 파일 목록을 손으로 만들어 돌려야 했다(DiaRUGA 070 에서 그렇게 했다).
    ap.add_argument("--all-images", action="store_true",
                    help="시야마다 합성본 + 프레임 전부에 돌린다 "
                         "(YOLO 회차. 기본은 시야마다 한 장)")
    # **자료 뿌리 아래의 실제 자리다.** 예전 기본값은 학습 산출 디렉토리
    # (`runs/segment/…/weights/best.pt`)를 가리켰는데 그 경로는 이 기계에 없다 —
    # `--backend yolo` 를 그냥 부르면 "가중치가 없다" 로 죽었다.
    #
    # **회차를 돌릴 것이므로 이름에 판을 적는다.** `<모형>-<자료판>-<입력크기>`
    # 에 새로 학습할 때마다 날짜를 덧붙인다 (`11m-v2seg-1280-260815.pt`).
    # 무엇이 무엇을 냈는지는 `Run.params.weights` 가 함께 남긴다.
    ap.add_argument("--weights", default="models/11n-synth-v2-1024.pt",
                    help="--backend yolo 일 때 쓸 가중치. 자료 뿌리 기준 상대경로도 "
                         "받는다. 새 회차는 이름에 날짜를 붙인다")
    # 낮게 뽑아 DB 에 넣고, 고르는 것은 나중에 한다. 운영점이 슬라이드마다
    # 다르다는 것을 devlog 025 §8 이 실측했다.
    ap.add_argument("--yolo-conf", type=float, default=0.01,
                    help="YOLO 신뢰도 하한 (기본 0.01 — 낮게 뽑아 둔다)")
    ap.add_argument("--yolo-imgsz", type=int, default=1024,
                    help="학습과 같아야 한다 (기본 1024 — 합성 자료의 크기)")
    ap.add_argument("--yolo-nms-iou", type=float, default=0.7)
    ap.add_argument("--batch", default="",
                    help="여러 슬라이드에 걸친 한 번의 작업을 묶는 이름표. "
                         "슬라이드마다 명령을 나눠 불러도 같은 이름이면 "
                         "한 덩어리로 남는다 (예: yolo-v1seg-frames)")
    ap.add_argument("--batch-note", default="",
                    help="묶음을 만들 때 함께 남길 메모 (왜 돌렸는가)")
    ap.add_argument("--keep-current", action="store_true",
                    help="새 검출을 is_current 로 올리지 않는다. 뷰어와 교정을 "
                         "건드리지 않고 다른 엔진을 나란히 재 볼 때 쓴다")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="처리 전 리사이즈 배율. YOLO 는 1.0 이어야 한다")
    ap.add_argument("--um-per-pixel", type=float, default=None,
                    help="픽셀 크기를 직접 지정 (기본: scale.py 가 사진 곁에서 읽는다)")
    ap.add_argument("--limit", type=int, default=0)

    # --- 판정 문턱 (judge.DEFAULTS) ----------------------------------------
    # 값을 바꿔 다시 거를 때는 검출기를 다시 돌릴 필요 없이 refilter.py 를 쓴다.
    g = ap.add_argument_group("판정")
    g.add_argument("--no-shape-filter", action="store_true",
                   help="판정 없이 크기 필터만 (지표는 그대로 기록)")
    g.add_argument("--min-um", type=float, default=JUDGE_DEFAULTS["min_um"],
                   help="타원 장축 하한 µm — 픽킹 분획과 맞춘다")
    g.add_argument("--max-um", type=float, default=JUDGE_DEFAULTS["max_um"])
    g.add_argument("--conf-min", type=float, default=JUDGE_DEFAULTS["conf_min"],
                   help="검출기 확신도 하한. 낮게 뽑아(--yolo-conf) 여기서 거른다")
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    # **YOLO 는 학습과 같은 해상도로 봐야 한다.** --scale 기본값이 0.5 라
    # 그대로 두면 이미지가 절반이 되고, imgsz=1280 이 거의 축소를 안 해
    # 개체가 학습 때보다 2배 크게 보인다. 조용히 성적만 떨어진다 —
    # 배율을 잘못 믿어 통과율이 1.6% 로 주저앉았던 일이 있다(DiaRUGA 015).
    if args.backend == "yolo" and args.scale != 1.0:
        raise SystemExit(
            f"--backend yolo 는 --scale 1.0 이어야 한다 (지금 {args.scale}). "
            f"가중치는 원본 해상도에 imgsz={args.yolo_imgsz} 로 학습했다")
    print(f"device={device} backend={args.backend}", file=sys.stderr)

    if args.slide:
        # 검출 대상을 DB 에서 고른다. 합성본이 원칙이다 — 초점 흐림 잔해가 줄어
        # 20 µm 이상 구간에서 더 잘 잡힌다. 싱글턴 시야는 합성본이 없으므로
        # 그 한 장을 그대로 쓴다. 예전에는 run_batch.sh 가 JSON 을 읽어 골랐다.
        from viewer.models import Slide                             # noqa: PLC0415
        try:
            slide = Slide.objects.get(slug=args.slide)
        except Slide.DoesNotExist:
            raise SystemExit(f"그런 슬라이드가 없다: {args.slide}")

        # **이 묶음이 이 권역을 보는가** (2026-08-26 · `batch_scope.py`).
        #
        # GPU 를 돌리기 전에, `runlog.start` 가 실행 행을 만들기도 전에 본다 —
        # 안 돌 일이면 실행 기록도 남지 않아야 한다. 폴러가 부르든 사람이
        # 부르든 같은 문을 지난다.
        #
        # **실패가 아니라 건너뛰기다**(0 으로 끝낸다). 폴러는 0이 아니면
        # "검출 실패" 를 로그에 적는데, 방침대로 안 돈 것은 실패가 아니다.
        if args.batch:
            from viewer.models import RunBatch                      # noqa: PLC0415
            b = RunBatch.objects.filter(kind="detect",
                                        label=args.batch).first()
            # 아직 없는 묶음은 조리법도 없다 — 막지 않는다(전부 본다).
            area = slide_area(slide)
            if b is not None and not batch_scope.allows(b.recipe, area):
                print(f"{slide.slug}: {batch_scope.why(b.recipe, area, b.label)}",
                      file=sys.stderr)
                return 0

        # 사람이 못 박은 배율은 폴더의 scale.toml(override) 이 맡는다 — scale.py
        data_root = Path(settings.DATA_ROOT)
        files = []
        for vp in slide.viewpoints.order_by("idx"):
            st = getattr(vp, "stack", None)
            if args.all_images:
                # **합성본과 프레임 전부.** YOLO 는 합성본이 아니라 원본을 보고,
                # 같은 개체가 어느 초점면에서 잡히는지가 다음 회차의 자료가 된다
                # (실측: yolo-3차 는 시야 452개에 프레임 검출 1,310개).
                if st and st.focused_path:
                    files.append(data_root / st.focused_path)
                files.extend(data_root / fr.path
                             for fr in vp.frames.order_by("seq"))
            elif st and st.focused_path:
                files.append(data_root / st.focused_path)
            else:
                fr = vp.sharpest_frame or vp.frames.order_by("seq").first()
                if fr:
                    files.append(data_root / fr.path)
        files = [f for f in files if f.exists()]
        print(f"{slide.slug}: 검출 대상 {len(files)}개"
              + (" (합성본+프레임)" if args.all_images else ""), file=sys.stderr)
    elif args.input:
        inp = Path(args.input)
        files = (sorted(p for p in inp.iterdir()
                        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff"))
                 if inp.is_dir() else [inp])
    else:
        raise SystemExit("--slide <slug> 또는 이미지 경로 중 하나가 필요하다")

    # **어느 슬라이드인지 못 박는다.** 프레임 이름은 슬라이드 사이에서 겹치므로
    # (같은 날 이어 찍으면 번호대가 이어진다) 이름만으로 시야를 찾으면 남의
    # 슬라이드로 풀린다. 경로로 들어온 경우에도 폴더로 슬라이드를 되찾는다.
    slide_obj = locals().get("slide")
    if slide_obj is None and args.input and not args.no_db:
        from viewer.models import Slide                             # noqa: PLC0415
        root = Path(settings.DATA_ROOT)
        probe = Path(args.input)
        rel = str(probe if probe.is_dir() else probe.parent)
        try:
            rel = str((probe if probe.is_dir() else probe.parent)
                      .resolve().relative_to(root.resolve()))
        except ValueError:
            rel = ""
        if rel:
            slide_obj = Slide.objects.filter(image_dir=rel).first()
        if slide_obj is None:
            # 합성본 디렉토리(stacked/)처럼 슬라이드 폴더가 아닌 경우가 있다.
            # 그때는 이름으로 찾되, 겹치면 find_viewpoint 가 건너뛴다.
            print("어느 슬라이드인지 경로로 정하지 못했다 — 이름으로 찾는다. "
                  "프레임 이름이 겹치면 건너뛴다 (--slide 를 주는 편이 안전하다)",
                  file=sys.stderr)
        else:
            print(f"경로로 슬라이드를 정했다: {slide_obj.slug}", file=sys.stderr)
    if args.limit:
        files = files[: args.limit]

    out_dir = Path(args.out) if args.out else (Path(settings.DATA_ROOT) / "out")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.um_per_pixel:
        print(f"픽셀 크기를 {args.um_per_pixel} µm/px 로 지정 — 메타를 읽지 않는다",
              file=sys.stderr)

    run = None
    if not args.no_db:
        run = runlog.start(
            "detect",
            batch_label=args.batch, batch_note=args.batch_note,
            params={"backend": args.backend, "scale": args.scale,
                    **{f: getattr(args, f) for f in judge.FIELDS},
                    "rebind_iou": args.rebind_iou, "n_files": len(files),
                    "all_images": bool(args.all_images),
                    # 회차를 돌리면 **무엇이 이 묶음을 냈는지**가 근거가 된다
                    **({"weights": weights_stamp(weights_path(args.weights)),
                        "yolo_conf": args.yolo_conf,
                        "yolo_imgsz": args.yolo_imgsz}
                       if args.backend == "yolo" else {})},
            host=socket.gethostname(), gpu=device, code_version=git_version())

    # 사람이 교정한 시야는 GPU 를 돌리기 **전에** 걸러 낸다.
    #
    # 재검출 자체는 안전하다 — 교정은 (viewpoint, mask_key) 에 붙고 rebind.py 가
    # 다시 맺어 준다(3단계). 다만 검출기가 미세하게 다른 마스크를 내므로 결과가 달라진다
    # (실측: 67건 중 exact 26 · iou 40 · 고아 1). 전수 검토를 마친 시야에서 그것이
    # 실수로 일어나면 안 된다. 특히 2026-07-31 부터 연결 성분 처리가 바뀌어
    # bbox 가 조여지므로, 옛 자료를 다시 돌리면 키가 대량으로 흔들린다.
    # **--keep-current 면 이 관문이 필요 없다.** 관문이 있는 이유는 재검출이
    # is_current 를 옮기고 교정을 다시 맺기 때문인데, keep-current 는 둘 다 안
    # 한다 — 쌓아만 둔다. 여기서 걸러 버리면 검토를 마친 264 시야가 전부
    # 빠져 나가 정작 비교하고 싶은 자리가 하나도 안 남는다.
    if args.keep_current:
        print("--keep-current: 새 검출을 is_current 로 올리지 않는다. "
              "뷰어와 교정은 그대로다", file=sys.stderr)
    elif not args.no_db and not args.force:
        # **이미지마다, 그리고 이 묶음 안에서 본다** (DiaRUGA P10 · 075).
        #
        # 예전에는 "이 **시야**에 현재 검출이 있는가" 였다. 그 물음은 시야마다
        # 이미지가 한 장이고 묶음이 하나일 때만 맞다. 지금은 둘 다 아니다:
        #
        # - 묶음을 안 가리면 **새 묶음으로 전수 재검출이 아예 안 돈다** —
        #   yolo-3차 가 덮은 시야가 전부 "이미 했다" 로 걸러진다
        # - 이미지를 안 가리면 **프레임까지 도는 회차(YOLO)를 이어 돌릴 수
        #   없다** — 합성본 하나가 되어 있으면 프레임 여섯 장이 통째로 빠진다
        keep, n_rev_skip, n_done_skip = [], 0, 0
        batch_id = run.batch_id if run is not None else None
        for f in files:
            vp, _, _ = find_viewpoint(f.stem, slide_obj)
            if vp is None:
                keep.append(f)
                continue
            # **이 묶음의 교정만 본다** (DiaRUGA P10 · 075). 관문이 있는 이유는 재검출이
            # `is_current` 를 옮기고 교정을 다시 맺기 때문인데, 둘 다 **묶음 안의
            # 일**이 됐다. 다른 묶음으로 돌리는 것은 사람의 판단을 건드리지
            # 않는다 — 오히려 그것이 회차의 목적이다(교정을 정답 삼아 새 회차를
            # 견준다). 안 가리면 **검토를 마친 시야일수록 새 회차에서 빠진다.**
            # 교정 표는 3단계에서 온다 — 그때까지는 건너뛸 교정이 없다
            if hasattr(vp, "object_reviews") and \
                    vp.object_reviews.filter(batch_id=batch_id).exists():
                n_rev_skip += 1
                continue
            # 이미 검출한 이미지는 건너뛴다. 주기 실행이 중간에 끊겼을 때 다음
            # 주기가 처음부터 다시 돌리면 한 슬라이드에 몇 시간이 또 든다.
            rel = str(f.relative_to(Path(settings.DATA_ROOT)))
            if vp.detections.filter(is_current=True, image__path=rel,
                                    run__batch_id=batch_id).exists():
                n_done_skip += 1
                continue
            keep.append(f)
        if n_rev_skip:
            print(f"이 묶음에 사람의 교정이 있는 시야 {n_rev_skip}개를 건너뛴다 "
                  f"(다시 검출하려면 --force)", file=sys.stderr)
        if n_done_skip:
            print(f"이 묶음에서 이미 검출한 이미지 {n_done_skip}개를 건너뛴다",
                  file=sys.stderr)
        files = keep
        if not files:
            print("검출할 것이 없다.", file=sys.stderr)
            # **열어 둔 실행을 닫는다.** 여기로 빠져나가면서 Run 을 안 닫아서,
            # 폴러가 매분 부르는 동안 `running` 이 하나씩 쌓였다(#96~#101).
            # 다음 실행의 close_stale 이 `failed` 로 닫으니 "실패한 실행" 이
            # 끝없이 늘어나 이력의 뜻이 흐려진다 — 실패한 것이 아니라 할 일이
            # 없었을 뿐이다.
            if not args.no_db and "run" in dir():
                run.status = "done"
                run.counts = {"detections": 0, "note": "검출할 것이 없었다"}
                run.finished_at = timezone.now()
                run.save(update_fields=["status", "counts", "finished_at"])
            # 다 끝나 있으면 상태를 열어 준다 — 앞 실행이 여기서 끊겼을 수 있다
            if args.slide:
                from viewer.models import Slide                     # noqa: PLC0415
                sl = Slide.objects.filter(slug=args.slide).first()
                if sl and mark_done_if_complete(sl):
                    print(f"  {sl.slug}: 자동 처리 완료 — 검토를 연다",
                          file=sys.stderr)
            return

    # GPU 를 잡는 것은 여기서부터다. 모델 적재도 VRAM 을 쓴다.
    gpu = gpu_lock() if device == "cuda" else contextlib.nullcontext()
    stack_ctx = contextlib.ExitStack()
    stack_ctx.enter_context(gpu)
    gen = load_generator(args.backend, device, args)
    scale_log = ScaleLog()
    n_det = n_cand = n_oom = n_oom_retry = 0
    bind = Counter()
    missing = []
    touched = set()
    try:
        for f in files:
            payload = oom_first = None
            try:
                payload = process(f, gen, args, out_dir, scale_log)
            except torch.cuda.OutOfMemoryError:
                oom_first = True

            # **재시도는 `except` 블록 밖에서 한다.** 그 안에서는 파이썬이
            # 역추적을 쥐고 있고, 역추적은 실패한 호출의 지역변수를 —— 즉 방금
            # 자리를 못 찾은 **그 큰 GPU 텐서들을** —— 살려 둔다. 참조가 남아
            # 있으면 `empty_cache()` 가 아무것도 못 비운다.
            #
            # 실제로 그래서 재시도가 소용없었다: 다시 시도할 때 남은 자리가
            # 0.9 GB 였는데 필요한 것은 2.4 GB 였다. 혼자 돌리면 6.05 GiB 로
            # 들어가는 프레임인데도 그랬다.
            if oom_first:
                gc.collect()
                torch.cuda.empty_cache()
                try:
                    payload = process(f, gen, args, out_dir, scale_log)
                    n_oom_retry += 1
                    print(f"  OOM 뒤 비우고 다시 해서 성공: {f.name}", file=sys.stderr)
                except torch.cuda.OutOfMemoryError:
                    payload = None
            if payload is None:
                # 조용히 넘기면 "검출 0개 · done" 으로 끝나 성공처럼 보인다
                print(f"OOM on {f.name} (다시 해도 실패) — 검출이 너무 많아 "
                      f"retina 마스크가 다 안 들어간다. --yolo-conf 를 올리거나 "
                      f"--yolo-imgsz 를 줄일 것", file=sys.stderr)
                gc.collect()
                torch.cuda.empty_cache()
                n_oom += 1
                continue
            if args.no_db:
                continue
            det, nc, stat = with_db_retry(
                lambda: save_detection(payload, f, run, args.rebind_iou,
                                       keep_current=args.keep_current,
                                       slide=slide_obj))
            if det is None:
                # 조용히 넘기지 않는다 — 그룹핑과 DB 가 어긋났다는 뜻이다
                missing.append(f.stem)
                print(f"  {f.stem}: 시야를 DB 에서 못 찾았다 — DB 에 남기지 않았다",
                      file=sys.stderr)
                continue
            n_det += 1
            n_cand += nc
            # --keep-current 면 재바인딩을 안 하므로 stat 이 None 이다
            if stat:
                bind += stat
            touched.add(det.viewpoint.slide_id)
            if stat and (stat["iou"] or stat["orphan"]):
                # 고아는 손실이 아니지만(geom 이 남는다) 뷰어가 못 그린다.
                # 반드시 보이게 한다 — 조용하면 한참 뒤에나 알게 된다.
                print(f"  {f.stem}: 교정 재바인딩 exact {stat['exact']} · "
                      f"iou {stat['iou']} · 고아 {stat['orphan']}")
    except Exception as e:
        if run:
            run.status = "failed"
            run.error = f"{type(e).__name__}: {e}"
            run.finished_at = timezone.now()
            run.counts = {"detections": n_det, "candidates": n_cand}
            run.save()
        raise

    stack_ctx.close()          # GPU 를 놓는다. 뒷정리는 DB 작업뿐이다

    if run:
        run.status = "done"
        run.finished_at = timezone.now()
        run.counts = {"detections": n_det, "candidates": n_cand,
                      "missing_viewpoint": len(missing), "oom": n_oom,
                      "oom_retry_ok": n_oom_retry,
                      **dict(bind)}
        # 전부 OOM 으로 죽었는데 done 으로 남으면 성공한 줄 안다
        # **하나라도 건너뛰었으면 done 이 아니다.** 예전에는 검출이 하나라도
        # 있으면 done 이었다 — GPU 를 다른 것이 침범해 9장이 조용히 빠졌는데
        # 실행은 성공으로 남았고, 나중에 프레임 수를 세어 보고서야 알았다.
        if n_oom:
            run.status = "partial"
            run.error = (f"OOM 으로 {n_oom}장을 건너뛰었다 — 그만큼 검출이 없다. "
                         f"GPU 를 다른 작업과 나눠 쓰고 있지 않은지 볼 것")
        if n_oom and not n_det:
            run.status = "failed"
            run.error = f"OOM {n_oom}건 — 검출된 것이 없다"
        run.save()
        # 슬라이드 하나가 다 끝났으면 검토를 연다.
        #
        # **--keep-current 면 손대지 않는다.** 나란히 재 보는 실험은 "자동 처리"
        # 가 아니다. 여기서 상태를 옮기면 `failed`(그룹핑이 미심쩍은 것)나
        # `processing` 인 슬라이드가 실험 한 번에 `done` 이 되어 검토가 열린다 —
        # 반쯤 처리된 것을 사람이 보게 되는 바로 그 사고다(DiaRUGA P01 §1).
        from viewer.models import Slide                             # noqa: PLC0415
        if args.keep_current:
            print("  --keep-current: 슬라이드 상태를 건드리지 않는다")
        else:
            for sl in Slide.objects.filter(pk__in=touched):
                if mark_done_if_complete(sl):
                    print(f"  {sl.slug}: 자동 처리 완료 — 검토를 연다")
                else:
                    print(f"  {sl.slug}: {sl.state} — {sl.state_note}")

        print(f"\n검출 {n_det}개 · 개체 {n_cand}개 · Run #{run.pk}")
        if bind:
            print(f"  교정 재바인딩: exact {bind['exact']} · iou {bind['iou']} · "
                  f"고아 {bind['orphan']}")
        if bind["orphan"]:
            print(f"  ** 고아 {bind['orphan']}개 — 뷰어에서 안 보인다. "
                  f"geom 은 남아 있다 (P02 8단계의 고아 화면이 필요하다)",
                  file=sys.stderr)
        if missing:
            print(f"  [확인필요] 시야를 못 찾은 이미지 {len(missing)}개: "
                  f"{missing[:5]}", file=sys.stderr)


if __name__ == "__main__":
    main()
