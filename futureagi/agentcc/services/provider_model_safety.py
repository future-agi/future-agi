"""Shared credential exclusion with a caller-key discovery-only grammar.

Manual persistence, saved-list projection and saved-key listing do not use G.
They still validate credential context and exclude credential-bearing IDs.

G: 1..256 ASCII characters; 1..6 nonempty /-separated segments, each
1..64 characters; each segment is 1..16 alphanumeric atoms separated by one
of . _ : @ -. Every atom is 1..10 characters. No credential introducer may
occur anywhere, except the finite preexisting compatibility names below.
This is a gateway allowlist, not a claim about vendor maximum lengths.

Secret exclusion is mandatory even for public catalog IDs, before and after
Google's discovery-only models/ normalization. Unknown secret types fail
closed, never disappear from comparison context. No rejected values escape.
"""

import base64
import re
from collections.abc import Mapping

_CONTEXT_ERROR = "Provider credential context is invalid. Rotate the credential."
_ATOM = re.compile(r"[A-Za-z0-9]{1,10}\Z", re.ASCII)
_SEGMENT_SEPARATORS = re.compile(r"[._:@-]", re.ASCII)
_COMPACT = re.compile(r"[^a-z0-9]", re.ASCII)
# Routing metadata shares the credential envelope but is not key material.
# Unknown fields remain secret; only these named scalar fields are exempt.
_PUBLIC_CREDENTIAL_METADATA = frozenset({"region", "aws_region"})
_RESERVED = (
    "sk-",
    "sk_",
    "gsk_",
    "pplx-",
    "aiza",
    "hf_",
    "xai-",
    "nvapi-",
    "fw_",
    "r8_",
    "akia",
    "asia",
    "eyj",
    "bearer",
    "basic",
)
# Exact compatibility names, not wildcard prefix exemptions. task-model is the
# inherited custom-ID control (its ordinary word ends with the letters sk).
_COMPATIBILITY_NAMES = frozenset(
    {"pplx-decider-v1.1-27b", "pplx-decider-v1-27b", "task-model"}
)


_ABSENT = object()


class InvalidProviderCredentialContext(ValueError):
    """Value-free exception: never attach credential objects or decoder errors."""

    code = "provider_credential_context_invalid"

    def __init__(self):
        super().__init__(_CONTEXT_ERROR)


def credential_context_strings(values, *, include_public_metadata=True):
    """Validate every leaf before transport, projection, or persistence.

    Only strings and JSON-like containers are supported. Bytes (including
    undecodable bytes), numbers, bool and null are refused, not coerced or
    ignored. Thus a numeric credential can never be interpolated as decimal
    header text. Empty strings/containers represent absent optional fields.

    Secret-marker generation omits string-valued routing metadata, but still
    validates it. Containers under metadata keys are not exempt: their leaves
    are classified independently, just like any other legacy container.
    """
    pending = [(values, 0, True)]
    seen = set()
    result = []
    visited = 0
    while pending:
        value, depth, include = pending.pop()
        visited += 1
        if depth > 16 or visited > 1024:
            raise InvalidProviderCredentialContext
        if isinstance(value, str):
            if len(value) > 16384:
                raise InvalidProviderCredentialContext
            try:
                value.encode("utf-8", errors="strict")
            except UnicodeError:
                raise InvalidProviderCredentialContext from None
            if include:
                result.append(value)
        elif isinstance(value, (Mapping, list, tuple, set)):
            # Containers may group legacy auxiliary fields, but the api_key
            # binding itself is a transport scalar, never a nested container.
            if (
                isinstance(value, Mapping)
                and "api_key" in value
                and not isinstance(value["api_key"], str)
            ):
                raise InvalidProviderCredentialContext
            if id(value) in seen:
                continue
            seen.add(id(value))
            if isinstance(value, Mapping):
                children = (
                    (
                        child,
                        include_public_metadata
                        or key not in _PUBLIC_CREDENTIAL_METADATA,
                    )
                    for key, child in value.items()
                )
            else:
                children = ((child, True) for child in value)
            pending.extend(
                (child, depth + 1, include_child) for child, include_child in children
            )
        else:
            raise InvalidProviderCredentialContext
    return tuple(result)


