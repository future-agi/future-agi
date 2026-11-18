"""Exception values and log messages are scrubbed on every event, not only on
the Vertex credential path.

Events go through a real Sentry client with the production ``before_send``
and are checked as the serialized envelopes it would send.
"""

import collections
import json
import logging

import pytest
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from tfc.logging.sentry import _before_breadcrumb
from tfc.tests.sentry_capture import sentry_capture

LOGGER = "tfc.tests.synthetic_scrubbing"
# Spaced credentials, kept out of the test bodies so that the stack frames'
# source lines in the envelope don't quote them.
SPACED_SECRET = "Sntlcorrect Sntlhorse Sntlbattery"
SPACED_ARGUMENT = "Sntlcorrect Sntlhorse"
# Quoted credentials whose closing quote is missing, on another line, of the
# other kind or escaped.
CUT_OFF_SECRET = "Sntlcutoff"
UNCLOSED_SECRET = "Sntlunclosed Sntlsecond Sntlthird"
MULTILINE_SECRET = "Sntlfirstline\nSntlsecondline"
MISMATCHED_SECRET = "Sntlopened Sntlwrongclose"
ESCAPED_SECRET = 'Sntlbefore\\" Sntlafterescape'
# One row per scrubbing probe. Each runs as an exception value and as a log
# message (``template`` % ``args`` when given, else the text itself). Secrets
# start with "Prb" and are kept out of the test bodies, as above.
ProbeCase = collections.namedtuple(
    "ProbeCase", "text expected secrets template args params formatted log_secrets"
)


def _probe(
    text,
    expected,
    secrets,
    template=None,
    args=(),
    params=None,
    formatted=None,
    log_secrets=None,
):
    return ProbeCase(
        text,
        expected,
        secrets,
        template,
        args,
        params,
        formatted,
        secrets if log_secrets is None else log_secrets,
    )


