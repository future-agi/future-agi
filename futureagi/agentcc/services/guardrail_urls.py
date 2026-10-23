"""URL well-formedness for the callbacks an org guardrail or alert config names.

A guardrail webhook
or external-provider check (``url``, ``endpoint``, ``base_url``) and an alert
channel (``url``) are dialled by the gateway. Two layers protect them:

- the gateway's guarded egress dialer refuses a loopback, private, link-local
  or cloud-metadata **address** at dial time (the address guard);
- Django refuses a **malformed** URL on save: an absolute ``http`` or
  ``https`` URL,
  with a host, no user or password, and no control characters.

Django judges only well-formedness, never the address class: a self-hosted LAN
host name (``http://ollama.internal:11434``) is well-formed and saved; the
operator opens its address with ``mcp.egress.private_allow``. So a legitimate
config is never refused here, and only a genuinely malformed URL is.

An empty string or a missing key is "no URL": an org check with no endpoint
takes the provider default, so it is not a URL to validate.
"""

from collections.abc import Mapping
from urllib.parse import urlsplit

from agentcc.services.config_push import OrgConfigRejected

_URL_SCHEMES = ("http", "https")

# The guardrail-check config keys the gateway builds a request URL from. A
# region, domain or cloud is a host fragment, not a URL, so it is not checked
# here; the egress guard still classifies the address it resolves to.
_GUARDRAIL_URL_FIELDS = ("url", "endpoint", "base_url")


def is_http_url(url):
    """True if ``url`` is an absolute http(s) URL with a host, no user or
    password and no control characters."""
    # Go's net/url refuses DEL (0x7f) and other control bytes, so such a URL
    # is refused here.
    if not isinstance(url, str) or any(
        c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url
    ):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:  # an unbalanced IPv6 bracket or a bad port
        return False
    if parts.username is not None or parts.password is not None:
        # A URL never carries a user or password.
        return False
    return parts.scheme.lower() in _URL_SCHEMES and bool(parts.hostname)


class BadGuardrailURL(OrgConfigRejected):
    """A guardrail check names a malformed callback URL."""

    code = "guardrail_bad_url"

    def __init__(self, path):
        self.param = path
        super().__init__(
            f"{path} must be an absolute http or https URL with a host and no "
            "user, password or control characters."
        )


class BadAlertingURL(OrgConfigRejected):
    """An alert channel names a malformed URL."""

    code = "alerting_bad_url"

    def __init__(self, path):
        self.param = path
        super().__init__(
            f"{path} must be an absolute http or https URL with a host and no "
            "user, password or control characters."
        )


def _guardrail_url_items(section, path="guardrails"):
    """Yield (field_path, name, field, value) for every URL-bearing field of
    every check in a guardrails section, in all three stored shapes: a rules
    list, a checks list, and a checks map keyed by name."""
    if not isinstance(section, Mapping):
        return
    rules = section.get("rules")
    if isinstance(rules, list):
        for i, check in enumerate(rules):
            if isinstance(check, Mapping):
                yield from _check_url_items(
                    f"{path}.rules[{i}]", check.get("name"), check.get("config")
                )
    checks = section.get("checks")
    if isinstance(checks, list):
        for i, check in enumerate(checks):
            if isinstance(check, Mapping):
                yield from _check_url_items(
                    f"{path}.checks[{i}]", check.get("name"), check.get("config")
                )
    elif isinstance(checks, Mapping):
        for name, check in checks.items():
            if isinstance(check, Mapping):
                yield from _check_url_items(
                    f"{path}.checks.{name}", name, check.get("config")
                )


def _name_key(name):
    """A hashable key for a check's or channel's name as a save sent it: a
    string is its own key, and any other value is keyed by its type and repr,
    never taken for the string with the same text. The save paths refuse a
    name that is not a string."""
    if isinstance(name, str):
        return name
    return (type(name).__name__, repr(name))


def _held_by_name(items):
    """Every stored URL under each key, for the changed-only checks: ``items``
    yields ``(key, url)``, the key built from a check's or a channel's name
    (``_name_key``). Two entries may share a name (the console does not stop
    it), so a key holds every URL stored under it, not one, and an unchanged
    URL never reads as new."""
    held = {}
    for key, url in items:
        held.setdefault(key, []).append(url)
    return held


def _check_url_items(check_path, name, cfg):
    if not isinstance(cfg, Mapping):
        return
    for field in _GUARDRAIL_URL_FIELDS:
        value = cfg.get(field)
        if value not in (None, ""):
            yield f"{check_path}.config.{field}", name, field, value


def guardrail_bad_url_path(section, path="guardrails"):
    """The field path of the first malformed guardrail callback URL, or None."""
    for field_path, _name, _field, value in _guardrail_url_items(section, path):
        if not is_http_url(value):
            return field_path
    return None


