"""Loopback proof helper: use the real FA signer and role mapping with a throwaway key.

This does not replace the Django session-exchange authentication/DB journey.
The caller supplies only synthetic memberships and a temporary signing key.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "futureagi"))
from accounts.omega_session import (
    HostAssertionSigner,
    delegation_role,
    emergency_authority,
)

request = json.load(sys.stdin)
signer = HostAssertionSigner.from_environment(
    {
        "OMEGA_SQUAD_DELEGATION_ISSUER": "https://host.example.test",
        "OMEGA_SQUAD_DELEGATION_KEY_ID": "emergency-proof",
        "OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE": sys.argv[1],
    }
)
role = delegation_role(request["orgLevel"], request["workspaceLevel"])
result = [
    signer.issue(
        subject=request["subject"],
        organization_id=request["organization"],
        workspace_id=request["workspace"],
        role=role,
        audience="omega-squad",
        auth_method="session",
        credential_id="synthetic-browser-session",
        products={"console": "active"},
        rollout_epoch=1,
        emergency_control=emergency_authority(
            request["orgLevel"], request["workspaceLevel"], "session"
        ),
    )["accessToken"]
    for _ in range(min(8, max(1, request.get("count", 1))))
]
print(json.dumps(result))
