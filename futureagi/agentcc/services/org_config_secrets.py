"""Org-config infrastructure secrets: encrypted at rest, masked to readers,
decrypted only for the gateway push.

An org config holds credentials for systems outside the gateway: the cache
backends' Redis password, S3 secret access key, Azure connection string and
SAS token and the semantic cache's vector-store API keys; the A2A agents'
tokens; the audit sinks' and alert channels' webhook headers; an alert
channel's URL when it carries a credential (``alert_url_carries_credential``:
a Slack incoming webhook, a token in the path or query); and
the MCP servers' auth (bearer token, API key, credential secret) and
managed-tool credential secrets. Each is a secret leaf under every name the gateway
contract accepts for it (a cache secret's camelCase spelling, the Azure
section's ``azureBlob`` and the MCP section's ``managedTools`` included).
Provider keys and guardrail-check credentials are encrypted elsewhere
(``provider_credential``, the console guardrail policy).

This module is the one place that knows where those secret leaves live. It
stores each one encrypted inline with the same Fernet ``CredentialManager`` as
the rest, under an ``enc::`` prefix, so a config write path (``write_config_
version_with``) can encrypt a new value and keep a stored one, a read path (the
serializer and the diff) can mask it, and the push (``config_push._build_
payload``) can decrypt it. The rules:

- write-only with keep-on-blank: a blank value keeps the stored secret, a mask
  the reader was shown keeps it (resolved by fingerprint: the leaf at the same
  pointer first, else any other leaf holding that stored value for the slot's
  origin, so a reordered list item keeps its own and one value stored for two
  hosts, such as a ``Content-Type`` header or a shared MCP token, saves), an
  unchanged ciphertext is kept, and only a genuinely new value is encrypted
  afresh;
- the origin rule: a secret bound to a URL is kept only for an
  origin (scheme, host and port) it is already stored with, and never attached
  to a URL when it was stored with none; a URL whose origin cannot be read (it
  does not parse) names none, so a secret is kept there only while that URL is
  left exactly as stored, and a leaf stored at it vouches for no other host;
- a mask with nothing behind it, or an MCP secret put straight into the config
  on ``POST org-configs/``, is refused.
"""

import base64
import copy
import hashlib
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

import structlog

from agentcc.services.config_push import OrgConfigRejected
from integrations.services.credentials import CredentialManager

logger = structlog.get_logger(__name__)

# The config sections that can hold an infrastructure secret.
SECRET_SECTIONS = ("cache", "a2a", "audit", "alerting", "mcp")

# Stored ciphertext carries this prefix; a reader is shown the mask instead.
ENCRYPTED_PREFIX = "enc::"
# The mask a reader sees: this stem plus a fingerprint of the stored
# ciphertext, so an unchanged secret masks to the same value (and is not a
# diff) while a rotated one masks differently, and neither reveals the value.
MASK_STEM = "__encrypted__"
_FINGERPRINT_LEN = 16

SECRET_IN_CONFIG = "secret_in_config"
SECRET_NOT_KEPT = "secret_not_kept"
SECRET_ORIGIN_CHANGED = "secret_origin_changed"

_DEFAULT_PORTS = {"http": 80, "https": 443, "ftp": 21}


class SecretNotKept(OrgConfigRejected):
    """A read-back mask names no stored secret, so there is nothing to keep."""

    code = SECRET_NOT_KEPT

    def __init__(self, pointer):
        self.pointer = pointer
        super().__init__(
            f"{pointer} was sent as a placeholder, but no secret is stored "
            "there to keep. Enter the value, or remove the field."
        )


class SecretOriginChanged(OrgConfigRejected):
    """A kept secret's URL moved to another origin, so it is not kept."""

    code = SECRET_ORIGIN_CHANGED

    def __init__(self, pointer, url_pointer):
        self.pointer = pointer
        self.url_pointer = url_pointer
        super().__init__(
            f"{url_pointer} sends the stored secret at {pointer} to a host it "
            "was not stored with (another origin, or a host added to a secret "
            "that had none). Enter the secret again, or clear it, before "
            "changing the host."
        )


