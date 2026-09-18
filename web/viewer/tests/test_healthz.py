"""`/healthz`. 빈 DB 는 unhealthy 고, 자료가 있으면 ok 다."""
import json

from django.conf import settings

from .base import ForGIATestCase
from .factories import make_world


class HealthzTests(ForGIATestCase):
    def test_empty_db_is_unhealthy(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 503)
        info = json.loads(r.content)
        self.assertEqual(info["status"], "unhealthy")
        self.assertEqual(info["db"]["slide"], 0)
        self.assertEqual(r["Cache-Control"], "no-store")

    def test_with_data_is_ok(self):
        make_world()
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        info = json.loads(r.content)
        self.assertEqual(info["status"], "ok")
        self.assertEqual(info["db"], {"site": 1, "locality": 1, "sample": 2, "slide": 3})
        # 문턱이 없으면 백업 나이는 알려만 준다
        self.assertIsNone(settings.BACKUP_MAX_AGE_H)
        self.assertEqual(info["backup"]["max_age_h"], None)
