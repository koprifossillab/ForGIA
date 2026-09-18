"""데이터와 설정을 담는 스키마. DiaRUGA v0.29.0 `web/viewer/models.py` 에서
층 넷(`Site`·`Locality`·`Sample`·`Slide`)만 먼저 가져왔다 (P01 0단계).

1단계에서 `RunBatch`·`Run`·`Viewpoint`·`Frame`·`Stack`·`Image` 가 왔다. 나머지 —
`Detection`·`Candidate`·`ThresholdSet`·`ClassDef`(2단계), `ViewpointReview`·
`ObjectReview`·`ForamObject`·`Taxon`(3단계) — 는 그 단계에서 가져온다.

DiaRUGA 와 다른 자리 (P01 2절·5절):

- **육상 노두 갈래가 없다.** `Locality.kind`·`Sample.sample_no` 를 안 가져왔다.
  시료가 남극 코어뿐이라서다. 생기면 DiaRUGA 063 을 보고 그때 더한다
- **분획·분할은 관찰(`Slide`)에 붙는다.** P01 5절의 표는 `Sample` 에 적었는데
  옮기면서 고쳤다 — 같은 71cm 시료를 `>63um` 과 `>125um` 으로 따로 픽킹하면
  슬라이드가 둘이고, 그것이 DiaRUGA 가 말하는 "관찰 = 시료 하나를 처리 방법을
  달리해 본 것" 이다. 분할(1/n)도 분획마다 하므로 같이 간다. **건시료 무게만
  시료의 것이다**
- **시야가 격자 한 칸이다** — `Slide.cells` 가 격자 칸 수를 들고,
  `Viewpoint.cell` 이 그 안의 번호다

DiaRUGA 에서 그대로 물려받은 규칙 둘은 그쪽 머리말 그대로다:

**1. 교정은 `Candidate` 가 아니라 `mask_key` 에 붙는다.** (3단계에서 온다)
**2. 검출은 덮어쓰지 않고 쌓는다.** (2단계에서 온다)
"""
from django.db import models

from .kpdc import doi_url as kpdc_doi_url, files_note as kpdc_files_note

# 폴더 이름 규칙은 `naming.py` 하나뿐이다 — 뷰어·파이프라인·마이그레이션이 같은
# 것을 본다.
from .naming import base_name as _base_name

# 실행 종류. RunBatch 와 Run 이 함께 쓰므로 위로 뺀다. `classify` 는 ForGIA 의
# 것이다 (4단계 분류기 · P01) — DiaRUGA 에는 없다.
RUN_KIND = [(k, k) for k in
            ("group", "stack", "detect", "classify", "refilter", "reconcile",
             "ingest", "export")]


