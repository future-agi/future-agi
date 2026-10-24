"""Encrypt every model-hub key at rest under INTEGRATION_ENCRYPTION_KEY.

``ApiKey.key``, every string of ``ApiKey.config_json`` and of
``CustomAIModel.key_config``, and ``SecretModel.key`` (the secrets a
dataset's vector-database and API-call columns use) are stored as ``enc::``
plus a Fernet token of ``{"v": key}`` under ``INTEGRATION_ENCRYPTION_KEY``
(``model_hub.services.key_at_rest``); this migration converts the rows
already stored, soft-deleted ones included.

- **Same key after as before.** Each value is read with the legacy reader
  and that is what gets encrypted. A value the legacy reader cannot decode
  is encrypted as stored: no secret stays outside ciphertext.
- **Idempotent.** A value already ``enc::`` plus a token this deployment
  decrypts is left alone, so a re-run (or ``model_hub_keys_at_rest --encrypt``
  after a deploy, for rows an old pod wrote meanwhile) only finishes the rest.
  ``enc::`` alone proves nothing: a value that only starts with it is old-
  encoding data like any other.
- **Unreadable left alone.** A well-formed token this deployment cannot
  decrypt, or a token stored where a key belongs (typed as a key, or wrapped
  in the old encoding by an earlier release's pod that re-saved the row), is
  never encrypted over; the rows are reported by id (never a value) so their
  orgs can enter the key again.
- **Never without a stable key.** With ``INTEGRATION_ENCRYPTION_KEY`` unset,
  made up per process by
  ``settings.py`` on a local install, or set to something Fernet refuses (a
  placeholder such as ``your-fernet-key``), nothing is converted: anything
  encrypted then could not be read by another process or after a restart, or
  could not be encrypted at all. The keys keep working in the old encoding,
  the upgrade goes on, and the operator is told how many rows stay
  unencrypted, what is wrong with the key (never its value), and to set it
  and run the command.
- **Reversible only by an explicit command.** ``backwards`` writes nothing: it
  refuses while any key is encrypted and names ``model_hub_keys_at_rest
  --to-legacy --confirm``, which stores the keys in the legacy encoding for a
  rollback to an earlier release. Then ``migrate`` can go back.
- **Batched** (``atomic = False``): each model is read in primary-key order,
  ``BATCH_SIZE`` rows at a time with ``SELECT ... FOR UPDATE``, rewritten and
  committed in a transaction of its own, so a failure rolls back one batch and
  a re-run finishes the rest; the batch size bounds each transaction.
- **Logs no key.** Output is counts and row ids only.

The decoding, the Fernet check and the columns are inlined here, not imported
from the live code, so this migration keeps treating stored values the same
way if that code changes later.
"""

import base64

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, migrations, transaction
from django.db.models import Q

ENCRYPTED_PREFIX = "enc::"
COMMAND = "model_hub_keys_at_rest"
BATCH_SIZE = 500

# Every column that holds a model-hub key, per model.
KEY_COLUMNS = (
    ("model_hub", "ApiKey", ("key", "config_json")),
    ("model_hub", "CustomAIModel", ("key_config",)),
    ("model_hub", "SecretModel", ("key",)),
)

CIPHERTEXT, UNREADABLE, LEGACY = "ciphertext", "unreadable", "legacy"
_LEGACY_SALT_BYTES = 16
_FERNET_VERSION = b"\x80"
_FERNET_OVERHEAD = 1 + 8 + 16 + 32  # version, timestamp, IV, HMAC
_AES_BLOCK = 16


def _credential_manager():
    from integrations.services.credentials import CredentialManager

    return CredentialManager


def _fernet_shaped(text):
    try:
        raw = base64.b64decode(text.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError):  # binascii.Error is a ValueError
        return False
    body = len(raw) - _FERNET_OVERHEAD
    return raw[:1] == _FERNET_VERSION and body >= _AES_BLOCK and body % _AES_BLOCK == 0


def _legacy_decode(stored):
    """The legacy reading of ``stored``, or ``stored`` itself when it cannot
    be read."""
    try:
        return base64.b64decode(stored)[_LEGACY_SALT_BYTES:].decode()
    except Exception:
        return stored


def _legacy_salt():
    # The legacy encoding's salt, from the settings module.
    from tfc.settings import settings as tfc_settings

    return tfc_settings.SECRET_KEY[:_LEGACY_SALT_BYTES].encode()