def saved_credential_context(values):
    """A saved credential as the read and push projection compares it.

    Rows saved before model-ID filtering existed may hold a null, boolean or
    number beside their key ("api_version": null, "zdr_attested": true). The
    push sent those providers (it skips null and empty values), so on the read
    and push path a null leaf is absent and a boolean or number leaf is not a
    marker: neither can spell a secret inside a model ID. An api_key that is
    present and not text still fails closed, as do bytes and every other leaf
    credential_context_strings refuses. Save and discovery paths do not use
    this: they keep refusing every non-text leaf.
    """
    visited = 0

    def clean(value, depth):
        nonlocal visited
        visited += 1
        if depth > 16 or visited > 1024:
            raise InvalidProviderCredentialContext
        if value is None or isinstance(value, (bool, int, float)):
            return _ABSENT
        if isinstance(value, Mapping):
            key = value.get("api_key")
            if key is not None and not isinstance(key, str):
                raise InvalidProviderCredentialContext
            cleaned = {}
            for name, child in value.items():
                child = clean(child, depth + 1)
                if child is not _ABSENT:
                    cleaned[name] = child
            return cleaned
        if isinstance(value, (list, tuple, set)):
            kept = (clean(child, depth + 1) for child in value)
            return [child for child in kept if child is not _ABSENT]
        # Text is checked by credential_context_strings; anything else (bytes)
        # is left for it to refuse.
        return value

    cleaned = clean(values, 0)
    return () if cleaned is _ABSENT else cleaned


def credential_text_leaves(values):
    """The text a saved credential holds, for a credential that also holds a
    value that is not text (an api_key saved as a number, null or list).

    Replacing such a credential screens the saved model IDs against the new
    credential and against this: every string leaf at any depth and every
    number leaf in its decimal form, so a model ID that spells an old numeric
    key is still held back. As in the markers, a string directly under the
    public routing metadata (region, aws_region) is left out. Null and boolean
    leaves cannot spell a secret and are skipped. Anything else the markers
    could not read (bytes, a string over 16384 characters or not UTF-8, other
    objects, or too deep or too large) raises InvalidProviderCredentialContext,
    so the caller treats the credential as unreadable. These values only ever
    remove model IDs; they never reach a transport.
    """
    pending = [(values, 0, True)]
    result = []
    visited = 0
    while pending:
        value, depth, include = pending.pop()
        visited += 1
        if depth > 16 or visited > 1024:
            raise InvalidProviderCredentialContext
        if isinstance(value, str):
            credential_context_strings(value)
            if include:
                result.append(value)
        elif value is None or isinstance(value, bool):
            continue
        elif isinstance(value, (int, float)):
            try:
                text = str(value)
            except ValueError:
                raise InvalidProviderCredentialContext from None
            if include:
                result.append(text)
        elif isinstance(value, Mapping):
            pending.extend(
                (child, depth + 1, key not in _PUBLIC_CREDENTIAL_METADATA)
                for key, child in value.items()
            )
        elif isinstance(value, (list, tuple, set)):
            pending.extend((child, depth + 1, True) for child in value)
        else:
            raise InvalidProviderCredentialContext
    return tuple(result)


def retires_credential_text(prior, current):
    """Whether a save drops a secret value the saved row holds.

    A saved model ID held back only because it spells such a value would be
    offered once the value is gone, so a save that retires one screens the
    saved IDs against it instead of keeping the stored list as it is.
    """
    return not set(credential_context_strings(prior)) <= set(
        credential_context_strings(current)
    )


def _compact(value):
    return _COMPACT.sub("", value.casefold())


def _credential_forms(values):
    """(form, window width) for every credential value; in-memory only."""
    for value in credential_context_strings(values, include_public_metadata=False):
        for secret in {value, value.strip()}:
            if not secret:
                continue
            data = secret.encode("utf-8")
            yield secret, 8
            yield data.hex(), 16
            for encoder in (base64.b64encode, base64.urlsafe_b64encode):
                yield encoder(data).decode("ascii").rstrip("="), 12


def _payload(form, text):
    # Resource prefix is structure, not secret entropy.
    return text[7:] if form.startswith("models/") else text