class RunBatch(models.Model):
    """한 번의 작업을 묶는다. `Run` 은 슬라이드마다 하나씩 생긴다.

    파이프라인은 **슬라이드 단위로 돈다** — 폴러가 새 슬라이드 하나를 받으면
    그것만 처리하기 때문이고, 그 단위가 맞다. 그런데 "전체를 한 번 훑었다" 는
    작업은 그 실행 여럿으로 흩어져 남는다. 엔진을 비교하려면 **그 한 번을 한
    덩어리로** 볼 수 있어야 한다 (YOLO 전체 대 SAM2 전체).

    `Run` 에 부모를 다는 대신 따로 둔 이유: 부모 `Run` 은 자기 `started_at`·
    `counts` 를 갖게 되어 뜻이 겹친다. 묶음은 실행이 아니라 **이름표**다.
    """

    kind = models.CharField(max_length=16, choices=RUN_KIND)
    # 사람이 고르는 이름. "yolo-v1seg" 처럼 무엇을 돌렸는지가 드러나야 한다
    label = models.CharField(max_length=120)
    note = models.TextField(blank=True)
    # **뷰어가 검토 대상으로 삼는 묶음** (DiaRUGA P10). 서버 설정이다 — 관리 화면에서
    # 고르고, 모두가 같은 것을 본다.
    #
    # **`Detection.is_current` 와 다른 것을 말한다.** 겹치는 이름을 쓰면 읽는
    # 사람이 반드시 헷갈려서 일부러 다르게 붙였다:
    #
    #   Detection.is_current   그 묶음 **안에서** 이 이미지의 최신 검출
    #   RunBatch.for_review    뷰어가 **검토 대상**으로 삼는 묶음
    #
    # 화면이 보여줄 검출은 **둘 다 켜진 것**이다 — 이 묶음을 검토하고 있고,
    # 그 묶음 안에서 최신인 검출 (`Detection.objects.reviewing()`).
    #
    # **URL·쿠키가 아니라 서버 설정인 이유** (DiaRUGA P10): 사람마다 다른 묶음을
    # 보면 `/review` 가 017·027·053 계열의 사고를 새로 만든다. 그 POST 는 범위를
    # 갈아치우고, 셋 다 "화면과 저장 대상이 어긋난" 사고였다.
    for_review = models.BooleanField(default=False, db_default=False)
    # **새 자료가 들어왔을 때 이 묶음을 어떻게 채우는가** (DiaRUGA P10 6단계 · 079).
    #
    # 묶음이 여럿인 것이 기본이 됐다 — `sam2-전수` · `yolo-3차` · `yolo-4차` 가
    # 나란히 있고, 새 슬라이드는 **그 전부에** 들어가야 한다. 지금 보고 있는
    # 묶음만 따라가면 나머지는 뒤처지고, 갈아타는 순간 빈 화면이 된다.
    #
    # 담는 것은 `segment_forams.py` 의 인자다:
    # `{"backend": "yolo", "weights": "models/…​.pt", "scale": 1.0,
    #   "all_images": true, "min_um": 10, "max_um": 150, …}`
    #
    # **비어 있으면 자동으로 안 돈다.** 끝난 회차를 그대로 두는 것이 기본이고,
    # 돌릴 것은 사람이 적어 준다 — 묶음이 늘 때마다 GPU 시간이 곱으로 는다.
    recipe = models.JSONField(default=dict, blank=True, db_default={})
    # **카탈로그 번호의 꼬리** (`catalog.py`). `RS23-GC03-071-g03-…-S1` 의 `S1`.
    #
    # **라벨에서 자동으로 뽑지 않는다.** `yolo-3차`·`yolo-4차` 가 같은 글자로
    # 누우면 **두 회차의 번호가 겹치고**, 그 번호는 이미 논문·표에 적힌 뒤다.
    # 관리 화면이 빈 칸에 첫 제안(`catalog.batch_code_seed`)만 채워 주고 정하는
    # 것은 사람이다. 라벨을 고쳐도 번호가 안 움직이는 것도 갈라 둔 덕이다.
    #
    # 비어 있으면 그 묶음의 개체는 **번호가 없다** — 화면이 그것을 적는다.
    # 조용히 `M`(손그림) 이나 라벨로 대신하면 엔진이 낸 것이 다른 것으로 기록된다.
    code = models.CharField(max_length=8, blank=True, default="", db_default="")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(fields=["kind", "label"],
                                    name="uniq_batch_label"),
            # **검토 대상은 하나뿐이다.** 둘이면 화면이 어느 것을 그릴지 모르고,
            # 그 상태는 예외가 안 나고 그냥 틀린다 — DB 가 막는다.
            models.UniqueConstraint(fields=["for_review"],
                                    condition=models.Q(for_review=True),
                                    name="uniq_batch_for_review"),
            # **묶음 코드가 겹치면 두 회차의 카탈로그 번호가 겹친다.** 빈 것은
            # 여럿일 수 있다 — 아직 안 정한 묶음이고, 그때는 번호가 아예 안 난다.
            models.UniqueConstraint(fields=["code"], condition=~models.Q(code=""),
                                    name="uniq_batch_code"),
            # **`M` 은 손그림 자리다** (`catalog.MANUAL_CODE`). 묶음이 그것을
            # 가져가면 사람이 그린 개체와 그 묶음의 개체가 한 번호 아래 섞인다.
            models.CheckConstraint(condition=~models.Q(code__in=["M", "m"]),
                                   name="batch_code_not_manual"),
        ]

    def __str__(self):
        return f"{self.label} ({self.kind})"