PROBES = {
    "quoted spaced single": _probe(
        "login failed password='Prbsa Prbsb' for bob",
        "login failed password='[Filtered]' for bob",
        ["Prbsa", "Prbsb"],
    ),
    "quoted spaced double": _probe(
        'login failed password="Prbda Prbdb" for bob',
        'login failed password="[Filtered]" for bob',
        ["Prbda", "Prbdb"],
    ),
    "unterminated single": _probe(
        "conn failed password='Prbcuta", "conn failed password='[Filtered]", ["Prbcuta"]
    ),
    "unterminated double": _probe(
        'conn failed token="Prbcutb', 'conn failed token="[Filtered]', ["Prbcutb"]
    ),
    "unclosed spaced": _probe(
        "login failed token='Prbua Prbub Prbuc",
        "login failed token='[Filtered]",
        ["Prbua", "Prbub", "Prbuc"],
    ),
    "newline inside quotes": _probe(
        "login failed password='Prbna\nPrbnb' end",
        "login failed password='[Filtered]' end",
        ["Prbna", "Prbnb"],
    ),
    "CRLF inside quotes": _probe(
        "login failed password='Prbcra\r\nPrbcrb' end",
        "login failed password='[Filtered]' end",
        ["Prbcra", "Prbcrb"],
    ),
    "mismatched quotes": _probe(
        "login failed password=\"Prbma Prbmb' tail",
        'login failed password="[Filtered]',
        ["Prbma", "Prbmb"],
    ),
    "JSON escaped quote, raw": _probe(
        'bad body {"password": "Prbea\\" Prbeb", "user": "bob"}',
        'bad body {"password": "[Filtered]", "user": "bob"}',
        ["Prbea", "Prbeb"],
    ),
    "JSON escaped quote, json.dumps": _probe(
        "bad body " + json.dumps({"password": 'Prbja" Prbjb', "user": "bob"}),
        'bad body {"password": "[Filtered]", "user": "bob"}',
        ["Prbja", "Prbjb"],
    ),
    "JSON encoded twice": _probe(
        "bad body "
        + json.dumps(json.dumps({"password": 'Prbwa" Prbwb', "user": "bob"})),
        'bad body "{\\"password\\": \\"[Filtered]\\", \\"user\\": \\"bob\\"}"',
        ["Prbwa", "Prbwb"],
    ),
    # A backslash run always escapes what follows (repr doubles it), so an
    # escaped backslash before the closing quote over-redacts to the end.
    "escaped backslash before the closing quote": _probe(
        'conn failed password="Prbba\\\\" tail Prbbb',
        'conn failed password="[Filtered]',
        ["Prbba", "Prbbb"],
    ),
    # Frame vars are repr()'d, which escapes the single quotes here.
    "both quote kinds": _probe(
        "conn failed password='Prbqa Prbqb' and api_key=\"Prbqc\"",
        "conn failed password='[Filtered]' and api_key=\"[Filtered]\"",
        ["Prbqa", "Prbqb", "Prbqc"],
    ),
    "both quote kinds, unterminated": _probe(
        'conn failed password=\'Prbqd and api_key="Prbqe"',
        "conn failed password='[Filtered]",
        ["Prbqd", "Prbqe"],
    ),
    "starts with [Filtered], later credential": _probe(
        "password='[Filtered] then token=Prbfa",
        "password='[Filtered]",
        ["Prbfa"],
    ),
    "starts with [Filtered], later %s credential": _probe(
        "conn password='Prbta and token=Prbtb",
        "conn password='[Filtered]",
        ["Prbta", "Prbtb"],
        "conn password='%s and token=%s",
        ("Prbta", "Prbtb"),
        ["[Filtered]", "[Filtered]"],
        "conn password='[Filtered]",
    ),
    # In plain text an unquoted value ends at the first space; as a %s
    # argument it goes whole.
    "positional spaced %s": _probe(
        "db login with password=Prbpa Prbpb",
        "db login with password=[Filtered] Prbpb",
        ["Prbpa"],
        "db login with password=%s",
        ("Prbpa Prbpb",),
        ["[Filtered]"],
        "db login with password=[Filtered]",
        log_secrets=["Prbpa", "Prbpb"],
    ),
    "positional spaced %r": _probe(
        "db login with password='Prbra Prbrb'",
        "db login with password='[Filtered]'",
        ["Prbra", "Prbrb"],
        "db login with password=%r",
        ("Prbra Prbrb",),
        ["[Filtered]"],
        "db login with password='[Filtered]'",
    ),
    "mapping %(name)s": _probe(
        "db login as bob with password='Prbma1 Prbma2'",
        "db login as bob with password='[Filtered]'",
        ["Prbma1", "Prbma2"],
        "db login as %(user)s with password='%(pw)s'",
        ({"user": "bob", "pw": "Prbma1 Prbma2"},),
        {"user": "bob", "pw": "[Filtered]"},
        "db login as bob with password='[Filtered]'",
    ),
    "unrelated short params": _probe(
        "abc step 1 api_key=Prbsh1",
        "abc step 1 api_key=[Filtered]",
        ["Prbsh"],
        "%s step %s api_key=%s1",
        ("abc", 1, "Prbsh"),
        ["abc", 1, "[Filtered]"],
        "abc step 1 api_key=[Filtered]1",
    ),
    "Bearer token": _probe(
        "upstream rejected Authorization: Bearer Prbbearer host=api",
        "upstream rejected Authorization: [Filtered] host=api",
        ["Prbbearer"],
    ),
    "key names": _probe(
        "api_key=Prbka token=Prbkb password=Prbkc secret=Prbkd host=db",
        "api_key=[Filtered] token=[Filtered] password=[Filtered] "
        "secret=[Filtered] host=db",
        ["Prbka", "Prbkb", "Prbkc", "Prbkd"],
    ),
    "already [Filtered]": _probe(
        "x password=[Filtered] token='[Filtered]' host=db",
        "x password=[Filtered] token='[Filtered]' host=db",
        [],
    ),
}


SENTINELS = (
    "SENTINEL-pw-7f3a",
    "SENTINEL-bearer-91c2",
    "SENTINEL-key-55d0",
    "SENTINEL-private-0b1e",
)


