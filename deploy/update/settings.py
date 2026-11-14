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

from .contracts import (AUTOMATIC_POLICY_BOUNDS, QUALIFICATION_POLICY, AutomationPolicy,
                        ContractError, MaintenanceWindowSpec, POLICY_BOUNDS)
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

# Host-only policy. FUTURE_AGI_UPDATE_MODE is NOT a setting: existing backup
# hooks receive that name as the installation topology. Durable controls in a
# later runner override configured enabled/pause; this loader does not read or
# write those controls and does not install a scheduler.
AUTOMATION_DEFAULTS: Dict[str, str] = {
    "FUTURE_AGI_UPDATE_AUTOMATION_MODE": "manual",
    "FUTURE_AGI_UPDATE_ENABLED": "false",
    "FUTURE_AGI_UPDATE_INTERVAL_SECONDS": "21600",
    "FUTURE_AGI_UPDATE_MAX_BACKOFF_SECONDS": "86400",
    "FUTURE_AGI_UPDATE_TIMEZONE": "UTC",
    "FUTURE_AGI_UPDATE_MAINTENANCE_WINDOW": "",
    "FUTURE_AGI_UPDATE_MAINTENANCE_DAYS": "MON,TUE,WED,THU,FRI,SAT,SUN",
    "FUTURE_AGI_UPDATE_MIN_START_BUDGET_SECONDS": "900",
    "FUTURE_AGI_UPDATE_BACKUP_MAX_AGE_SECONDS": "3600",
    "FUTURE_AGI_UPDATE_STAGE_MAX_AGE_SECONDS": "86400",
    "FUTURE_AGI_UPDATE_AUTOMATIC_ROLLBACK": "false",
    "FUTURE_AGI_UPDATE_PROBE_TIMEOUT_SECONDS": "60",
    "FUTURE_AGI_UPDATE_PULL_TIMEOUT_SECONDS": "1800",
    "FUTURE_AGI_UPDATE_TICK_TIMEOUT_SECONDS": "14400",
    "FUTURE_AGI_UPDATE_QUALIFICATION_POLICY": "",
    "FUTURE_AGI_UPDATE_RESTORE_TIMEOUT_SECONDS": "3600",
    "FUTURE_AGI_UPDATE_RECOVERY_STARTUP_TIMEOUT_SECONDS": "1200",
    "FUTURE_AGI_UPDATE_STABILITY_PERIOD_SECONDS": "60",
    "FUTURE_AGI_UPDATE_QUIESCENCE_RESERVE_SECONDS": "300",
    "FUTURE_AGI_UPDATE_METADATA_RESERVE_SECONDS": "300",
    "FUTURE_AGI_UPDATE_ESTIMATE_SAFETY_FACTOR": "2",
    "FUTURE_AGI_UPDATE_MIGRATION_TIMEOUT_SECONDS": "1200",
    "FUTURE_AGI_UPDATE_MIGRATION_STALL_TIMEOUT_SECONDS": "300",
    "FUTURE_AGI_UPDATE_DISK_WATERMARK_RESERVE_BYTES": "1073741824",
}
_WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_WINDOW_RE = re.compile(r"((?:[01][0-9]|2[0-3]):[0-5][0-9])-((?:[01][0-9]|2[0-3]):[0-5][0-9])")
# Shared field names and bounds also validate direct AutomationPolicy callers.
_POLICY_KEYS = {
    name: "FUTURE_AGI_UPDATE_" + ("INTERVAL_SECONDS" if name == "check_interval_seconds" else name.upper())
    for name in POLICY_BOUNDS
}

