"""No MCP or connector secret, and no raw gateway key, in an error report
(review fix round 1, the Sentry finding; stage 2 recheck, the raw key).

The MCP credential vault's create and rotate (POST /agentcc/mcp/credentials/,
POST /agentcc/mcp/credentials/{id}/rotate/), the catalogue add (POST
/agentcc/connectors/apps/{app}/add/) and the push's assembly
(``assemble_for_push``) hold a customer's secret in clear: in the request
body, and in frame locals named ``value``, ``credential`` and ``data``. The
app reports errors with frame locals and request bodies
(tfc/logging/sentry.py: include_local_variables, send_default_pii,
max_request_body_size medium), so an unexpected failure inside one of them
must not carry the secret.

A new gateway key is held in clear too, under ``raw_key`` and as ``key`` in
the gateway's answer, by release's key create (``auth_bridge.provision_key``,
which the API keys page and the Omega EE agent key mint both call) and by the
mint (``mint_agent_key``, then the view and its answer) until its answer is
sent. A failure there, such as the mint's audit write after its commit, must
not carry the key, wherever it sits among a frame's variables (the SDK reports
a frame's first 10; ``_every_local`` keeps them all).

Each report is built by a real ``sentry_sdk.Client`` with local variables on,
through the app's own pipeline: the EventScrubber denylist and the
before_send hook init_sentry installs. Its transport sends nothing. Each write
is reported twice:

- as the Django integration reports a request, with the request's URL,
  method and parsed body on the event, so before_send's rule for the MCP
  secret routes applies: no canary anywhere in the report;
- with no request on the event (a push runs from many routes and from
  tasks), so before_send's rule for the names that hold a secret in the
  app's agentcc frames applies: no canary in any frame of the app or of the
  frameworks serving it.

Only the app's pipeline is tested. With no scrubber at all, these frames do
hold the secret: a string in clear has no seal (CN11's ``SealedSecret`` covers
the OAuth client secret that way). Every value here is a fake.
"""

import copy
import json
from functools import partial
from unittest.mock import Mock, patch

import pytest
import sentry_sdk
import sentry_sdk.serializer as sentry_serializer
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber
from sentry_sdk.transport import Transport

from agentcc.models import AgentccAPIKey
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.services import auth_bridge
from agentcc.services import mcp_credentials as vault
from agentcc.services import mcp_service_provisioning as provisioning
from agentcc.tests import test_connector_catalog as _catalog_tests
from agentcc.tests import test_mcp_service_provisioning as _provisioning_tests
from agentcc.tests.test_mcp_credentials import GITHUB_URL, _seed
from agentcc.views import mcp_service_provisioning as provisioning_views
from tfc.logging.sentry import SENSITIVE_KEY_SUBSTRINGS, _get_before_send

pytestmark = [pytest.mark.integration, pytest.mark.api]

# The catalogue tests' fixtures: the fixture bundle, the gateway's probe faked
# at its HTTP client (autouse there and here), the probe's budget lifted
# (autouse) and the recorded config pushes.
bundle = _catalog_tests.bundle
probe = _catalog_tests.probe
probe_budget = _catalog_tests.probe_budget
pushes = _catalog_tests.pushes
# The provisioning tests' gateway, faked at its admin client: every key it
# makes has a new random value, kept in ``gateway.keys``.
gateway = _provisioning_tests.gateway

CREDENTIALS = "/agentcc/mcp/credentials/"
CANARY = "fake-fr1-mcp-secret-canary-not-real"
CANARY_2 = "fake-fr1-mcp-rotated-canary-not-real"
CANARY_3 = "fake-fr1-mcp-second-server-canary-not-real"
GITLAB_URL = "https://mcp.gitlab.example.com/mcp"

APP_DENYLIST = list(DEFAULT_DENYLIST) + [
    k for k in SENSITIVE_KEY_SUBSTRINGS if k not in DEFAULT_DENYLIST
]

# Frames that exist only because a test drives the call: the test clients'
# (the repo's WorkspaceAwareAPIClient lives in conftest), which build the
# request from the plaintext body, and this module's own.
_TEST_MODULES = ("django.test", "rest_framework.test", "conftest", __name__)


class _SendNothing(Transport):
    def capture_envelope(self, envelope):
        pass


class _StoreFailed(RuntimeError):
    pass