@pytest.fixture
def transport():
    with sentry_capture(
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        before_breadcrumb=_before_breadcrumb,
    ) as transport:
        yield transport


def _sent(transport) -> str:
    return json.dumps(transport.events)


def _assert_no_sentinel(transport):
    sent = _sent(transport)
    for sentinel in SENTINELS:
        assert sentinel not in sent


def test_credentials_in_a_non_vertex_exception_message_are_not_sent(transport):
    try:
        raise ConnectionError(
            f"login failed: password={SENTINELS[0]} host=db.internal "
            f"Authorization: Bearer {SENTINELS[1]}"
        )
    except ConnectionError as error:
        sentry_sdk.capture_exception(error)

    assert len(transport.events) == 1
    _assert_no_sentinel(transport)
    value = transport.events[0]["exception"]["values"][-1]
    assert value["type"] == "ConnectionError"
    assert value["value"].startswith("login failed: password=[Filtered]")
    assert "host=db.internal" in value["value"]
    assert value["stacktrace"]["frames"][-1]["function"] == (
        "test_credentials_in_a_non_vertex_exception_message_are_not_sent"
    )


def test_a_credential_document_in_an_exception_message_is_not_sent(transport):
    try:
        raise ValueError(json.dumps({"private_key": SENTINELS[3], "type": "svc"}))
    except ValueError as error:
        sentry_sdk.capture_exception(error)

    _assert_no_sentinel(transport)
    assert transport.events[0]["exception"]["values"][-1]["type"] == "ValueError"


def test_credentials_in_a_log_message_are_not_sent(transport):
    logging.getLogger(LOGGER).error(
        "provider rejected api_key=%s for org %s", SENTINELS[2], "org-synthetic"
    )
    logging.getLogger(LOGGER).error(
        f"upstream call failed with x-api-key={SENTINELS[2]}"
    )

    assert len(transport.events) == 2
    _assert_no_sentinel(transport)
    logentry = transport.events[0]["logentry"]
    assert "org-synthetic" in json.dumps(logentry)
    assert "provider rejected api_key=" in json.dumps(logentry)


def test_an_ordinary_error_keeps_its_message(transport):
    try:
        raise ZeroDivisionError("division by zero in row 3 (max_tokens=4096)")
    except ZeroDivisionError as error:
        sentry_sdk.capture_exception(error)
    logging.getLogger(LOGGER).error(
        "eval batch %s failed after %d retries", "batch-7", 3
    )

    value = transport.events[0]["exception"]["values"][-1]
    assert value["value"] == "division by zero in row 3 (max_tokens=4096)"
    logentry = transport.events[1]["logentry"]
    assert logentry["message"] == "eval batch %s failed after %d retries"
    assert logentry["params"] == ["batch-7", 3]


def test_a_quoted_spaced_credential_in_an_exception_is_not_sent(transport):
    try:
        raise PermissionError(f"login failed password='{SPACED_SECRET}' for bob")
    except PermissionError as error:
        sentry_sdk.capture_exception(error)

    sent = _sent(transport)
    for word in SPACED_SECRET.split():
        assert word not in sent
    value = transport.events[0]["exception"]["values"][-1]["value"]
    assert value == "login failed password='[Filtered]' for bob"


def test_a_spaced_credential_passed_as_a_log_argument_is_not_sent(transport):
    logging.getLogger(LOGGER).error("db login with password=%s", SPACED_ARGUMENT)

    sent = _sent(transport)
    for word in SPACED_ARGUMENT.split():
        assert word not in sent
    logentry = transport.events[0]["logentry"]
    assert logentry["params"] == ["[Filtered]"]
    assert logentry["formatted"] == "db login with password=[Filtered]"


def test_only_the_arguments_that_form_the_credential_are_redacted(transport):
    logging.getLogger(LOGGER).error("%s step %s api_key=%s1", "abc", 1, "x")

    assert transport.events[0]["logentry"]["params"] == ["abc", 1, "[Filtered]"]
    assert "api_key=x1" not in _sent(transport)