class SecretInConfig(OrgConfigRejected):
    """An MCP secret was put straight into the config on a generic write."""

    code = SECRET_IN_CONFIG

    def __init__(self, pointers):
        self.pointers = tuple(pointers)
        super().__init__(
            f"{', '.join(self.pointers)} cannot be set on this endpoint. Save "
            "an MCP server's credential through update-mcp-server (the MCP "
            "page), which stores it write-only."
        )


# --- crypto --------------------------------------------------------------


def _encrypt(clear):
    """``enc::`` plus Fernet ciphertext of ``clear``. Every value is
    encrypted, whatever it starts with, so decrypting the result always
    yields exactly the value that was sent."""
    blob = CredentialManager.encrypt({"v": clear})
    text = blob.decode("utf-8") if isinstance(blob, bytes) else blob
    return ENCRYPTED_PREFIX + text


def _decrypt(blob):
    """The clear value behind an ``enc::`` ciphertext; ``ValueError`` when it
    cannot be decrypted."""
    raw = blob[len(ENCRYPTED_PREFIX) :]
    data = CredentialManager.decrypt(raw.encode("utf-8"))
    if not isinstance(data, dict) or "v" not in data:
        raise ValueError("unexpected secret blob shape")
    return data["v"]


# What a stored secret leaf holds. The ``enc::`` prefix alone proves nothing: a
# stored clear value is any string, one that merely begins with ``enc::``
# included.
CLEAR = "clear"  # a clear value, whatever it starts with
CIPHERTEXT = "ciphertext"  # enc:: plus a blob this deployment decrypts
UNREADABLE = "unreadable"  # enc:: plus a well-formed Fernet token it cannot

_FERNET_VERSION = b"\x80"
_FERNET_OVERHEAD = 1 + 8 + 16 + 32  # version, timestamp, IV, HMAC
_AES_BLOCK = 16


def _fernet_shaped(text):
    """Whether ``text`` is structurally a Fernet token (no key needed): the
    urlsafe-base64 of a version byte, timestamp, IV, whole AES blocks and an
    HMAC. A clear secret is never one by accident."""
    try:
        raw = base64.b64decode(text.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError):  # binascii.Error is a ValueError
        return False
    body = len(raw) - _FERNET_OVERHEAD
    return raw[:1] == _FERNET_VERSION and body >= _AES_BLOCK and body % _AES_BLOCK == 0


def _read_stored(value):
    """``(kind, clear)`` for a stored secret leaf: ``(CIPHERTEXT, the clear
    value)`` when it decrypts to a stored blob, ``(UNREADABLE, None)`` for a
    well-formed token that does not, and ``(CLEAR, value)`` for anything else.
    """
    if not isinstance(value, str) or not value.startswith(ENCRYPTED_PREFIX):
        return CLEAR, value
    try:
        return CIPHERTEXT, _decrypt(value)
    except Exception:
        if _fernet_shaped(value[len(ENCRYPTED_PREFIX) :]):
            return UNREADABLE, None
        return CLEAR, value


def stored_kind(value):
    """``CIPHERTEXT``, ``UNREADABLE`` or ``CLEAR`` (see ``_read_stored``)."""
    return _read_stored(value)[0]


def is_ciphertext(value):
    """Stored ciphertext, readable or not; ``False`` for a clear value, even
    one that starts with ``enc::``."""
    return stored_kind(value) != CLEAR


def _fingerprint(ciphertext):
    return hashlib.sha256(ciphertext.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]


def _mask(ciphertext):
    return MASK_STEM + _fingerprint(ciphertext)


# A placeholder is the bare stem or the exact mask a read returns (the stem
# and the fingerprint). Any other string, one that merely starts with the
# stem included, is a secret value.
_MASK_RE = re.compile(re.escape(MASK_STEM) + f"(?:[0-9a-f]{{{_FINGERPRINT_LEN}}})?")


def is_mask(value):
    return isinstance(value, str) and _MASK_RE.fullmatch(value) is not None


# --- the secret leaves of a config ---------------------------------------


