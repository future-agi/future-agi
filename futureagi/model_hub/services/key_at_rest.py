"""Model-hub keys at rest: Fernet ciphertext under INTEGRATION_ENCRYPTION_KEY.

Model-hub provider keys (``ApiKey.key`` and the strings of ``ApiKey.config_json``),
custom-model credentials (``CustomAIModel.key_config``) and dataset secrets
(``SecretModel.key``) are stored the way the gateway's provider credentials
and the org-config secrets are: ``enc::`` plus a Fernet token of
``{"v": key}`` made by ``integrations.services.credentials.CredentialManager``
under ``INTEGRATION_ENCRYPTION_KEY`` (the key Future AGI Cloud sets, the Helm
chart generates and keeps, and the installer fills).

Rules, the same as the org-config secrets (``agentcc.services.org_config_
secrets``):

- **write:** a value a caller sets is a new key and is always encrypted, even
  when it looks like stored ciphertext. Only a value that is unchanged from
  what the row loaded keeps its ciphertext (``encrypt_tree``);
- **read:** ``reveal`` decrypts, where a key is used (a provider call) and to
  build its mask;
  a stored key this deployment cannot decrypt raises ``KeyUnreadable`` (a
  ``ValueError`` naming the key and ``INTEGRATION_ENCRYPTION_KEY``), so a
  caller never sends garbage or falls back to a FutureAGI key;
- **display:** ``mask`` shows the first and last four characters (computed
  in-process; the key and its ciphertext never leave),
  and a placeholder for a key that cannot be decrypted, so the list still
  loads and the key can be replaced;
- **old encoding:** a value in the old encoding (rows not yet migrated, or
  written by an old pod during a deploy) reads back exactly as the old reader
  read it and is encrypted on the row's next save; migration 0125 and the
  ``model_hub_keys_at_rest`` command convert the rest;
- **no stable key:** without ``INTEGRATION_ENCRYPTION_KEY``, with one made up
  per process, or with one Fernet refuses (a placeholder such as
  ``your-fernet-key``), keys are stored in the legacy encoding so existing
  installs keep working; a warning names the variable and the fix
  (at startup and on every save while the key is malformed, once per process
  while it is unset or made up), and ``--check`` reports the rows.
"""

import base64

import structlog
from cryptography.fernet import Fernet
from django.conf import settings

from integrations.services.credentials import CredentialManager

logger = structlog.get_logger(__name__)

ENCRYPTED_PREFIX = "enc::"
UNREADABLE_MASK = "•••• cannot be decrypted, enter this key again"

CIPHERTEXT = "ciphertext"  # enc:: plus a token this deployment decrypts
UNREADABLE = "unreadable"  # enc:: plus a well-formed token it cannot
LEGACY = "legacy"  # anything else: the old encoding, read as the old reader did

_LEGACY_SALT_BYTES = 16
_FERNET_VERSION = b"\x80"
_FERNET_OVERHEAD = 1 + 8 + 16 + 32  # version, timestamp, IV, HMAC
_AES_BLOCK = 16


class KeyUnreadable(ValueError):
    """A stored key this deployment cannot decrypt. Never replaced by another
    key: the caller fails with this message."""

    def __init__(self, what):
        self.what = what
        super().__init__(
            f"The stored {what} cannot be decrypted with this deployment's "
            "INTEGRATION_ENCRYPTION_KEY (it changed, is not a valid Fernet key, "
            "or this process started without it). Enter the key again in "
            "Settings; no other key is used in its place."
        )


# --- one stored string --------------------------------------------------------


# Why this deployment cannot encrypt model-hub keys (``key_problem``).
NO_KEY = "is not set"
EPHEMERAL_KEY = "is made up for this process"
MALFORMED_KEY = "is not a valid Fernet key (32 url-safe base64-encoded bytes)"

# What an operator does about it. Names the variable, never its value.
KEY_FIX = (
    "set INTEGRATION_ENCRYPTION_KEY to a valid Fernet key (the one this "
    "deployment encrypted with, if it had one; otherwise a new one from "
    "`python -c 'from cryptography.fernet import Fernet; "
    "print(Fernet.generate_key().decode())'`), keep it with the database "
    "backups, restart, then run `manage.py model_hub_keys_at_rest --encrypt`"
)