class Run(models.Model):
    """실행 이력. 지금까지 아무 데도 없어서 stack_report.json 이 덮어써졌다."""

    KIND = RUN_KIND
    # `partial` — 돌긴 했는데 일부를 건너뛴 것. `done` 과 갈라야 한다.
    # GPU 를 다른 작업이 침범해 9장이 조용히 빠졌는데 실행은 done 이었고,
    # 나중에 프레임 수를 세어 보고서야 알았다.
    STATUS = [(s, s) for s in ("running", "done", "partial", "failed")]

    kind = models.CharField(max_length=16, choices=KIND)
    # 여러 슬라이드에 걸친 한 번의 작업을 묶는 이름표. 비어 있어도 된다 —
    # 폴러가 슬라이드 하나만 처리하는 평소 실행에는 묶을 것이 없다.
    batch = models.ForeignKey("RunBatch", null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="runs")
    slide = models.ForeignKey("Slide", null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="runs")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=STATUS, default="running")
    error = models.TextField(blank=True)
    # 무슨 설정으로 돌렸나 — {"scale":1.0,"points_per_side":48,...}
    params = models.JSONField(default=dict, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    host = models.CharField(max_length=64, blank=True)
    gpu = models.CharField(max_length=64, blank=True)
    code_version = models.CharField(max_length=64, blank=True)

    class Meta:
        indexes = [models.Index(fields=["kind", "-started_at"])]

    def __str__(self):
        return f"{self.kind} #{self.pk} ({self.status})"




class Site(models.Model):
    """채취 지역. 폴더명의 앞 토막(RS23, WAP13)이다.

    지역 코드의 정식 명칭은 사람이 채운다 — 코드만으로는 단정할 수 없다.

    **`area` 와 `region` 은 다른 칸이다.** `region` 은 "로스해"·"Bigo Bay" 같은
    세부 지명이고, `area` 는 목록을 가르는 상위 칸이다. 지금은 남극 하나뿐이지만
    칸을 두는 것은 DiaRUGA 가 한국 시료가 들어왔을 때 이 칸이 없어 화면을
    갈라 넣느라 애먹었기 때문이다(DiaRUGA 063).
    """

    AREA = [("ant", "남극")]

    code = models.CharField(max_length=32, unique=True)     # RS23
    name = models.CharField(max_length=200, blank=True)     # 사람이 채운다
    # **`db_default` 를 함께 준다.** `default` 는 파이썬 쪽이라 이 모델을 아는
    # 코드에만 붙는다. 이 DB 는 뷰어 이미지와 파이프라인 이미지가 함께 쓰는데
    # 판이 따로 돌아서, 파이프라인이 옛 코드일 때 `Site` 를 새로 만들면 이 칸을
    # 아예 안 보내고 `NOT NULL constraint failed` 로 죽는다 — DiaRUGA 에서
    # 실제로 NAS 반입이 그렇게 막혔다. DB 가 스스로 채우게 두면 옛 코드도 돈다.
    area = models.CharField(max_length=8, choices=AREA,
                            default="ant", db_default="ant")
    region = models.CharField(max_length=200, blank=True)   # 로스해 / 웨델해 …
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        verbose_name = "지역"
        ordering = ["code"]

    def __str__(self):
        return self.name or self.code


class Locality(models.Model):
    """지점 하나 — 시추코어 하나다.

    DiaRUGA 는 노두도 여기 앉아 `kind` 로 갈랐다. ForGIA 는 코어뿐이라 그 칸이
    없다 — 생기면 그때 `kind`·`collect_kind` 를 DiaRUGA 063 대로 더한다.

    시료가 이 아래 달리므로 **한 지점에서 깊이에 따른 군집 변화**를 질의할 수
    있다.
    """

    site = models.ForeignKey(Site, on_delete=models.CASCADE,
                             related_name="localities")
    code = models.CharField(max_length=32)                  # GC03
    # 채취 방식 (`gravity core` · `box core`). 사람이 적는 자유 문자열이다.
    # GC = gravity core 처럼 코드 앞 글자가 뜻을 갖는 일이 많지만 단정하지 않는다.
    collect_kind = models.CharField(max_length=64, blank=True, db_default="")
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    water_depth_m = models.FloatField(null=True, blank=True)
    collected_at = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)
    # KPDC(극지 데이터 센터)의 항목. `ops/fetch_kpdc.py`(1단계)가 채운다 —
    # `KOPRI-KPDC-00001836` 같은 Entry ID 이고 DOI 는 `10.22663/<id>` 다.
    # 좌표·수심·채취일은 그 페이지에서 **빈 칸만** 채워진다 (DiaRUGA 197).
    kpdc_id = models.CharField(max_length=32, blank=True, db_default="")
    kpdc_meta = models.JSONField(null=True, blank=True)

    class Meta:
        verbose_name = "지점"
        ordering = ["site", "code"]
        constraints = [models.UniqueConstraint(fields=["site", "code"],
                                               name="uniq_locality_code")]

    def __str__(self):
        return f"{self.site.code}-{self.code}"

    @property
    def kpdc_url(self) -> str:
        """DOI 로 간다 — KPDC 페이지로 넘어간다. 검색 페이지 주소는 uuid 라
        `kpdc_meta["url"]` 에만 둔다."""
        return kpdc_doi_url(self.kpdc_id)

    @property
    def kpdc_files_note(self) -> str:
        return kpdc_files_note(self.kpdc_meta)


