"""The Observe project name of a Console agent (learning-service PLAN section 1, stage 1).

One concern: ``agent/<slug>-<shortid>``.

- ``slug``: the agent's name in lowercase ASCII letters and digits, other runs as
  one dash, at most 40 characters; ``agent`` when nothing is left.
- ``shortid``: the first 12 hex digits of SHA-256 over the EE agent id, so the
  name belongs to that agent and not to its display name. Another agent made later
  under the same name gets another project; the same id always names the same one,
  so a retry or a racing ensure lands on one row.

The name is fixed when the link is made (``tracer.models.omega_deployment_link``).
"""

import hashlib
import re
import unicodedata

AGENT_PROJECT_PREFIX = "agent/"
SLUG_MAX = 40
SHORT_ID_HEX = 12
FALLBACK_SLUG = "agent"

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def agent_slug(agent_name: str) -> str:
    """``agent_name`` as lowercase ASCII letters, digits and single dashes (module docstring)."""
    folded = (
        unicodedata.normalize("NFKD", agent_name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    slug = _NOT_SLUG.sub("-", folded).strip("-")[:SLUG_MAX].rstrip("-")
    return slug or FALLBACK_SLUG


def agent_short_id(ee_agent_id: str) -> str:
    """The agent's 12-hex-digit tag, from its EE id."""
    return hashlib.sha256(ee_agent_id.encode("utf-8")).hexdigest()[:SHORT_ID_HEX]


def agent_project_name(agent_name: str, ee_agent_id: str) -> str:
    """The project an agent's traces land in when its link is made."""
    return (
        f"{AGENT_PROJECT_PREFIX}{agent_slug(agent_name)}-{agent_short_id(ee_agent_id)}"
    )