def key_problem():
    """Why this deployment cannot encrypt model-hub keys, or ``None`` when it
    can: ``NO_KEY`` (``INTEGRATION_ENCRYPTION_KEY`` unset), ``EPHEMERAL_KEY``
    (made up for this process by ``settings.py`` on a local install, so
    another process or a restart could not read what it encrypted) or
    ``MALFORMED_KEY`` (set to something Fernet refuses, such as the
    ``your-fernet-key`` placeholder, so encrypting would fail)."""
    key = getattr(settings, "INTEGRATION_ENCRYPTION_KEY", "")
    if not key:
        return NO_KEY
    if getattr(settings, "INTEGRATION_ENCRYPTION_KEY_EPHEMERAL", False):
        return EPHEMERAL_KEY
    try:
        Fernet(key.encode() if isinstance(key, str) else key)
    except (TypeError, ValueError):  # binascii.Error is a ValueError
        return MALFORMED_KEY
    return None


def stable_key():
    """Whether this deployment has a valid encryption key every process
    shares. Without one (``key_problem``) keys are kept in the legacy encoding,
    and ``model_hub_keys_at_rest --check`` reports them."""
    return key_problem() is None


def _legacy_encode(clear):
    """The legacy stored form, so an earlier release (after a rollback) reads it
    and does not wrap it again."""
    from tfc.settings import settings as tfc_settings

    salt = tfc_settings.SECRET_KEY[:_LEGACY_SALT_BYTES].encode()
    return base64.b64encode(salt + clear.encode()).decode()


def encrypt(clear):
    """``enc::`` plus Fernet ciphertext of ``clear``; the old encoding when this
    deployment has no stable key (``stable_key``)."""
    if not stable_key():
        return _legacy_encode(clear)
    blob = CredentialManager.encrypt({"v": clear})
    return ENCRYPTED_PREFIX + (
        blob.decode("ascii") if isinstance(blob, bytes) else blob
    )


_warned_no_stable_key = False


def warn_if_not_encrypted(**where):
    """After a row holding a key is saved: when this deployment has no stable
    key, say the key was stored in the old encoding and how to fix that. On
    every save while the key is malformed (a mistake to fix); once per
    process while it is unset or made up per process (what a local or compose
    install without a key expects). Never logs a key or the variable's
    value."""
    global _warned_no_stable_key
    problem = key_problem()
    if problem is None:
        return
    if problem != MALFORMED_KEY:
        if _warned_no_stable_key:
            return
        _warned_no_stable_key = True
    logger.warning(
        "model_hub_keys_not_encrypted",
        reason=(
            f"INTEGRATION_ENCRYPTION_KEY {problem}; model-hub keys are stored "
            "in the old encoding"
        ),
        fix=KEY_FIX,
        **where,
    )


def warn_at_startup():
    """Once per process start (``ModelHubConfig.ready``): name a malformed
    ``INTEGRATION_ENCRYPTION_KEY`` and the fix. An unset key is what a local
    install expects and is reported on save and by ``--check`` instead."""
    if key_problem() != MALFORMED_KEY:
        return
    logger.warning(
        "model_hub_encryption_key_invalid",
        reason=(
            f"INTEGRATION_ENCRYPTION_KEY {MALFORMED_KEY}; model-hub keys are "
            "stored in the old encoding until it is fixed, and keys stored "
            "encrypted under a valid key cannot be read"
        ),
        fix=KEY_FIX,
    )


def _fernet_shaped(text):
    """Whether ``text`` is structurally a Fernet token (no key needed)."""
    try:
        raw = base64.b64decode(text.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError):  # binascii.Error is a ValueError
        return False
    body = len(raw) - _FERNET_OVERHEAD
    return raw[:1] == _FERNET_VERSION and body >= _AES_BLOCK and body % _AES_BLOCK == 0


def legacy_decode(stored):
    """The legacy reading of ``stored``, or ``stored`` itself when it cannot
    be read."""
    try:
        return base64.b64decode(stored)[_LEGACY_SALT_BYTES:].decode()
    except Exception:
        return stored


def _is_token(text):
    return text.startswith(ENCRYPTED_PREFIX) and _fernet_shaped(
        text[len(ENCRYPTED_PREFIX) :]
    )


def read(stored):
    """``(kind, clear)`` for a stored string; never raises. ``clear`` is
    ``None`` for ``UNREADABLE``. A stored value is decrypted (or read as the
    old reader did) once, never again: one whose clear value is itself an
    ``enc::`` token (a ciphertext typed as a key, or one an earlier release's
    pod wrapped in the old encoding) is ``UNREADABLE``, so it is neither
    decrypted to another row's key nor sent anywhere as a key."""
    kind, clear = _read_once(stored)
    if kind != UNREADABLE and _is_token(clear):
        return UNREADABLE, None
    return kind, clear


