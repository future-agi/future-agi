"""Updater settings: one documented precedence for every FUTURE_AGI_UPDATE_* key.

Precedence: command-line flag, then the process environment, then the
host-only file update.conf in the checkout, then the default. The settings
never live in .env: the Standalone app service reads .env as env_file, so a
backup command written there would reach the app container.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Mapping, Optional

from .manifest import CHANNELS

CONFIG_FILE = "update.conf"
POLICIES = ("patch", "minor", "major")

DEFAULT_MANIFEST_URL = (
    "https://github.com/future-agi/future-agi/releases/latest/download/future-agi-update-manifest.json"
)
# The signer bin/update trusts: the release workflow at the manifest's own tag.
# Because the identity has the form https://github.com/<owner>/<repo>/
# .github/workflows/<file>@refs/tags/{version}, verification also pins the
# certificate's GitHub workflow repository (<owner>/<repo>) and ref
# (refs/tags/<version>): the identity alone names the workflow file, which
# any other repository can call (trust.workflow_claims). A fork pinning its
# own identity of this form gets the same check for its own repository.
DEFAULT_TRUSTED_IDENTITY = (
    "https://github.com/future-agi/future-agi/.github/workflows/release-update-manifest.yml@refs/tags/{version}"
)
DEFAULT_TRUSTED_ISSUER = "https://token.actions.githubusercontent.com"
# cosign runs from this image when no cosign v3 or later is on PATH. Pinned by
# digest (it decides whether a release is trusted) and kept at the cosign
# release the manifest is signed with (.github/actions/setup-helm-tools).
DEFAULT_COSIGN_IMAGE = (
    "ghcr.io/sigstore/cosign/cosign:v3.1.3"
    "@sha256:9e5c2f2edc34351160407ca3416c61855bdf9403c3c5936e0f0be7fc261611b8"
)

DEFAULTS: Dict[str, str] = {
    "FUTURE_AGI_UPDATE_MANIFEST_URL": DEFAULT_MANIFEST_URL,
    "FUTURE_AGI_UPDATE_CHANNEL": "stable",
    "FUTURE_AGI_UPDATE_POLICY": "minor",
    "FUTURE_AGI_UPDATE_BACKUP_COMMAND": "",
    "FUTURE_AGI_UPDATE_BACKUP_TIMEOUT_SECONDS": "3600",
    "FUTURE_AGI_UPDATE_WAIT_TIMEOUT_SECONDS": "1200",
    "FUTURE_AGI_UPDATE_TRUSTED_IDENTITY": DEFAULT_TRUSTED_IDENTITY,
    "FUTURE_AGI_UPDATE_TRUSTED_ISSUER": DEFAULT_TRUSTED_ISSUER,
    "FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY": "",
    "FUTURE_AGI_UPDATE_COSIGN_OFFLINE": "false",
    "FUTURE_AGI_UPDATE_COSIGN_IMAGE": DEFAULT_COSIGN_IMAGE,
}

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SettingsError(ValueError):
    """A setting has a value the updater cannot use."""


def read_config_file(path: Path) -> Dict[str, str]:
    """Parse update.conf: KEY=value lines, # comments, optional quotes."""

    values: Dict[str, str] = {}
    if not path.exists():
        return values
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SettingsError(f"cannot read {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        # Name the line, never the bytes: the file may hold a backup command
        # with credentials in it.
        number = exc.object[:exc.start].count(b"\n") + 1
        raise SettingsError(f"{path}:{number}: not UTF-8 text; save the file as UTF-8") from exc
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise SettingsError(f"{path}:{number}: expected KEY=value")
        key, value = (part.strip() for part in stripped.split("=", 1))
        if not _KEY_RE.fullmatch(key):
            raise SettingsError(f"{path}:{number}: invalid key {key!r}")
        if not key.startswith("FUTURE_AGI_UPDATE_"):
            raise SettingsError(f"{path}:{number}: {key} is not an updater setting (FUTURE_AGI_UPDATE_*)")
        if key not in DEFAULTS:
            raise SettingsError(f"{path}:{number}: unknown setting {key}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


class Settings:
    def __init__(self, values: Mapping[str, str], sources: Mapping[str, str]) -> None:
        self._values = dict(values)
        self.sources = dict(sources)

    def get(self, key: str) -> str:
        return self._values[key]

    def origin(self, key: str) -> str:
        """' (from <source>)' for an error message, so the operator knows where to fix the value."""

        source = self.sources.get(key)
        return f" (from {source})" if source else ""

    def integer(self, key: str, minimum: int, maximum: int) -> int:
        raw = self._values[key]
        if not re.fullmatch(r"[0-9]+", raw or ""):
            raise SettingsError(f"{key} must be a whole number of seconds, not {raw!r}{self.origin(key)}")
        value = int(raw)
        if not minimum <= value <= maximum:
            raise SettingsError(f"{key} must be between {minimum} and {maximum}, not {value}{self.origin(key)}")
        return value

    def boolean(self, key: str) -> bool:
        raw = self._values[key].strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("", "0", "false", "no", "off"):
            return False
        raise SettingsError(f"{key} must be true or false, not {self._values[key]!r}{self.origin(key)}")

    @property
    def policy(self) -> str:
        return self._values["FUTURE_AGI_UPDATE_POLICY"]

    @property
    def channel(self) -> str:
        return self._values["FUTURE_AGI_UPDATE_CHANNEL"]


def load_settings(root: Path, overrides: Optional[Mapping[str, Optional[str]]] = None,
                  environ: Optional[Mapping[str, str]] = None, *, verify: bool = True) -> Settings:
    """The settings, each from the first layer that sets it, checked so that a bad value fails early.

    `verify` says whether the command verifies a release signature (check and
    apply do; status and rollback never do). A combination that only that
    verification cannot use, FUTURE_AGI_UPDATE_COSIGN_OFFLINE without a public
    key, is refused only then; every value is still checked on its own.
    """

    environ = os.environ if environ is None else environ
    file_values = read_config_file(root / CONFIG_FILE)
    values: Dict[str, str] = {}
    sources: Dict[str, str] = {}
    for key, default in DEFAULTS.items():
        override = (overrides or {}).get(key)
        if override is not None:
            values[key], sources[key] = override, "command line"
        elif key in environ:
            values[key], sources[key] = environ[key], "environment"
        elif key in file_values:
            values[key], sources[key] = file_values[key], CONFIG_FILE
        else:
            values[key], sources[key] = default, "default"
    settings = Settings(values, sources)
    if settings.policy not in POLICIES:
        raise SettingsError(
            f"FUTURE_AGI_UPDATE_POLICY must be one of {', '.join(POLICIES)}, not {settings.policy!r}"
            f"{settings.origin('FUTURE_AGI_UPDATE_POLICY')}"
        )
    if settings.channel not in CHANNELS:
        raise SettingsError(
            f"FUTURE_AGI_UPDATE_CHANNEL must be one of {', '.join(CHANNELS)}, not {settings.channel!r}"
            f"{settings.origin('FUTURE_AGI_UPDATE_CHANNEL')}"
        )
    settings.integer("FUTURE_AGI_UPDATE_BACKUP_TIMEOUT_SECONDS", 1, 86400)
    settings.integer("FUTURE_AGI_UPDATE_WAIT_TIMEOUT_SECONDS", 60, 7200)
    offline = settings.boolean("FUTURE_AGI_UPDATE_COSIGN_OFFLINE")
    if verify and offline and not settings.get("FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY").strip():
        # Keyless verification (the default) checks the signing certificate
        # against Sigstore's transparency log: it cannot work offline.
        raise SettingsError(
            "FUTURE_AGI_UPDATE_COSIGN_OFFLINE is true"
            f"{settings.origin('FUTURE_AGI_UPDATE_COSIGN_OFFLINE')}, but FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY is not "
            "set: offline verification needs a public key, because keyless verification (the default) checks "
            "Sigstore's transparency log over the network. Set FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY to the public "
            "key the release is signed with, or set FUTURE_AGI_UPDATE_COSIGN_OFFLINE=false"
        )
    url = settings.get("FUTURE_AGI_UPDATE_MANIFEST_URL")
    if "://" in url and not url.startswith("https://"):
        # Show only the scheme: the rest of a URL can carry credentials.
        scheme = url[:url.index("://") + 3]
        raise SettingsError(
            f"FUTURE_AGI_UPDATE_MANIFEST_URL must be an https:// URL or a local file path, not a {scheme!r} URL"
            f"{settings.origin('FUTURE_AGI_UPDATE_MANIFEST_URL')}"
        )
    identity = settings.get("FUTURE_AGI_UPDATE_TRUSTED_IDENTITY")
    if not identity.strip():
        raise SettingsError(
            "FUTURE_AGI_UPDATE_TRUSTED_IDENTITY must not be empty"
            f"{settings.origin('FUTURE_AGI_UPDATE_TRUSTED_IDENTITY')}"
        )
    return settings
