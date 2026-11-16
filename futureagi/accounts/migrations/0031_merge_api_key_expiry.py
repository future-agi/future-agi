from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0026_orgapikey_expires_at"),
        ("accounts", "0030_queue_existing_demo_trace_publications"),
    ]

    operations = []
