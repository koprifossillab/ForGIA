"""데이터와 설정을 담는 스키마. DiaRUGA v0.29.0 `web/viewer/models.py` 에서
층 넷(`Site`·`Locality`·`Sample`·`Slide`)만 먼저 가져왔다 (P01 0단계).

나머지 — `Viewpoint`·`Frame`·`Stack`·`Image`(1단계), `Detection`·`Candidate`·
`ThresholdSet`·`ClassDef`·`RunBatch`·`Run`(2단계), `ViewpointReview`·
`ObjectReview`·`ForamObject`·`Taxon`(3단계) — 는 그 단계에서 가져온다.

DiaRUGA 와 다른 자리 (P01 2절·5절):

- **육상 노두 갈래가 없다.** `Locality.kind`·`Sample.sample_no` 를 안 가져왔다.
  시료가 남극 코어뿐이라서다. 생기면 DiaRUGA 063 을 보고 그때 더한다
- **분획·분할은 관찰(`Slide`)에 붙는다.** P01 5절의 표는 `Sample` 에 적었는데
  옮기면서 고쳤다 — 같은 71cm 시료를 `>63um` 과 `>125um` 으로 따로 픽킹하면
  슬라이드가 둘이고, 그것이 DiaRUGA 가 말하는 "관찰 = 시료 하나를 처리 방법을
  달리해 본 것" 이다. 분할(1/n)도 분획마다 하므로 같이 간다. **건시료 무게만
  시료의 것이다**
- **시야가 격자 한 칸이다** — `Slide.cells` 가 격자 칸 수를 들고, 1단계의
  `Viewpoint.cell` 이 그 안의 번호다

DiaRUGA 에서 그대로 물려받은 규칙 둘은 그쪽 머리말 그대로다:

**1. 교정은 `Candidate` 가 아니라 `mask_key` 에 붙는다.** (3단계에서 온다)
**2. 검출은 덮어쓰지 않고 쌓는다.** (2단계에서 온다)
"""
from django.db import models

# 폴더 이름 규칙은 `naming.py` 하나뿐이다 — 뷰어·파이프라인·마이그레이션이 같은
# 것을 본다.
from .naming import base_name as _base_name


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