class Sample(models.Model):
    """시료 하나 — 지점에서 한 깊이를 떠 온 것.

    **관찰의 위가 여기다.** 시료 행이 있으면 관찰은 그것을 가리키기만 하므로
    같은 시료의 관찰 둘이 서로 다른 소속을 가질 수 없다 (DiaRUGA 063 이 그
    사고를 겪고 이 층을 세웠다).

    **분획·분할은 여기 없다.** 그것은 시료를 처리한 결과이고 처리마다 슬라이드가
    따로 나오므로 `Slide` 의 것이다. 시료의 것은 **건시료 무게**뿐이다 — 계수표가
    개체/g 로 환산할 때 분모가 된다.
    """

    locality = models.ForeignKey(Locality, on_delete=models.CASCADE,
                                 related_name="samples")
    # 폴더에서 온 시료 코드. `71cm`. **화면에 그대로 쓴다** — 사람이 부르는
    # 이름이라 숫자로 다시 만들면(`71.0cm`) 폴더와 안 맞아 보인다.
    code = models.CharField(max_length=64)
    # 기준점(해저면)에서부터의 깊이.
    depth_cm = models.FloatField(null=True, blank=True)
    # 처리 전 건시료 무게 (g). **NULL 허용** — 안 잰 시료가 있고, 그것을 0 으로
    # 적으면 개체/g 가 무한대가 된다. 비어 있으면 계수표가 환산을 안 한다.
    dry_weight_g = models.FloatField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        verbose_name = "시료"
        # 지점 안에서는 깊이순 — 깊이에 따른 변화를 보는 것이 분석 목적이다.
        ordering = ["locality", "depth_cm", "code"]
        constraints = [models.UniqueConstraint(fields=["locality", "code"],
                                               name="uniq_sample_code")]
        indexes = [models.Index(fields=["locality", "depth_cm"])]

    def __str__(self):
        return f"{self.locality}-{self.code}"