class _AppReports:
    """Error reports through the app's pipeline, as init_sentry configures it
    (the denylist, before_send, local variables and request bodies on)."""

    def __init__(self):
        self.events = []
        app_before_send = _get_before_send()

        def keep(event, hint):
            event = app_before_send(event, hint)
            if event is not None:
                self.events.append(event)
            return None

        self.client = sentry_sdk.Client(
            dsn="https://public@127.0.0.1:9/1",
            transport=_SendNothing,
            default_integrations=False,
            enable_backpressure_handling=False,
            send_default_pii=True,
            include_local_variables=True,
            include_source_context=True,
            max_request_body_size="medium",
            event_scrubber=EventScrubber(denylist=APP_DENYLIST, recursive=True),
            before_send=keep,
        )

    def report(self, call, expected, request=None):
        """The report of ``call``'s ``expected`` exception. ``request`` (url,
        method, data) is put on the event as the Django integration puts the
        request being served."""
        sent = len(self.events)
        try:
            call()
        except expected:
            with sentry_sdk.new_scope() as scope:
                scope.set_client(self.client)
                if request is not None:

                    def add_request(event, hint, request=request):
                        event["request"] = copy.deepcopy(request)
                        return event

                    scope.add_event_processor(add_request)
                scope.capture_exception()
        else:
            pytest.fail("expected an exception")
        assert len(self.events) == sent + 1
        return self.events[-1]


@pytest.fixture
def reports():
    app = _AppReports()
    yield app
    app.client.close()


def _dump(value):
    return json.dumps(value, default=str)


def _frames(event):
    return [
        frame
        for exception in event["exception"]["values"]
        for frame in exception["stacktrace"]["frames"]
    ]


def _app_frames(event):
    """The report's frames from the app's own code and the frameworks serving
    the request, as production would report them."""
    return [
        frame
        for frame in _frames(event)
        if not (frame.get("module") or "").startswith(_TEST_MODULES)
    ]


def _frames_of(event, module, function):
    """Every frame of ``module.function`` in the report: a chained exception
    (the error the test client re-raises, the context it was raised in)
    repeats the frames it shares, so there can be more than one."""
    found = [
        f
        for f in _frames(event)
        if (f.get("module"), f.get("function")) == (module, function)
    ]
    assert found, f"no frame of {module}.{function} in the report"
    return found


def _served(path, body):
    """The request on the event, as the Django integration reports it."""
    return {"url": f"http://testserver{path}", "method": "POST", "data": body}


def _raise_holding(value):
    held = value  # noqa: F841 - read by the report
    raise ZeroDivisionError("canary")


def _write_reports(reports, path, body, failing, expected=_StoreFailed):
    """The two reports of one failing write: as a served request, and with
    no request on the event."""
    post = partial(failing, path, body)
    served = reports.report(post, expected, request=_served(path, body))
    bare = reports.report(post, expected)
    return served, bare


def test_frame_locals_reach_a_report(reports):
    """The checks below can fail: a frame's locals do reach a report."""
    report = reports.report(partial(_raise_holding, CANARY), ZeroDivisionError)
    assert CANARY in _dump(report)


def test_vault_create_failure_reports_no_secret(auth_client, user, pushes):
    """An unexpected failure inside POST /agentcc/mcp/credentials/."""
    _seed(user.organization)
    body = {"server_id": "github", "kind": "bearer", "value": CANARY}
    reports = _AppReports()
    try:
        with patch.object(
            AgentccMCPCredential.objects, "create", side_effect=_StoreFailed("db down")
        ):
            served, bare = _write_reports(
                reports,
                CREDENTIALS,
                body,
                lambda path, data: auth_client.post(path, data, format="json"),
            )
    finally:
        reports.client.close()

    assert CANARY not in _dump(served)
    assert "data" not in served["request"]
    functions = [f["function"] for f in _app_frames(bare)]
    assert "post" in functions and "create" in functions
    assert CANARY not in _dump(_app_frames(bare))
    for view in _frames_of(bare, "agentcc.views.mcp_credentials", "post"):
        assert "[Filtered]" in _dump(view["vars"])
    assert not AgentccMCPCredential.objects.filter(server_id="github").exists()


def test_vault_rotate_failure_reports_no_secret(auth_client, user, pushes):
    """An unexpected failure inside POST /agentcc/mcp/credentials/{id}/rotate/."""
    org = user.organization
    _seed(org)
    created = vault.create(org, "github", kind="bearer", value=CANARY, actor=user)
    path = f"{CREDENTIALS}{created.credential.pk}/rotate/"
    body = {"value": CANARY_2}
    reports = _AppReports()
    try:
        with patch.object(
            AgentccMCPCredential, "save", side_effect=_StoreFailed("lock timeout")
        ):
            served, bare = _write_reports(
                reports,
                path,
                body,
                lambda path, data: auth_client.post(path, data, format="json"),
            )
    finally:
        reports.client.close()

    for report in (served, bare):
        assert CANARY not in _dump(report["exception"])
    assert CANARY_2 not in _dump(served)
    assert "data" not in served["request"]
    functions = [f["function"] for f in _app_frames(bare)]
    assert "post" in functions and "rotate" in functions
    assert CANARY_2 not in _dump(_app_frames(bare))
    for rotate in _frames_of(bare, "agentcc.services.mcp_credentials", "rotate"):
        assert rotate["vars"]["value"] == "[Filtered]"


