from django.db import migrations


class Migration(migrations.Migration):
    # Three leaves meet here: the released request-log uniqueness (0029, from
    # 0028), auto-reload consent receipts (0052, from 0051) and the MCP
    # service and credential line (through 0057, from 0051). Every branch
    # runs; this changes nothing. 0058-0070 are left to the MCP line, whose
    # own numbers continue from 0057.
    dependencies = [
        ("prism", "0029_request_log_unique_request_id"),
        ("prism", "0052_auto_reload_consent"),
        ("prism", "0057_mcp_credential_origin"),
    ]

    operations = []