def reject_bad_guardrail_urls(section, path="guardrails"):
    """Refuse a guardrails section that names a malformed callback URL. Raises
    BadGuardrailURL naming the field."""
    found = guardrail_bad_url_path(section, path)
    if found:
        raise BadGuardrailURL(found)


def reject_changed_bad_guardrail_urls(section, stored):
    """Refuse a save whose guardrails section introduces or changes a check's
    callback URL to a malformed one. A malformed URL already stored under the
    same check name and field, left unchanged, is not the save's doing, so a
    save that only toggles or edits something else (including turning that very
    check off) is not blocked; the gateway's egress guard still checks the
    address at dial time. Raises BadGuardrailURL naming the field."""
    held = _held_by_name(
        ((_name_key(name), field), value)
        for _path, name, field, value in _guardrail_url_items(stored)
    )
    for field_path, name, field, value in _guardrail_url_items(section):
        if value in held.get((_name_key(name), field), ()):
            continue
        if not is_http_url(value):
            raise BadGuardrailURL(field_path)


def reject_bad_policy_check_urls(checks):
    """Refuse a guardrail policy's checks list (a policy sync pushes it into
    the org config, outside the config writer) that names a malformed
    callback URL. Raises BadGuardrailURL naming checks[i].config.<field>."""
    reject_changed_bad_policy_check_urls(checks, None)


def reject_changed_bad_policy_check_urls(checks, stored):
    """reject_bad_policy_check_urls for a policy update: a malformed URL the
    policy already stores under the same check name and field, unchanged, is
    not the update's doing, so an edit of another check (or of anything else
    in that one) is not blocked. ``stored`` is the policy's stored checks
    list, or None for a new policy."""
    held = _held_by_name(
        ((_name_key(name), field), value)
        for check in (stored if isinstance(stored, list) else ())
        if isinstance(check, Mapping)
        for _path, name, field, value in _check_url_items(
            "", check.get("name"), check.get("config")
        )
    )
    if not isinstance(checks, list):
        return
    for i, check in enumerate(checks):
        if not isinstance(check, Mapping):
            continue
        for field_path, name, field, value in _check_url_items(
            f"checks[{i}]", check.get("name"), check.get("config")
        ):
            if value in held.get((_name_key(name), field), ()):
                continue
            if not is_http_url(value):
                raise BadGuardrailURL(field_path)


def _alert_url_as_sent(url):
    """An alert channel's ``url`` as the gateway would get it. A URL that
    carries a credential is stored encrypted (org_config_secrets), so stored
    ciphertext is read as its clear value, an unreadable
    token as no URL (the push drops it), and a read-back mask as no URL here:
    the write path resolves it to the stored value and checks that value
    afterwards (``reject_changed_bad_alerting_urls`` after the secrets are
    encrypted). Anything else is the value as sent."""
    from agentcc.services.org_config_secrets import clear_url, is_mask

    if not isinstance(url, str) or is_mask(url):
        return None if is_mask(url) else url
    return clear_url(url)


def _alert_channel_items(alerting):
    """Yield (field_path, name, url) for each alert channel, in both the list
    and the name-keyed object shapes the UI writes (as config_push coerces
    them), with the URL as the gateway would get it (``_alert_url_as_sent``).
    A channel with no url (a log channel) yields nothing, nor does a read-back
    mask (the write path checks the value it keeps)."""
    if not isinstance(alerting, Mapping):
        return
    channels = alerting.get("channels")
    if isinstance(channels, list):
        for i, ch in enumerate(channels):
            if isinstance(ch, Mapping):
                url = _alert_url_as_sent(ch.get("url"))
                if url not in (None, ""):
                    name = ch.get("name")
                    key = _name_key(name) if name is not None else i
                    yield f"alerting.channels[{i}].url", key, url
    elif isinstance(channels, Mapping):
        for name, ch in channels.items():
            if isinstance(ch, Mapping):
                url = _alert_url_as_sent(ch.get("url"))
                if url not in (None, ""):
                    yield f"alerting.channels.{name}.url", name, url


def alerting_bad_url_path(alerting):
    """The field path of the first malformed alert channel URL, or None."""
    for field_path, _name, url in _alert_channel_items(alerting):
        if not is_http_url(url):
            return field_path
    return None


def reject_bad_alerting_urls(alerting):
    """Refuse an alerting section whose channel names a malformed URL. Raises
    BadAlertingURL naming the field."""
    found = alerting_bad_url_path(alerting)
    if found:
        raise BadAlertingURL(found)


def reject_changed_bad_alerting_urls(alerting, stored):
    """Refuse a save that introduces or changes an alert channel URL to a
    malformed one. A malformed URL already stored under the same channel name,
    unchanged, does not block an unrelated save. Raises BadAlertingURL."""
    held = _held_by_name(
        (name, url) for _path, name, url in _alert_channel_items(stored)
    )
    for field_path, name, url in _alert_channel_items(alerting):
        if url in held.get(name, ()):
            continue
        if not is_http_url(url):
            raise BadAlertingURL(field_path)