DEFAULTS: Dict[str, str] = {
    **AUTOMATION_DEFAULTS,
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
        # Bound conversion on all supported Pythons, including 3.9 without an
        # interpreter digit limit; retain legacy leading-zero support.
        digits = raw.lstrip("0") or "0"
        if len(digits) > max(len(str(minimum)), len(str(maximum))):
            raise SettingsError(f"{key} must be between {minimum} and {maximum}, not {raw}{self.origin(key)}")
        value = int(digits)
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
    def automation_policy(self) -> AutomationPolicy:
        """Immutable configured policy, not effective durable controls or eligibility.

        Window grammar is empty (automatic apply blocked), ``always``, or
        HH:MM-HH:MM; weekdays are unique MON..SUN start days (ISO 1..7).
        Timezone names require local zoneinfo; no fallback or package install.
        Parsing a window does not evaluate DST or authorize a start.
        """
        mode_key = "FUTURE_AGI_UPDATE_AUTOMATION_MODE"
        mode = self.get(mode_key)
        if mode not in ("manual", "notify", "download", "apply"):
            raise SettingsError(f"{mode_key} must be manual, notify, download or apply{self.origin(mode_key)}")
        numbers = {name: self.integer(key, *POLICY_BOUNDS[name]) for name, key in _POLICY_KEYS.items()}
        qualification = self.get('FUTURE_AGI_UPDATE_QUALIFICATION_POLICY')
        if qualification not in ('', QUALIFICATION_POLICY):
            raise SettingsError('FUTURE_AGI_UPDATE_QUALIFICATION_POLICY must be empty or ' + QUALIFICATION_POLICY)
        numbers.update({name: self.integer('FUTURE_AGI_UPDATE_' + name.upper(), *bounds)
                        for name, bounds in AUTOMATIC_POLICY_BOUNDS.items()})
        if qualification and numbers['migration_stall_timeout_seconds'] > numbers['migration_timeout_seconds']:
            raise SettingsError('FUTURE_AGI_UPDATE_MIGRATION_STALL_TIMEOUT_SECONDS must not exceed '
                                'FUTURE_AGI_UPDATE_MIGRATION_TIMEOUT_SECONDS')
        if numbers["max_backoff_seconds"] < numbers["check_interval_seconds"]:
            key = "FUTURE_AGI_UPDATE_MAX_BACKOFF_SECONDS"
            raise SettingsError(f"{key} must be >= FUTURE_AGI_UPDATE_INTERVAL_SECONDS{self.origin(key)}")
        days_key = "FUTURE_AGI_UPDATE_MAINTENANCE_DAYS"
        days = self.get(days_key).split(",")
        if any(day not in _WEEKDAYS for day in days) or len(set(days)) != len(days):
            raise SettingsError(f"{days_key} must be unique comma-separated MON..SUN start days{self.origin(days_key)}")
        zone_key = "FUTURE_AGI_UPDATE_TIMEZONE"
        zone = self.get(zone_key)
        try:
            # Validate even when the window is unset, so enabling later cannot
            # silently switch an invalid zone to UTC.
            always = MaintenanceWindowSpec("always", zone)
        except ContractError:
            raise SettingsError(f"{zone_key} requires a valid locally available IANA timezone"
                                f"{self.origin(zone_key)}; install system tzdata if needed") from None
        window_key = "FUTURE_AGI_UPDATE_MAINTENANCE_WINDOW"
        raw = self.get(window_key)
        window = None
        if raw == "always":
            window = always
        elif raw:
            match = _WINDOW_RE.fullmatch(raw)
            if match is None or match[1] == match[2]:
                raise SettingsError(f"{window_key} must be empty, always, or HH:MM-HH:MM with distinct endpoints"
                                    f"{self.origin(window_key)}")
            window = MaintenanceWindowSpec("interval", zone, tuple(_WEEKDAYS.index(day) + 1 for day in days),
                                           match[1], match[2])
        try:
            return AutomationPolicy(
                mode=mode, enabled=self.boolean("FUTURE_AGI_UPDATE_ENABLED"), channel=self.channel,
                version_policy=self.policy, timezone=zone, maintenance_window=window,
                automatic_rollback=self.boolean("FUTURE_AGI_UPDATE_AUTOMATIC_ROLLBACK"),
                qualification_policy=qualification or None, **numbers,
            )
        except ContractError:
            key = "FUTURE_AGI_UPDATE_TICK_TIMEOUT_SECONDS"
            if qualification:
                raise SettingsError(f'{key} must cover pull + backup + migration + target startup + '
                                    'restore + recovery startup + 2*(stability + quiescence + metadata)'
                                    f'{self.origin(key)}') from None
            raise SettingsError(f"{key} is too small for scheduled apply: require backup + pull + 2*wait + 600;"
                                " with automatic rollback require backup + 2*pull + 4*wait + 1200"
                                f"{self.origin(key)}") from None

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
    # Unknown ambient keys remain ignored for legacy hook/environment
    # compatibility; explicit configuration and CLI overrides are closed.
    if overrides is not None and any(key not in DEFAULTS for key in overrides):
        raise SettingsError("unknown setting in command line overrides")
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
    for key, value in values.items():
        if type(value) is not str:
            raise SettingsError(f"{key} must be text (from {sources[key]})")
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
    # Validate all configured automation inputs even for local/status commands.
    # This only parses values and consults local tzdata, never Docker/network.
    _ = settings.automation_policy
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
