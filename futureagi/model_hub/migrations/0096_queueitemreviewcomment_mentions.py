# Generated manually on 2026-05-12

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0095_queueitemnote"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="queueitemreviewcomment",
            name="mentioned_users",
            field=models.ManyToManyField(
                blank=True,
                related_name="mentioned_annotation_review_comments",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
