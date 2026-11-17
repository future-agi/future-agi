"""The API secret pepper rule bin/update shares with bin/install.

A Standalone or Distributed install made before bin/install generated a pepper
(v1.58.0) has no FI_API_SECRET_PEPPER in .env, so Compose hands the app and the
collector the default printed in the repository. bin/install replaces such a
pepper (bin/lib/secrets.sh rotate_published_api_secret_pepper); bin/update
applies the same rule before an update (apply.Updater), so the release that
hashes API secrets never seals them under a value anyone can read. One case
table (deploy/tests/test_update_pepper_step.py) runs against both rules.

The rule: an unset, empty, published-default or CHANGEME- pepper is replaced by
a new 64-hex value, and FI_API_SECRET_PEPPERS_RETIRED becomes its own entries
plus the value Compose used until now (the published default when there was
none), each trimmed and kept once, in first-occurrence order, so keys sealed
under that value still verify. The previous value is always retired: a retired
value no hash names costs nothing, and the operator drops it once
`manage.py api_secrets status` reports `hashed_under_retired_pepper: 0`. Any
other pepper is the operator's and never changes. An entry that cannot be
written as a single-quoted literal (a single quote, two backslashes, a trailing
backslash) is refused rather than guessed at.

Nothing here writes a file or prints a value: messages name variables only.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import envfile

PEPPER = "FI_API_SECRET_PEPPER"
RETIRED = "FI_API_SECRET_PEPPERS_RETIRED"
NAMES = (PEPPER, RETIRED)
# The default the compose files publish; CHANGEME- marks a placeholder to replace.
PUBLISHED = "local-dev-only-api-secret-pepper-replace-me"
PLACEHOLDER_PREFIX = "CHANGEME-"
_NEW = re.compile(r"[0-9a-f]{64}")
# What bash's [[:space:]] trims off each retired entry (str.strip() also takes \x1c-\x1f).
_TRIM = " \t\n\v\f\r"


class PepperRefused(ValueError):
    """The rule cannot be applied without guessing; the message names variables only."""


def new_pepper() -> str:
    return secrets.token_hex(32)


def replaced(value: Optional[str]) -> bool:
    """Whether the rule replaces this pepper (None: unset)."""

    return not value or value == PUBLISHED or value.startswith(PLACEHOLDER_PREFIX)


def retired_list(current: Optional[str], retired: Optional[str]) -> str:
    """FI_API_SECRET_PEPPERS_RETIRED once `current`, the pepper being replaced, joins it."""

    entries: List[str] = []
    for part in f"{retired or ''},{current or PUBLISHED}".split(","):
        part = part.strip(_TRIM)
        if not part or part in entries:
            continue
        if "'" in part or "\\\\" in part or part.endswith("\\"):
            raise PepperRefused(
                f"an entry of {RETIRED} cannot be kept as written (it holds a single quote "
                f"or an escaped backslash), so {PEPPER} cannot be replaced safely. Remove "
                f"or rewrite that entry, or set {PEPPER} by hand")
        entries.append(part)
    return ",".join(entries)


def rotation(current: Optional[str], retired: Optional[str],
             generate: Callable[[], str] = new_pepper) -> Optional[Dict[str, str]]:
    """The two new values, retired list first as bin/install writes them; None: no change."""

    if not replaced(current):
        return None
    entries = retired_list(current, retired)
    value = generate()
    if not isinstance(value, str) or not _NEW.fullmatch(value):
        raise PepperRefused(f"could not generate a new {PEPPER}")
    return {RETIRED: entries, PEPPER: value}


def values(text: str) -> Tuple[Optional[str], Optional[str]]:
    """The pepper and the retired list as Compose reads them from env file text.

    None for an unset one; the last definition wins. Refused, as bin/install's
    reader refuses them, wherever only Compose itself could settle what a
    definition of either key means: a "$" outside single quotes
    (interpolation), a backslash inside double quotes (escapes), two
    backslashes inside single quotes, a carriage return, or a quote that is
    never closed.
    """

    found: Dict[str, str] = {}
    for definition in envfile._definitions(text):
        if definition.key not in NAMES:
            continue
        value = definition.value
        raw = text[definition.token:definition.token_end]
        if ((definition.quote != "'" and "$" in value)
                or (definition.quote == '"' and "\\" in raw)
                or (definition.quote == "'" and "\\\\" in value)
                or "\r" in value
                or (definition.quote == "" and value[:1] in ("'", '"'))):
            raise PepperRefused(
                f"cannot safely read {PEPPER} / {RETIRED} from the env file: write each as "
                "one single-line literal value (no interpolation, escapes or unclosed quotes)")
        found[definition.key] = value
    return found.get(PEPPER), found.get(RETIRED)


def read_values(path: Path) -> Tuple[Optional[str], Optional[str]]:
    """values() of the env file at `path` (an EnvFileError if it cannot be read)."""

    return values(envfile._read_text(path, path))


def exported(environ: Mapping[str, str]) -> List[str]:
    """Which of the two names the process environment holds at all, even empty."""

    return [name for name in NAMES if name in environ]


def exported_text(names: Sequence[str], env_name: str) -> str:
    """Why bin/update refuses a shell export of either name, and what to do: names only."""

    listed = " and ".join(names)
    verb, them = ("is", "it") if len(names) == 1 else ("are", "them")
    return (f"{listed} {verb} set in your shell, and Docker Compose prefers {them} to "
            f"{env_name}, so the update would run with a pepper {env_name} does not keep. "
            f"If the stack runs with the exported value, put it in {env_name} first "
            f"({PEPPER}=<that value>, or add it to {RETIRED}); then run "
            f"`unset {' '.join(names)}` and run bin/update again. Nothing was changed")


def production_text(env_name: str, setup_command: Optional[str]) -> str:
    """Why bin/update --production refuses before planning, naming deploy/setup.sh.

    `setup_command` is the setup.sh command for this checkout, or None when
    the env file is not the deploy/.env.production that setup.sh edits.
    """

    first = (f"{env_name} has no {PEPPER} of its own (it is unset, empty, the published "
             "default or a CHANGEME- placeholder), and the production Compose files "
             "require one, so nothing can be planned")
    if setup_command is not None:
        how = (f"Run `{setup_command}`: it sets a new one and keeps the previous value in "
               f"{RETIRED}, the way bin/update does for other installs")
    else:
        how = (f"Set a new one in {env_name} (`openssl rand -hex 32`) and add the previous "
               f"value to {RETIRED}, as deploy/setup.sh does for deploy/.env.production")
    return f"{first}. {how}; then run bin/update again. Nothing was changed"


def set_text(env_name: str) -> str:
    """What apply says once the step's env commit is accepted: names only."""

    return (f"Set {PEPPER} to a new value and kept the previous one in {RETIRED}, in a "
            f"change of {env_name} of its own made before the backup; API keys sealed under "
            "the previous value still verify")
