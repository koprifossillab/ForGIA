"""URL. DiaRUGA v0.29.0 `web/viewer/urls.py` 와 **같은 주소 모양**이다 — 두 뷰어를
오가는 사람이 같은 자리를 찾을 수 있어야 한다. 아직 없는 화면(`/atlas/` ·
`/compare/` · 오프라인, 5단계)은 그 단계에서 같은 이름으로 더한다. ForGIA 의
것은 `api/taxon/suggest`(학명 자동완성)와 `system-settings/taxa/` 뿐이다.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("img", views.image, name="image"),
    path("crop", views.crop, name="crop"),
    path("healthz", views.healthz, name="healthz"),
    # 교정 저장 — **그 (이미지, 묶음) 의 교정 전체를 갈아치운다** (views.save_review)
    path("review", views.save_review, name="save_review"),
    path("loc/<str:site_code>/<str:core_code>/", views.core_page, name="core"),
    path("core/<str:site_code>/<str:core_code>/", views.core_redirect),
    path("system-settings/", views.system_settings, name="system_settings"),
    path("system-settings/ops/", views.system_settings_ops,
         name="system_settings_ops"),
    path("system-settings/pipeline/", views.system_settings_pipeline,
         name="system_settings_pipeline"),
    path("manage/", views.settings_redirect),
    path("manage/ops/", views.settings_redirect, {"tab": "ops"}),
    path("manage/pipeline/", views.settings_redirect, {"tab": "pipeline"}),
    path("d/<slug:slug>/", views.dataset, name="dataset"),
    path("d/<slug:slug>/edit/", views.dataset_edit, name="dataset_edit"),
    path("d/<slug:slug>/mark-all/", views.mark_all, name="mark_all"),
    path("d/<slug:slug>/detections/", views.detections, name="detections"),
    path("d/<slug:slug>/crops/", views.crops, name="crops"),
    # 개체 카탈로그 — 동정하는 자리 (DiaRUGA P16·P18)
    path("d/<slug:slug>/catalog/", views.catalog, name="catalog"),
    path("d/<slug:slug>/catalog/save", views.save_catalog, name="save_catalog"),
    # 학명 자동완성 — `Taxon` 표(WoRMS 반입)에서 찾는다. 켠 것이 먼저다
    path("api/taxon/suggest", views.taxon_suggest, name="taxon_suggest"),
    path("system-settings/taxa/", views.system_settings_taxa,
         name="system_settings_taxa"),
    path("manage/taxa/", views.settings_redirect, {"tab": "taxa"}),
    path("thresholds/", views.threshold_page, name="thresholds_all"),
    path("d/<slug:slug>/thresholds/", views.threshold_page, name="thresholds"),
    path("api/threshold/preview", views.threshold_preview, name="threshold_preview"),
    path("api/threshold/apply", views.threshold_apply, name="threshold_apply"),
    path("api/threshold/masks", views.threshold_masks, name="threshold_masks"),
    path("api/threshold/history", views.threshold_history, name="threshold_history"),
    path("d/<slug:slug>/g/<int:gid>/", views.group, name="group"),
    # 같은 개체 묶음 (DiaRUGA P11) — 묶음 하나 단위. /review 에 안 싣는 이유는
    # views.save_object_link 머리말에 있다.
    path("d/<slug:slug>/g/<int:gid>/link", views.save_object_link,
         name="save_link"),
    # 검출 마스크를 다른 판에도 앉힌다 (DiaRUGA P19). `/link` 와 같은 자리다
    path("d/<slug:slug>/g/<int:gid>/spread", views.spread_detection,
         name="spread_detection"),
    # 시야 가르기. POST 전용이고 confirm=1 인 두 번째 POST 만 실제로 고친다.
    path("d/<slug:slug>/g/<int:gid>/split", views.split_group,
         name="split_group"),
    path("api/d/<slug:slug>.json", views.api_dataset, name="api_dataset"),
]
