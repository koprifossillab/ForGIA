"""URL. DiaRUGA v0.29.0 `web/viewer/urls.py` 에서 0단계 것만 — 나머지 주소는
그 화면이 오는 단계에서 같은 모양으로 더한다 (`/d/<slug>/` · `/d/<slug>/g/<n>/`
· `/atlas/` · `/system-settings/` …). **주소의 모양은 DiaRUGA 와 같게 둔다** —
두 뷰어를 오가는 사람이 같은 자리를 찾을 수 있어야 한다.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("healthz", views.healthz, name="healthz"),
]