def _secret_windows(values):
    """Substring markers for credential forms at least one window long.

    Raw/trimmed text windows: 8 characters. Base64 (standard and URL-safe,
    without padding): 12. Hex: 16. Also compare case-folded alphanumeric
    compactions to catch insertion of routing separators. No probabilistic
    entropy or key-shape score is used.

    A form shorter than its window is not a substring marker: see
    _short_secret_forms.
    """
    exact, compact = set(), set()
    for form, width in _credential_forms(values):
        text = form.casefold()
        payload = _payload(form, text)
        if len(payload) < width:
            continue
        exact.add(text)
        exact.update(payload[i : i + width] for i in range(len(payload) - width + 1))
        folded = _compact(payload)
        if folded:
            compact.add(folded)
            if len(folded) >= width:
                compact.update(
                    folded[i : i + width] for i in range(len(folded) - width + 1)
                )
    return exact, compact


def _short_secret_forms(values):
    """Case-folded credential forms shorter than their window.

    These remove a model ID only when the whole ID equals one of them. Local
    servers take short placeholder keys ("x", "EMPTY", "none"); used as
    substrings, such a value and its short hex or base64 forms ("x" is 78 and
    eA) matched ordinary model IDs ("mixtral-8x7b", "o3-deep-research"), and
    a provider saved with one lost its saved models in every read and in the
    config pushed to the gateway. A value this short carries no secret worth
    hiding inside a longer ID; an ID equal to it is still never shown.
    """
    return {
        form.casefold()
        for form, width in _credential_forms(values)
        if len(_payload(form, form.casefold())) < width
    }


def _matches_grammar(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or not value.isascii():
        return False
    segments = value.split("/")
    if not 1 <= len(segments) <= 6:
        return False
    for segment in segments:
        if not 1 <= len(segment) <= 64:
            return False
        atoms = _SEGMENT_SEPARATORS.split(segment)
        if not 1 <= len(atoms) <= 16 or any(
            not _ATOM.fullmatch(atom) for atom in atoms
        ):
            return False
    return value in _COMPATIBILITY_NAMES or not any(
        word in value.casefold() for word in _RESERVED
    )


def model_id_profile(provider_name, api_format):
    name = (provider_name or "").lower()
    if name == "vertex":
        return "compatible"
    if name == "anthropic" or api_format == "anthropic":
        return "native"
    if name in ("google", "gemini", "google_gemini") or api_format in (
        "google",
        "gemini",
    ):
        return "google"
    return "native" if name == "cohere" else "compatible"


def _admitted(value, markers, enforce_discovery_grammar, whole=frozenset()):
    """Every path excludes credentials; only untrusted discovery uses G."""
    if not isinstance(value, str) or not value:
        return False
    if enforce_discovery_grammar and not _matches_grammar(value):
        return False
    exact, compact = markers
    folded = value.casefold()
    if folded in whole:
        return False
    compressed = _compact(value)
    return not any(marker in folded for marker in exact) and not any(
        marker in compressed for marker in compact
    )


def filter_provider_model_ids(
    values,
    submitted_secrets,
    *,
    provider_name="",
    api_format="openai",
    normalize_google=False,
    enforce_discovery_grammar=True,
    saved_projection=False,
):
    """Return values/count, validating context even for [].

    Disable G for manual persistence, saved-list projection and saved-key
    listing. Credential exclusion remains mandatory in every mode. Callers
    must never include saved credentials in a caller-key discovery context.
    saved_projection (the console reads and the config push of a saved row
    only) compares saved_credential_context(submitted_secrets).
    """
    if saved_projection:
        submitted_secrets = saved_credential_context(submitted_secrets)
    markers = _secret_windows(submitted_secrets)
    whole = _short_secret_forms(submitted_secrets)
    values = values if isinstance(values, (list, tuple)) else []
    strip_google = (
        normalize_google and model_id_profile(provider_name, api_format) == "google"
    )
    kept = []
    for value in values:
        if not _admitted(value, markers, enforce_discovery_grammar, whole):
            continue
        candidate = value.removeprefix("models/") if strip_google else value
        if _admitted(candidate, markers, enforce_discovery_grammar, whole):
            kept.append(candidate)
    return {"models": kept, "dropped_model_count": len(values) - len(kept)}
