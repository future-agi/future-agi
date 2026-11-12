import hashlib

from django.db import migrations, models


def fill_digests(apps, schema_editor):
    Approval = apps.get_model("prism", "AgentccMCPApproval")
    for row in Approval.objects.using(schema_editor.connection.alias).all().iterator():
        row.approval_digest = hashlib.sha256(row.approval_id.encode()).hexdigest()
        row.save(update_fields=["approval_digest"])


class Migration(migrations.Migration):
    dependencies = [("prism", "0074_api_key_provisioning_state")]
    operations = [
        migrations.AddField(
            model_name="agentccmcpapproval",
            name="approval_digest",
            field=models.CharField(max_length=64, null=True),
        ),
        migrations.RunPython(fill_digests, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="agentccmcpapproval",
            name="approval_digest",
            field=models.CharField(max_length=64, unique=True),
        ),
        migrations.AlterField(
            model_name="agentccmcpapproval",
            name="approval_id",
            field=models.TextField(),
        ),
    ]