class Slide(models.Model):
    """관찰 하나 = 폴더 하나 = 픽킹 슬라이드 하나. 그 안에 격자 칸(시야)이 있다.

    **이 표는 관찰만 담는다.** 시료가 어느 지점의 몇 cm 인가는 `Sample` 이 안다.

    **관찰은 시료 하나를 처리 방법이나 회차를 달리해 여러 번 본 것**이고 서로
    동등하다 (DiaRUGA 2026-08-06). ForGIA 에서 "처리 방법" 의 첫째가 **분획**이다
    — 같은 시료를 `>63um` 과 `>125um` 으로 따로 픽킹하면 슬라이드가 둘이다.
    그래서 `fraction_um`·`split_denom` 이 여기 있다.

    **이름을 `Observation` 으로 안 바꾼다.** 픽킹 슬라이드라는 물건이 실재하고
    폴더 하나가 그것 하나다.
    """

    STATE = [(s, s) for s in
             ("pending", "copying", "processing", "done", "failed")]

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=120, unique=True)
    image_dir = models.CharField(max_length=500)
    # **`SET_NULL` 이다.** 시료를 지워도 관찰과 그 아래 검토가 남아야 한다 —
    # 소속은 다시 붙일 수 있지만 교정은 재생성 불가다. 소속을 잃은 관찰은
    # 관리 화면이 잡아낸다 (DiaRUGA 063).
    sample = models.ForeignKey("Sample", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="slides")

    # --- 관찰 -------------------------------------------------------------
    # 폴더 이름 뒤의 `(1)`·`(2)`. 접미사가 없으면 `0`.
    #
    # **재촬영과 다른 축이다.** 같은 폴더명을 다른 날 올리면 슬러그가 촬영일로
    # 갈리는데 그것은 "같은 관찰을 다시 찍은 것" 이다. 접미사는 "다른 관찰" 이다.
    #
    # **왜 `obs_label` 과 칸이 둘인가.** 한 칸에 두면 폴더에서 다시 읽을 때
    # 자동값이 사람이 적은 것을 덮는다. 반입·그룹핑은 `obs_no` 만 쓰고
    # `obs_label` 은 절대 안 건드린다 (`update_or_create` 의 `defaults` 에서 뺀다).
    obs_no = models.PositiveSmallIntegerField(default=0, db_default=0)
    # 사람이 붙이는 뜻. **10자다** (DiaRUGA 2026-08-05) — 짧게 못 박아 두면
    # 배지 하나가 목록의 열을 통째로 미는 일이 아예 안 생긴다.
    obs_label = models.CharField(max_length=10, blank=True, default="",
                                 db_default="")

    # --- 처리 — 이 슬라이드에 무엇이 올라와 있나 --------------------------
    # 체 눈 크기 (µm). `>125um` 의 125. 폴더 이름에서 읽고(`naming.parse_fraction`)
    # 없으면 사람이 화면에서 적는다. **폴더에서 못 읽은 것은 안 쓴다** — 사람이
    # 채운 것을 자동값이 지우면 안 된다.
    fraction_um = models.FloatField(null=True, blank=True)
    # 분할 비율의 분모. 분획을 1/8 로 갈라 픽킹했으면 `8`. 전량이면 `1`.
    # **NULL 은 "모른다"** 이고 `1` 과 다르다 — 계수표는 NULL 이면 환산을 안 한다.
    split_denom = models.PositiveIntegerField(null=True, blank=True)
    # 픽킹 슬라이드의 격자 칸 수 (60 · 32 …). 시야(`Viewpoint.cell`)가 이 안의
    # 번호다. 비어 있으면 격자를 모른다는 뜻이고, 칸 번호는 그래도 매겨진다.
    cells = models.PositiveSmallIntegerField(null=True, blank=True)

    # 같은 시료의 관찰이 여럿이면 **합계가 조용히 두 배가 된다.** 어느 관찰이
    # 대표인지는 코드가 정할 수 없으므로 사람이 고른다.
    #
    # **합계는 `exclude_from_totals` 만 본다 — `hide_in_list` 는 안 본다.**
    # 섞으면 보기 토글 한 번에 같은 자료가 다른 숫자를 낸다 (DiaRUGA 056).
    hide_in_list = models.BooleanField(default=False, db_default=False)
    exclude_from_totals = models.BooleanField(default=False, db_default=False)
    # 사람이 적는 설명. state_note 와 갈라 둔다 — 그쪽은 자동 처리가 덮어쓴다.
    description = models.TextField(blank=True, default="")
    # 그룹핑에 쓴 상관 임계값. `group_focus_series --corr-thresh` 가 적는다 —
    # 나중에 시야가 이상할 때 어떤 값으로 묶였는지 되짚는 자리다.
    corr_thresh = models.FloatField(null=True, blank=True)
    # NAS 로 폴더가 계속 들어오면 상태 관리가 필요해진다
    state = models.CharField(max_length=12, choices=STATE, default="done")
    state_note = models.TextField(blank=True)
    discovered_at = models.DateTimeField(null=True, blank=True)
    copied_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    class Meta:
        verbose_name = "관찰"
        # 시료순(그 안이 깊이순이다), 그 다음 분획·관찰 번호순.
        # **이름으로 가르지 않는다** — `(10)` 이 `(2)` 앞에 온다.
        ordering = ["sample", "fraction_um", "obs_no", "name"]
        indexes = [models.Index(fields=["sample", "obs_no"])]

    def __str__(self):
        return self.name

    # --- 시료를 거쳐 가는 지름길 -------------------------------------------
    #
    # 매번 `slide.sample.locality.site` 를 쓰면 `sample` 이 없는 슬라이드(소속을
    # 잃은 관찰)에서 터진다 — 여기서 한 번만 막는다.
    # `select_related("sample__locality__site")` 를 함께 걸 것.
    @property
    def locality(self):
        return self.sample.locality if self.sample_id else None

    @property
    def site(self):
        return self.sample.locality.site if self.sample_id else None

    @property
    def depth_cm(self):
        return self.sample.depth_cm if self.sample_id else None

    @property
    def fraction_badge(self) -> str:
        """화면에 낼 분획. `>125 µm`. 없으면 빈 문자열."""
        return f">{self.fraction_um:g} µm" if self.fraction_um is not None else ""

    @property
    def obs_badge(self) -> str:
        """화면에 낼 관찰 이름표. 낼 것이 없으면 빈 문자열.

        **관찰이 하나뿐인 시료는 아무것도 안 낸다** (DiaRUGA 2026-08-05). 사람이
        이름표를 적었다면 `0` 이라도 낸다.
        """
        return self.obs_label or (f"#{self.obs_no}" if self.obs_no else "")

    @property
    def base_name(self) -> str:
        """관찰 접미사를 뗀 폴더 이름 — 같은 시료·분획의 관찰들이 공유하는 것."""
        return _base_name(self.name)

    def sibling_observations(self):
        """같은 시료의 다른 관찰들. 번호순이다.

        **시료가 없는 관찰은 이름으로 되짚는다.** 소속을 잃은 것을 관리 화면이
        어디에 붙일지 추천해야 하는데, 그때는 이름밖에 근거가 없다 (DiaRUGA).
        """
        if self.sample_id:
            return list(self.sample.slides.exclude(pk=self.pk)
                        .order_by("obs_no", "id"))
        base = self.base_name
        if not base:
            return []
        qs = (Slide.objects.filter(name__startswith=base).exclude(pk=self.pk)
              .select_related("sample__locality__site").order_by("obs_no", "id"))
        return [s for s in qs if s.base_name == base]