def _raise_and_capture(message: str) -> None:
    try:
        raise PermissionError(message)
    except PermissionError as error:
        sentry_sdk.capture_exception(error)


def _words(secret: str) -> list[str]:
    return secret.replace('\\"', " ").split()


@pytest.mark.parametrize("quote", ["'", '"'])
def test_a_cut_off_quoted_credential_is_not_sent(transport, quote):
    _raise_and_capture(f"conn failed password={quote}{CUT_OFF_SECRET}")

    assert CUT_OFF_SECRET not in _sent(transport)
    value = transport.events[0]["exception"]["values"][-1]["value"]
    assert value == f"conn failed password={quote}[Filtered]"


def test_an_unclosed_quoted_spaced_credential_is_not_sent(transport):
    _raise_and_capture(f"login failed token='{UNCLOSED_SECRET}")

    sent = _sent(transport)
    for word in _words(UNCLOSED_SECRET):
        assert word not in sent
    value = transport.events[0]["exception"]["values"][-1]["value"]
    assert value == "login failed token='[Filtered]"


def test_a_quoted_credential_spanning_lines_is_not_sent(transport):
    _raise_and_capture(f"login failed password='{MULTILINE_SECRET}' end")

    sent = _sent(transport)
    for word in _words(MULTILINE_SECRET):
        assert word not in sent
    value = transport.events[0]["exception"]["values"][-1]["value"]
    assert value == "login failed password='[Filtered]' end"


def test_a_credential_closed_by_the_other_quote_is_not_sent(transport):
    _raise_and_capture(f"login failed password=\"{MISMATCHED_SECRET}' tail")

    sent = _sent(transport)
    for word in _words(MISMATCHED_SECRET):
        assert word not in sent
    value = transport.events[0]["exception"]["values"][-1]["value"]
    assert value == 'login failed password="[Filtered]'


def test_an_escaped_quote_does_not_end_a_json_credential(transport):
    _raise_and_capture(f'bad body {{"password": "{ESCAPED_SECRET}", "user": "bob"}}')

    sent = _sent(transport)
    for word in _words(ESCAPED_SECRET):
        assert word not in sent
    value = transport.events[0]["exception"]["values"][-1]["value"]
    assert value == 'bad body {"password": "[Filtered]", "user": "bob"}'


def test_an_unclosed_quoted_log_argument_is_not_sent(transport):
    logging.getLogger(LOGGER).error("db login with password='%s", UNCLOSED_SECRET)

    sent = _sent(transport)
    for word in _words(UNCLOSED_SECRET):
        assert word not in sent
    logentry = transport.events[0]["logentry"]
    assert logentry["params"] == ["[Filtered]"]
    assert logentry["formatted"] == "db login with password='[Filtered]"


def _capture_probe(message: str) -> None:
    try:
        raise ConnectionError(message)
    except ConnectionError as error:
        sentry_sdk.capture_exception(error)


def _log_probe(template: str, args: tuple) -> None:
    logging.getLogger(LOGGER).error(template, *args)


@pytest.mark.parametrize("case", PROBES.values(), ids=PROBES.keys())
def test_probe_table_as_an_exception_value(transport, case):
    _capture_probe(case.text)

    sent = _sent(transport)
    for secret in case.secrets:
        assert secret not in sent
    assert "[Filtered]]" not in sent
    assert transport.events[0]["exception"]["values"][-1]["value"] == case.expected


@pytest.mark.parametrize("case", PROBES.values(), ids=PROBES.keys())
def test_probe_table_as_a_log_message(transport, case):
    if case.template is None:
        _log_probe(case.text.replace("%", "%%"), ())
    else:
        _log_probe(case.template, case.args)

    sent = _sent(transport)
    for secret in case.log_secrets:
        assert secret not in sent
    assert "[Filtered]]" not in sent
    logentry = transport.events[0]["logentry"]
    if case.template is None:
        assert logentry.get("formatted", logentry["message"]) == case.expected
    else:
        assert logentry["params"] == case.params
        assert logentry["formatted"] == case.formatted