class _Slot:
    """One secret leaf: its JSON pointer, the container that holds it, its key
    there, and the pointer of the URL it is bound to (``None`` when a secret
    is not sent to a URL, e.g. an S3 or Azure key, a managed-tool credential).
    ``url`` is the bound URL's current value.

    A self-bound slot is a URL that is itself the secret (an alert channel's
    credential URL): it is sent to the host it names, so the origin rule has
    nothing to compare, and it keeps only a value stored at another
    self-bound slot. ``owner`` names the entry it belongs to, so a blank
    keeps that entry's own URL, never the one that was at its index (a list
    index moves when an earlier entry is removed): ``(name, rank)``, the
    alert channel's name and how many earlier entries of the list carry the
    same name (the console lets two channels share one), or ``(key, 0)`` for
    a name-keyed entry; ``None`` when the name is not a string."""

    __slots__ = (
        "pointer",
        "_container",
        "_key",
        "url_pointer",
        "url",
        "self_bound",
        "owner",
    )

    def __init__(
        self,
        pointer,
        container,
        key,
        url_pointer=None,
        url=None,
        *,
        self_bound=False,
        owner=None,
    ):
        self.pointer = pointer
        self._container = container
        self._key = key
        self.url_pointer = url_pointer
        self.url = url
        self.self_bound = self_bound
        self.owner = owner

    @property
    def value(self):
        return self._container[self._key]

    def set(self, value):
        self._container[self._key] = value

    def delete(self):
        del self._container[self._key]


def _str_items(container, key):
    """``(key, value)`` for every string under ``container[key]`` when it is a
    dict (e.g. a headers map); nothing otherwise."""
    node = container.get(key) if isinstance(container, Mapping) else None
    if not isinstance(node, Mapping):
        return
    for name, value in list(node.items()):
        if isinstance(value, str):
            yield node, name


def _enumerate(value):
    """``(index-or-key, entry)`` for a list or a name-keyed dict of entries."""
    if isinstance(value, list):
        yield from enumerate(value)
    elif isinstance(value, Mapping):
        yield from value.items()


def _bound_url(container, *keys):
    for key in keys:
        value = container.get(key) if isinstance(container, Mapping) else None
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list) and value and isinstance(value[0], str):
            return value[0]
    return None


# An alert channel URL path segment that is a plain word: lowercase letters,
# joined by "-", "_" or ".", and short. Anything else (a digit, an upper-case
# letter, "%", a long run) can be a token.
_PLAIN_PATH_SEGMENT = re.compile(r"[a-z]+(?:[-_.][a-z]+)*")
_PLAIN_PATH_SEGMENT_MAX = 24


def alert_url_carries_credential(channel_type, url):
    """Whether an alert channel's URL carries a credential, so it is a secret
    leaf.

    Whether a URL holds a token cannot be read from its shape for certain, so
    the rule errs towards secret (a false positive only masks a URL that is
    still delivered):

    - a ``slack`` channel's URL is its incoming-webhook credential;
    - so is any URL that does not parse, or that has a user or password, a
      query or a fragment;
    - and any URL with a path segment that is not a plain word (lowercase
      letters joined by ``-``, ``_`` or ``.``, at most 24 characters): a
      Discord, Mattermost, Teams or Zapier hook, an n8n UUID, a long token.

    So ``https://alerts.example.com/hooks/on-call`` stays readable.
    """
    if not isinstance(url, str) or not url:
        return False
    if isinstance(channel_type, str) and channel_type.strip().lower() == "slack":
        return True
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 (raises ValueError for a bad port)
    except ValueError:
        return True
    if parts.username is not None or parts.password is not None:
        return True
    if parts.query or parts.fragment:
        return True
    return any(
        segment
        and (
            len(segment) > _PLAIN_PATH_SEGMENT_MAX
            or not _PLAIN_PATH_SEGMENT.fullmatch(segment)
        )
        for segment in parts.path.split("/")
    )


def _alert_url_is_slot(channel):
    """Whether an alert channel's ``url`` is a secret leaf: a credential URL
    in clear, stored ciphertext, a read-back mask, or a blank (which keeps a
    stored one)."""
    value = channel.get("url")
    if not isinstance(value, str):
        return False
    if value == "" or is_mask(value) or is_ciphertext(value):
        return True
    return alert_url_carries_credential(channel.get("type"), value)


