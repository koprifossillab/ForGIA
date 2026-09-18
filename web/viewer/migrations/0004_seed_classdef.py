"""분류 표의 첫 네 줄 — 형태·보존 상태 (P01 2절 ③ · models.ClassDef 머리말).

**분류를 더할 때 채울 것 여덟**(DiaRUGA 038·040)을 여기서 다 채운다 — `base.html`
의 CSS(`.badge.<badge>` · 마스크 색)도 같은 커밋에 있다. 색은 연보라 테마 위에서
갈리게: 온전은 강조색, 파손은 주황, 파편은 옅은 보라, 비유공충은 회색.

`reverse` 는 행을 지우지 않는다 — 그 분류로 붙은 교정이 이름 없는 분류가 된다.
`active=False` 로 끈다.
"""
from django.db import migrations

ROWS = [
    # key, label, short, badge, color, hotkey, counted, sort_order
    ("foram", "온전", "온전", "foram", "196,181,253", "q", True, 0),
    ("broken", "파손", "파손", "broken", "255,180,84", "w", True, 1),
    ("fragment", "파편", "파편", "fragment", "150,140,190", "e", False, 2),
    ("other", "비유공충", "기타", "other", "130,130,130", "r", False, 3),
]


def seed(apps, schema_editor):
    ClassDef = apps.get_model("viewer", "ClassDef")
    for key, label, short, badge, color, hot, counted, order in ROWS:
        ClassDef.objects.update_or_create(
            key=key,
            defaults={"label": label, "short": short, "badge": badge,
                      "color": color, "hotkey": hot, "counted": counted,
                      "is_taxon": False, "sort_order": order, "active": True})


def unseed(apps, schema_editor):
    ClassDef = apps.get_model("viewer", "ClassDef")
    ClassDef.objects.filter(key__in=[r[0] for r in ROWS]).update(active=False)


class Migration(migrations.Migration):
    dependencies = [("viewer", "0003_detections")]
    operations = [migrations.RunPython(seed, unseed)]