class Viewpoint(models.Model):
    """시야 하나 = 픽킹 슬라이드의 격자 한 칸 (DiaRUGA 의 "그룹").

    Group 이라 하지 않는다 — SQL·Django 양쪽에서 뜻이 겹친다.
    """

    slide = models.ForeignKey(Slide, on_delete=models.CASCADE,
                              related_name="viewpoints")
    idx = models.IntegerField()                 # URL 의 g0
    tag = models.CharField(max_length=120)      # g000_Snap-21365-21370
    # **픽킹 슬라이드의 격자 칸 번호** (P01 5절). 시야 하나가 격자 한 칸이다.
    # 그룹핑이 촬영 순서대로 1 부터 매기고(`group_focus_series`), 건너뛴 칸이
    # 있으면 사람이 화면에서 고친다. **NULL 은 "모른다"** 다 — 격자가 없는
    # 슬라이드나 옛 자료. `idx` 와 다른 축이다: `idx` 는 묶은 순서(URL), `cell`
    # 은 슬라이드 위의 자리. 처음엔 같지만 사람이 고치면 갈린다.
    cell = models.PositiveSmallIntegerField(null=True, blank=True)
    n_frames = models.IntegerField(default=0)
    span_sec = models.FloatField(null=True, blank=True)
    sharpest_frame = models.ForeignKey("Frame", null=True, blank=True,
                                       on_delete=models.SET_NULL,
                                       related_name="sharpest_of")
    grouping_run = models.ForeignKey(Run, null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name="viewpoints")

    class Meta:
        ordering = ["slide", "idx"]
        constraints = [models.UniqueConstraint(fields=["slide", "idx"],
                                               name="uniq_viewpoint_idx")]

    def __str__(self):
        return f"{self.slide.slug} g{self.idx}"



