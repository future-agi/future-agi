"""Synthetic retained-state fixture; copied into the disposable E2E image only.

Invoked explicitly over container stdin, never installed as an HTTP issuer.
No product creation path, model save signal or StateProbe mutation is used.
"""

import json
import os
import re
import sys
import uuid


def seed(data):
    from accounts.api_secrets import hash_secret, verify_secret
    from accounts.models import (
        OrganizationMembership,
        OrgApiKey,
        User,
        WorkspaceMembership,
    )
    from django.db import transaction
    from django.utils import timezone

    if os.environ.get("E2E_RETAINED_CREDENTIAL_FIXTURES") != "1":
        raise ValueError("retained fixture opt-in required")
    if os.environ.get("DJANGO_SETTINGS_MODULE") != "tfc.settings.e2e":
        raise ValueError("retained fixture requires the E2E image settings")
    if set(data) != {"email", "organizationId", "workspaceId", "name"}:
        raise ValueError("unexpected retained fixture fields")
    if not re.fullmatch(r"e2e-[a-zA-Z0-9-]+@futureagi\.com", data["email"]):
        raise ValueError("synthetic actor required")
    if not re.fullmatch(r"e2e-obs61-[a-zA-Z0-9-]+", data["name"]):
        raise ValueError("synthetic fixture name required")
    org_id, workspace_id = (
        uuid.UUID(data["organizationId"]),
        uuid.UUID(data["workspaceId"]),
    )
    with transaction.atomic(using="default"):
        user = User.objects.using("default").get(email=data["email"], is_active=True)
        OrganizationMembership.no_workspace_objects.using("default").get(
            organization_id=org_id, user=user, is_active=True, deleted=False
        )
        WorkspaceMembership.no_workspace_objects.using("default").get(
            workspace_id=workspace_id,
            workspace__organization_id=org_id,
            workspace__is_active=True,
            workspace__deleted=False,
            user=user,
            is_active=True,
            deleted=False,
        )
        result = {}
        for kind in ("system", "retired"):
            api_key, secret = uuid.uuid4().hex, uuid.uuid4().hex
            # A legitimate secret hash isolates eligibility, not a wrong-password refusal.
            row = OrgApiKey(
                name=f"{data['name']}-{kind}",
                organization_id=org_id,
                workspace_id=workspace_id,
                user=user,
                api_key=api_key,
                secret_key="",
                secret_key_hash=hash_secret(secret),
                secret_key_ciphertext="",
                type="system" if kind == "system" else "user",
                enabled=True,
                deleted=False,
                retired_at=timezone.now() if kind == "retired" else None,
                retired_reason="e2e-retained" if kind == "retired" else None,
            )
            # bulk_create intentionally avoids the production save/issuance path.
            OrgApiKey.no_workspace_objects.using("default").bulk_create([row])
            stored = OrgApiKey.no_workspace_objects.using("default").get(pk=row.pk)
            if not verify_secret(stored.secret_key_hash, "", secret).ok:
                raise ValueError("retained fixture secret readback failed")
            if (
                stored.type != row.type
                or stored.retired_at != row.retired_at
                or stored.user_id != user.pk
                or stored.organization_id != org_id
                or stored.workspace_id != workspace_id
                or not stored.enabled
                or stored.deleted
                or stored.secret_key
                or stored.secret_key_ciphertext
            ):
                raise ValueError("retained fixture state readback failed")
            result[kind] = {"id": str(row.pk), "apiKey": api_key, "secretKey": secret}
        return result


def main():
    import django

    django.setup()
    # These values travel only through the parent pipe; never printed to job logs.
    result = seed(json.loads(sys.stdin.read(4096)))
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Django errors can include SQL parameters; suppress their bodies and causes.
        sys.stderr.write("Retained credential fixture failed; details withheld\n")
        sys.exit(1)