def test_catalog_add_failure_reports_no_secret(
    auth_client, user, bundle, pushes, probe
):
    """An unexpected failure inside POST /agentcc/connectors/apps/{app}/add/,
    after the provider's tools were listed with the key."""
    path = "/agentcc/connectors/apps/acme-docs/add/"
    body = {"provider": "acme-mcp", "mode": "agent", "credential": {"value": CANARY}}
    reports = _AppReports()
    try:
        with patch.object(
            AgentccMCPCredential.objects, "create", side_effect=_StoreFailed("db down")
        ):
            served, bare = _write_reports(
                reports,
                path,
                body,
                lambda path, data: auth_client.post(path, data, format="json"),
            )
    finally:
        reports.client.close()

    # The add reached the write: the key listed the provider's tools first.
    assert probe.calls and probe.calls[-1]["auth"]["value"] == CANARY
    assert CANARY not in _dump(served)
    assert "data" not in served["request"]
    functions = [f["function"] for f in _app_frames(bare)]
    assert "post" in functions and "add_connector" in functions
    assert CANARY not in _dump(_app_frames(bare))
    for add in _frames_of(bare, "agentcc.services.connector_catalog", "add_connector"):
        assert add["vars"]["credential"] == "[Filtered]"
    assert not AgentccMCPCredential.objects.filter(server_id="acme-docs").exists()


def test_push_assembly_failure_reports_no_secret(user, reports):
    """An unexpected failure inside ``assemble_for_push``, after one
    credential was decrypted for the push: no request is being served (a push
    runs from many routes and from tasks), so the name rule alone holds."""
    org = user.organization
    _seed(
        org,
        {
            "servers": {
                "github": {"url": GITHUB_URL, "transport": "http"},
                "gitlab": {"url": GITLAB_URL, "transport": "http"},
            }
        },
    )
    with patch("agentcc.services.config_push.get_gateway_client"):
        vault.create(org, "github", kind="bearer", value=CANARY, actor=user)
        vault.create(org, "gitlab", kind="bearer", value=CANARY_3, actor=user)
    real = vault._decrypted

    def decrypted(row):
        if row.server_id == "gitlab":
            raise _StoreFailed("the second row failed")
        return real(row)

    with patch.object(vault, "_decrypted", side_effect=decrypted):
        # github is decrypted first (rows are ordered by server id), so the
        # frame holds its secret in ``value`` and ``secrets`` when gitlab's
        # read fails.
        report = reports.report(partial(vault.assemble_for_push, org.pk), _StoreFailed)

    for assembly in _frames_of(
        report, "agentcc.services.mcp_credentials", "assemble_for_push"
    ):
        assert assembly["vars"]["value"] == "[Filtered]"
        assert "secrets" in assembly["vars"]
    assert CANARY not in _dump(_app_frames(report))
    assert CANARY_3 not in _dump(report)


def _raw_keys_in(value, gateway):
    """The raw gateway keys ``gateway`` made that appear in ``value``."""
    dumped = _dump(value)
    return [raw for raw in gateway.keys.values() if raw in dumped]


def _every_local():
    """Reports taken inside this keep every variable of a frame. The SDK
    keeps a frame's first 10 (``sentry_sdk.serializer.MAX_DATABAG_BREADTH``),
    which can leave out the one that holds a key, or keep it once a function
    gains or loses a variable; the rule that redacts the key must hold
    wherever it sits among a frame's variables."""
    return patch.object(sentry_serializer, "MAX_DATABAG_BREADTH", 10_000)


def _raise_holding_late():
    """Raises with the canary in the 12th of its 12 variables."""
    a = b = c = d = e = f = g = h = i = j = k = 0  # noqa: F841
    held = CANARY  # noqa: F841 - read by the report
    raise ZeroDivisionError("canary")


def test_every_local_reaches_a_report_taken_with_every_local(reports):
    """The checks below can fail: the SDK leaves out a frame's 11th variable
    and later ones, and ``_every_local`` keeps them."""

    def late_vars(report):
        return _frames_of(report, __name__, "_raise_holding_late")[0]["vars"]

    as_sent = late_vars(reports.report(_raise_holding_late, ZeroDivisionError))
    assert "a" in as_sent and "held" not in as_sent
    with _every_local():
        every = late_vars(reports.report(_raise_holding_late, ZeroDivisionError))
    assert CANARY in every["held"]


