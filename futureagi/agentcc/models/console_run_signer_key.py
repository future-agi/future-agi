"""The agent service deployments' public keys for FA's console-run signer.

fi-security ruling 4 (GWC2197-CONSOLE-RUN-SIGNER-DESIGN-R1-RULINGS-r1-FISEC.json):
rows are written only by the operator command
``manage.py agentcc_console_run_signer_keys``, never by an HTTP route or a
default admin form; at most two active keys per deployment; list and revoke;
every change audit-logged. A row holds the raw 32-byte Ed25519 PUBLIC key; no
private key ever passes through FA. Revoking a key refuses its next assertion at
once, without a redeploy.
"""

from django.db import models

KID_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"
DEPLOYMENT_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"
MAX_ACTIVE_PER_DEPLOYMENT = 2


class AgentccConsoleRunSignerKey(models.Model):
    kid = models.CharField(max_length=64, primary_key=True)
    deployment = models.CharField(max_length=64, db_index=True)
    public_key = models.BinaryField(max_length=32)
    created_by = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        db_table = "agentcc_console_run_signer_key"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kid__regex=KID_PATTERN),
                name="console_run_signer_key_kid",
            ),
            models.CheckConstraint(
                condition=models.Q(deployment__regex=DEPLOYMENT_PATTERN),
                name="console_run_signer_key_deployment",
            ),
        ]
