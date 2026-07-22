from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tracer', '0083_merge_20260610_1220'),
    ]

    operations = [
        migrations.AlterField(
            model_name='observabilityprovider',
            name='provider',
            field=models.CharField(
                choices=[
                    ('vapi', 'Vapi'),
                    ('eleven_labs', 'Eleven Labs'),
                    ('retell', 'Retell'),
                    ('livekit', 'LiveKit'),
                    ('others', 'Others'),
                    ('bland', 'Bland.ai'),
                    ('twilio', 'Twilio'),
                ],
                max_length=255,
            ),
        ),
    ]
