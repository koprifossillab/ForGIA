"""URL. DiaRUGA v0.29.0 `web/viewer/urls.py` 와 **같은 주소 모양**이다 — 두 뷰어를
오가는 사람이 같은 자리를 찾을 수 있어야 한다. 아직 없는 화면(`/d/<slug>/crops/`
· `/atlas/` · `/system-settings/ops/` …)은 그 단계에서 같은 이름으로 더한다.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("img", views.image, name="image"),
    path("healthz", views.healthz, name="healthz"),
    path("loc/<str:site_code>/<str:core_code>/", views.core_page, name="core"),
    path("core/<str:site_code>/<str:core_code>/", views.core_redirect),
    path("system-settings/", views.system_settings, name="system_settings"),
    path("system-settings/pipeline/", views.system_settings_pipeline,
         name="system_settings_pipeline"),
    path("manage/", views.settings_redirect),
    path("manage/pipeline/", views.settings_redirect, {"tab": "pipeline"}),
    path("d/<slug:slug>/", views.dataset, name="dataset"),
    path("d/<slug:slug>/edit/", views.dataset_edit, name="dataset_edit"),
    path("d/<slug:slug>/g/<int:gid>/", views.group, name="group"),
]