# The names agentcc.contracts.gateway_admin accepts for each secret field and
# for the sections that hold one (its validation aliases), the snake_case name
# first, as the contract reads it first. The config writer stores a section as
# it was sent, so every spelling, and both spellings of a section sent
# together, is a secret leaf.
_S3_SECRET_KEYS = ("secret_access_key", "secretAccessKey")
_AZURE_SECTIONS = ("azure_blob", "azureBlob")
_AZURE_SECRET_KEYS = ("connection_string", "connectionString", "sas_token", "sasToken")
_API_KEY_KEYS = ("api_key", "apiKey")
_MANAGED_TOOLS_SECTIONS = ("managed_tools", "managedTools")


def iter_secret_slots(sections):
    """Yield a ``_Slot`` for every secret leaf present in ``sections`` (a dict
    keyed by section name). Containers are live, so ``set``/``delete`` write
    through to ``sections``."""
    cache = sections.get("cache")
    if isinstance(cache, Mapping):
        redis = cache.get("redis")
        if isinstance(redis, Mapping) and "password" in redis:
            yield _Slot(
                "/cache/redis/password",
                redis,
                "password",
                "/cache/redis/address",
                _bound_url(redis, "address", "addresses"),
            )
        s3 = cache.get("s3")
        if isinstance(s3, Mapping):
            for key in _S3_SECRET_KEYS:
                if key in s3:
                    yield _Slot(f"/cache/s3/{key}", s3, key)
        for section in _AZURE_SECTIONS:
            azure = cache.get(section)
            if isinstance(azure, Mapping):
                for key in _AZURE_SECRET_KEYS:
                    if key in azure:
                        yield _Slot(f"/cache/{section}/{key}", azure, key)
        semantic = cache.get("semantic")
        if isinstance(semantic, Mapping):
            for backend in ("qdrant", "weaviate", "pinecone"):
                node = semantic.get(backend)
                if not isinstance(node, Mapping):
                    continue
                for key in _API_KEY_KEYS:
                    if key in node:
                        yield _Slot(
                            f"/cache/semantic/{backend}/{key}",
                            node,
                            key,
                            f"/cache/semantic/{backend}/url",
                            _bound_url(node, "url"),
                        )

    a2a = sections.get("a2a")
    if isinstance(a2a, Mapping):
        agents = a2a.get("agents")
        for name, agent in _enumerate(agents):
            if not isinstance(agent, Mapping):
                continue
            auth = agent.get("auth")
            url_pointer = f"/a2a/agents/{name}/url"
            url = _bound_url(agent, "url")
            for key in ("token", "key"):
                if isinstance(auth, Mapping) and key in auth:
                    yield _Slot(
                        f"/a2a/agents/{name}/auth/{key}",
                        auth,
                        key,
                        url_pointer,
                        url,
                    )

    audit = sections.get("audit")
    if isinstance(audit, Mapping):
        for idx, sink in _enumerate(audit.get("sinks")):
            if not isinstance(sink, Mapping):
                continue
            url = _bound_url(sink, "url")
            for node, name in _str_items(sink, "headers"):
                yield _Slot(
                    f"/audit/sinks/{idx}/headers/{name}",
                    node,
                    name,
                    f"/audit/sinks/{idx}/url",
                    url,
                )

    alerting = sections.get("alerting")
    if isinstance(alerting, Mapping):
        channels = alerting.get("channels")
        # How many entries of each name the walk has passed: a list entry is
        # owned by its name and that count, since two channels may share a
        # name (every entry counts, whether or not its URL is a secret, so a
        # rank names the same channel in the stored list and the new one).
        # A name-keyed entry's pointer is its name already.
        ranks = {}
        for idx, channel in _enumerate(channels):
            if not isinstance(channel, Mapping):
                continue
            name = channel.get("name") if isinstance(channels, list) else idx
            owner = None
            if isinstance(name, str):
                owner = (name, ranks.get(name, 0))
                ranks[name] = owner[1] + 1
            if _alert_url_is_slot(channel):
                # Yielded before the headers, so a write path has resolved
                # (kept or encrypted) the URL by the time the headers below
                # read it for the origin rule.
                yield _Slot(
                    f"/alerting/channels/{idx}/url",
                    channel,
                    "url",
                    f"/alerting/channels/{idx}/url",
                    None,
                    self_bound=True,
                    owner=owner,
                )
            url = _bound_url(channel, "url")
            for node, name in _str_items(channel, "headers"):
                yield _Slot(
                    f"/alerting/channels/{idx}/headers/{name}",
                    node,
                    name,
                    f"/alerting/channels/{idx}/url",
                    url,
                )

    yield from _iter_mcp_slots(sections.get("mcp"))