def _is_token(text):
    return text.startswith(ENCRYPTED_PREFIX) and _fernet_shaped(
        text[len(ENCRYPTED_PREFIX) :]
    )


def _read(manager, stored):
    """``(kind, clear)``. A value whose clear form is itself an ``enc::``
    token (typed as a key, or wrapped by an earlier release's pod) is
    ``UNREADABLE``: no key can be recovered from it."""
    kind, clear = _read_once(manager, stored)
    if kind != UNREADABLE and _is_token(clear):
        return UNREADABLE, None
    return kind, clear


def _read_once(manager, stored):
    if stored.startswith(ENCRYPTED_PREFIX):
        token = stored[len(ENCRYPTED_PREFIX) :]
        try:
            data = manager.decrypt(token.encode("utf-8"))
        except Exception:
            data = None
        if isinstance(data, dict) and isinstance(data.get("v"), str):
            return CIPHERTEXT, data["v"]
        if _fernet_shaped(token):
            return UNREADABLE, None
    return LEGACY, _legacy_decode(stored)


def _encrypt(manager, clear):
    blob = manager.encrypt({"v": clear})
    return ENCRYPTED_PREFIX + (
        blob.decode("ascii") if isinstance(blob, bytes) else blob
    )


def _walk(value, leaf):
    """``value`` with every non-empty string passed through ``leaf``."""
    if isinstance(value, dict):
        return {k: _walk(v, leaf) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, leaf) for v in value]
    if isinstance(value, str) and value:
        return leaf(value)
    return value


def _leaves(value):
    """Every non-empty string in ``value``."""
    if isinstance(value, dict):
        for v in value.values():
            yield from _leaves(v)
    elif isinstance(value, list):
        for v in value:
            yield from _leaves(v)
    elif isinstance(value, str) and value:
        yield value


def _has_value(fields):
    q = Q()
    for name in fields:
        q |= Q(**{f"{name}__isnull": False})
    return q


def _rewrite(apps, transform, out):
    """Pass every key value through ``transform(value, where) -> new``, one
    batch of rows per transaction; return the number of rows changed."""
    changed = 0
    for app, model_name, fields in KEY_COLUMNS:
        model = apps.get_model(app, model_name)
        rows_of = model._base_manager.db_manager(DEFAULT_DB_ALIAS)
        last_pk = None
        while True:
            with transaction.atomic(using=DEFAULT_DB_ALIAS):
                batch = rows_of.select_for_update().filter(_has_value(fields))
                if last_pk is not None:
                    batch = batch.filter(pk__gt=last_pk)
                rows = list(
                    batch.order_by("pk").values_list("pk", *fields)[:BATCH_SIZE]
                )
                for pk, *values in rows:
                    updates = {}
                    for name, value in zip(fields, values, strict=True):
                        new = (
                            transform(value, (model_name, pk, name)) if value else value
                        )
                        if new != value:
                            updates[name] = new
                    if updates:
                        rows_of.filter(pk=pk).update(**updates)
                        changed += 1
            if len(rows) < BATCH_SIZE:
                break
            last_pk = rows[-1][0]
    return changed


def _say(out):
    return out or print


KEY_UNSET = "is not set"
KEY_EPHEMERAL = "is made up for this process"
KEY_MALFORMED = "is not a valid Fernet key (32 url-safe base64-encoded bytes)"


def key_problem():
    """Why this deployment cannot encrypt the keys (``KEY_UNSET``,
    ``KEY_EPHEMERAL``, ``KEY_MALFORMED``), or ``None`` when it can. Never
    returns or names the key's value."""
    key = getattr(settings, "INTEGRATION_ENCRYPTION_KEY", "")
    if not key:
        return KEY_UNSET
    if getattr(settings, "INTEGRATION_ENCRYPTION_KEY_EPHEMERAL", False):
        return KEY_EPHEMERAL
    try:
        Fernet(key.encode() if isinstance(key, str) else key)
    except (TypeError, ValueError):  # binascii.Error is a ValueError
        return KEY_MALFORMED
    return None


def key_fix():
    """What the operator does when ``key_problem`` is not ``None``."""
    return (
        "Set INTEGRATION_ENCRYPTION_KEY to a valid Fernet key (the one this "
        "deployment encrypted with, if it had one), keep it with the database "
        f"backups, restart, then run `manage.py {COMMAND} --encrypt`."
    )


