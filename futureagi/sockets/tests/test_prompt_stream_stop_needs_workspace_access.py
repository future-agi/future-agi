"""Stopping a prompt stream takes the workspace check that starting one takes.

Every socket here is opened through ``tfc.asgi.application`` with a one-time
ticket for a real access session, as the web app connects to
``ws/prompt-stream/``. Only the runs themselves are stood in for: every frame,
answer and close comes from the real consumer, and a recorded stop is read
back through the manager's own ``is_*_stopped`` for the organization and id.

1. A connection that names a workspace the person cannot use: a stop gets the
   answer the run path gives there, nothing is recorded and no
   ``stop_acknowledged`` arrives.
2. The person's own workspace: the stop is recorded and acknowledged.
3. A connection that names no workspace: the soft answer, nothing recorded.
4. A SAML session for another organization's workspace: the stop is refused.
5. A person with no organization: the connect is answered with 4002, with no
   accept.
6. A person with an organization: the connect is accepted and stays open.
7. Product code resolves a stop's workspace only through the full check.
"""

import ast
import itertools
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from django.core.cache import cache
from django.utils import timezone

from accounts.models.auth_token import AuthToken, AuthTokenOrigin, AuthTokenType
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.handoff_exchange import KIND_WS, issue
from model_hub.utils.websocket_direct_manager import WebSocketDirectManager
from saml2_auth.models import SAMLMetadataModel
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.integration

PROMPT_STREAM_PATH = "/ws/prompt-stream/"
STOP_KINDS = ("stop_streaming", "stop_improve_prompt", "stop_generate_prompt")
RUNNERS = ("run_template_async", "improve_prompt_async", "generate_prompt_async")
UNUSABLE_WORKSPACES = (
    "another_organizations_workspace",
    "own_organization_workspace_without_membership",
)
UNUSABLE_CASES = list(itertools.product(UNUSABLE_WORKSPACES, STOP_KINDS))
PERMISSION_MESSAGE = "You do not have permission to use this workspace."
PRODUCT_FOLDERS = ("sockets", "model_hub")
# The answer to a connect that is accepted and stays open.
OPEN = (True, None)


# --------------------------------------------------------------------- people


def _person(label, organization, role=OrganizationRoles.MEMBER):
    return User.objects.create_user(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        name=f"Example {label}",
        organization=organization,
        organization_role=role,
    )


def _membership(user, organization, *, owner=False, is_active=True):
    return OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=OrganizationRoles.OWNER if owner else OrganizationRoles.MEMBER,
        level=Level.OWNER if owner else Level.MEMBER,
        is_active=is_active,
    )


def _workspace(organization, owner, name, *, is_default=True):
    return Workspace.no_workspace_objects.create(
        name=name,
        organization=organization,
        is_default=is_default,
        is_active=True,
        created_by=owner,
    )


def _join(user, workspace, membership):
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role="Workspace Member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
        organization_membership=membership,
    )


def _organization(label):
    """An organization with its owner and a default workspace."""
    organization = Organization.objects.create(name=f"Example {label}")
    owner = _person(f"{label}-owner", organization, role=OrganizationRoles.OWNER)
    _membership(owner, organization, owner=True)
    return organization, owner, _workspace(organization, owner, f"Example {label}")


def _session(user, **saml_scope):
    """An access session row, as signing in creates one."""
    return AuthToken.no_workspace_objects.create(
        user=user,
        auth_type=AuthTokenType.ACCESS.value,
        last_used_at=timezone.now(),
        is_active=True,
        **saml_scope,
    )


@pytest.fixture
def world(transactional_db, settings, monkeypatch):
    """Committed rows (the consumer reads them on other threads), an isolated
    cache for tickets and stops, the in-memory channel layer, and stand-ins
    for the three runs."""
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    settings.CACHES = {
        **settings.CACHES,
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"prompt-stream-stop-{uuid.uuid4()}",
        },
    }
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }
    cache.clear()

    runs = []

    async def run_stand_in(**kwargs):
        runs.append(sorted(kwargs))

    for runner in RUNNERS:
        monkeypatch.setattr(f"sockets.prompt_stream_consumer.{runner}", run_stand_in)

    # Imported here, outside the event loop, as the server imports it at start.
    from saml2_auth.tests.ws_helpers import WebsocketCommunicator
    from tfc.asgi import application

    clear_workspace_context()
    org_a, owner_a, workspace_a = _organization("a")
    org_b, _owner_b, workspace_b = _organization("b")
    second_workspace_a = _workspace(
        org_a, owner_a, "Example a second", is_default=False
    )

    # A member of A who uses A's default workspace only.
    member = _person("member", org_a)
    _join(member, workspace_a, _membership(member, org_a))

    # A member of A and of B (with B's workspace), signed in by A's SAML IdP.
    saml_person = _person("saml-person", org_a)
    _membership(saml_person, org_a)
    _join(saml_person, workspace_b, _membership(saml_person, org_b))
    idp = SAMLMetadataModel.no_workspace_objects.create(
        organization=org_a,
        identity_type=SAMLMetadataModel.IDENTITY_OKTA,
        relay_state=f"example-relay-{uuid.uuid4().hex}",
        is_enabled=True,
        meta="",
    )

    # A person whose only membership is inactive: no organization.
    removed_person = _person("removed-person", org_a)
    _membership(removed_person, org_a, is_active=False)

    data = SimpleNamespace(
        application=application,
        communicator=WebsocketCommunicator,
        workspace_a=workspace_a,
        workspace_b=workspace_b,
        another_organizations_workspace=workspace_b,
        own_organization_workspace_without_membership=second_workspace_a,
        member_session=_session(member),
        saml_session=_session(
            saml_person,
            auth_origin=AuthTokenOrigin.SAML,
            scoped_organization=org_a,
            origin_idp=idp,
            origin_idp_generation=idp.security_generation,
        ),
        removed_session=_session(removed_person),
        runs=runs,
    )
    clear_workspace_context()
    yield data
    cache.clear()
    clear_workspace_context()