class Frame(models.Model):
    """사진 한 장. 촬영 메타데이터(µm/px, 시각)는 `pipeline/scale.py` 가 읽는다.

    `um_per_pixel_source` 는 그 값을 **어디서 읽었는가**다 (P01 5절) — `leica`
    (LAS X 메타) · `exif`(ImageDescription) · `toml`(폴더의 `scale.toml`, 사람이
    적은 것) · `sidecar`(합성본 옆 `_scale.json`) · `default`. 계측값을 의심할
    때 이 칸부터 본다.
    """

    SOURCE = [(s, s) for s in ("leica", "exif", "toml", "sidecar", "default", "cli")]

    slide = models.ForeignKey(Slide, on_delete=models.CASCADE,
                              related_name="frames")
    # 그룹핑 전에는 비어 있다
    viewpoint = models.ForeignKey(Viewpoint, null=True, blank=True,
                                 on_delete=models.SET_NULL,
                                 related_name="frames")
    name = models.CharField(max_length=120)     # Snap-21365
    path = models.CharField(max_length=500)
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    um_per_pixel = models.FloatField(null=True, blank=True)
    um_per_pixel_source = models.CharField(max_length=8, choices=SOURCE, blank=True)
    acquired_at = models.DateTimeField(null=True, blank=True)
    sharpness = models.FloatField(null=True, blank=True)
    is_sharpest = models.BooleanField(default=False)
    seq = models.IntegerField(default=0)        # 폴더 안 순서
    # 이 행이 DB 에 들어온 때. **촬영 시각(`acquired_at`)과 다르다** — 그쪽은
    # 사진에 딸린 XML 이 알려 주는 것이고, 이쪽은 우리 시스템이 받은 때다.
    # 반입 이력을 되짚을 때 필요하다.
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)
    # 광학계 정보는 전 사진 동일하고 지금 쓰는 곳이 없다. 칼럼 20개를 미리
    # 만들면 대부분 비므로 필요해질 때 여기 담는다.
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["slide", "seq"]
        constraints = [models.UniqueConstraint(fields=["slide", "name"],
                                               name="uniq_frame_name")]

    def __str__(self):
        return self.name



class Stack(models.Model):
    """all-in-focus 합성본. *_scale.json 과 stack_report.json 을 합친 것."""

    viewpoint = models.OneToOneField(Viewpoint, on_delete=models.CASCADE,
                                     related_name="stack")
    focused_path = models.CharField(max_length=500)
    depth_path = models.CharField(max_length=500, blank=True)
    depth_npz_path = models.CharField(max_length=500, blank=True)
    um_per_pixel = models.FloatField(null=True, blank=True)
    native_um_per_pixel = models.FloatField(null=True, blank=True)
    resize_scale = models.FloatField(default=1.0)
    um_per_pixel_source = models.CharField(max_length=8, blank=True)
    ref_frame = models.ForeignKey(Frame, null=True, blank=True,
                                  on_delete=models.SET_NULL,
                                  related_name="ref_of")
    align_failed = models.IntegerField(default=0)
    object_px_frac = models.FloatField(null=True, blank=True)
    sharpness_best_single = models.FloatField(null=True, blank=True)
    sharpness_fused = models.FloatField(null=True, blank=True)
    gain = models.FloatField(null=True, blank=True)
    run = models.ForeignKey(Run, null=True, blank=True,
                            on_delete=models.SET_NULL, related_name="stacks")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.focused_path



