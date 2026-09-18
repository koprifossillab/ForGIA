#!/bin/bash
# 뷰어 컨테이너 시작 (P03).
set -e

cd /app/web

# 기존 DB 에 전부 적용돼 있으면 아무 일도 하지 않는다. 새 장비에 올릴 때를 위해 둔다.
python manage.py migrate --noinput

# collectstatic 은 하지 않는다 — 이 뷰어는 정적 파일이 없다.
# 템플릿이 CSS·JS 를 인라인으로 들고 있어 {% static %} 을 한 번도 쓰지 않는다.

exec gunicorn forgiaweb.wsgi:application \
    --bind 0.0.0.0:9090 \
    --workers 3 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