# -------------------------------------------------------------------- sockets


async def _connect(world, session, query):
    """Open ``ws/prompt-stream/`` with a fresh ticket, as the web app does.

    Returns the socket and its answer to the connect, read past the accept:
    ``(True, None)`` when it is accepted and stays open, ``(True, code)`` when
    a close follows the accept, ``(False, code)`` when a close answers it.
    ``receive_output`` stops the consumer when it times out, so an open socket
    is told apart with ``receive_nothing`` first.
    """
    ticket = issue(
        KIND_WS,
        {
            "user_id": str(session.user_id),
            "session_id": str(session.id),
            "path": PROMPT_STREAM_PATH,
            "query": query,
        },
    )
    url = PROMPT_STREAM_PATH + "?" + urlencode({"ticket": ticket})
    socket = world.communicator(world.application, url)
    connected, detail = await socket.connect(timeout=5)
    if not connected:
        return socket, (False, detail)
    if await socket.receive_nothing(timeout=0.5):
        return socket, OPEN
    message = await socket.receive_output(timeout=1)
    return socket, (True, message.get("code", message["type"]))


async def _frames(socket, *, until_close):
    """The frames that answer a request, and the close code if a close
    follows them (``None`` when the socket goes quiet and stays open)."""
    frames = []
    message = await socket.receive_output(timeout=5)
    while message["type"] != "websocket.close":
        frames.append(json.loads(message["text"]))
        if not until_close and await socket.receive_nothing(timeout=1):
            return frames, None
        message = await socket.receive_output(timeout=5)
    return frames, message.get("code", 1000)


def _work(kind):
    """A stop of ``kind``: its correlation, the action its soft answer names,
    and the frame that starts the same kind of work."""
    if kind == "stop_streaming":
        correlation = {"template_id": str(uuid.uuid4()), "version": "v1"}
        return correlation, "stop stream", {"type": "run_template", **correlation}
    if kind == "stop_improve_prompt":
        correlation = {"improve_id": f"improve_{uuid.uuid4()}"}
        start = {
            "type": "improve_prompt",
            "existing_prompt": "Summarize a text.",
            "improvement_requirements": "Keep it short.",
        }
        return correlation, "stop improve", start
    correlation = {"generation_id": f"generate_{uuid.uuid4()}"}
    start = {"type": "generate_prompt", "statement": "Summarize a text."}
    return correlation, "stop generate", start


async def _is_stopped(kind, organization_id, correlation):
    """Whether a stop of ``kind`` is recorded, as the manager itself reads it."""
    manager = WebSocketDirectManager(
        organization_id=organization_id,
        channel_name="",
        session_uuid="",
        channel_layer=None,
    )
    if kind == "stop_streaming":
        return await manager.is_streaming_stopped(
            correlation["template_id"], correlation["version"]
        )
    if kind == "stop_improve_prompt":
        return await manager.is_improve_prompt_stopped(correlation["improve_id"])
    return await manager.is_generate_prompt_stopped(correlation["generation_id"])


async def _stop(world, session, query, kind, organization_id):
    """Send one stop of ``kind`` on a fresh connection and observe the answer.

    The stop is read back under ``organization_id``: the organization of the
    workspace the connection names, or the person's own one when it names none.
    """
    correlation, action, _start = _work(kind)
    socket, connect = await _connect(world, session, query)
    frames, close = [], None
    try:
        if connect == OPEN:
            await socket.send_json_to({"type": kind, **correlation})
            frames, close = await _frames(socket, until_close=False)
    finally:
        await socket.disconnect()
    errors = [frame["message"] for frame in frames if frame.get("type") == "error"]
    observed = {
        "connect": connect,
        "frames": [frame.get("type") for frame in frames],
        "correlated": all(
            frame.get(key) == value
            for frame in frames
            for key, value in correlation.items()
        ),
        "error": errors[0] if errors else None,
        "close": close,
        "recorded": await _is_stopped(kind, organization_id, correlation),
    }
    return observed, action