class Image(models.Model):
    """**검출을 돌릴 수 있는 이미지 한 장.** (DiaRUGA P06 2단계, 2026-08-05)

    ## 왜 만드는가

    `시야 1:N 프레임 1:N 검출` 이 이 스키마에서 성립하지 않았다 — **합성본은
    프레임이 아니기 때문이다.** 검출이 도는 이미지가 `Stack.focused_path` 아니면
    `Frame.path` 라 테이블이 둘이고, 그래서 `Detection` 이 `target`(`stack|frame`)
    + nullable `frame` 으로 **다형 연관을 흉내 내고** 있었다.

    그 흉내가 실제로 값을 치렀다. `ObjectReview` 의 열쇠가 `(viewpoint, mask_key)`
    인데 이것은 **시야마다 볼 이미지가 한 장**일 때만 성립한다. YOLO 처럼 프레임
    마다 검출을 내면 깨진다 — 실측으로 시야 452개 중 203개(45%)에서 프레임끼리
    `mask_key` 가 겹친다. 고치려고 `(viewpoint, frame, mask_key)` 로 가면 합성본의
    `frame` 이 NULL 이라 **유일 제약이 82%에 대해 조용히 작동을 멈춘다**(NULL 은
    서로 다른 값으로 친다).

    이 테이블이 생기면 그 문제가 **사라진다.** `(image, mask_key)` 하나면 되고,
    판별자 문자열도 `__stack__` 센티널도 NULL 규칙도 필요 없다.

    ## 열쇠는 `path` 다

    `DATA_ROOT` 기준 상대경로이고 **파일 하나에 행 하나**다. 실측으로 프레임
    1,318 · 합성본 317 · 깊이맵 317 = 1,952개가 전부 겹치지 않는다. 자연 열쇠가
    있는데 대리 열쇠를 만들 이유가 없고, `viewpoint` 를 넣으면 그룹핑 전 프레임
    (viewpoint 가 비어 있다)에서 다시 NULL 문제가 생긴다.

    ## 무엇을 담지 않는가

    **촬영·합성 메타는 그대로 `Frame`·`Stack` 에 둔다.** 이 테이블은 정체(identity)만
    맡는다 — `Frame` 은 선명도·촬영시각·`seq`, `Stack` 은 정렬 실패·품질 지표처럼
    **합성 실행의 산물**을 들고 있고, 그것들은 이미지가 아니라 그 이미지를 만든
    일에 대한 기록이다.

    ## `kind="depth"` 는 검출이 붙지 않는다

    깊이맵은 Z 좌표가 없는 상대값이라 볼 것은 되지만 검출 대상이 아니다(실측으로
    검출 0건). 지금까지는 **관행으로만** 그랬는데, 이제 종류가 스키마에 적힌다.

    ## 지금은 아무도 안 쓴다

    P06 은 넓히고(2) → 채우고(3) → 파이프라인을 옮기고(4) → 조인다(5). 지금은
    2단계라 이 테이블이 서 있기만 하고 `Detection.image`·`ObjectReview.image` 는
    **nullable** 이다 — 옛 파이프라인 이미지의 INSERT 가 죽으면 안 되기 때문이다
    (뷰어와 파이프라인은 판이 따로 돈다).
    """

    KIND = [("stack", "합성본"), ("frame", "프레임"), ("depth", "깊이맵")]

    # 그룹핑 전 프레임은 시야가 없다 — `Frame.viewpoint` 와 같은 사정이고
    # `SET_NULL` 인 것도 같은 이유다.
    #
    # **`CASCADE` 로 두면 안 된다.** 시야 가르기(`regroup.apply_split`)는 시야를
    # 지우고 다시 만드는데 **프레임은 살아남는다** — 그 사이에 이미지 행이 같이
    # 죽으면 디스크에 파일이 그대로 있는데 테이블에서만 사라진다.
    # **이 테이블은 디스크의 파일을 비추는 것**이고, 시야는 그 파일에 붙는 이름표다.
    viewpoint = models.ForeignKey(Viewpoint, null=True, blank=True,
                                  on_delete=models.SET_NULL,
                                  related_name="images")
    kind = models.CharField(max_length=8, choices=KIND)
    path = models.CharField(max_length=500, unique=True)
    # 어디서 왔는가. **둘의 `on_delete` 가 다르다 — 성격이 다르기 때문이다.**
    #
    # **프레임은 원본 사진이다.** 시야를 갈라도 그 자리에 그대로 있고 다른 시야로
    # 묶일 뿐이다 — 그래서 `SET_NULL` 이고 이미지 행도 살아남는다.
    #
    # **합성본·깊이맵은 그 묶음에서 나온 것이다.** 묶음이 갈리면 무효다 — 서로
    # 다른 시야가 된 프레임들을 합쳐 놓은 그림이라 아무것도 아닌 것이 된다.
    # 그래서 `CASCADE` 로 `Stack` 과 함께 죽는다. 다시 합성하면 새로 생긴다.
    frame = models.OneToOneField(Frame, null=True, blank=True,
                                 on_delete=models.SET_NULL,
                                 related_name="image")
    stack = models.ForeignKey(Stack, null=True, blank=True,
                              on_delete=models.CASCADE,
                              related_name="images")
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["viewpoint", "kind", "path"]
        indexes = [models.Index(fields=["viewpoint", "kind"])]

    def __str__(self):
        return f"{self.kind}:{self.path}"