def _iter_mcp_slots(mcp):
    if not isinstance(mcp, Mapping):
        return
    servers = mcp.get("servers")
    for server_id, server in _enumerate(servers):
        if not isinstance(server, Mapping):
            continue
        url_pointer = f"/mcp/servers/{server_id}/url"
        url = _bound_url(server, "url")
        auth = server.get("auth")
        for key in ("token", "key"):
            if isinstance(auth, Mapping) and key in auth:
                yield _Slot(
                    f"/mcp/servers/{server_id}/auth/{key}",
                    auth,
                    key,
                    url_pointer,
                    url,
                )
        credential = server.get("credential")
        secret = credential.get("secret") if isinstance(credential, Mapping) else None
        if isinstance(secret, Mapping) and "value" in secret:
            yield _Slot(
                f"/mcp/servers/{server_id}/credential/secret/value",
                secret,
                "value",
                url_pointer,
                url,
            )
    for section in _MANAGED_TOOLS_SECTIONS:
        managed = mcp.get(section)
        credentials = (
            managed.get("credentials") if isinstance(managed, Mapping) else None
        )
        for cred_id, entry in _enumerate(credentials):
            secret = entry.get("secret") if isinstance(entry, Mapping) else None
            if isinstance(secret, Mapping) and "value" in secret:
                yield _Slot(
                    f"/mcp/{section}/credentials/{cred_id}/secret/value",
                    secret,
                    "value",
                )


def _sections_of(config):
    return {name: getattr(config, name) for name in SECRET_SECTIONS}


# --- origin rule ---------------------------------------------------------


def _origin(url):
    """``(scheme, host, port)`` of a URL or a bare ``host:port`` authority
    (a Redis address has no scheme), default ports filled in, scheme and host
    case-folded; ``None`` when it does not parse or names no host."""
    if not isinstance(url, str) or not url:
        return None
    text = url.strip()
    try:
        parts = urlsplit(text)
        if not parts.scheme or not parts.hostname:
            # A bare "host:port" (Redis): parse it as an authority.
            parts = urlsplit("//" + text)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    scheme = (parts.scheme or "").lower()
    return scheme, host, port or _DEFAULT_PORTS.get(scheme)


# --- encrypt (write path) ------------------------------------------------


def encrypt_config_secrets(config, active):
    """Store every secret leaf of ``config``'s sections encrypted, applying
    keep-on-blank, the fingerprint-resolved mask, the unchanged-ciphertext
    keep and the origin rule against ``active`` (the version being replaced).
    Mutates ``config`` in place. Raises ``SecretNotKept`` or
    ``SecretOriginChanged``. Run inside the config write, under the org lock."""
    stored = _sections_of(active) if active is not None else {}
    by_pointer = {}  # pointer -> (stored value, bound_url)
    # Fingerprint of the mask -> every leaf holding that stored value, as
    # (stored value, bound_url, pointer, self_bound) in walk order: one value
    # can sit at several leaves (a Content-Type header on two alert channels,
    # one MCP token for servers on two hosts), and migration 0047 stores it
    # there as one ciphertext per org, so each of those leaves reads back the
    # same mask.
    by_fingerprint = {}
    # Each self-bound slot's stored (value, url) by its owner, an alert
    # channel's (name, rank), for a blank whose list index moved. One entry
    # per channel: two channels that share a name differ in rank.
    self_bound_by_owner = {}
    for slot in iter_secret_slots(stored):
        # Record every stored leaf, ciphertext or clear, by pointer and, beside
        # every other leaf holding the same value, by the fingerprint a reader
        # was shown, so keep-on-blank and a read-back mask work either way and
        # a clear value is encrypted the next time it is kept.
        if isinstance(slot.value, str) and slot.value:
            by_pointer[slot.pointer] = (slot.value, slot.url)
            by_fingerprint.setdefault(_fingerprint(slot.value), []).append(
                (slot.value, slot.url, slot.pointer, slot.self_bound)
            )
            if slot.self_bound and slot.owner is not None:
                self_bound_by_owner[slot.owner] = (slot.value, slot.url)

    for slot in iter_secret_slots(_sections_of(config)):
        value = slot.value
        if not isinstance(value, str):
            # A non-string in a secret field is left to contract validation.
            continue
        if value == "":
            if slot.self_bound and slot.owner is not None:
                # An alert channel keeps its own URL after the list moved, and
                # of two channels that share a name the k-th keeps the URL
                # stored for the k-th. An owner stored with no secret (a plain
                # or blank URL) or not stored at all (a new channel) keeps
                # nothing, never another channel's URL.
                kept = self_bound_by_owner.get(slot.owner)
            else:
                kept = by_pointer.get(slot.pointer)
            if kept is None:
                if not slot.self_bound:
                    slot.delete()  # nothing stored: the option is cleared
                # A blank alert URL with nothing stored stays the blank it was
                # (a new channel's, a log channel's): no URL either way.
                continue
            _keep(slot, kept)
        elif is_mask(value):
            _keep(
                slot,
                _stored_for_mask(slot, by_fingerprint, value[len(MASK_STEM) :]),
            )
        elif (at := by_pointer.get(slot.pointer)) and at[0] == value:
            # Unchanged: a merge left the stored value (ciphertext, or a clear
            # one) in place. The pointer alone decides, for a self-bound slot
            # too: no read path returns ciphertext, so a value that is byte for
            # byte the one stored at this pointer came from the stored row,
            # whichever entry now sits there. It is kept verbatim, never
            # wrapped in a second layer.
            _keep(slot, at)
        else:
            slot.set(_encrypt(value))