async def _run_path_answer(world, session, query, kind):
    """The connect answer, error message and close code that starting the same
    kind of work gets on this connection."""
    _correlation, _action, start = _work(kind)
    socket, connect = await _connect(world, session, query)
    frames, close = [], None
    try:
        if connect == OPEN:
            await socket.send_json_to(start)
            frames, close = await _frames(socket, until_close=True)
    finally:
        await socket.disconnect()
    errors = [frame["message"] for frame in frames if frame.get("type") == "error"]
    return connect, (errors[-1] if errors else None), close


# ---------------------------------------------------------------------- stops


@pytest.mark.asyncio
@pytest.mark.parametrize(("workspace_name", "kind"), UNUSABLE_CASES)
async def test_a_stop_in_a_workspace_the_person_cannot_use_gets_the_run_paths_answer(
    world, workspace_name, kind
):
    """A stop in a workspace the person cannot use gets the answer that
    starting a run there gets: the same error and close code. Nothing is
    recorded, no ``stop_acknowledged`` arrives and no run starts."""
    workspace = getattr(world, workspace_name)
    query = {"workspace_id": str(workspace.id)}

    run = await _run_path_answer(world, world.member_session, query, kind)
    observed, _action = await _stop(
        world, world.member_session, query, kind, workspace.organization_id
    )

    assert run == (OPEN, PERMISSION_MESSAGE, 4003)
    assert observed == {
        "connect": OPEN,
        "frames": ["error"],
        "correlated": True,
        "error": run[1],
        "close": run[2],
        "recorded": False,
    }
    assert world.runs == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", STOP_KINDS)
async def test_a_stop_in_the_persons_own_workspace_is_recorded_and_acknowledged(
    world, kind
):
    """Control: a stop in a workspace the person can use is recorded and
    acknowledged, and the connection stays open."""
    workspace = world.workspace_a
    observed, _action = await _stop(
        world,
        world.member_session,
        {"workspace_id": str(workspace.id)},
        kind,
        workspace.organization_id,
    )

    assert observed == {
        "connect": OPEN,
        "frames": ["stop_acknowledged"],
        "correlated": True,
        "error": None,
        "close": None,
        "recorded": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", STOP_KINDS)
async def test_a_stop_on_a_connection_with_no_workspace_gets_the_soft_answer(
    world, kind
):
    """Guard: on a connection that names no workspace a stop gets the soft
    answer, the connection stays open and nothing is recorded."""
    observed, action = await _stop(
        world, world.member_session, {}, kind, world.workspace_a.organization_id
    )

    assert observed == {
        "connect": OPEN,
        "frames": ["error"],
        "correlated": True,
        "error": f"Cannot {action}: workspace not resolved.",
        "close": None,
        "recorded": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", STOP_KINDS)
async def test_a_stop_in_a_saml_session_for_another_organizations_workspace_is_refused(
    world, kind
):
    """Guard: a SAML session acts for one organization. A stop for a workspace
    of another organization, one the person uses outside that session, is
    refused: an error answers it, nothing is recorded and no
    ``stop_acknowledged`` arrives."""
    workspace = world.workspace_b
    observed, _action = await _stop(
        world,
        world.saml_session,
        {"workspace_id": str(workspace.id)},
        kind,
        workspace.organization_id,
    )

    assert observed["connect"] == OPEN
    assert observed["frames"] == ["error"]
    assert observed["correlated"] is True
    assert observed["recorded"] is False


# ------------------------------------------------------------------- connects


@pytest.mark.asyncio
async def test_a_person_with_no_organization_is_closed_with_4002_at_connect(world):
    """A person whose only membership is inactive has no organization: the
    connect is answered with 4002 and no accept, as the app's other live
    connections answer it."""
    socket, connect = await _connect(
        world, world.removed_session, {"workspace_id": str(world.workspace_a.id)}
    )
    await socket.disconnect()

    assert connect == (False, 4002)


@pytest.mark.asyncio
async def test_a_person_with_an_organization_connects(world):
    """Control: a person with an organization is accepted, and the socket
    stays open after the accept."""
    socket, connect = await _connect(
        world, world.member_session, {"workspace_id": str(world.workspace_a.id)}
    )
    await socket.disconnect()

    assert connect == OPEN


# ---------------------------------------------------------------- source scan


def _product_files():
    backend = Path(__file__).resolve().parents[2]
    for folder in PRODUCT_FOLDERS:
        for path in sorted((backend / folder).rglob("*.py")):
            relative = path.relative_to(backend)
            if "tests" in relative.parts or path.name.startswith("test_"):
                continue
            yield relative, path


def test_every_stop_resolves_its_workspace_through_the_full_check():
    """Product code resolves a stop's workspace only through the full check
    (``WorkspaceAccessGate.resolve``): no file under ``sockets`` or
    ``model_hub``, tests aside, calls or names ``resolve_org_for_stop``."""
    references = []
    for relative, path in _product_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, ast.Name):
                name = node.id
            else:
                continue
            if name == "resolve_org_for_stop":
                references.append(f"{relative}:{node.lineno}")

    assert references == []