def _read_once(stored):
    if stored.startswith(ENCRYPTED_PREFIX):
        token = stored[len(ENCRYPTED_PREFIX) :]
        try:
            data = CredentialManager.decrypt(token.encode("utf-8"))
        except Exception:
            data = None
        if isinstance(data, dict) and isinstance(data.get("v"), str):
            return CIPHERTEXT, data["v"]
        if _fernet_shaped(token):
            return UNREADABLE, None
    return LEGACY, legacy_decode(stored)


def reveal(stored, what):
    """The clear key; ``KeyUnreadable`` when it cannot be decrypted."""
    kind, clear = read(stored)
    if kind == UNREADABLE:
        raise KeyUnreadable(what)
    return clear


# --- a key field: a string, or a JSON key's every string ----------------------


def _pair_map(value, loaded, leaf):
    """``value`` with every string passed through ``leaf(string, stored)``;
    ``stored`` says whether it is the string the row loaded (or last saved) at
    the same place, so a stored value is told from one a caller set."""
    if isinstance(value, dict):
        before = loaded if isinstance(loaded, dict) else {}
        return {k: _pair_map(v, before.get(k), leaf) for k, v in value.items()}
    if isinstance(value, list):
        before = loaded if isinstance(loaded, list) else []
        return [
            _pair_map(v, before[i] if i < len(before) else None, leaf)
            for i, v in enumerate(value)
        ]
    if isinstance(value, str):
        return leaf(value, isinstance(loaded, str) and value == loaded)
    return value


def reveal_tree(value, what, loaded=None):
    """The clear form of a key field: a stored string decrypted (``KeyUnreadable``
    when it cannot be), a string set in memory and not yet saved as it is."""
    return _pair_map(
        value, loaded, lambda s, stored: reveal(s, what) if stored and s else s
    )


def encrypt_tree(value, loaded=None):
    """A key field as it is stored. A string unchanged since the row loaded it
    keeps its ciphertext (one in the old encoding is encrypted from what the
    old reader read); every other string is a new value and is encrypted,
    whatever it looks like. An empty string holds nothing and stays empty."""
    return _pair_map(value, loaded, keep_or_encrypt)


def keep_or_encrypt(value, stored):
    if not value:
        return value
    if stored:
        kind, clear = read(value)
        return encrypt(clear) if kind == LEGACY and stable_key() else value
    return encrypt(value)


# --- display --------------------------------------------------------------------


def mask_text(clear):
    """The mask the key screens show (``mask_key``'s text rule)."""
    if not clear:
        return clear
    if len(clear) <= 8:
        visible = min(4, len(clear))
        return clear[:visible] + "*" * max(len(clear) - visible, 4)
    return clear[:4] + "*" * min(max(len(clear) - 8, 0), 10) + clear[-4:]


def mask_leaf(clear):
    """The mask the key screens show for one JSON value."""
    return clear[0:4] + "*" * 6 + clear[-4:] if clear else clear


def mask(value, loaded=None):
    """The masked display of a key field with the rules ``api_key.mask_key``
    applies (a dict's strings by the leaf rule, a lone string or a
    list item by the text rule); a placeholder for a stored string that cannot
    be decrypted. Never raises, and never returns a key or its ciphertext."""

    def show(s, before, rule):
        if not s:
            return s
        if not (isinstance(before, str) and s == before):
            return rule(s)  # set in memory, not yet saved: already clear
        kind, clear = read(s)
        return UNREADABLE_MASK if kind == UNREADABLE else rule(clear)

    def node(v, before, rule):
        if isinstance(v, str):
            return show(v, before, rule)
        if isinstance(v, dict):
            b = before if isinstance(before, dict) else {}
            return {
                k: (
                    show(x, b.get(k), mask_leaf)
                    if isinstance(x, str)
                    else node(x, b.get(k), mask_text)
                )
                for k, x in v.items()
            }
        if isinstance(v, list):
            b = before if isinstance(before, list) else []
            return [
                node(x, b[i] if i < len(b) else None, mask_text)
                for i, x in enumerate(v)
            ]
        return v

    return node(value, loaded, mask_text)