def _origin_allows(slot, stored_url):
    """Whether the origin rule lets ``slot`` keep a secret stored with
    ``stored_url``. A slot bound to no URL, or whose URL is unset, sends it to
    no host, so it keeps. Otherwise the stored URL must vouch for the slot's:
    the same string, or both name an origin (scheme, host and port) and it is
    the same one."""
    if slot.self_bound:
        # The secret is the URL: it goes to the host it names, which is the
        # value kept, not another one.
        return True
    if slot.url_pointer is None or slot.url is None:
        return True
    # A secret stored with no bound URL (the S3 key, the Azure strings, a
    # managed-tool secret) is kept only where it is sent to no host: it is
    # never attached to a URL on a keep.
    if stored_url is None:
        return False
    if stored_url == slot.url:
        return True
    # A URL _origin cannot read (a port out of range, an unbalanced IPv6
    # bracket, a bracketed host name that is not an IP address; the contract's
    # URLs are plain strings, so one can be stored) names no origin: a secret
    # is not moved to it, and a leaf stored at it vouches for no other host.
    # A bound URL that is itself a secret (an alert channel's credential URL)
    # is stored encrypted: the origin rule reads the clear value on both sides.
    stored_origin = _origin(clear_url(stored_url))
    return stored_origin is not None and stored_origin == _origin(clear_url(slot.url))


def clear_url(value):
    """A bound URL as the gateway gets it: stored ciphertext decrypted, an
    unreadable token as nothing (it names no origin), anything else as it
    is."""
    kind, clear = _read_stored(value)
    if kind == CIPHERTEXT:
        return clear
    if kind == UNREADABLE:
        return None
    return value


def _stored_for_mask(slot, by_fingerprint, fingerprint):
    """The stored ``(value, url)`` a read-back mask keeps at ``slot``.

    Every leaf holding the masked value is a candidate, the one at the slot's
    own pointer first, then the rest in walk order; the first whose stored URL
    the origin rule lets the slot keep is it. The same fingerprint is the same
    stored value, so a candidate at another leaf is the same secret, already
    sent to that origin: a reordered list item keeps its own, and one value
    stored for two hosts keeps at each. Raises ``SecretNotKept`` when nothing
    is stored behind the mask, and ``SecretOriginChanged`` when the value is
    stored only for other origins (or with no URL, for a URL-bound slot)."""
    # A URL that is the secret keeps only a value stored as such a URL, and
    # no other slot keeps one: an alert URL's mask pasted into a token field,
    # or a token's mask pasted as an alert URL, has nothing to keep.
    candidates = [
        c for c in by_fingerprint.get(fingerprint) or () if c[3] == slot.self_bound
    ]
    if not candidates:
        raise SecretNotKept(slot.pointer)
    own = [c for c in candidates if c[2] == slot.pointer]
    others = [c for c in candidates if c[2] != slot.pointer]
    for value, url, _pointer, _self_bound in own + others:
        if _origin_allows(slot, url):
            return value, url
    raise SecretOriginChanged(slot.pointer, slot.url_pointer)


