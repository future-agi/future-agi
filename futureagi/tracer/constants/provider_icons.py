"""Provider icons for spans read from ClickHouse, served by this install's UI.

Each value is a root-relative path. The browser resolves it against the page
that renders it, which is the frontend: its image (nginx) and the Vite dev
server both serve ``frontend/public/`` at the site root. So the icon comes
from the install's own origin on Future AGI Cloud, on a self-hosted install
and on every local or cloud test stack, and no response names another host.

The path is never prefixed with BASE_URL: that is the API's origin, which
does not serve ``frontend/public/``.

Sources of the files (all in this repository):
- ``/icons/settings/{openai,anthropic,google}.png``: the UI's provider icons
  already in ``frontend/public/icons/settings/``.
- ``/assets/providers/{vapi,retell}.svg``: the symbol of each vendor's mark from
  ``frontend/src/assets/icons/platform-logos/{vapi,retell}.svg`` (the
  Simulate platform logos), without the wordmark letters so it reads at
  14 px, and filled #7C7C86 instead of ``currentColor``: an ``<img>`` gives
  ``currentColor`` no inherited colour, so it drew black, which is invisible
  on the dark theme. #7C7C86 is at least 3:1 on both themes' chip
  background (#F8F8F8 and #18181B).

frontend/nginx.conf serves every file under ``frontend/public/`` immutable for
a year, and these names carry no content hash: a changed icon gets a new file
name and a new entry here, never an edit in place
(tracer/tests/test_provider_icons_same_origin.py pins the two SVGs).

Before this module the ClickHouse reader returned absolute icon URLs on the
production UI host (its /static/providers/<name>.svg), so every stack's
browser requested production.
"""

from __future__ import annotations

PROVIDER_ICON_PATHS: dict[str, str] = {
    "openai": "/icons/settings/openai.png",
    "anthropic": "/icons/settings/anthropic.png",
    "google": "/icons/settings/google.png",
    "gcp.vertex.agent": "/icons/settings/google.png",
    "vapi": "/assets/providers/vapi.svg",
    "retell": "/assets/providers/retell.svg",
}


def provider_icon_path(provider: str | None) -> str | None:
    """The root-relative icon path for ``provider``, or None when unmapped.

    The UI draws its generic model icon for None.
    """
    if not provider:
        return None
    return PROVIDER_ICON_PATHS.get(provider.strip().lower())