def _mint_reports(gateway, reports):
    """The two reports of one failing mint through POST
    /agentcc/mcp/service/agent-keys/, as a served request and with no request
    on the event, each with a new Idempotency-Key."""
    org, owner, workspace = _provisioning_tests._org()
    _provisioning_tests._seed(org)
    ee, _ = _provisioning_tests._ee(org, owner)

    def mint(path, data):
        return ee.post(
            path,
            data,
            format="json",
            HTTP_IDEMPOTENCY_KEY=_provisioning_tests.uuid7(),
        )

    body = {"workspace": str(workspace.id)}
    return _write_reports(reports, _provisioning_tests.KEYS, body, mint)


@_provisioning_tests.CN22_OFF_733
@pytest.mark.django_db
def test_agent_key_mint_failure_reports_no_raw_key(gateway, reports):
    """An unexpected failure in POST /agentcc/mcp/service/agent-keys/ after the
    mint committed: the audit write of the new key raises (a DB error) while
    ``mint_agent_key`` holds the key in clear, before the view has sent it.
    Every variable of each frame is kept."""
    with (
        patch.object(
            provisioning, "_audit_ee", side_effect=_StoreFailed("audit log down")
        ) as audit,
        _every_local(),
    ):
        served, bare = _mint_reports(gateway, reports)

    # Both mints made their key and reached the audit of it.
    assert len(gateway.keys) == 2
    assert [c.args[1] for c in audit.call_args_list] == [
        provisioning.AUDIT_AGENT_KEY_MINTED
    ] * 2
    assert _raw_keys_in(served, gateway) == []
    functions = [f["function"] for f in _app_frames(bare)]
    assert "post" in functions and "mint_agent_key" in functions
    assert _raw_keys_in(_app_frames(bare), gateway) == []
    for minted in _frames_of(
        bare, "agentcc.services.mcp_service_provisioning", "mint_agent_key"
    ):
        assert minted["vars"]["raw_key"] == "[Filtered]"


def _no_response(*args, **kwargs):
    raise _StoreFailed("the answer could not be built")


@_provisioning_tests.CN22_OFF_733
@pytest.mark.django_db
def test_agent_key_mint_answer_failure_reports_no_raw_key(gateway, reports):
    """An unexpected failure in POST /agentcc/mcp/service/agent-keys/ after
    ``mint_agent_key`` returned, as the view builds its answer: the view holds
    the key as ``raw_key`` and ``_ok`` as ``key`` in the answer. The report is
    taken as the SDK takes it, the first 10 variables of each frame, which
    include both."""
    with patch.object(provisioning_views, "Response", _no_response):
        served, bare = _mint_reports(gateway, reports)

    assert len(gateway.keys) == 2
    for report in (served, bare):
        assert _raw_keys_in(_app_frames(report), gateway) == []
    for view in _frames_of(bare, "agentcc.views.mcp_service_provisioning", "post"):
        assert view["vars"]["raw_key"] == "[Filtered]"
    for answer in _frames_of(bare, "agentcc.views.mcp_service_provisioning", "_ok"):
        assert answer["vars"]["result"]["key"] == "[Filtered]"


@pytest.mark.django_db
def test_gateway_key_create_failure_reports_no_raw_key(gateway, reports):
    """An unexpected failure in release's key create
    (``auth_bridge.provision_key``) after the gateway answered with the new
    key: storing the key's row fails, so the key is revoked on the gateway and
    the error is reported while the frame holds the key in ``raw_key`` and in
    the gateway's answer. Every variable of each frame is kept."""
    org, owner, _ = _provisioning_tests._org()
    # provision_key stores the row through the locked queryset's get_or_create;
    # that store is the one that fails here.
    keys = Mock(db=AgentccAPIKey.no_workspace_objects.db)
    keys.get_or_create.side_effect = _StoreFailed("db down")
    with (
        patch.object(
            AgentccAPIKey.no_workspace_objects,
            "select_for_update",
            return_value=keys,
        ),
        _every_local(),
    ):
        report = reports.report(
            partial(
                auth_bridge.provision_key,
                name="CI key",
                user=owner,
                organization=org,
            ),
            _StoreFailed,
        )

    (key_id,) = gateway.keys
    assert gateway.revoked == [key_id]
    assert _raw_keys_in(_app_frames(report), gateway) == []
    for create in _frames_of(report, "agentcc.services.auth_bridge", "provision_key"):
        assert create["vars"]["raw_key"] == "[Filtered]"
        assert create["vars"]["result"]["key"] == "[Filtered]"
        assert key_id in _dump(create["vars"]["result"])
