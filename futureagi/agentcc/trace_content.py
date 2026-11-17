"""Disclosure and audit identity shared by tool-span capture surfaces."""

DISCLOSURE = (
    "Full-content capture exports tool inputs and outputs to your organization's "
    "collector. It masks secret-named fields and recognized Bearer, sk-, AIza, "
    "AKIA/ASIA and JWT formats. Prefix-less Future AGI keys can remain "
    "in other text. Other sensitive content may "
    "still be exported. Off by default; only an organization Owner or Admin can "
    "enable it. Enabling and disabling are audited."
)
AUDIT_ACTION = "agentcc.trace_content_changed"