def _keep(slot, kept):
    stored_value, stored_url = kept
    if not _origin_allows(slot, stored_url):
        raise SecretOriginChanged(slot.pointer, slot.url_pointer)
    # A stored clear value (any string, one starting with "enc::" included) is
    # encrypted as it is kept; stored ciphertext is kept verbatim
    # (re-encrypting it would change the ciphertext and so the mask, making
    # every save a spurious diff), and an unreadable token is kept as it is,
    # never wrapped again.
    slot.set(stored_value if is_ciphertext(stored_value) else _encrypt(stored_value))


# --- mask (read paths) ---------------------------------------------------


def masked_section(name, value):
    """A deep copy of one section with every secret leaf replaced by its mask;
    the section unchanged when it holds no secret or is not one that can."""
    if name not in SECRET_SECTIONS or not isinstance(value, Mapping):
        return value
    sections = {name: copy.deepcopy(value)}
    for slot in iter_secret_slots(sections):
        if isinstance(slot.value, str) and slot.value:
            slot.set(_mask_value(slot.value))
    return sections[name]


def mask_config_data(data):
    """Mask every secret section of a serialized org config and add
    ``secrets_set``: the pointers of the leaves a secret is stored at. Each
    section is deep-copied before masking, so the model instance the
    serializer read is never mutated."""
    set_pointers = []
    for name in SECRET_SECTIONS:
        if name in data and isinstance(data[name], Mapping):
            section = {name: copy.deepcopy(data[name])}
            for slot in iter_secret_slots(section):
                if isinstance(slot.value, str) and slot.value:
                    set_pointers.append(slot.pointer)
                    slot.set(_mask_value(slot.value))
            data[name] = section[name]
    data["secrets_set"] = sorted(set_pointers)
    return data


def _mask_value(value):
    # A stored ciphertext masks by its own fingerprint; a stored clear value is
    # masked too, never shown.
    return _mask(value)


# --- decrypt (push path) -------------------------------------------------


def decrypt_payload_secrets(payload, *, org_id=None):
    """Decrypt every stored-ciphertext secret leaf of a built gateway payload
    in place, so the push carries clear values. A clear value (even one that
    starts with ``enc::``) is sent as it is. A well-formed token that cannot be
    decrypted is dropped and logged by path and org (never its value), as the
    guardrail push does, so one unreadable secret never fails the org's whole
    config."""
    sections = {name: payload.get(name) for name in SECRET_SECTIONS}
    for slot in iter_secret_slots(sections):
        kind, clear = _read_stored(slot.value)
        if kind == CIPHERTEXT:
            slot.set(clear)
        elif kind == UNREADABLE:
            slot.delete()
            logger.warning(
                "org_config_secret_undecryptable",
                org_id=str(org_id) if org_id is not None else None,
                path=slot.pointer,
            )
    return payload


# --- MCP secret refusal (POST org-configs) -------------------------------

_MCP_SECRET_PREFIXES = ("/mcp/servers/", "/mcp/managed_tools/", "/mcp/managedTools/")


def new_clear_mcp_secret_paths(mcp_value, stored_mcp):
    """The MCP secret pointers a ``POST org-configs/`` write sets to a new
    clear value: not blank, not a mask, not the stored ciphertext. A kept or
    rotated secret saved through update-mcp-server is encrypted there; this
    endpoint stores no new MCP secret."""
    stored = {}
    for slot in iter_secret_slots(
        {"mcp": stored_mcp if isinstance(stored_mcp, Mapping) else {}}
    ):
        if is_ciphertext(slot.value):
            stored[slot.pointer] = slot.value
    new = []
    for slot in iter_secret_slots(
        {"mcp": mcp_value if isinstance(mcp_value, Mapping) else {}}
    ):
        value = slot.value
        if not isinstance(value, str) or value == "" or is_mask(value):
            continue
        if stored.get(slot.pointer) == value:
            continue
        new.append(slot.pointer)
    return sorted(new)