def forwards(apps, schema_editor, out=None):
    say = _say(out)
    problem = key_problem()
    if problem:
        # No valid key every process shares: encrypting now would fail, or
        # make the keys unreadable to every other process and after a
        # restart, and stopping would block the upgrade. The keys keep working
        # in the old encoding.
        legacy, unreadable, _ = count(apps)
        say(
            f"model_hub keys at rest: {legacy} row(s) left in the old "
            f"encoding: INTEGRATION_ENCRYPTION_KEY {problem}. {key_fix()}"
        )
        if unreadable:
            say(
                f"model_hub keys at rest: {unreadable} row(s) hold a key this "
                "deployment cannot decrypt; they stay as they are."
            )
        return
    manager = _credential_manager()
    unreadable = []

    def encrypt_leaf(where):
        def leaf(stored):
            kind, clear = _read(manager, stored)
            if kind == UNREADABLE:
                unreadable.append(where)
                return stored
            if kind == CIPHERTEXT:
                return stored
            return _encrypt(manager, clear)

        return leaf

    changed = _rewrite(
        apps, lambda value, where: _walk(value, encrypt_leaf(where)), out
    )
    say(f"model_hub keys at rest: {changed} row(s) encrypted.")
    for model_name, pk, name in sorted({(m, str(p), n) for m, p, n in unreadable}):
        say(
            f"model_hub keys at rest: {model_name} {pk} {name} holds no key this "
            "deployment can read (a token it cannot decrypt, or one stored where "
            "a key belongs); left as it is: its org must enter the key again."
        )


def count(apps):
    """``(rows with a value in the old encoding, rows with a value this
    deployment cannot decrypt, rows checked)``; reads only."""
    manager = _credential_manager()
    legacy = unreadable = checked = 0
    for app, model_name, fields in KEY_COLUMNS:
        model = apps.get_model(app, model_name)
        rows_of = model._base_manager.db_manager(DEFAULT_DB_ALIAS)
        for values in (
            rows_of.filter(_has_value(fields))
            .values_list(*fields)
            .iterator(chunk_size=BATCH_SIZE)
        ):
            kinds = {_read(manager, s)[0] for v in values for s in _leaves(v)}
            checked += 1
            legacy += LEGACY in kinds
            unreadable += UNREADABLE in kinds
    return legacy, unreadable, checked


def _encrypted_rows(apps):
    """Rows holding any ``enc::`` token, readable or not (no key needed)."""
    found = 0
    for app, model_name, fields in KEY_COLUMNS:
        model = apps.get_model(app, model_name)
        rows_of = model._base_manager.db_manager(DEFAULT_DB_ALIAS)
        for values in (
            rows_of.filter(_has_value(fields))
            .values_list(*fields)
            .iterator(chunk_size=BATCH_SIZE)
        ):
            found += any(
                s.startswith(ENCRYPTED_PREFIX)
                and _fernet_shaped(s[len(ENCRYPTED_PREFIX) :])
                for v in values
                for s in _leaves(v)
            )
    return found


def to_legacy(apps, out=None):
    """The explicit reverse (``model_hub_keys_at_rest --to-legacy --confirm``):
    every key this deployment decrypts is stored again in the legacy encoding,
    so an earlier release reads it. Only for a rollback."""
    say = _say(out)
    manager = _credential_manager()
    salt = _legacy_salt()
    unreadable = []

    def legacy_leaf(where):
        def leaf(stored):
            kind, clear = _read(manager, stored)
            if kind == UNREADABLE:
                unreadable.append(where)
            if kind != CIPHERTEXT:
                return stored
            return base64.b64encode(salt + clear.encode()).decode()

        return leaf

    changed = _rewrite(apps, lambda value, where: _walk(value, legacy_leaf(where)), out)
    say(f"model_hub keys at rest: {changed} row(s) stored in the old encoding.")
    if unreadable:
        say(
            f"model_hub keys at rest: {len({w[1] for w in unreadable})} row(s) "
            "cannot be decrypted and stay as they are."
        )


def backwards(apps, schema_editor):
    remaining = _encrypted_rows(apps)
    if remaining:
        raise RuntimeError(
            f"{remaining} model-hub key row(s) are encrypted, and an earlier release "
            "cannot read them. Run "
            f"`manage.py {COMMAND} --to-legacy --confirm` first (it stores them in "
            "the legacy encoding), then migrate back."
        )


class Migration(migrations.Migration):
    # One transaction per batch, not one for the whole backfill.
    atomic = False

    dependencies = [
        ("model_hub", "0124_widen_model_hub_key_columns"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
