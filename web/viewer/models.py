"""데이터와 설정을 담는 스키마. DiaRUGA v0.29.0 `web/viewer/models.py` 에서
층 넷(`Site`·`Locality`·`Sample`·`Slide`)만 먼저 가져왔다 (P01 0단계).

1단계에서 `RunBatch`·`Run`·`Viewpoint`·`Frame`·`Stack`·`Image` 가, 2단계에서
`ThresholdSet`·`ClassDef`·`Setting`·`Detection`·`Candidate` 가, 3단계에서
`ViewpointReview`·`ObjectReview`·`ForamObject`·`Taxon` 이 왔다. 도감·코어 자료
(`Atlas*`·`CoreSeries`)는 5단계다.

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

**1. 교정은 `Candidate` 가 아니라 `mask_key` 에 붙는다.** 검출을 다시 돌리면 후보
행이 새로 생기므로 FK 로 매면 사람의 판단이 조인 실패로 사라진다. `mask_key`(bbox
문자열)를 진짜 키로 두고 `candidate` 는 바인딩 결과로 채운다.

**2. 검출은 덮어쓰지 않고 쌓는다.** `Detection.is_current` 가 뷰어가 볼 것을
가리킨다. 교체 전후를 같은 시야로 비교해야 하기 때문이다.
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


class ThresholdSet(models.Model):
    """판정 문턱. DiaRUGA 는 열한 칸(텍스처·타원·신장비)인데 ForGIA 는 **셋**이다 —
    크기 하한·상한(타원 장축 µm)과 검출기 확신도 하한(P01 2절 ②). 불투명 개체라
    텍스처·타원 관문은 뜻이 없고, "유공충인가" 는 분류기(4단계)가 답한다.

    테이블로 두면 이름을 붙여 비교할 수 있다 — "conf 0.25 vs 0.4 를 같은 시야에
    걸고 개수를 나란히". 같은 조합이면 한 행을 공유한다(`threshold_set_for`).
    칸을 더할 때는 `pipeline/judge.py` 의 `DEFAULTS`·`FIELDS` 와 함께 간다.
    """

    name = models.CharField(max_length=120, blank=True)
    min_um = models.FloatField(default=63.0)
    max_um = models.FloatField(default=2000.0)
    conf_min = models.FloatField(default=0.25)
    is_default = models.BooleanField(default=False)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    FIELDS = ("min_um", "max_um", "conf_min")

    def as_dict(self):
        return {f: getattr(self, f) for f in self.FIELDS}

    def __str__(self):
        return self.name or f"문턱 #{self.pk}"


class ClassDef(models.Model):
    """분류 정의 — **형태·보존 상태만** (P01 2절 ③). 분류학(종)은 3단계의 `Taxon`
    으로 간다. DiaRUGA 는 이 표 하나에 형태(원형·봉상)와 속(Eucampia…)을 함께
    담았는데, 종이 수백인 유공충에서는 단축키 순환·색·CSS 배지가 안 선다.

    **분류를 더할 때 채울 것** — DiaRUGA 038·040 이 겪은 것 그대로다. 하나라도
    비면 예외는 안 나고 그 분류만 화면에서 조용히 다르게 굴러간다:

        label       전체 이름
        short       약칭. 자리가 좁은 곳에서 쓴다. 비면 label 을 쓴다
        badge       배지 CSS 클래스. `base.html` 에 `.badge.<badge>` 규칙이 있어야 한다
        color       "R,G,B". **`base.html` 의 CSS 도 함께 고쳐야 한다**. 비면 마스크가 투명해진다
        hotkey      검토 화면 단축키(3단계). 비면 그 분류만 메뉴로만 지정된다
        counted     개체 수로 세는가 (파편은 False)
        sort_order  열·메뉴·단축키 순환의 차례

    첫 네 줄은 `migrations/0003` 이 심는다 — `foram`(온전) · `broken`(파손) ·
    `fragment`(파편) · `other`(비유공충). `is_taxon` 칸은 DiaRUGA 와 같게 두되
    **여기서는 늘 False 다** — 분류학은 이 표에 안 앉는다.

    `check_db.py` 의 "4. 분류" 가 hotkey·color 가 빈 것을 잡는다.
    **되돌릴 때는 지우지 말고 `active=False` 로 끈다** — 행을 지우면 그 분류로
    붙인 교정이 이름 없는 분류가 되어 화면에서 안 읽힌다.
    """

    key = models.CharField(max_length=32, unique=True)
    label = models.CharField(max_length=64)
    short = models.CharField(max_length=16, blank=True)
    badge = models.CharField(max_length=16, blank=True)
    color = models.CharField(max_length=24, blank=True)   # "196,181,253"
    is_taxon = models.BooleanField(default=False)
    counted = models.BooleanField(default=True)
    hotkey = models.CharField(max_length=8, blank=True)
    sort_order = models.IntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "key"]

    def __str__(self):
        return self.label


class Setting(models.Model):
    """그 밖의 설정. 경로처럼 배포마다 다른 값은 여기 두지 않는다(환경변수)."""

    key = models.CharField(max_length=64, unique=True)
    value = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key



class DetectionQuerySet(models.QuerySet):

    def reviewing(self):
        """**뷰어가 보여줄 검출** (DiaRUGA P10). 검토 대상 묶음의, 그 묶음 안 최신 것.

        `is_current=True` 를 코드 여기저기에 적지 않고 여기 하나로 모은다.
        예전에는 12개 파일에 흩어져 있었고, **뜻이 바뀌는데 자리가 흩어져 있으면
        전부 틀린다 — 그런데 예외가 안 난다.**

        **묻는 것이 둘로 갈린다.** 이 메서드를 쓸 자리와 아닌 자리가 있다:

        | 무엇을 묻는가 | 무엇을 쓰는가 |
        |---|---|
        | 뷰어가 보여줄 검출 (화면·집계·문턱) | **`reviewing()`** |
        | 이 묶음 안의 최신 (파이프라인·prune·rebind) | `is_current` 그대로 |

        뒤엣것까지 바꾸면 파이프라인이 **검토 대상이 아닌 묶음에 쌓을 때** 자기
        검출을 못 찾는다.
        """
        return self.filter(is_current=True, run__batch__for_review=True)



class Detection(models.Model):
    """이미지 한 장에 대한 검출 실행.

    재실행마다 새 행을 쌓는다 — 덮어쓰면 엔진 교체 전후를 비교할 수 없다.

    **뷰어가 보는 것은 `Detection.objects.reviewing()` 이다** (DiaRUGA P10) —
    `is_current` 하나가 아니라 **묶음의 `for_review` 와 함께 봐야** 한다.
    `is_current` 는
    "그 묶음 **안에서** 최신" 이라는 좁은 뜻이고, 어느 묶음을 볼지는
    `RunBatch.for_review` 가 정한다.
    """

    objects = DetectionQuerySet.as_manager()

    viewpoint = models.ForeignKey(Viewpoint, on_delete=models.CASCADE,
                                 related_name="detections")
    # **어느 이미지에 대한 검출인가.** 예전에는 `target`(`stack|frame`) +
    # nullable `frame` 으로 다형 연관을 흉내 냈다 — 합성본이 `Frame` 이 아니라
    # 테이블이 둘이었기 때문이다. `Image` 가 그것을 없앴다 (DiaRUGA P06).
    # 무엇에 붙은 검출인가는 `image.kind` 가, 어느 프레임인가는 `image.frame`
    # 이 말한다.
    image = models.ForeignKey("Image", on_delete=models.CASCADE,
                              related_name="detections")
    image_path = models.CharField(max_length=500)
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    scale = models.FloatField(default=1.0)
    um_per_pixel = models.FloatField(null=True, blank=True)
    um_per_pixel_native = models.FloatField(null=True, blank=True)
    um_per_pixel_source = models.CharField(max_length=8, blank=True)
    um_per_pixel_backfilled = models.BooleanField(default=False)
    n_raw_masks = models.IntegerField(default=0)
    n_sized = models.IntegerField(default=0)
    thresholds = models.ForeignKey(ThresholdSet, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="detections")
    run = models.ForeignKey(Run, null=True, blank=True,
                            on_delete=models.SET_NULL, related_name="detections")
    is_current = models.BooleanField(default=True)
    superseded_by = models.ForeignKey("self", null=True, blank=True,
                                      on_delete=models.SET_NULL,
                                      related_name="supersedes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["viewpoint", "is_current"])]

    @property
    def batch(self):
        """이 검출이 속한 묶음. **교정의 열쇠에 들어간다** (DiaRUGA P09 5.1).

        `run` 도 `Run.batch` 도 없을 수 있어 `None` 이 나온다 — 묶음에 안 든
        `detect` 실행이 70개 있고 전부 검출을 하나도 안 남긴 것들이다. 그런
        검출에 교정이 앉으면 batch 없는 교정이 되므로 `check_db.py` 가 센다.

        조인이 둘 걸린다 — 여럿을 돌 때는 `select_related("run__batch")`.
        """
        return self.run.batch if self.run_id else None

    def __str__(self):
        return f"{self.image_path} ({'현재' if self.is_current else '이전'})"



class Candidate(models.Model):
    """개체 하나. 통과분·탈락분을 한 테이블에 담고 `passed` 로 가른다.

    문턱을 바꾸면 개체가 무리를 옮겨 다니므로(지금은 두 배열 사이로 옮기느라
    파일을 다시 쓴다) `passed` 칼럼 하나면 refilter 가 UPDATE 한 번이다.
    """

    detection = models.ForeignKey(Detection, on_delete=models.CASCADE,
                                  related_name="candidates")
    # bbox 로 만든 키. 교정 기록이 이것으로 붙는다.
    mask_key = models.CharField(max_length=64)
    # 검출기가 낸 원시 마스크의 순번. 판정으로 재부여되는 표시용 id 와 다르다 —
    # 이것이 있어야 out/*.json 을 그대로 재현할 수 있다(내보내기).
    raw_id = models.IntegerField(null=True, blank=True)

    bbox_x = models.IntegerField()
    bbox_y = models.IntegerField()
    bbox_w = models.IntegerField()
    bbox_h = models.IntegerField()
    center_x = models.IntegerField(null=True, blank=True)
    center_y = models.IntegerField(null=True, blank=True)
    area_px = models.IntegerField(default=0)
    area_um2 = models.FloatField(null=True, blank=True)
    major_um = models.FloatField(null=True, blank=True)
    minor_um = models.FloatField(null=True, blank=True)
    long_side_um = models.FloatField(null=True, blank=True)
    short_side_um = models.FloatField(null=True, blank=True)
    aspect_ratio = models.FloatField(null=True, blank=True)
    fill_ratio = models.FloatField(null=True, blank=True)

    shape_ok = models.BooleanField(default=False)
    circularity = models.FloatField(null=True, blank=True)
    convexity = models.FloatField(null=True, blank=True)
    solidity = models.FloatField(null=True, blank=True)
    elongation = models.FloatField(null=True, blank=True)
    ellipse_iou = models.FloatField(null=True, blank=True)

    # 규조의 areolae 텍스처 자리 — ForGIA 는 안 잰다(불투명 개체). 칸은 DiaRUGA 와
    # 같게 두어 `NUM` 목록·내보내기 형식이 그대로 돌게 한다. 늘 NULL 이다
    texture = models.FloatField(null=True, blank=True)
    # YOLO 의 conf. DiaRUGA 는 SAM2 의 두 값 자리에 conf 를 넣었고 이름을 그대로
    # 두었다 — `judge.conf_min` 이 `predicted_iou` 를 본다
    predicted_iou = models.FloatField(null=True, blank=True)
    stability_score = models.FloatField(null=True, blank=True)

    # [x0,y0,x1,y1,...] 평탄 배열. 용량의 대부분이지만 마스크를 그리는 근거다.
    # rle 은 지금도 항상 null 이라 옮기지 않는다.
    polygon = models.JSONField(default=list, blank=True)

    passed = models.BooleanField(default=False)
    cls = models.CharField(max_length=32, blank=True)
    reject = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["detection", "mask_key"],
                                               name="uniq_candidate_key")]
        indexes = [
            models.Index(fields=["detection", "passed"]),
            models.Index(fields=["cls"]),
            models.Index(fields=["major_um"]),
            models.Index(fields=["texture"]),
        ]

    @property
    def bbox_xywh(self):
        return [self.bbox_x, self.bbox_y, self.bbox_w, self.bbox_h]

    def __str__(self):
        return f"{self.mask_key} ({self.cls or '미분류'})"



# ─────────────────────────────────────────────── 교정·동정 (3단계 · DiaRUGA P09·P12)
#
# `ViewpointReview`·`ObjectReview` 는 DiaRUGA v0.29.0 그대로다. `DiatomObject` 는
# `ForamObject` 가 됐고 종명 문자열(`species`) 대신 **`Taxon` FK** 를 든다 —
# 유공충은 종이 수십~수백이고 과-속-종 계층이 있어 자유 문자열로는 계수표가 안
# 선다 (P01 2절 ③ · 5절 "분류 체계 정본은 WoRMS").


class ViewpointReview(models.Model):
    """시야 단위 교정 상태 — 완료·코멘트.

    **완료는 묶음마다, 코멘트는 시야마다다** (DiaRUGA 073). `done` 은 "이 묶음이
    낸 검출을 여기서 다 봤다" 라 묶음의 것이고, `note` 는 "이 시야가 이러이러하다"
    라 묶음을 갈아도 참이다. 완료를 시야에 매달았더니 옛 묶음을 보고 붙인 완료가
    새 묶음 화면에 그대로 붙어 **아무도 안 본 검출이 검토 완료로 보였다.**

    **`batch` 가 `NULL` 인 행이 시야 코멘트를 든다.** 사람이 쓴 글이라 재생성
    불가이고, 행 전체를 묶음에 매달면 묶음을 갈 때마다 사라진다.
    """

    viewpoint = models.ForeignKey(Viewpoint, on_delete=models.CASCADE,
                                  related_name="reviews")
    # `PROTECT` — 묶음을 지우면 그 회차의 검토 기록이 통째로 날아간다
    batch = models.ForeignKey("RunBatch", null=True, blank=True,
                              on_delete=models.PROTECT,
                              related_name="viewpoint_reviews")
    # 고칠 것이 없어 교정이 비어도 검토는 끝났을 수 있다 — 따로 남긴다
    done = models.BooleanField(default=False)
    note = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["viewpoint", "batch"],
                condition=models.Q(batch__isnull=False),
                name="uniq_vpreview_batch"),
            # **NULL 끼리는 안 부딪힌다** — 시야 코멘트 행이 여럿 서는 것을
            # 막으려면 조건을 뒤집은 제약이 따로 있어야 한다
            models.UniqueConstraint(
                fields=["viewpoint"],
                condition=models.Q(batch__isnull=True),
                name="uniq_vpreview_note"),
        ]

    def __str__(self):
        if self.batch_id is None:
            return f"{self.viewpoint} 코멘트"
        return f"{self.viewpoint} [{self.batch}] {'완료' if self.done else '미완'}"


class ObjectReview(models.Model):
    """개체 단위 교정 — **그 판에서 그 마스크를 어떻게 봤는가.** 재생성 불가한 자료다.

    `candidate` 는 바인딩 결과일 뿐이고 진짜 키는 **`(image, batch, mask_key)`**
    다. `geom` 에 기하를 스스로 들고 있어 검출기가 바뀌어도 읽을 수 있다 —
    지운 것까지 전부 저장한다(학습의 어려운 음성 표본이다). **회차가 돌면 이
    표가 사실상 정답 자료 표가 된다** (DiaRUGA P09).

    뜻으로는 `MaskJudgement` 다 — 남은 칸이 전부 *사람이 그 마스크에 대해 한
    일*이다(지움·되살림·확인·기하 수정·손그림·대표 고르기). "이것이 무엇인가"
    (분류·종)는 `ForamObject` 에 산다. DiaRUGA 는 개명 비용 때문에 이름을 두었고
    여기서는 **같은 이름을 쓴다** — 그쪽 시험·문서를 그대로 옮기려고.

    | 칸 | 무엇에 대한 판단인가 | 사는 곳 |
    |---|---|---|
    | `removed`·`accepted`·`geom` | 그 batch 가 낸 그 마스크가 틀렸다/맞다 | **여기** |
    | `label`·`taxon`·`grade`·`pose`·`note` | 이 개체가 온전한 Globigerina 다 | **`ForamObject`** |
    | 사람이 그린 마스크 | 여기 개체가 있다 | 이미지 — **어느 batch 에도 없다** |
    """

    BIND = [(b, b) for b in ("exact", "iou", "manual", "orphan")]
    SOURCE = [(s, s) for s in ("engine", "manual")]

    # 편의용 — 진짜 열쇠는 `(image, mask_key)` 다. `image.viewpoint` 와 어긋나면
    # 안 된다
    viewpoint = models.ForeignKey(Viewpoint, on_delete=models.CASCADE,
                                 related_name="object_reviews")
    # **어느 이미지를 보고 한 판단인가** (DiaRUGA P06). 시야마다 볼 이미지가 한
    # 장이 아니다 — 프레임별 검출을 검토하면 `mask_key` 가 프레임끼리 겹친다
    image = models.ForeignKey("Image", on_delete=models.CASCADE,
                              related_name="object_reviews")
    # **어느 검출을 보고 한 판단인가** (DiaRUGA P09 5.1). 없이 두면 엔진을 갈 때 옛
    # 판단이 새 검출에 IoU 로 옮겨 붙는다 — 사람은 자기가 지우지 않은 것이
    # 지워져 있는 것을 보게 된다. **`NULL` 은 사람이 그린 개체다.**
    batch = models.ForeignKey("RunBatch", null=True, blank=True,
                              on_delete=models.PROTECT,
                              related_name="object_reviews")
    mask_key = models.CharField(max_length=64)
    candidate = models.ForeignKey(Candidate, null=True, blank=True,
                                  on_delete=models.SET_NULL,
                                  related_name="reviews")
    bind_method = models.CharField(max_length=8, choices=BIND, default="orphan")
    bind_score = models.FloatField(null=True, blank=True)
    # {"bbox": [x,y,w,h], "polygon": [...]}
    geom = models.JSONField(default=dict, blank=True)

    # 사람이 그린 개체인가. `batch is None` 에서 파생시킬 수도 있지만 두 칸을
    # 따로 두어 **검사가 둘을 대조할 수 있게** 한다 (`check_db` 8번).
    # `db_default` 를 함께 준다 — `rebind` 가 파이프라인 컨테이너에서 이 표를 쓴다
    source = models.CharField(max_length=8, choices=SOURCE, default="engine",
                              db_default="engine")
    # 엔진이 낸 기하를 사람이 고쳤다 — **회차별 수렴 지표**다 (DiaRUGA P09 5.7)
    geom_edited = models.BooleanField(default=False, db_default=False)

    # **이 판정이 가리키는 개체** (DiaRUGA P12). 여러 프레임에 걸쳐 잡힌 같은
    # 개체가 이 FK 로 하나가 된다. **비어 있지 않다** — 판정 행이 생기는 순간
    # 개체도 함께 생긴다(대개 1:1, 사람이 묶으면 N:1). **지운 마스크도 개체를
    # 갖는다** — 지웠다 되살리는 사이에 묶음이 깨지지 않게
    foram_object = models.ForeignKey("ForamObject", on_delete=models.CASCADE,
                                     related_name="members")
    # 이 개체의 얼굴 — 학습 자료로 뽑을 때, 목록에 보일 때 이 판을 쓴다.
    # 개체마다 정확히 하나(0 은 저장 쪽이 막는다. DB 제약은 "둘 이상" 만 막는다)
    is_rep = models.BooleanField(default=False, db_default=False)

    # 둘을 한 칼럼으로 합치지 않는다 — "사람이 지웠다가 이긴다" 는 규칙이
    # 두 값의 조합으로 표현된다
    removed = models.BooleanField(default=False)
    accepted = models.BooleanField(default=False)
    # **검토 완료가 자동으로 붙이는 확인** — 마스크마다 누른 것이 아니라 완료
    # 한 번이 남은 것 전부에 퍼진 것이다. `accepted`(엔진이 떨어뜨린 것을
    # 사람이 되살림)와 축이 다르다. **학습에서 손그림과 같은 무게로 쓰면 안
    # 된다.** `data.confirm_kept` 가 적는다. 화면은 이 칸을 모른다 — `/review`
    # payload 에 없으므로 `save_review` 의 청소가 지우지 않도록 `keys` 에 얹는다
    auto_confirmed = models.BooleanField(default=False, db_default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # 한 개체는 한 이미지에서 하나다
            models.UniqueConstraint(fields=["foram_object", "image"],
                                    name="uniq_objreview_object_image"),
            # 대표는 개체마다 하나
            models.UniqueConstraint(fields=["foram_object"],
                                    condition=models.Q(is_rep=True),
                                    name="uniq_objreview_rep"),
            # **batch 가 열쇠에 들어간다**
            models.UniqueConstraint(
                fields=["image", "batch", "mask_key"],
                condition=models.Q(batch__isnull=False),
                name="uniq_objreview_key"),
            # 사람이 그린 것은 `batch` 가 NULL 이라 위 제약이 안 잡는다
            models.UniqueConstraint(
                fields=["image", "mask_key"],
                condition=models.Q(batch__isnull=True),
                name="uniq_objreview_manual"),
        ]
        indexes = [
            models.Index(fields=["viewpoint", "bind_method"]),
            models.Index(fields=["bind_method"]),
            models.Index(fields=["image", "batch"]),
            models.Index(fields=["source"]),
            models.Index(fields=["foram_object"]),
        ]

    # **읽기 전용 통로다.** 분류·종은 `ForamObject` 에 살지만 읽는 자리가 많아
    # 여기서 비춘다. 쓰기는 막혀 있다 — `o.label = …` 은 `AttributeError` 다.
    # 부르는 쪽은 `select_related("foram_object")` 를 걸어야 한다
    @property
    def label(self) -> str:
        return self.foram_object.label

    @property
    def species(self) -> str:
        return self.foram_object.species

    @property
    def note(self) -> str:
        return self.foram_object.note

    def __str__(self):
        marks = [n for n, v in (("삭제", self.removed), ("복구", self.accepted),
                                ("메모", self.note)) if v]
        return f"{self.mask_key}{' ★' if self.is_rep else ''} {'·'.join(marks) or '-'}"


class ForamObject(models.Model):
    """개체 하나 — **사람이 하나로 보는 대상** (DiaRUGA P12 의 `DiatomObject`).

    초점면 3~5장에 같은 개체가 서너 번 잡히는데, 그것이 하나라는 것을 이 표가
    말한다. 판정(`ObjectReview`)이 여기에 매달리고, **분류·종은 여기 산다** —
    "이것이 Globigerina 다" 는 개체의 성질이지 어느 판에서 봤느냐의 성질이
    아니다. 모든 판정이 개체를 갖는다(1:1) — **묶기 = 개체 둘을 합치는 것**,
    **풀기 = 개체를 가르는 것**. 시야를 못 넘고 회차도 안 넘는다.

    ## DiaRUGA 와 다른 것

    - **`species` 문자열이 `taxon` FK 가 됐다.** 종이 수백이고 계층이 있어
      자유 문자열로는 계수표(5단계)가 안 선다. 화면은 `Taxon` 자동완성으로
      고르고, 목록에 없는 이름은 **거절한다** — WoRMS 전체를 반입해 두니
      "없는 이름" 은 오기이거나 아직 반입 안 된 것이다. `species` 는 읽기
      통로로 남겨 DiaRUGA 의 읽는 자리(카탈로그·내보내기)가 그대로 돌게 한다
    - **자세(`POSE`)** 가 규조의 valve/girdle 이 아니라 유공충의 네 면이다
    """

    viewpoint = models.ForeignKey(Viewpoint, on_delete=models.CASCADE,
                                  related_name="foram_objects")
    # 어느 묶음의 검출을 보며 묶었나. PROTECT — 묶음을 지우려면 그 검출을 보며
    # 만든 사람의 묶음부터 지워야 한다
    batch = models.ForeignKey(RunBatch, on_delete=models.PROTECT,
                              null=True, blank=True,
                              related_name="foram_objects")
    # **분류** — `ClassDef` 가 정한 형태·보존 목록에서 고른다 (온전·파손·파편·비유공충)
    label = models.CharField(max_length=32, blank=True)
    # **동정 결과** — `Taxon` 하나. `label` 과 축이 다르다: 저쪽은 "어떤 상태인가",
    # 이쪽은 "무엇인가". **재생성 불가다** — 현미경을 보며 고른 것이고
    # `export_review.py` 가 내보낸다. PROTECT — 쓰인 학명은 못 지운다(끄기만)
    taxon = models.ForeignKey("Taxon", on_delete=models.PROTECT,
                              null=True, blank=True, related_name="foram_objects")
    # **자세는 개체의 성질이다** — 초점을 옮겨도 누운 자세는 그대로라 묶인
    # 판들이 이 값을 나눠 갖는다. 묶을 때 값이 엇갈리면 거절한다. 완형에만 매긴다
    POSE = [("umbilical", "umbilical view"), ("spiral", "spiral view"),
            ("edge", "edge view"), ("apertural", "apertural view"),
            ("other", "other position")]
    pose = models.CharField(max_length=10, choices=POSE, blank=True,
                            default="", db_default="")
    # **등급 — 이 개체가 얼마나 좋은 표본인가.** 순서가 있다(A > B > C). 어느
    # 판이 잘 보이는가는 `is_rep` 가 말한다 — 두 축을 갈라 둔다. **`A` 는
    # 종까지 동정된 것을 뜻하되 매기는 사람의 기준이지 시스템이 막는 규칙이
    # 아니다.** 완형에만 매긴다 — 화면이 칸을 감추고 서버가 다시 검사한다
    GRADE = [("A", "A — 동정키도 완형도 잘 드러난다"),
             ("B", "B — 완형이나 형태가 덜 드러난다 · 또는 상태가 나쁘나 동정키가 남았다"),
             ("C", "C — 완형도 동정키도 잘 안 드러난다")]
    grade = models.CharField(max_length=1, choices=GRADE, blank=True,
                             default="", db_default="")
    # **코멘트 — 이 개체를 두고 사람이 적는 말.** 재생성 불가. 묶을 때
    # 엇갈려도 거절하지 않고 잇는다 (`data.merge_into_object`)
    note = models.TextField(blank=True, default="", db_default="")
    # **카탈로그 번호를 어느 판정에서 뽑나** (DiaRUGA P18). 번호는 파생이고
    # 저장하는 것은 재료의 출처다. 대표(`is_rep`)와 축을 가른다 — 얼굴은 대표,
    # 이름은 앵커. 묶어도 안 움직인다. 앵커 판정이 지워지면 `SET_NULL` 이고
    # 남은 멤버 중 가장 오래된 것으로 넘긴다(`data.reanchor`)
    anchor = models.ForeignKey("ObjectReview", on_delete=models.SET_NULL,
                               null=True, blank=True,
                               related_name="anchor_of")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["viewpoint"]),
                   models.Index(fields=["viewpoint", "batch"]),
                   models.Index(fields=["taxon"])]

    # 읽기 통로 — DiaRUGA 의 `species` 문자열을 읽던 자리가 그대로 돈다.
    # 부르는 쪽은 `select_related("taxon")` 을 건다. **쓰기는 이름을 `Taxon` 으로
    # 찾아 앉힌다** (`Taxon.resolve`) — 없는 이름은 `ValueError` 다
    @property
    def species(self) -> str:
        return self.taxon.name if self.taxon_id else ""

    @species.setter
    def species(self, name):
        self.taxon = Taxon.resolve(name)

    def __str__(self):
        return f"obj#{self.pk} vp={self.viewpoint_id} ({self.members.count()})"


# ─────────────────────────────────────────────────────────── 분류 체계 (Taxon)

TAXON_RANK = [(r, r) for r in
              ("Phylum", "Subphylum", "Class", "Subclass", "Order", "Suborder",
               "Superfamily", "Family", "Subfamily", "Genus", "Subgenus",
               "Species", "Subspecies", "Variety", "Forma")]
# WoRMS 의 `status` 는 열 가지가 넘는다(accepted · unaccepted · junior subjective
# synonym · taxon inquirendum · nomen dubium …). 목록으로 가두지 않고 **그대로
# 적는다** — 유효한가는 `accepted` 하나로 가른다 (`Taxon.valid`)
HABIT = [("", "—"), ("planktonic", "부유성"), ("benthic", "저서성")]


class Taxon(models.Model):
    """학명 하나 — **WoRMS 를 정본으로 한 계층** (P01 5절).

    `migrate/import_worms.py` 가 WoRMS REST 로 유공충(AphiaID 1410) 아래를 통째로
    반입한다. **쓰는 것만 `active` 로 켠다** — 자동완성은 켠 것만 내고, 목록을
    사람이 따로 만들지 않는다. 동정에 한 번 쓰이면 저절로 켜진다
    (`data.resolve_taxon`).

    **행을 지우지 않는다.** `ForamObject.taxon` 이 PROTECT 로 잡고 있고, 반입을
    다시 돌려도 `aphia_id` 로 맞춰 갱신만 한다. 이명(`unaccepted`)은
    `accepted` FK 로 유효명을 가리킨다 — 화면은 이명을 골라도 받되 유효명을
    옆에 보여준다. 반입 전에 사람이 손으로 넣은 행은 `aphia_id` 가 비어 있다.

    `habit`(부유성/저서성)은 WoRMS 에 없다 — 과·목 수준에서 사람이 켜고
    `import_worms.py --habit` 이 아래로 물려준다.
    """

    name = models.CharField(max_length=160)
    rank = models.CharField(max_length=16, choices=TAXON_RANK, blank=True)
    parent = models.ForeignKey("self", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="children")
    aphia_id = models.IntegerField(null=True, blank=True, unique=True)
    status = models.CharField(max_length=40, default="accepted", db_default="accepted")
    # 이명일 때 유효명. 유효명 자신은 비어 있다
    accepted = models.ForeignKey("self", null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="synonyms")
    authority = models.CharField(max_length=160, blank=True, default="", db_default="")
    habit = models.CharField(max_length=12, choices=HABIT, blank=True,
                             default="", db_default="")
    # 자동완성에 낼 것. 동정에 쓰이면 저절로 켜진다
    active = models.BooleanField(default=False, db_default=False)
    # 화석종인가 (WoRMS `isExtinct`). 남극 코어라 화석도 본다
    extinct = models.BooleanField(default=False, db_default=False)
    note = models.TextField(blank=True, default="", db_default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "taxa"
        indexes = [models.Index(fields=["active", "name"]),
                   models.Index(fields=["rank"])]
        constraints = [models.UniqueConstraint(fields=["name", "authority"],
                                               name="uniq_taxon_name_authority")]

    @classmethod
    def resolve(cls, name, *, activate: bool = True):
        """이름 → 행. 빈 이름은 `None`(종을 비운다). **없는 이름은 거절한다**
        (`ValueError`) — WoRMS 전체가 반입돼 있으니 없는 이름은 오기이거나 반입이
        안 된 것이고, 조용히 새 행을 만들면 같은 종이 두 줄이 된다(DiaRUGA 의
        자유 문자열이 겪은 일). 같은 이름이 여럿이면 유효명 → 작은 `aphia_id`
        차례. `"이름 명명자"` 로 적으면 그 행만 맞는다. `activate` 면 켠다.
        """
        import re
        from django.db.models import Case, When
        name = re.sub(r"\s+", " ", str(name or "")).strip()
        if not name:
            return None
        qs = cls.objects.filter(name__iexact=name)
        if not qs.exists():
            for i in range(len(name), 0, -1):
                if name[i - 1] != " ":
                    continue
                qs = cls.objects.filter(name__iexact=name[:i - 1],
                                        authority__iexact=name[i:])
                if qs.exists():
                    break
        row = (qs.order_by(Case(When(status="accepted", then=0), default=1),
                           "aphia_id", "pk").first())
        if row is None:
            raise ValueError(f"목록에 없는 학명이다: {name}")
        if activate and not row.active:
            cls.objects.filter(pk=row.pk).update(active=True)
            row.active = True
        return row

    @property
    def worms_url(self) -> str:
        return (f"https://www.marinespecies.org/aphia.php?p=taxdetails&id={self.aphia_id}"
                if self.aphia_id else "")

    @property
    def valid(self) -> "Taxon":
        """유효명 — 이명이면 `accepted`, 아니면 자기 자신."""
        return self.accepted if self.accepted_id else self

    def __str__(self):
        return self.name
