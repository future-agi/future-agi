"""Offline tests for the harness. No model calls, no network, no credentials.

Every case here encodes something that must stay true for a generated environment to be
trustworthy: the contract cannot be structurally wrong, an unsupported agent source refuses
rather than half-works, and the submit gate returns its problems instead of writing a bad file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from harness.cli import build_parser
from harness.scenario import Scenario
from harness.session import ARTIFACT, DONE, TEXT, TOOL, Event
from harness.tools import accept_contract, qualified
from harness.understand import load, opening

from harness import (
    AgentContract,
    GitHubSource,
    RepoSource,
    SpecSource,
    ToolSpec,
    artifact_dir,
    load_skill,
    provider_env,
    register_source,
    resolve,
    supported,
    validate_contract,
)


async def _list_tools(instance) -> list:
    """The server's tools, whichever mcp handler API this version exposes.

    Classic mcp keys the handler table by request class and wraps results in ServerResult;
    newer mcp keys it by method string with HandlerEntry(handler, params_type) values and
    returns the result bare. The tests only care about the tool list either way.
    """
    table = getattr(instance, "request_handlers", None)
    if table is not None:
        from mcp.types import ListToolsRequest

        for key, handler in table.items():
            if getattr(key, "__name__", "") == "ListToolsRequest":
                result = await handler(ListToolsRequest(method="tools/list"))
                return list(result.root.tools)
        return []
    entry = instance._request_handlers["tools/list"]
    result = await entry.handler(None, None)
    return list(result.tools)


async def _call_tool(instance, name: str, arguments: dict) -> str:
    """Call one tool through the server and hand back the text it answered with."""
    from mcp.types import CallToolRequestParams

    table = getattr(instance, "request_handlers", None)
    if table is not None:
        from mcp.types import CallToolRequest

        for key, handler in table.items():
            if getattr(key, "__name__", "") == "CallToolRequest":
                answer = await handler(
                    CallToolRequest(
                        method="tools/call",
                        params=CallToolRequestParams(name=name, arguments=arguments),
                    )
                )
                return answer.root.content[0].text
        raise AssertionError("this server has no tool-call handler")
    entry = instance._request_handlers["tools/call"]
    answer = await entry.handler(None, CallToolRequestParams(name=name, arguments=arguments))
    return answer.content[0].text


def _schema_of(tool) -> dict:
    """A tool's input schema under either mcp Tool field casing."""
    schema = getattr(tool, "inputSchema", None)
    return schema if schema is not None else tool.input_schema


def _contract(**overrides) -> AgentContract:
    payload = {
        "agent": "drive_thru",
        "tools": [ToolSpec(name="order", args=["item_id"])],
        "real_use_cases": ["order an item"],
    }
    payload.update(overrides)
    return AgentContract(**payload)


# --- contract ------------------------------------------------------------------------


def test_valid_contract_has_no_problems():
    assert validate_contract(_contract()) == []


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"agent": " "}, "empty:agent"),
        ({"tools": []}, "no-tools"),
        ({"real_use_cases": []}, "no-use-cases"),
    ],
)
def test_validate_contract_catches_structural_problems(overrides, expected):
    assert expected in validate_contract(_contract(**overrides))


def test_duplicate_tool_names_are_rejected_and_named():
    """Names the offender: tool_names() is a set, so a naive length comparison never fires."""
    contract = _contract(tools=[ToolSpec(name="order"), ToolSpec(name="order")])
    assert "duplicate-tool-names:order" in validate_contract(contract)


def test_types_declared_for_arguments_that_do_not_exist_are_rejected():
    """A type on an argument the tool does not take means the reader misread the signature,
    and a world built from it would be wrong in a way nothing downstream could detect."""
    contract = _contract(
        tools=[ToolSpec(name="order", args=["item_id"], arg_types={"size": "str"})]
    )
    assert "tool[order]:types-for-unknown-args:size" in validate_contract(contract)


def test_brief_carries_argument_types_into_downstream_prompts():
    contract = _contract(
        tools=[
            ToolSpec(
                name="remove_order_item",
                args=["order_id"],
                arg_types={"order_id": "list[str]"},
            )
        ]
    )
    assert "remove_order_item(order_id: list[str])" in contract.brief()


def test_shapes_are_normalised_rather_than_rejected():
    """Benign shape variance is not a grounding error; rejecting it burns turns for nothing."""
    contract = AgentContract.model_validate(
        {
            "agent": "x",
            "one_liner": ["a", "b"],
            "hard_constraints": "only one rule",
            "data_schema": [1, 2],
        }
    )
    assert contract.one_liner == "a\nb"
    assert contract.hard_constraints == ["only one rule"]
    assert contract.data_schema == {"value": [1, 2]}


# --- sources -------------------------------------------------------------------------


def test_repo_and_spec_sources_are_registered():
    assert {"repo", "github", "spec"}.issubset(set(supported()))


def test_unsupported_source_refuses_and_names_what_exists():
    with pytest.raises(NotImplementedError) as raised:
        resolve("browser", name="x")
    assert "repo" in str(raised.value)


def test_repo_source_gets_read_tools_and_a_briefing_that_points_at_the_code(tmp_path):
    source = RepoSource(name="a", root=tmp_path)
    assert source.builtin_tools() == ("Read", "Glob", "Grep")
    assert str(tmp_path) in source.briefing()


def test_github_source_reads_like_a_repository(tmp_path):
    source = GitHubSource(name="a", root=tmp_path, url="https://github.com/acme/agent")
    assert source.builtin_tools() == ("Read", "Glob", "Grep")
    assert "https://github.com/acme/agent" in source.briefing()


def test_spec_source_gets_no_file_tools_because_there_is_nothing_to_read():
    source = SpecSource(
        name="a", system_prompt="you are a bot", tool_schema=[{"name": "t"}]
    )
    assert source.builtin_tools() == ()
    briefing = source.briefing()
    assert "you are a bot" in briefing and "t" in briefing


def test_a_new_kind_of_agent_is_a_registration_not_a_code_change():
    register_source("fake", lambda **kw: RepoSource(name=kw["name"], root="."))
    assert resolve("fake", name="z").name == "z"


# --- session events ------------------------------------------------------------------


@pytest.mark.parametrize(
    "event,expected",
    [
        (Event(TEXT, text="hello"), "hello"),
        (Event(TOOL, tool="Read", detail={"target": "agent.py"}), "  [Read agent.py]"),
        (Event(TOOL, tool="Grep"), "  [Grep]"),
        (
            Event(ARTIFACT, detail={"path": "a/contract.json"}),
            "  [saved a/contract.json]",
        ),
    ],
)
def test_events_render_for_a_terminal(event, expected):
    assert event.line() == expected


def test_done_event_reports_outcome_turns_and_spend():
    line = Event(
        DONE, detail={"outcome": "success", "turns": 9, "cost_usd": 0.36}
    ).line()
    assert "success" in line and "turns=9" in line and "0.36" in line


# --- the submit gate -----------------------------------------------------------------


def test_submit_writes_the_contract_when_it_is_valid(tmp_path):
    result = accept_contract(
        {
            "agent": "drive_thru",
            "tools": [{"name": "order", "args": ["item_id"]}],
            "real_use_cases": ["order an item"],
        },
        tmp_path,
    )
    assert not result.get("is_error")
    written = json.loads((tmp_path / "contract.json").read_text())
    assert written["agent"] == "drive_thru"


def test_submit_returns_problems_and_writes_nothing_when_invalid(tmp_path):
    """The gate reports into the conversation so the next turn can fix it, which is the only
    reason a bad contract does not reach disk."""
    result = accept_contract(
        {"agent": "drive_thru", "tools": [], "real_use_cases": []}, tmp_path
    )
    assert result.get("is_error")
    text = result["content"][0]["text"]
    assert "no-tools" in text and "no-use-cases" in text
    assert not (tmp_path / "contract.json").exists()


def test_load_returns_none_when_the_stage_produced_nothing(tmp_path):
    assert load(tmp_path) is None


# --- the world gate ------------------------------------------------------------------


def _cart_world():
    from harness.world import GeneratedWorld

    class W(GeneratedWorld):
        name = "cart"
        tools = [{"name": "add"}, {"name": "lst"}]
        handlers = {
            "add": (
                "def handle(args, db):\n"
                "    if 'item_id' not in args: raise ToolError('item_id is required')\n"
                "    m = db.one('SELECT * FROM menu WHERE id=?', [args['item_id']])\n"
                "    if not m: raise ToolError('no item %r' % args['item_id'])\n"
                "    db.execute('INSERT INTO cart (item_id) VALUES (?)', [args['item_id']])\n"
                "    return {'ok': 1}\n"
            ),
            "lst": "def handle(args, db):\n    return db.query('SELECT * FROM cart')\n",
        }

    world = W(":memory:")
    world.connection.executescript(
        "CREATE TABLE menu(id TEXT PRIMARY KEY); CREATE TABLE cart(item_id TEXT);"
    )
    world.connection.execute("INSERT INTO menu VALUES ('big_mac')")
    world.connection.commit()
    contract = AgentContract(
        agent="cart",
        real_use_cases=["add an item"],
        tools=[
            ToolSpec(name="add", args=["item_id"], arg_values={"item_id": ["big_mac"]}),
            ToolSpec(name="lst"),
        ],
    )
    return world, contract


_SEQUENCE = [
    {
        "name": "add-then-list",
        "calls": [
            {"tool": "add", "arguments": {"item_id": "big_mac"}},
            {"tool": "lst", "arguments": {}},
        ],
        "expect_state": {"cart.count": 1},
    }
]


def test_a_sound_world_passes_every_probe():
    from harness.world import probe

    world, contract = _cart_world()
    report = probe(world, contract, sequences=_SEQUENCE)
    assert report.score == 1.0, report.summary()


def test_probing_leaves_the_world_exactly_as_it_found_it():
    """Probes mutate. Without reverting between them, each inherits the last one's debris and
    a sequence expecting one row finds several, which reads as a bug in the world."""
    from harness.world import probe

    world, contract = _cart_world()
    probe(world, contract, sequences=_SEQUENCE)
    assert world.state()["cart"] == []


def test_probing_is_repeatable():
    from harness.world import probe

    world, contract = _cart_world()
    first = probe(world, contract, sequences=_SEQUENCE).score
    second = probe(world, contract, sequences=_SEQUENCE).score
    assert first == second == 1.0


def test_a_tool_that_succeeds_on_a_nonexistent_id_fails_the_gate():
    """The defect the whole thing exists to catch: a call that should have been refused."""
    from harness.world import probe

    world, contract = _cart_world()
    world.handlers["add"] = (
        "def handle(args, db):\n"
        "    db.execute('INSERT INTO cart (item_id) VALUES (?)', [args.get('item_id')])\n"
        "    return {'ok': 1}\n"
    )
    report = probe(world, contract, sequences=_SEQUENCE)
    assert any("does not exist" in failure.detail for failure in report.failures), (
        report.summary()
    )
    assert report.score < 0.85


def test_a_crash_is_distinguished_from_a_refusal():
    from harness.world import probe

    world, contract = _cart_world()
    world.handlers["add"] = (
        "def handle(args, db):\n    return {'id': args['item_id']}\n"
    )
    report = probe(world, contract, sequences=_SEQUENCE)
    assert any("crashed instead of refusing" in f.detail for f in report.failures)


def test_a_world_reverts_to_a_checkpoint():
    world, _ = _cart_world()
    mark = world.checkpoint()
    world.call("add", {"item_id": "big_mac"})
    assert len(world.state()["cart"]) == 1
    world.revert(mark)
    assert world.state()["cart"] == []


# --- wiring --------------------------------------------------------------------------


def test_provider_env_pins_the_model_and_never_invents_a_project(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    env = provider_env("claude-sonnet-4-6")
    assert env["CLAUDE_CODE_USE_VERTEX"] == "1"
    assert env["ANTHROPIC_MODEL"] == "claude-sonnet-4-6"
    assert "ANTHROPIC_VERTEX_PROJECT_ID" not in env


def test_qualified_tool_name_matches_the_mcp_convention():
    assert qualified("contract", "submit_contract") == "mcp__contract__submit_contract"


def test_the_skill_exists_and_forbids_guessing():
    text = load_skill("understand-agent")
    assert "submit_contract" in text
    assert "guess" in text.lower()


def test_artifacts_land_under_the_agent_name():
    assert artifact_dir("drive_thru").as_posix().endswith("sessions/drive_thru")


def test_cli_defaults_to_staying_open_for_corrections():
    args = build_parser().parse_args(["understand", "--name", "a", "--path", "."])
    assert args.interactive is True
    assert (
        build_parser()
        .parse_args(["understand", "--name", "a", "--path", ".", "--once"])
        .interactive
        is False
    )


def test_opening_names_the_agent_and_asks_for_the_contract(tmp_path):
    text = opening(RepoSource(name="drive_thru", root=tmp_path))
    assert "drive_thru" in text and "submit_contract" in text


# --- state expectations, shared by the gate and the grading --------------------------


_STATE = {"orders": [{"id": "a", "item": "big_mac"}], "menu": [{"id": "big_mac"}]}


# --- scenarios -----------------------------------------------------------------------


def _saved_world(tmp_path):
    from harness.world.snapshot import save

    world, contract = _cart_world()
    save(world, tmp_path, notes="test world")
    return tmp_path, contract


def _scenario(**overrides):
    payload = {
        "name": "orders-a-big-mac",
        "tests": "the ordinary case",
        "goal": "order a big mac",
        "persona": "brisk",
        "opening": "one big mac please",
        "expect_state": {"cart.count": 1},
    }
    payload.update(overrides)
    return payload


# --- running and grading -------------------------------------------------------------


def test_declared_types_become_something_a_tool_schema_can_carry():
    from harness.run.targets import _python_type

    assert _python_type("list[str]") is list
    assert _python_type("int") is int
    assert _python_type("") is str


def test_the_agent_under_test_is_told_its_own_rules():
    from harness.run.targets import agent_prompt

    _world, contract = _cart_world()
    contract.hard_constraints = ["never substitute an item without asking"]
    assert "never substitute" in agent_prompt(contract)


def test_the_cli_exposes_every_stage_and_one_conversation_across_them():
    parser = build_parser()
    assert parser.parse_args(["scenarios", "--name", "a", "--count", "10"]).count == 10
    assert parser.parse_args(["run", "--name", "a"]).target == "local"


def test_talking_to_it_needs_nothing_on_the_command_line():
    """Which agent, where it lives and how many scenarios are all things you say."""
    parser = build_parser()
    assert parser.parse_args(["chat"]).name is None
    assert parser.parse_args(["chat"]).path is None


def test_a_conversation_resumes_at_whichever_stage_the_artifacts_reached(tmp_path):
    from harness.chat import BUILD, SCENARIOS, UNDERSTAND, open_conversation

    conversation = open_conversation(name="a", path=str(tmp_path), out=tmp_path)
    assert conversation._resume_at() == UNDERSTAND

    accept_contract(
        {
            "agent": "a",
            "real_use_cases": ["order"],
            "tools": [{"name": "add", "args": ["item_id"]}],
        },
        tmp_path,
    )
    assert conversation._resume_at() == BUILD

    _saved_world(tmp_path)
    assert conversation._resume_at() == SCENARIOS


def test_where_a_conversation_is_agrees_with_what_was_built(tmp_path):
    from harness.chat import SCENARIOS, open_conversation

    accept_contract(
        {
            "agent": "a",
            "real_use_cases": ["order"],
            "tools": [{"name": "add", "args": ["item_id"]}],
        },
        tmp_path,
    )
    _saved_world(tmp_path)
    conversation = open_conversation(name="a", path=str(tmp_path), out=tmp_path)
    assert conversation.stage_name == SCENARIOS
    assert conversation.next_stage() is None


def test_a_conversation_with_no_agent_starts_by_asking_which_one():
    from harness.chat import RECEPTION, open_conversation

    conversation = open_conversation()
    assert conversation.source is None
    assert conversation.stage_name == RECEPTION
    assert conversation.next_stage() is None


def test_pointing_at_an_agent_settles_where_its_artifacts_go(tmp_path):
    import asyncio

    from harness.chat import UNDERSTAND, open_conversation
    from harness.sources import RepoSource

    conversation = open_conversation()
    conversation._found["source"] = RepoSource(name="mine", root=tmp_path)

    async def _settle():
        # Reception is the only stage whose result is not a file, so the conversation reads it
        # back rather than looking on disk. Advancing needs a live session, so only the
        # settling half is exercised here.
        settled = conversation._found.pop("source")
        conversation.source = settled
        conversation.out = conversation.out or artifact_dir(settled.name)

    asyncio.run(_settle())
    assert conversation.out.as_posix().endswith("sessions/mine")
    assert conversation._resume_at() == UNDERSTAND


def test_pointing_at_somewhere_that_does_not_exist_is_refused(tmp_path):
    from harness.reception import point_at

    found = {}
    refused = point_at("mine", str(tmp_path / "nope"), "repo", found)
    assert refused["is_error"] and found == {}

    accepted = point_at("mine", str(tmp_path), "repo", found)
    assert not accepted.get("is_error")
    assert found["source"].name == "mine"


def test_pointing_at_a_github_url_clones_it_into_the_session(tmp_path, monkeypatch):
    from harness.reception import point_at

    called = {}

    def clone(command, **kwargs):
        called["command"] = command
        destination = Path(command[-1])
        destination.mkdir(parents=True)
        return type("Completed", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr("harness.sources.subprocess.run", clone)
    found = {}
    source_dir = tmp_path / "session" / "source"
    accepted = point_at(
        "demo-agent",
        "https://github.com/acme/demo-agent",
        "github",
        found,
        source_dir=source_dir,
    )

    assert not accepted.get("is_error")
    assert called["command"][:4] == ["git", "clone", "--depth", "1"]
    assert found["source"].root == source_dir
    assert found["source"].kind == "github"


@pytest.mark.parametrize(
    "url",
    ["git@github.com:acme/demo-agent.git", "https://example.com/acme/demo-agent", "https://github.com/acme"],
)
def test_github_source_refuses_urls_that_cannot_be_public_https_clones(tmp_path, url):
    from harness.reception import point_at

    refused = point_at("demo-agent", url, "github", {}, source_dir=tmp_path / "source")
    assert refused["is_error"]


def test_how_many_scenarios_is_something_you_say():
    from harness.scenario_tools import TOOL_NAMES

    assert "aim_for" in TOOL_NAMES


# --- amending the contract -----------------------------------------------------------


def _written_contract(tmp_path):
    accept_contract(
        {
            "agent": "cart",
            "real_use_cases": ["add an item"],
            "tools": [
                {
                    "name": "add",
                    "args": ["item_id"],
                    "arg_values": {"item_id": ["big_mac"]},
                }
            ],
        },
        tmp_path,
    )
    return load(tmp_path)


def test_the_agent_can_be_taught_a_value_it_did_not_accept(tmp_path):
    """A world that gains an item the agent cannot name holds dead data, and every scenario
    about it can only fail. The two have to move together."""
    from harness.amend import widen

    contract = _written_contract(tmp_path)
    done, said = widen(
        contract,
        tmp_path,
        tool_name="add",
        argument="item_id",
        values=["mango_smoothie"],
        why="added to the menu this morning",
    )
    assert done, said
    assert "mango_smoothie" in contract.tools[0].arg_values["item_id"]
    # the stage's own copy and the file agree, or the stage checks against an action space
    # that no longer exists
    assert "mango_smoothie" in load(tmp_path).tools[0].arg_values["item_id"]


def test_an_amendment_is_recorded_rather_than_blended_in(tmp_path):
    from harness.amend import widen

    contract = _written_contract(tmp_path)
    widen(
        contract,
        tmp_path,
        tool_name="add",
        argument="item_id",
        values=["mango_smoothie"],
        why="added to the menu this morning",
    )
    recorded = load(tmp_path).amendments
    assert len(recorded) == 1
    assert "mango_smoothie" in recorded[0] and "this morning" in recorded[0]


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"tool_name": "nope"}, "is not a tool this agent has"),
        ({"argument": "colour"}, "takes no argument"),
        ({"why": "  "}, "say why"),
        ({"values": ["big_mac"]}, "already accepts"),
    ],
)
def test_an_amendment_that_makes_no_sense_is_refused(tmp_path, overrides, expected):
    from harness.amend import widen

    contract = _written_contract(tmp_path)
    call = {
        "tool_name": "add",
        "argument": "item_id",
        "values": ["mango_smoothie"],
        "why": "because",
    }
    call.update(overrides)
    done, said = widen(contract, tmp_path, **call)
    assert not done and expected in said
    assert load(tmp_path).amendments == []


# --- what a stage is allowed to do ---------------------------------------------------


def test_a_stage_may_use_nothing_it_was_not_given():
    """Deny by default, not deny-a-list. A session is offered whatever its host exposes, and an
    allow-by-default gate let a host search tool through that cost a stage its whole budget."""
    import asyncio

    from harness.config import permission_gate

    gate = permission_gate(granted=["Read", "Glob"])
    for refused in ("Write", "Edit", "Bash", "Task", "ToolSearch", "WebFetch"):
        verdict = asyncio.run(gate(refused, {}, None))
        assert type(verdict).__name__ == "PermissionResultDeny"
        assert "not part of this stage" in verdict.message

    allowed = asyncio.run(gate("Read", {"file_path": "a.py"}, None))
    assert type(allowed).__name__ == "PermissionResultAllow"


def test_a_question_still_reaches_the_operator():
    import asyncio

    from harness.config import permission_gate

    asked = {}

    async def ask(tool_name, payload, _context):
        asked["tool"] = tool_name
        return "answered"

    assert asyncio.run(permission_gate(ask)("AskUserQuestion", {}, None)) == "answered"
    assert asked["tool"] == "AskUserQuestion"


# --- the tools a stage actually publishes ---------------------------------------------


def _published(server):
    """The tool names an in-process MCP server really exposes."""
    import asyncio


    instance = server.get("instance") if isinstance(server, dict) else server

    async def ask():
        return sorted(tool.name for tool in await _list_tools(instance))

    return asyncio.run(ask())


def test_every_stage_publishes_exactly_the_tools_it_claims(tmp_path):
    """A tool listed in TOOL_NAMES but left out of the server is granted, named in error
    messages, and does not exist. The model then hunts for it and works around the gate."""
    from harness.run import tools as runs
    from harness.world import tools as world

    from harness import scenario_tools as scenarios

    root, contract = _saved_world(tmp_path)
    server, _kept = scenarios.scenario_tools(contract, root, root, wanted=1)
    assert _published(server) == sorted(scenarios.TOOL_NAMES)

    built, _world = world.world_tools(contract, root)
    assert _published(built) == sorted(world.TOOL_NAMES)

    assert _published(runs.run_tools(root, root)) == sorted(runs.TOOL_NAMES)


def test_a_failed_call_is_not_reported_as_success():
    """A call that failed upstream still arrives with subtype "success", so reporting subtype
    verbatim tells somebody their stage worked when nothing happened."""
    from harness.session import _why_it_failed

    class Failed:
        api_error_status = 400
        errors = ['{"error":"invalid_grant","error_subtype":"invalid_rapt"}']

    said = _why_it_failed(Failed())
    assert "GOOGLE_APPLICATION_CREDENTIALS" in said and ".env.acceptance" in said

    class Other:
        api_error_status = 529
        errors = ["overloaded"]

    assert "529" in _why_it_failed(Other())


def test_the_credentials_in_play_are_said_out_loud(monkeypatch):
    from harness.config import credentials_hint

    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/keys/service-account.json")
    assert credentials_hint() == "credentials: service-account.json"

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS")
    assert "gcloud login" in credentials_hint()


def test_a_run_notices_when_it_was_billed_to_a_model_nobody_asked_for():
    """Asking for a model is not the same as getting one: the CLI has its own default, and a
    request that quietly does not take shows up only on the invoice."""
    from claude_agent_sdk import ClaudeAgentOptions
    from harness.session import Stage

    stage = Stage(ClaudeAgentOptions(model="claude-haiku-4-5"), name="s")
    stage.models_used = {"claude-haiku-4-5-20251001"}
    assert stage.unexpected_models() == set()

    stage.models_used = {"claude-opus-4-7"}
    assert stage.unexpected_models() == {"claude-opus-4-7"}


def test_an_agent_already_built_can_be_reopened_without_its_repository(tmp_path):
    """Coming back to fix a scenario should not mean pointing at the source again."""
    from harness.chat import SCENARIOS, Conversation

    accept_contract(
        {
            "agent": "a",
            "real_use_cases": ["order"],
            "tools": [{"name": "add", "args": ["item_id"]}],
        },
        tmp_path,
    )
    _saved_world(tmp_path)
    resumed = Conversation(source=None, out=tmp_path)
    assert resumed.stage_name == SCENARIOS


def test_a_rule_the_source_never_stated_can_be_added_and_is_recorded(tmp_path):
    """A hard constraint is told to the agent under test and graded by the judge, so adding one
    changes what is being tested and has to be visible as ours rather than the agent's."""
    from harness.amend import add_rule

    contract = _written_contract(tmp_path)
    done, said = add_rule(
        contract,
        tmp_path,
        rule="stays polite to customers",
        why="asked for on the call",
    )
    assert done and "graded from here on" in said
    reloaded = load(tmp_path)
    assert "stays polite to customers" in reloaded.hard_constraints
    assert "rule added" in reloaded.amendments[0] and "polite" in reloaded.amendments[0]

    again, why = add_rule(
        contract, tmp_path, rule="Stays Polite To Customers", why="again"
    )
    assert not again and "already has that rule" in why

    unexplained, said = add_rule(contract, tmp_path, rule="be fast", why=" ")
    assert not unexplained and "say why" in said


def test_a_collection_is_read_and_written_in_the_same_place():
    """A world can hold records in two places at once: a store the harness stood up, and the
    state the agent's own code keeps, adopted whole. `state()` merges them and lets the store win
    a name clash. The write path has to resolve a name the same way, or a scenario's setup
    changes one copy while its checks read the other, and every run is graded against a world
    that was never set up. Nothing else would show it: both copies exist and both look right."""
    from harness.world.runtime import GeneratedWorld
    from harness.world.stores import open_store

    store = open_store("in_process")
    store.start()
    store.start_collection("orders", keyed=True)
    store.add("orders", {"_id": "A1", "status": "pending", "who": "store"})

    world = GeneratedWorld(store=store)
    # The same collection name, in the agent's own state. Contrived, but this is exactly the
    # shape an adopted agent with a container store beside it produces.
    world.state_object = {"orders": {"A1": {"status": "pending", "who": "agent"}}}

    # The store wins the read...
    assert world.state()["orders"][0]["who"] == "store"
    # ...so it must win the write too.
    world.change("orders", "A1", {"status": "cancelled"}, by="_id")
    assert world.state()["orders"][0]["status"] == "cancelled"
    # and the agent's own copy is untouched, rather than half the world moving.
    assert world.state_object["orders"]["A1"]["status"] == "pending"
    world.close()


def test_the_modality_can_be_corrected_when_the_source_reads_the_other_way(tmp_path):
    """Modality picks the world, the simulated person and the transport, so a wrong one runs a
    different test rather than a weaker one. It is also the field a source settles worst: an
    agent's code reads the same answering a chat window or a phone call, so a repository that
    looks like a text benchmark reads as text even when the operator has deployed it to a phone
    number. Without this there was no correction short of running the whole stage again, which
    reads the same source and reaches the same answer."""
    from harness.amend import set_modality

    contract = _written_contract(tmp_path)
    contract.modality = "chat"

    done, said = set_modality(
        contract, tmp_path, modality="voice", why="deployed on Vapi, customers phone in"
    )
    assert done and "voice" in said
    reloaded = load(tmp_path)
    assert reloaded.modality == "voice"
    # Recorded as ours, like every other amendment, so the source and the correction stay apart.
    assert any("modality chat -> voice" in one for one in reloaded.amendments)

    same, said = set_modality(contract, tmp_path, modality="voice", why="again")
    assert not same and "already says voice" in said

    unknown, said = set_modality(contract, tmp_path, modality="telepathy", why="why not")
    assert not unknown and "is not a modality" in said

    unexplained, said = set_modality(contract, tmp_path, modality="chat", why=" ")
    assert not unexplained and "say why" in said
    assert load(tmp_path).modality == "voice"


def test_a_rule_the_agent_does_not_have_can_be_taken_away(tmp_path):
    """A rule nobody has is worse than a missing one: the agent is told to obey it and the
    judge fails it for not doing something it was never supposed to do."""
    from harness.amend import add_rule, drop_rule

    contract = _written_contract(tmp_path)
    add_rule(contract, tmp_path, rule="never upsell", why="misread from a comment")
    done, said = drop_rule(
        contract, tmp_path, rule="upsell", why="the source never says that"
    )
    assert done, said
    assert load(tmp_path).hard_constraints == []
    assert "rule removed" in load(tmp_path).amendments[-1]

    missing, said = drop_rule(contract, tmp_path, rule="be nice", why="x")
    assert not missing and "no rule like that" in said


def test_a_misread_tool_can_be_corrected(tmp_path):
    """The most damaging thing stage one can get wrong: every argument name flows into the
    handlers, the probes and the scenarios."""
    from harness.amend import fix_tool

    contract = _written_contract(tmp_path)
    done, said = fix_tool(
        contract,
        tmp_path,
        tool_name="add",
        args=["item_ids"],
        why="the signature takes a list, singular was a misread",
    )
    assert done, said
    fixed = load(tmp_path).tools[0]
    assert fixed.args == ["item_ids"]
    # values recorded against the old name must not silently survive under a name nobody uses
    assert "item_id" not in fixed.arg_values
    assert "dropped values recorded for item_id" in said


def test_a_tool_the_agent_does_not_have_can_be_removed(tmp_path):
    from harness.amend import fix_tool

    contract = _written_contract(tmp_path)
    contract.tools.append(ToolSpec(name="checkout", args=["id"]))
    done, said = fix_tool(
        contract,
        tmp_path,
        tool_name="checkout",
        remove=True,
        why="no such tool in the source",
    )
    assert done and "1 tools left" in said
    assert load(tmp_path).tool_names() == {"add"}


def test_correcting_a_contract_without_saying_why_is_refused(tmp_path):
    from harness.amend import drop_rule, fix_tool

    contract = _written_contract(tmp_path)
    assert not fix_tool(contract, tmp_path, tool_name="add", args=["x"], why=" ")[0]
    assert not drop_rule(contract, tmp_path, rule="anything", why="")[0]


def test_a_read_only_handler_does_not_poison_every_later_probe():
    """SQLite refuses to restore into a connection with a transaction open, and a handler that
    only reads leaves one behind. Unsettled, the first such handler makes the world impossible
    to check or save: "destination database is in use"."""
    from harness.world import probe

    world, contract = _cart_world()
    # lst only queries, which is what leaves the read transaction open
    world.call("lst", {})
    mark = world.checkpoint()
    world.call("add", {"item_id": "big_mac"})
    world.call("lst", {})
    world.revert(mark)
    assert world.state()["cart"] == []

    report = probe(world, contract, sequences=_SEQUENCE)
    assert report.score == 1.0, report.summary()


def test_a_row_put_in_wrong_can_be_taken_out_again(tmp_path):
    """Seeding only inserts. Without a way to remove a row, the only way left to make a check
    pass is to change the contract, which repairs the wrong thing."""
    import asyncio

    from harness.world import tools as world_tools

    _root, contract = _saved_world(tmp_path)
    server, world = world_tools.world_tools(contract, tmp_path)
    assert "change_data" in world_tools.TOOL_NAMES
    assert _published(server) == sorted(world_tools.TOOL_NAMES)

    world.connection.execute("INSERT INTO menu VALUES ('curry_sauce')")
    world.connection.commit()

    async def call(name, payload):
        instance = server.get("instance") if isinstance(server, dict) else server
        return await _call_tool(instance, name, payload)

    said = asyncio.run(
        call("change_data", {"sql": "DELETE FROM menu WHERE id='curry_sauce'"})
    )
    assert "1 rows changed" in said
    assert not [row for row in world.state()["menu"] if row["id"] == "curry_sauce"]

    refused = asyncio.run(call("change_data", {"sql": "SELECT * FROM menu"}))
    assert "UPDATE or DELETE" in refused


# --- the environment step: world, simulator prompt, sub-goal catalogue ---------------


def test_a_sub_goal_that_settles_nothing_is_rejected():
    """Every scenario referencing it would report a result nobody should believe."""
    from harness.catalogue import SubGoal, validate_sub_goal

    assert validate_sub_goal(SubGoal(name="x", what="means something")) != []
    settled = SubGoal(
        name="order-placed",
        what="the order reached the system",
        check="def check(world, calls):\n    return None\n",
    )
    assert validate_sub_goal(settled) == []
    assert settled.deterministic()

    judged = SubGoal(
        name="polite", what="stayed polite", judged="nothing observable shows tone"
    )
    assert validate_sub_goal(judged) == [] and not judged.deterministic()


def test_a_check_must_actually_define_one():
    from harness.catalogue import SubGoal, validate_sub_goal

    problems = validate_sub_goal(
        SubGoal(name="x", what="y", check="rows = world.state()['orders']")
    )
    assert any("check(world, calls)" in problem for problem in problems)


def test_a_simulator_prompt_without_a_slot_runs_the_same_conversation_every_time():
    from harness.simulator import fill, validate_simulator_prompt, variables_in

    fixed = (
        "You are a customer calling a drive-thru. Speak naturally, one turn at a time. "
        * 2
    )
    assert any(
        "no variables" in problem for problem in validate_simulator_prompt(fixed)
    )

    written = fixed + "\n\nWhat you want: {{ instruction }}\nWhat you know: {{ facts }}"
    assert validate_simulator_prompt(written) == []
    assert variables_in(written) == {"instruction", "facts"}

    filled, missing = fill(written, {"instruction": "order a big mac"})
    assert "order a big mac" in filled and missing == ["facts"]


def test_a_conversational_simulator_prompt_requires_a_persona_slot():
    from harness.simulator import validate_simulator_prompt

    prompt = "You are a customer. " * 10 + "\nWhat you want: {{ instruction }}"

    assert any("no persona slot" in problem for problem in validate_simulator_prompt(prompt, require_persona=True))
    assert validate_simulator_prompt(prompt + "\nWho you are: {{ persona }}", require_persona=True) == []


def test_a_persona_is_a_structured_simulator_prompt_slot():
    from harness.scenario import Persona, Scenario
    from harness.simulator import fill

    scenario = Scenario(
        name="anxious-rider",
        instruction="You need help finding your pickup point.",
        persona=Persona(
            name="Maya",
            occupation="rider",
            languages=["English", "Hindi"],
            accent="South Asian English",
            personality="anxious",
            communication_style="direct and concise",
            keywords=["in a noisy curbside area", "will ask for clarification"],
            multilingual=True,
            metadata={"pickup_context": "busy airport curb"},
        ),
    )

    filled, missing = fill("Caller:\n{{ persona }}\n\nNeed:\n{{ instruction }}", scenario.slots())

    assert missing == []
    assert "Name: Maya" in filled
    assert "Occupation: rider" in filled
    assert "Personality: anxious" in filled
    assert "Language(s): English, Hindi" in filled
    assert "Accent: South Asian English" in filled
    assert "Key Traits: in a noisy curbside area, will ask for clarification" in filled
    assert "Pickup Context: busy airport curb" in filled


def test_an_empty_persona_is_rejected():
    from harness.catalogue import Catalogue, SubGoal
    from harness.scenario import Persona, Scenario, validate_scenario

    scenario = Scenario(
        name="empty-persona",
        instruction="Place an order.",
        persona=Persona(),
        solution=[{"tool": "place", "arguments": {}}],
        sub_goals=["placed"],
    )
    catalogue = Catalogue(sub_goals=[SubGoal(name="placed", what="placed", judged="visible only to a judge")])

    problems = validate_scenario(scenario, catalogue, {}, "{{ persona }}\n{{ instruction }}")

    assert "persona has no details" in problems


def test_a_persona_must_contain_the_profile_that_drives_variation():
    from harness.catalogue import Catalogue, SubGoal
    from harness.scenario import Persona, Scenario, validate_scenario

    scenario = Scenario(
        name="thin-persona",
        instruction="Place an order.",
        persona=Persona(name="Maya"),
        solution=[{"tool": "place", "arguments": {}}],
        sub_goals=["placed"],
    )
    catalogue = Catalogue(sub_goals=[SubGoal(name="placed", what="placed", judged="visible only to a judge")])

    problems = validate_scenario(scenario, catalogue, {}, "{{ persona }}\n{{ instruction }}")

    assert any("persona is incomplete" in problem for problem in problems)
    assert all(field in problems[0] for field in ("personality", "languages", "accent"))


def test_a_check_that_raises_is_broken_not_failed():
    """A typo in an assertion must never read as a finding about the agent."""
    from harness.checks import run_check

    world, _contract = _cart_world()
    ok = run_check(
        "def check(world, calls):\n    return None\n", world, [], name="fine"
    )
    assert ok.held and not ok.broken

    failed = run_check(
        "def check(world, calls):\n    return 'no rows'\n", world, [], name="says-why"
    )
    assert not failed.held and not failed.broken and failed.said == "no rows"

    typo = run_check(
        "def check(world, calls):\n    return world.state()['nope'][0]\n",
        world,
        [],
        name="typo",
    )
    assert typo.broken and "KeyError" in typo.said


def test_a_check_can_insist_on_the_arguments_not_just_the_call():
    """Booking 10 PM when 11 PM was asked for is a failure, and detecting it is deterministic."""
    from harness.checks import run_check

    world, _contract = _cart_world()
    world.call("add", {"item_id": "big_mac"})
    source = (
        "def check(world, calls):\n"
        "    made = [c for c in calls if c.name == 'add']\n"
        "    if not made:\n        return 'never added anything'\n"
        "    if made[0].arguments.get('item_id') != 'fries':\n"
        "        return 'added %r, expected fries' % made[0].arguments.get('item_id')\n"
        "    return None\n"
    )
    outcome = run_check(source, world, world.calls, name="right-item")
    assert not outcome.held and "expected fries" in outcome.said


# --- scenarios as deltas, and the two gates ------------------------------------------


def _built_environment(tmp_path):
    """A saved world plus a catalogue, which is what the environment step leaves behind."""
    from harness.catalogue import Catalogue, SubGoal, save_catalogue
    from harness.world.snapshot import save

    world, contract = _cart_world()
    save(world, tmp_path, notes="test", sequences=[])
    catalogue = Catalogue(
        sub_goals=[
            SubGoal(
                name="item-added",
                what="the item reached the cart",
                check=(
                    "def check(world, calls):\n"
                    "    rows = world.state()['cart']\n"
                    "    if len(rows) != 1: return '%d rows, expected 1' % len(rows)\n"
                    "    return None\n"
                ),
            ),
            SubGoal(
                name="right-item",
                what="the call carried the item that was asked for",
                check=(
                    "def check(world, calls):\n"
                    "    made = [c for c in calls if c.name == 'add' and c.ok]\n"
                    "    if not made: return 'add was never called'\n"
                    "    got = made[0].arguments.get('item_id')\n"
                    "    return None if got == 'big_mac' else 'added %r' % got\n"
                ),
            ),
            SubGoal(name="polite", what="stayed polite", judged="tone leaves no trace"),
        ]
    )
    save_catalogue(catalogue, tmp_path)
    return tmp_path, contract, catalogue


def _delta(**overrides):
    payload = {
        "name": "adds-a-big-mac",
        "use_case": "order an item",
        "instruction": "Order one Big Mac.",
        "solution": [{"tool": "add", "arguments": {"item_id": "big_mac"}}],
        "sub_goals": ["item-added", "right-item"],
    }
    payload.update(overrides)
    return payload


def test_a_scenario_is_proved_before_it_is_kept(tmp_path):
    from harness.scenario_tools import accept_scenario

    root, _contract, catalogue = _built_environment(tmp_path)
    kept = []
    said = accept_scenario(_delta(), world_root=root, catalogue=catalogue, kept=kept)
    assert not said.get("is_error"), said
    assert "All three gates pass" in said["content"][0]["text"]
    assert [one.name for one in kept] == ["adds-a-big-mac"]


def test_a_scenario_whose_solution_cannot_pass_its_own_checks_is_refused(tmp_path):
    """Either the scenario is impossible or the checks are wrong. Both have happened."""
    from harness.scenario_tools import accept_scenario

    root, _contract, catalogue = _built_environment(tmp_path)
    said = accept_scenario(
        _delta(solution=[{"tool": "add", "arguments": {"item_id": "sushi"}}]),
        world_root=root,
        catalogue=catalogue,
        kept=[],
    )
    assert said["is_error"]
    text = said["content"][0]["text"]
    assert "reference solution does not pass" in text
    assert "refused by the world" in text and "sushi" in text


def test_a_scenario_whose_checks_pass_with_nothing_done_is_refused(tmp_path):
    """A check that passes without the agent acting grades nothing while reporting a result."""
    from harness.catalogue import SubGoal, save_catalogue
    from harness.scenario_tools import accept_scenario

    root, _contract, catalogue = _built_environment(tmp_path)
    catalogue.sub_goals.append(
        SubGoal(
            name="always",
            what="always true",
            check="def check(world, calls):\n    return None\n",
        )
    )
    save_catalogue(catalogue, root)
    said = accept_scenario(
        _delta(sub_goals=["always"]), world_root=root, catalogue=catalogue, kept=[]
    )
    assert said["is_error"] and "grade nothing" in said["content"][0]["text"]


def test_a_check_that_cannot_fail_without_calls_is_named_even_though_it_is_kept(tmp_path):
    """A check comparing calls against rows holds when there are no calls at all, so it reports
    itself as held for an agent that did nothing. The scenario is still graded by its other
    checks, so it is kept, but sub-goals are shared and that one would roll up as a pass."""
    from harness.catalogue import SubGoal, save_catalogue
    from harness.prove import prove
    from harness.scenario import Scenario
    from harness.scenario_tools import accept_scenario

    root, _contract, catalogue = _built_environment(tmp_path)
    catalogue.sub_goals.append(
        SubGoal(
            name="quantity-respected",
            what="as many rows as there were calls",
            check=(
                "def check(world, calls):\n"
                "    made = [c for c in calls if c.name == 'add' and c.ok]\n"
                "    rows = world.state()['cart']\n"
                "    if len(rows) != len(made):\n"
                "        return '%d calls, %d rows' % (len(made), len(rows))\n"
                "    return None\n"
            ),
        )
    )
    save_catalogue(catalogue, root)
    delta = _delta(sub_goals=["item-added", "quantity-respected"])
    said = accept_scenario(delta, world_root=root, catalogue=catalogue, kept=[])
    text = said["content"][0]["text"]

    assert not said.get("is_error"), text
    assert "All three gates pass" in text
    assert "quantity-respected" in text and "held with nothing done" in text
    proof = prove(Scenario(**delta), catalogue, root)
    assert proof.holds and proof.weak == ["quantity-respected"]


def test_a_store_that_is_not_sqlite_needs_nothing_above_it_to_change(tmp_path):
    """The harness writes the schema, the seed and the changes in whatever its agent's store
    speaks. What it cannot do from a prompt is execute them, freeze the result and put it back, so
    those are the only things a store owes the world.

    This registers a store that is not a database at all and drives the world through it: calls,
    state, the scenario mutation vocabulary, freezing and restoring. Nothing above the store is
    told which kind it is, which is the property that lets Postgres or ClickHouse drop in.
    """
    import json

    from harness.world.runtime import GeneratedWorld
    from harness.world.stores import open_store, register_store

    class Ledger:
        """Records in a plain mapping, with its own statement language. Not SQL, deliberately."""

        engine = "ledger"
        key = "ledger"

        def __init__(self, database: str = "", **_extra):
            self.held: dict[str, list[dict]] = {}

        def execute(self, statement: str, params=()) -> int:
            verb, _, rest = statement.partition(" ")
            if verb == "make":
                self.held.setdefault(rest.strip(), [])
                return 0
            if verb == "add":
                name, _, body = rest.partition(" ")
                self.held.setdefault(name, []).append(json.loads(body))
                return 1
            if verb == "clear":
                name = rest.strip()
                count = len(self.held.get(name) or [])
                self.held[name] = []
                return count
            raise ValueError(f"this store does not understand {verb!r}")

        def query(self, statement: str, params=()) -> list[dict]:
            return list(self.held.get(statement.strip(), []))

        def collections(self) -> list[str]:
            return sorted(self.held)

        def records(self, collection: str) -> list[dict]:
            return list(self.held.get(collection, []))

        def holds(self, collection: str) -> bool:
            return collection in self.held

        def add(self, collection: str, record) -> int:
            self.held.setdefault(collection, []).append(dict(record))
            return 1

        def amend(self, collection: str, key: str, changes, *, by: str = "") -> int:
            changed = 0
            for row in self.held.get(collection, []):
                if row.get(by or "order_id") == key:
                    row.update(dict(changes))
                    changed += 1
            return changed

        def remove(self, collection: str, key: str = "", *, by: str = "") -> int:
            rows = self.held.get(collection, [])
            if not key:
                self.held[collection] = []
                return len(rows)
            kept = [r for r in rows if r.get(by or "order_id") != key]
            self.held[collection] = kept
            return len(rows) - len(kept)

        def freeze(self):
            from harness.world.stores import Snapshot

            return Snapshot(rows=json.loads(json.dumps(self.held)))

        def restore(self, snapshot) -> None:
            self.held = {name: list(rows) for name, rows in snapshot.rows.items()}

        def save_to(self, path) -> None:
            from pathlib import Path

            Path(path).mkdir(parents=True, exist_ok=True)
            (Path(path) / "ledger.json").write_text(json.dumps(self.held), encoding="utf-8")

        def load_from(self, path) -> None:
            from pathlib import Path

            self.held = json.loads((Path(path) / "ledger.json").read_text(encoding="utf-8"))

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def dsn(self) -> str:
            return "ledger://"

        def apply(self, script: str) -> None:
            for line in script.splitlines():
                if line.strip():
                    self.execute(line.strip())

        def close(self) -> None:
            return None

    register_store(Ledger.engine, Ledger)
    world = GeneratedWorld(store=open_store("ledger"))

    # What the harness writes, in this store's own language rather than SQL.
    world.store.execute("make orders")
    world.store.execute('add orders {"order_id": "o1", "status": "pending"}')
    assert world.state()["orders"] == [{"order_id": "o1", "status": "pending"}]

    # A tool runs against it through the same handler contract.
    world.handlers["ship"] = (
        "def handle(args, db):\n"
        "    rows = db.query('orders')\n"
        "    if not rows:\n"
        "        raise ToolError('nothing to ship')\n"
        "    return rows[0]['order_id']\n"
    )
    assert world.call("ship", {}).result == "o1"

    # Saving and loading, which is what every scenario depends on.
    world.store.save_to(tmp_path)
    world.store.execute("clear orders")
    assert world.state()["orders"] == []
    world.store.load_from(tmp_path)
    assert len(world.state()["orders"]) == 1

    # And the same store going back in memory, which is what the gates use between probes.
    kept = world.store.freeze()
    world.store.execute("clear orders")
    world.store.restore(kept)
    assert len(world.state()["orders"]) == 1

    # And the world's own vocabulary, which is what a scenario's setup uses.
    world.put("orders", {"order_id": "o2", "status": "pending"})
    assert len(world.state()["orders"]) == 2
    assert world.drop("orders") == 2

    # A refusal is still a refusal, with no store-specific handling anywhere.
    refused = world.call("ship", {})
    assert refused.refused and not refused.ok


def test_a_world_check_that_cannot_fail_is_named(tmp_path):
    """The environment's checks are written by whoever built it, so nothing independent confirms
    they work. Breaking the world on purpose is that confirmation: a check that stays green
    through a world with no data and no working tools is not verifying anything."""
    from harness.checks import run_world_check
    from harness.world.mutate import blind, unnoticed
    from harness.world.runtime import GeneratedWorld
    from harness.world.snapshot import restore, save

    world = GeneratedWorld(":memory:")
    world.connection.executescript(
        "CREATE TABLE items (id TEXT); INSERT INTO items VALUES ('a');"
    )
    world.connection.commit()
    world.handlers["add"] = (
        "def handle(args, db):\n"
        "    db.execute('INSERT INTO items (id) VALUES (?)', [args['id']])\n"
        "    return 'added'\n"
    )

    real = "def check(world):\n    return None if world.state()['items'] else 'no items'\n"
    hollow = "def check(world):\n    return None\n"

    save(world, tmp_path, sequences=[{"name": "x", "calls": []}])
    survived = unnoticed(
        tmp_path,
        [("real", real), ("hollow", hollow)],
        run=lambda source, broken: run_world_check(source, broken, name="c"),
        restore=restore,
    )

    # Emptying the world is what the real check is about, so it has to notice.
    assert "real" not in survived["emptied"]
    # And the one that inspects nothing survives every kind of damage, which is how it is caught.
    assert blind(survived) == ["hollow"]


def test_the_world_keeps_the_checks_that_prove_it(tmp_path):
    """A world reopened without them would have to have them rewritten before it could be saved
    again, and they are judgement about this agent rather than anything a schema implies."""
    from harness.world.runtime import GeneratedWorld
    from harness.world.snapshot import read_manifest, restore, save

    world = GeneratedWorld(":memory:")
    world.connection.executescript("CREATE TABLE t (id TEXT); INSERT INTO t VALUES ('a');")
    world.connection.commit()
    written = {"holds": "def check(world):\n    return None if world.state()['t'] else 'empty'\n"}

    save(world, tmp_path, sequences=[{"name": "s", "calls": []}], world_checks=written)
    assert list(read_manifest(tmp_path).get("world_checks") or {}) == ["holds"]
    # And a restored world can still be verified without rewriting them.
    again = restore(tmp_path)
    assert again.state()["t"]


def test_a_restored_world_still_knows_how_this_agent_says_no(tmp_path):
    """Set at build time, read at run time, and those are different processes.

    Without it every refusal returned as a value is recorded as a success. A live call showed
    what that costs: the agent's own lookup answered "Error: user not found" twice, the record
    said both were fine, and the failure was then attributed to the agent re-calling a lookup
    that had in fact never worked once.
    """
    from harness.world.runtime import GeneratedWorld
    from harness.world.snapshot import restore, save

    world = GeneratedWorld(":memory:")
    world.refusal_signature = 'strings starting with "Error: "'
    world.handlers = {"look": "def handle(args, db):\n    return 'Error: user not found'\n"}
    assert world.call("look").refused

    save(world, tmp_path, sequences=[])
    assert restore(tmp_path).call("look").refused


def _models_within(annotation):
    """The pydantic models reachable from one field's type, through lists and optionals."""
    from pydantic import BaseModel

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    found = []
    for inner in getattr(annotation, "__args__", ()):
        found.extend(_models_within(inner))
    return found


def test_every_contract_field_is_advertised_to_the_model(tmp_path):
    """A field the model cannot see is a field that never gets filled.

    The adoption fields are the case that matters: with `tool_entrypoints` empty the build stage
    cannot tell that the agent ships its own tools, so it writes replacements and nothing
    downstream shows the difference. The failure is silent, which is why this is checked by
    reflection over the model rather than by remembering to keep a list in step.
    """
    from pathlib import Path

    from harness.contract import AgentContract
    from pydantic import BaseModel

    advertised = (
        Path(__file__).resolve().parents[1] / "src" / "harness" / "tools.py"
    ).read_text(encoding="utf-8")
    # Set by the amendment tools during later stages, never by whoever submits the contract.
    ours = {"amendments"}

    def named(model: type[BaseModel]) -> list[str]:
        """Every field the model would have to fill, nested ones included.

        Nested, because that is where this went wrong the second time: the top-level field was
        advertised and the shape underneath it was not, so the model filled in a store's kind and
        never its host, port or seam. A field nobody can see is a field nobody fills.
        """
        found: list[str] = []
        for name, field in model.model_fields.items():
            found.append(name)
            for inner in _models_within(field.annotation):
                found.extend(named(inner))
        return found

    missing = sorted(
        {
            name
            for name in named(AgentContract)
            if name not in ours and f'"{name}"' not in advertised
        }
    )
    assert not missing, f"submit_contract never mentions: {missing}"


def test_an_agent_inside_a_package_is_importable_from_its_package_root(tmp_path):
    """Pointing at the part under test is the normal way to point at a packaged agent, and its
    own imports resolve from the repository above it. Adding only the directory named makes every
    such import fail as "No module named <package>", which reads as the package being absent
    rather than as us having pointed at the middle of it, and blocks adoption entirely."""
    import sys

    from harness.world.runtime import GeneratedWorld

    root = tmp_path / "repo"
    inner = root / "agentpkg" / "envs" / "retail"
    inner.mkdir(parents=True)
    for package in (root / "agentpkg", root / "agentpkg" / "envs", inner):
        (package / "__init__.py").write_text("", encoding="utf-8")
    (inner / "data.py").write_text("def load():\n    return {'orders': []}\n", encoding="utf-8")

    roots = GeneratedWorld._import_roots(str(inner))
    assert str(inner) in roots, "where it sits stays importable, for a flat agent"
    assert str(root) in roots, "and the package root, or its own imports cannot resolve"
    # Stops at the first directory that is not itself a package, the way Python does.
    assert str(root.parent) not in roots

    kept = list(sys.path)
    try:
        GeneratedWorld(":memory:").reach(str(inner))
        loaded = __import__("agentpkg.envs.retail.data", fromlist=["load"])
        assert loaded.load() == {"orders": []}
    finally:
        sys.path[:] = kept
        for name in [one for one in sys.modules if one.startswith("agentpkg")]:
            del sys.modules[name]

    # An agent that is not in a package is unchanged: one directory, the one named.
    plain = tmp_path / "flat"
    plain.mkdir()
    assert GeneratedWorld._import_roots(str(plain)) == [str(plain)]
    assert GeneratedWorld._import_roots("") == []


def test_the_adoption_fields_survive_the_write_path(tmp_path):
    """Advertising them is half of it. They also have to reach the stage that acts on them."""
    from harness.tools import accept_contract
    from harness.understand import load

    said = accept_contract(
        {
            "agent": "x",
            "tools": [{"name": "t", "args": ["a"]}],
            "real_use_cases": ["a plain sentence"],
            "implementation": "present",
            "tool_entrypoints": [
                {
                    "tool": "t",
                    "mode": "import",
                    "module": "pkg.mod",
                    "callable": "K.invoke",
                    "first_arg": "data",
                }
            ],
            "refusal_signature": "a string beginning with Error:",
            "data_store": {"kind": "in_process", "loaded_by": "pkg.data.load"},
            "runtime": {"language": "python", "install": "uv sync"},
        },
        tmp_path,
    )
    assert not said.get("is_error"), said

    written = load(tmp_path)
    assert written is not None
    # The question the build stage actually asks before writing anything.
    assert written.adoptable("t"), "the build stage would write a replacement instead"
    assert written.refusal_signature
    assert written.data_store and written.data_store.loaded_by == "pkg.data.load"
    # And it has to be visible in the grounding block, or the stage never reads it.
    assert "pkg.mod.K.invoke" in written.brief()


def test_a_scenario_naming_a_sub_goal_nobody_defined_is_refused(tmp_path):
    from harness.scenario_tools import accept_scenario

    root, _contract, catalogue = _built_environment(tmp_path)
    said = accept_scenario(
        _delta(sub_goals=["invented-here"]),
        world_root=root,
        catalogue=catalogue,
        kept=[],
    )
    assert said["is_error"]
    assert "not in the catalogue" in said["content"][0]["text"]


def test_a_scenario_with_no_solution_cannot_be_proved(tmp_path):
    from harness.scenario_tools import accept_scenario

    root, _contract, catalogue = _built_environment(tmp_path)
    said = accept_scenario(
        _delta(solution=[]), world_root=root, catalogue=catalogue, kept=[]
    )
    assert said["is_error"] and "no solution" in said["content"][0]["text"]


def test_a_suite_where_no_sub_goal_is_shared_does_not_roll_up(tmp_path):
    """If a payment step appears in 50 scenarios, the results should say where payment fails."""
    from harness.catalogue import Catalogue, SubGoal
    from harness.scenario import Scenario
    from harness.scenario_tools import not_ready

    catalogue = Catalogue(
        sub_goals=[SubGoal(name=f"g{i}", what="x", judged="y") for i in range(4)]
    )
    private = [
        Scenario(name=f"s{i}", instruction="do it", sub_goals=[f"g{i}"])
        for i in range(4)
    ]
    assert any("rolls up" in problem for problem in not_ready(private, 4, catalogue))

    shared = [
        Scenario(name=f"s{i}", instruction="do it", sub_goals=["g0"]) for i in range(4)
    ]
    assert not_ready(shared, 4, catalogue) == []


def test_the_simulator_prompt_slots_a_scenario_leaves_unfilled_are_caught(tmp_path):
    from harness.scenario import Scenario, validate_scenario

    root, _contract, catalogue = _built_environment(tmp_path)
    prompt = (
        "You are a customer. " * 10
        + "\nWhat you want: {{ instruction }}\nAlso: {{ mood }}"
    )
    scenario = Scenario.model_validate(_delta())
    problems = validate_scenario(scenario, catalogue, {"cart": [], "menu": []}, prompt)
    assert any("mood" in problem for problem in problems)


# --- the voice webhook, answered by the world ----------------------------------------


def test_a_hosted_agents_tool_call_is_answered_by_the_world():
    """The whole voice integration: a webhook, answered by running the call rather than by
    looking up a canned response. A mock that always succeeds tells an agent it removed an item
    that was never added."""
    import json
    import urllib.request

    from harness.run.voice import WorldWebhook

    world, _contract = _cart_world()
    webhook = WorldWebhook().start()
    try:
        webhook.bind(world)

        def call(name, arguments):
            body = json.dumps(
                {
                    "message": {
                        "toolCalls": [
                            {
                                "id": "call-1",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ]
                    }
                }
            ).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{webhook.port}/tool",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as answer:
                return json.loads(answer.read())["results"][0]["result"]

        assert "1" in call("add", {"item_id": "big_mac"})
        # the world really wrote the row, so a read-after-write flow is right
        assert len(world.state()["cart"]) == 1

        # and it can refuse, which a canned mock cannot
        refused = call("add", {"item_id": "sushi"})
        assert "sushi" in refused
        assert len(world.state()["cart"]) == 1

        # the world answers for a tool the agent does not have, naming the ones it does
        unknown = call("checkout", {})
        assert "no such tool" in unknown and "add" in unknown
        # every call is recorded with its arguments, which is what grading reads
        assert [c.name for c in webhook.calls] == ["add", "add", "checkout"]
    finally:
        webhook.stop()


def test_repointing_changes_only_where_the_agents_tools_are_answered():
    """The assistant's tools are the agent's — names, arguments and enums belong to whoever
    built it. Redefining them would mean testing an agent we wrote."""
    from harness.run.voice import pointed_at

    theirs = [
        {
            "type": "function",
            "function": {
                "name": "order_combo_meal",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "meal_id": {"type": "string", "enum": ["combo_big_mac"]}
                    },
                    "required": ["meal_id"],
                },
            },
            "server": {"url": "https://dead-tunnel.example/tool"},
        }
    ]
    moved = pointed_at(theirs, "https://ours.example")
    assert moved[0]["server"]["url"] == "https://ours.example/tool"
    # everything else is untouched
    assert moved[0]["function"] == theirs[0]["function"]
    assert theirs[0]["server"]["url"] == "https://dead-tunnel.example/tool"


def test_a_scenario_fills_the_simulator_prompt_before_a_call_is_placed(tmp_path):
    from harness.run.live import prepare
    from harness.scenario import Scenario
    from harness.simulator import save_simulator_prompt

    root, _contract, _catalogue = _built_environment(tmp_path)
    save_simulator_prompt(
        "You are at the counter. " * 8 + "\nWhat you are here to do: {{ instruction }}",
        root,
    )
    world, instruction = prepare(
        Scenario(name="s", instruction="Order one Big Mac."), root
    )
    try:
        assert "Order one Big Mac." in instruction
        assert "{{" not in instruction
    finally:
        world.close()


def test_a_scenario_that_leaves_a_slot_empty_never_reaches_a_call(tmp_path):
    """An unfilled slot would be read out to the caller verbatim."""
    import pytest as _pytest
    from harness.run.live import prepare
    from harness.scenario import Scenario
    from harness.simulator import save_simulator_prompt

    root, _contract, _catalogue = _built_environment(tmp_path)
    save_simulator_prompt(
        "You are at the counter. " * 8 + "\nDo: {{ instruction }}\nMood: {{ mood }}",
        root,
    )
    with _pytest.raises(RuntimeError, match="mood"):
        prepare(Scenario(name="s", instruction="Order one Big Mac."), root)


def test_a_live_run_is_refused_before_it_costs_anything(monkeypatch):
    """Missing credentials must be caught up front. Discovering them after the world is
    restored, the tunnel is up and the assistant is repointed wastes the expensive part and
    reports a failure that says nothing about the agent."""
    from harness.run.tools import missing_prerequisites

    # Which credentials are wanted follows the case, so the case is set rather than inherited:
    # a 1.x case reaches a LiveKit worker and would not ask for these at all.
    monkeypatch.setenv("HARNESS_VOICE_CASE", "2.1.2")
    monkeypatch.delenv("VAPI_API_KEY", raising=False)
    monkeypatch.delenv("VAPI_ASSISTANT_ID", raising=False)
    problems = missing_prerequisites()
    assert any("VAPI_API_KEY" in problem for problem in problems)

    monkeypatch.setenv("VAPI_API_KEY", "x")
    monkeypatch.setenv("VAPI_ASSISTANT_ID", "y")
    monkeypatch.setenv("HARNESS_WEBHOOK_URL", "https://example.invalid")
    assert missing_prerequisites() == []


def test_a_livekit_case_asks_for_its_own_credentials_and_no_tunnel(monkeypatch):
    """A worker we run ourselves is reached over LiveKit and calls the world on the network it
    shares with us. Demanding a hosted assistant's credentials, or a way to expose the webhook
    publicly, reports a working setup as broken."""
    from harness.run.tools import missing_prerequisites

    monkeypatch.setenv("HARNESS_VOICE_CASE", "1.1.2")
    monkeypatch.delenv("VAPI_API_KEY", raising=False)
    monkeypatch.delenv("VAPI_ASSISTANT_ID", raising=False)
    monkeypatch.delenv("HARNESS_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)

    problems = missing_prerequisites()
    assert any("LIVEKIT_API_KEY" in problem for problem in problems)
    assert not any("VAPI" in problem for problem in problems)
    assert not any("cloudflared" in problem for problem in problems)

    monkeypatch.setenv("LIVEKIT_API_KEY", "x")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "y")
    monkeypatch.setenv("LIVEKIT_TARGET_AGENT_NAME", "an-agent")
    assert missing_prerequisites() == []


def test_running_is_a_stage_of_the_conversation():
    """Placing a call was the one step that could only be a command. If it drops out of the
    stage order it silently becomes one again, and the chat ends at scenarios."""
    from harness import chat

    assert chat._NEXT[chat.SCENARIOS] == chat.RUN
    assert chat._NEXT[chat.RUN] == chat.DONE


def test_a_run_result_survives_being_written_and_read(tmp_path):
    from harness.checks import Outcome
    from harness.run.live import LiveRun
    from harness.run.tools import as_record, load_results, save_results

    run = LiveRun(
        scenario="orders-a-big-mac",
        settled=[Outcome("combo_placed", True), Outcome("no_extras", False, "added fries")],
        judged=["explained_itself"],
        calls=["order(...) -> ok"],
    )
    record = as_record(run)
    assert record["passed"] is False and record["met"] == 1 and record["of"] == 2

    save_results([record], tmp_path)
    assert load_results(tmp_path) == [record]


def test_a_tool_a_stage_was_not_given_is_denied_by_the_hook():
    """can_use_tool alone does not do this. An allowed_tools entry approves its tools before the
    callback runs, and the SDK warns the callback is shadowed; a host ToolSearch reached every
    stage, returned nothing and cost a turn. The PreToolUse hook is consulted for every call."""
    import asyncio

    from harness.config import gate_hooks

    hooks = gate_hooks(["mcp__world__seed"])
    refuse = hooks["PreToolUse"][0].hooks[0]

    granted = asyncio.run(refuse({"tool_name": "mcp__world__seed"}, None, None))
    assert granted == {}

    asked = asyncio.run(refuse({"tool_name": "AskUserQuestion"}, None, None))
    assert asked == {}

    denied = asyncio.run(refuse({"tool_name": "ToolSearch"}, None, None))
    said = denied["hookSpecificOutput"]
    assert said["permissionDecision"] == "deny"
    assert "ToolSearch is not part of this stage" in said["permissionDecisionReason"]
    assert "mcp__world__seed" in said["permissionDecisionReason"]


def test_every_stage_gates_with_the_hook_not_only_the_callback():
    """One stage left on the callback alone is one stage a host tool still reaches."""
    import inspect

    from harness.run import grade, stage, targets

    from harness import build, reception, scenarios

    for module in (build, reception, scenarios, stage, targets, grade):
        source = inspect.getsource(module)
        if "permission_gate(" in source:
            assert "gate_hooks(allowed)" in source, f"{module.__name__} has no hook gate"


def test_writing_new_results_keeps_the_ones_not_rerun(tmp_path):
    """The live stage and the local suite share runs.json. Re-running one scenario must not
    erase the record of another, whichever writer gets there second."""
    from harness.run.tools import load_results, save_results

    save_results(
        [{"scenario": "a", "passed": True}, {"scenario": "b", "passed": False}], tmp_path
    )
    fresh = [r for r in load_results(tmp_path) if r.get("scenario") != "b"]
    fresh.append({"scenario": "b", "passed": True, "transcript": "hello"})
    save_results(fresh, tmp_path)

    kept = {r["scenario"]: r for r in load_results(tmp_path)}
    assert kept["a"]["passed"] is True
    assert kept["b"]["passed"] is True and kept["b"]["transcript"] == "hello"


def test_submit_contract_schema_teaches_and_leaves_gating_to_the_gate(tmp_path):
    """Every field marked required is rejected by the schema layer one at a time, a full model
    turn each, before accept_contract can explain anything. Only the fields validate_contract
    refuses to live without may be required; the rest are optional and gated with real messages."""
    import asyncio

    from harness.contract import MODALITIES
    from harness.tools import contract_tools

    server = contract_tools(tmp_path)
    instance = server.get("instance") if isinstance(server, dict) else server

    async def schema_of():
        tools = await _list_tools(instance)
        return _schema_of(tools[0]) if tools else {}

    schema = asyncio.run(schema_of())
    # Nothing required at the schema layer: accept_contract is the only gate, and it reports
    # every problem at once with what to do, which a JSON-schema rejection cannot.
    # Nothing required: this layer runs before the tool body, so whatever it rejects never
    # reaches the code that could have understood it. accept_contract is the single gate.
    assert schema.get("required") == []
    assert "required" not in schema["properties"]["tools"]["items"]
    # And the schema has to teach, not just validate — it is shown before the first call.
    described = [
        name for name, spec in schema["properties"].items() if spec.get("description")
    ]
    assert len(described) >= 10, "properties must describe themselves"
    assert schema["properties"]["modality"]["enum"] == list(MODALITIES)
    assert schema["properties"]["tools"]["items"]["properties"]["arg_values"]


def test_a_bare_conversational_contract_is_nudged_once_then_accepted(tmp_path):
    """No rules and no prompt excerpt on a conversational agent almost always means the prompt
    was not found, so the first submission bounces with directions. The second goes through,
    because a gate with no way past would permanently block an agent that genuinely has none."""
    import asyncio

    from harness.tools import contract_tools

    server = contract_tools(tmp_path)
    instance = server.get("instance") if isinstance(server, dict) else server

    async def call(payload):
        return await _call_tool(instance, "submit_contract", payload)

    payload = {
        "agent": "quiet",
        "tools": [{"name": "act", "args": ["x"]}],
        "real_use_cases": ["do the thing"],
    }
    first = asyncio.run(call(dict(payload)))
    # Both thin spots are reported together, not one per turn.
    assert "system_prompt_excerpt" in first and "data_schema" in first
    assert "submit again" in first
    assert not (tmp_path / "contract.json").exists()

    second = asyncio.run(call(dict(payload)))
    assert "Accepted" in second
    assert (tmp_path / "contract.json").exists()


def test_granting_a_tool_rebuilds_the_gate_not_just_the_list(tmp_path):
    """The hook closes over the granted set when the stage is built, so appending to
    allowed_tools alone leaves the new tool denied. grant() must rebuild all three."""
    import asyncio

    from claude_agent_sdk import ClaudeAgentOptions
    from harness.config import gate_hooks
    from harness.session import Stage

    allowed = ["Read"]
    options = ClaudeAgentOptions(
        system_prompt="x", allowed_tools=allowed, permission_mode="default",
        setting_sources=[], max_turns=1,
    )
    options.hooks = gate_hooks(allowed)
    stage = Stage(options, name="t")
    stage.grant("flow", object(), ["hand_to_next_stage"])

    assert "mcp__flow__hand_to_next_stage" in options.allowed_tools
    refuse = options.hooks["PreToolUse"][0].hooks[0]
    granted = asyncio.run(refuse({"tool_name": "mcp__flow__hand_to_next_stage"}, None, None))
    assert granted == {}


def test_handoff_is_refused_until_the_stage_has_its_artifact(tmp_path):
    """Moving on is decided by code, from the artifacts, never by the model wanting to."""
    import asyncio

    from harness.chat import Conversation

    conversation = Conversation(source=None, out=tmp_path, workspace=tmp_path)
    conversation.stage_name = "understand"
    server = conversation._flow_server()
    instance = server.get("instance") if isinstance(server, dict) else server

    async def call():
        return await _call_tool(
            instance, "hand_to_next_stage", {"request": "create the world"}
        )

    said = asyncio.run(call())
    assert "not produced its artifact" in said
    assert not conversation._handoff

    (tmp_path / "contract.json").write_text(
        '{"agent": "a", "tools": [{"name": "t"}], "real_use_cases": ["u"]}'
    )
    said = asyncio.run(call())
    assert "Handed over" in said
    assert conversation._handoff["request"] == "create the world"


def test_every_problem_is_reported_at_once_with_what_to_do(tmp_path):
    """Revealing the next problem only after the last is fixed costs a turn per problem and
    reads as though the rules are being invented as it goes."""
    from harness.tools import accept_contract

    result = accept_contract({"agent": "", "tools": [], "real_use_cases": []}, tmp_path)
    said = result["content"][0]["text"]
    assert result["is_error"]
    # all three, in one answer
    assert "empty:agent" in said and "no-tools" in said and "no-use-cases" in said
    # and each carries what to do about it, not only its code
    assert "artifact folder" in said and "real tools" in said
    assert not (tmp_path / "contract.json").exists()


def test_a_contract_sent_inside_a_wrapper_is_unwrapped(tmp_path):
    """A contract is a nested thing being described, so it arrives as {"contract": {...}} often
    enough to matter. Every field is right; only the envelope is wrong, and rejecting that
    teaches nothing while costing a turn."""
    from harness.tools import accept_contract, unwrapped

    inner = {
        "agent": "wrapped",
        "tools": [{"name": "act", "args": ["x"]}],
        "real_use_cases": ["do the thing"],
        "hard_constraints": ["a rule"],
        "system_prompt_excerpt": "you are a bot",
    }
    assert unwrapped({"contract": inner}) == inner
    assert unwrapped(inner) == inner
    # a real field that merely holds a dict must not be mistaken for an envelope
    plain = {"agent": "x", "tools": [], "data_schema": {"agent": 1}}
    assert unwrapped(plain) == plain

    result = accept_contract({"contract": inner}, tmp_path)
    assert not result.get("is_error"), result["content"][0]["text"]
    assert (tmp_path / "contract.json").exists()


@pytest.mark.parametrize(
    "written",
    [
        {"name": "order", "parameters": ["item_id", "size"]},
        {"name": "order", "arguments": ["item_id", "size"]},
        {"name": "order", "params": ["item_id", "size"]},
        {"name": "order", "arg_types": {"item_id": "str", "size": "str"}},
        {"name": "order", "parameters": {"item_id": "str", "size": "str"}},
    ],
)
def test_a_tool_written_with_a_synonym_still_records_its_arguments(written):
    """args drives the handlers, the probes and every scenario. It is also the field most often
    written under another name, and a contract bounced for a synonym costs a turn and teaches
    nothing about the agent."""
    spec = ToolSpec.model_validate(written)
    assert spec.args == ["item_id", "size"]


def test_a_tool_that_really_takes_nothing_stays_empty():
    """A tool genuinely taking no arguments is ordinary and must not be invented into one."""
    assert ToolSpec.model_validate({"name": "list_order_items"}).args == []


def test_a_stringified_contract_is_parsed_rather_than_refused(tmp_path):
    from harness.tools import accept_contract, unwrapped

    inner = {
        "agent": "stringy",
        "tools": [{"name": "act", "args": ["x"]}],
        "real_use_cases": ["do it"],
        "hard_constraints": ["a rule"],
    }
    assert unwrapped({"contract": json.dumps(inner)}) == inner
    assert unwrapped({"payload": json.dumps({"contract": inner})}) == inner
    assert not accept_contract({"contract": json.dumps(inner)}, tmp_path).get("is_error")


def test_an_unrecognised_payload_is_told_what_arrived(tmp_path):
    """Otherwise the answer is 'agent is empty, there are no tools' about a submission that
    contained both, and the only way out is guessing at the packaging."""
    from harness.tools import accept_contract

    said = accept_contract({"stuff": 1, "other": 2}, tmp_path)["content"][0]["text"]
    assert "What arrived was: other, stuff" in said
    assert "top-level arguments" in said


def test_a_skill_only_names_tools_its_stage_actually_has():
    """A SKILL.md is the method; the tools are the surface it is written against. They live in
    different files, so a renamed tool leaves the skill telling the model to call something that
    does not exist — and the model then hunts for it and works around the gate. Nothing else
    catches that, because both halves are individually valid."""
    import re

    from harness.config import SKILLS_ROOT
    from harness.run import tools as run_tools
    from harness.tools import CONTRACT_SERVER  # noqa: F401
    from harness.world import tools as world_tools

    from harness import scenario_tools

    surface = {
        "understand-agent": {"submit_contract"},
        "build-environment": set(world_tools.TOOL_NAMES),
        "write-scenarios": set(scenario_tools.TOOL_NAMES),
        "run-scenarios": set(run_tools.TOOL_NAMES),
    }
    # A skill also backticks the names of fields it is telling the model to fill in. Those are
    # not tools, and the list of them is derived rather than hand-kept so it cannot go stale.
    from harness.catalogue import SubGoal
    from harness.contract import AgentContract, ToolSpec
    from harness.scenario import Persona, Scenario

    fields = set()
    for model in (AgentContract, ToolSpec, Scenario, Persona, SubGoal):
        fields |= set(model.model_fields)
    # Names from the check-writing examples the skills contain.
    from harness.contract import MODALITIES

    ignore = (
        fields
        | set(MODALITIES)
        | {"handle", "check", "args", "db", "world", "calls", "json", "ToolError"}
    )

    for stage, tools in surface.items():
        text = (SKILLS_ROOT / stage / "SKILL.md").read_text(encoding="utf-8")
        # `name` or `name(` — the way a skill refers to a tool it wants called.
        mentioned = set(re.findall(r"`([a-z_][a-z0-9_]*)\(?`", text))
        unknown = {
            name
            for name in mentioned - tools - ignore
            if name not in {"hand_to_next_stage", "AskUserQuestion"}
        }
        assert not unknown, f"{stage}/SKILL.md names tools that do not exist: {sorted(unknown)}"


def test_a_contract_with_tools_but_no_data_is_nudged_once(tmp_path):
    """The world is built from data_schema and base_environment. Without them the build stage has
    no schema to create and no rows to seed, so every tool call it makes refuses — and that looks
    like a strict world rather than an empty one."""
    import asyncio

    from harness.tools import contract_tools

    server = contract_tools(tmp_path)
    instance = server.get("instance") if isinstance(server, dict) else server

    async def call(payload):
        return await _call_tool(instance, "submit_contract", payload)

    payload = {
        "agent": "dataless",
        "tools": [{"name": "act", "args": ["x"]}],
        "real_use_cases": ["do the thing"],
        "hard_constraints": ["a rule"],
        "system_prompt_excerpt": "you are a bot",
    }
    first = asyncio.run(call(dict(payload)))
    assert "data_schema" in first and "submit again" in first
    assert not (tmp_path / "contract.json").exists()

    second = asyncio.run(call(dict(payload)))
    assert "Accepted" in second

    assert (tmp_path / "contract.json").exists()


def test_only_a_tool_that_says_it_saved_reports_an_artifact():
    """Matching any path-shaped token in any result meant reading a file announced itself as an
    artifact: the stage looks like it is producing output while it is still only looking around,
    and a front end reloads its panes on every read."""
    from dataclasses import dataclass

    from harness.session import _saved_path

    @dataclass
    class Block:
        content: object
        is_error: bool = False

    # a read
    assert _saved_path(Block("     1\timport json\n     2\tfrom pathlib import Path")) == ""
    assert _saved_path(Block("/some/agent/envs/retail/__init__.py")) == ""
    # a write
    assert _saved_path(Block("Accepted and saved to out/contract.json.")) == "out/contract.json"
    assert _saved_path(Block("Saved 3 scenarios to out/scenarios.json.")) == "out/scenarios.json"
    # list-shaped content, as the SDK sometimes gives it
    assert (
        _saved_path(Block([{"text": "Saved to artifacts/x/world.sqlite"}]))
        == "artifacts/x/world.sqlite"
    )


@pytest.mark.parametrize(
    "written,field,expected",
    [
        ({"use_cases": ["a"]}, "real_use_cases", ["a"]),
        ({"scenarios": ["a"]}, "real_use_cases", ["a"]),
        ({"rules": ["r"]}, "hard_constraints", ["r"]),
        ({"constraints": ["r"]}, "hard_constraints", ["r"]),
        ({"system_prompt": "p"}, "system_prompt_excerpt", "p"),
        ({"instructions": "p"}, "system_prompt_excerpt", "p"),
        ({"schema": {"a": 1}}, "data_schema", {"a": 1}),
        ({"seed_data": {"t": []}}, "base_environment", {"t": []}),
    ],
)
def test_a_field_written_under_the_obvious_name_still_lands(written, field, expected):
    """Every one of these was written by a model that had read the schema and still reached for
    the more obvious word. Bouncing it produces a loop: the answer to `use_cases` was
    'no-use-cases', which reads as missing rather than misnamed, so the same submission comes
    back with the shape changed and the name untouched."""
    contract = AgentContract.model_validate({"agent": "x", **written})  # our name already set
    assert getattr(contract, field) == expected


def test_the_agent_name_can_arrive_as_name():
    assert AgentContract.model_validate({"name": "bot", "tools": []}).agent == "bot"


def test_our_own_name_wins_when_both_are_given():
    contract = AgentContract.model_validate(
        {"agent": "x", "real_use_cases": ["ours"], "use_cases": ["theirs"]}
    )
    assert contract.real_use_cases == ["ours"]


def test_the_gate_names_the_field_it_wants(tmp_path):
    """A code alone cannot be acted on when the mistake is the field's name."""
    from harness.tools import accept_contract

    said = accept_contract({"agent": "x", "tools": [], "real_use_cases": []}, tmp_path)
    text = said["content"][0]["text"]
    assert "`real_use_cases`" in text and "not `use_cases`" in text
    assert "`tools`" in text


# --- scenario folders and the ready gate ---------------------------------------------


def test_the_ready_gate_refuses_a_scenario_whose_world_was_never_set_up(tmp_path):
    """The precondition gate. A scenario about the last five items is only a test of the agent
    if there really are five; otherwise the agent fails for something we got wrong, and it reads
    as the agent's fault."""
    from harness.prove import prove

    root, _contract, catalogue = _built_environment(tmp_path)
    scenario = Scenario.model_validate(
        _delta(
            ready_code=(
                "def ready(world):\n"
                "    rows = world.state()['cart']\n"
                "    return None if rows else 'the cart is empty; this scenario needs one item'\n"
            )
        )
    )
    proof = prove(scenario, catalogue, root)
    assert not proof.ready
    assert not proof.holds
    assert "the cart is empty" in proof.why()
    assert "test us rather than the agent" in proof.why()
    assert proof.gates() == {"ready": False, "solvable": False, "not_vacuous": False}


def test_setup_code_makes_the_world_the_scenario_presumes(tmp_path):
    """setup runs, then ready confirms it worked, and only then is anything else asked."""
    from harness.prove import prove

    root, _contract, catalogue = _built_environment(tmp_path)
    scenario = Scenario.model_validate(
        _delta(
            setup_code=(
                "def setup(world):\n"
                "    world.connection.execute(\"INSERT INTO menu (id) VALUES ('sushi')\")\n"
                "    world.connection.commit()\n"
            ),
            ready_code=(
                "def ready(world):\n"
                "    ids = [r['id'] for r in world.state()['menu']]\n"
                "    return None if 'sushi' in ids else 'sushi was never added to the menu'\n"
            ),
        )
    )
    proof = prove(scenario, catalogue, root)
    assert proof.ready and proof.holds, proof.why()


def test_the_setups_own_calls_are_not_credited_to_the_agent(tmp_path):
    """A check that counts calls must not see the ones the scenario made on its own behalf."""
    from harness.prove import prepared

    root, _contract, _catalogue = _built_environment(tmp_path)
    scenario = Scenario.model_validate(
        _delta(
            setup_code=(
                "def setup(world):\n"
                "    world.call('add', {'item_id': 'big_mac'})\n"
            )
        )
    )
    world, applied, ready = prepared(scenario, root)
    try:
        assert applied.ok and ready.ok
        assert len(world.state()["cart"]) == 1, "the setup should have acted"
        assert world.calls == [], "but its calls are not the agent's"
    finally:
        world.close()


def test_broken_setup_is_ours_and_says_so(tmp_path):
    from harness.prove import prove

    root, _contract, catalogue = _built_environment(tmp_path)
    scenario = Scenario.model_validate(_delta(setup_code="def setup(world):\n    world.nope()\n"))
    proof = prove(scenario, catalogue, root)
    assert not proof.ready
    assert proof.broken, "a setup that raises is our mistake, not a failing scenario"
    assert "AttributeError" in proof.why_not_ready




def test_a_kept_scenario_becomes_a_folder_of_files(tmp_path):
    """The files are the artifact, not a rendering of one. Something you can open and run is
    something you can argue with."""
    from harness.folder import folder_for, read_folder
    from harness.scenario_tools import write_scenarios

    root, _contract, catalogue = _built_environment(tmp_path)
    scenario = Scenario.model_validate(
        _delta(
            setup_code="def setup(world):\n    pass\n",
            ready_code="def ready(world):\n    return None\n",
        )
    )
    index = write_scenarios([scenario], root, catalogue)

    here = folder_for(root, scenario.name)
    assert (here / "scenario.json").exists()
    assert (here / "setup.py").exists()
    assert (here / "ready.py").exists()
    # One file per deterministic sub-goal; the judged one has no check to write.
    assert sorted(p.name for p in (here / "checks").iterdir()) == [
        "item-added.py",
        "right-item.py",
    ]
    assert index.name == "scenarios.json"

    # The code lives in the files, not duplicated into the JSON, so the two cannot drift.
    body = json.loads((here / "scenario.json").read_text())
    assert "setup_code" not in body and "ready_code" not in body

    # And it reads back whole.
    again = read_folder(root, scenario.name)
    assert again is not None
    assert again.setup_code.strip() == "def setup(world):\n    pass"
    assert again.solution == scenario.solution


def test_a_check_file_runs_on_its_own_and_agrees_with_the_harness(tmp_path):
    """The same file, the same answer, whether the harness runs it or a person does. If those
    two could disagree, neither could be trusted."""
    import subprocess
    import sys

    from harness.folder import folder_for, write_folder
    from harness.prove import prepared

    root, _contract, catalogue = _built_environment(tmp_path)
    scenario = Scenario.model_validate(_delta())
    write_folder(scenario, catalogue, root)

    # Leave the world in the state a passing run would have left it in.
    world, _applied, _ready = prepared(scenario, root)
    try:
        world.call("add", {"item_id": "big_mac"})
    finally:
        world.close()

    check_file = folder_for(root, scenario.name) / "checks" / "item-added.py"
    done = subprocess.run(
        [sys.executable, str(check_file), str(root / "world.sqlite")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # The world on disk is the base world, which has an empty cart, so this check should fail —
    # and the point is that it says so rather than erroring.
    assert done.returncode in (0, 1), done.stderr[-400:]
    assert "held" in done.stdout or "FAILED" in done.stdout, done.stdout + done.stderr[-300:]


def test_every_stage_is_told_what_the_harness_is_for():
    """A stage that knows only its own step does its step well and still gets the point of it
    wrong: it works around a gate instead of fixing what the gate named, or it reports a number
    that quietly skipped half its checks."""
    for stage in ("understand-agent", "build-environment", "write-scenarios", "run-scenarios"):
        text = load_skill(stage)
        assert text.startswith("# The harness"), stage
        assert "# The stage you are in now" in text, stage
        # the ideas a stage must not be able to miss
        assert "Code decides what is true" in text, stage
        assert "refusal" in text and "crash" in text, stage


# --- sessions: one conversation, one folder -------------------------------------------


def test_a_session_is_a_folder_that_knows_what_it_holds(tmp_path):
    """Nothing is held in memory that is not also on disk, so closing the page, restarting the
    server or coming back tomorrow all resume by reading the folder."""
    from harness import sessions

    one = sessions.create(agent="drive_thru", source="/somewhere/agent", base=tmp_path)
    assert one.id.startswith("drive-thru-")
    assert (one.path / "session.json").exists()

    has = one.has()
    assert has == {
        "contract": False, "world": False, "simulator_prompt": False,
        "sub_goals": 0, "scenarios": 0, "validated": None,
        "runs": 0, "runs_passed": 0, "messages": 0,
    }

    again = sessions.load(one.id, tmp_path)
    assert again is not None
    assert again.agent == "drive_thru" and again.source == "/somewhere/agent"


def test_two_goes_at_the_same_agent_are_two_sessions(tmp_path):
    from harness import sessions

    first = sessions.create(agent="same", base=tmp_path)
    second = sessions.create(agent="same", base=tmp_path)
    assert first.id != second.id
    assert {one.id for one in sessions.every(tmp_path)} == {first.id, second.id}


def test_a_native_webrtc_campaign_is_visible_as_session_runs(tmp_path):
    from harness import sessions

    one = sessions.create(agent="voice", base=tmp_path)
    campaign = one.path / "webrtc-runs" / "run_20260819_121907"
    campaign.mkdir(parents=True)
    (campaign / "results.json").write_text(
        json.dumps(
            [
                {
                    "scenario": "book-a-ride",
                    "passed": True,
                    "voice_status": "completed",
                    "deterministic_met": 2,
                    "deterministic_of": 2,
                    "tool_calls": [
                        {
                            "name": "book_ride",
                            "arguments": {"confirmed": True},
                            "ok": True,
                        }
                    ],
                    "transcript": "customer: book it\nagent: your ride is booked",
                }
            ]
        ),
        encoding="utf-8",
    )

    assert one.has()["runs"] == 1
    assert one.has()["runs_passed"] == 1
    run = sessions._runs(one.path)[0]
    assert run["scenario"] == "book-a-ride"
    assert run["met"] == run["of"] == 2
    assert run["calls"] == ['book_ride({"confirmed": true}) -> ok']
    assert "your ride is booked" in run["transcript"]


def test_the_conversation_is_kept_in_the_session_folder(tmp_path):
    """A refresh must not lose what was said."""
    from harness import sessions

    one = sessions.create(agent="talky", base=tmp_path)
    sessions.remember(one.path, sessions.Message(role="you", text="hello", stage="reception"))
    sessions.remember(
        one.path,
        sessions.Message(
            role="harness", text="hi", stage="reception",
            tools=[{"label": "point at agent", "said": ["Pointed at talky"]}],
        ),
    )
    said = sessions.history(one.path)
    assert [m["role"] for m in said] == ["you", "harness"]
    assert said[1]["tools"][0]["label"] == "point at agent"
    assert one.has()["messages"] == 2

    # A half-written final line is what a killed process leaves; it must not take the rest.
    with (one.path / "chat.jsonl").open("a", encoding="utf-8") as file:
        file.write('{"role": "you", "text": "cut off')
    assert len(sessions.history(one.path)) == 2


def test_deleting_a_session_will_not_reach_outside_the_sessions_root(tmp_path):
    """A mistyped id must never take anything else with it."""
    from harness import sessions

    one = sessions.create(agent="doomed", base=tmp_path)
    outsider = tmp_path.parent / "not-a-session"
    outsider.mkdir(exist_ok=True)

    assert sessions.remove("../not-a-session", tmp_path) is False
    assert outsider.exists()
    assert sessions.remove("no-such-session", tmp_path) is False

    assert sessions.remove(one.id, tmp_path) is True
    assert not one.path.exists()


def test_any_stage_whose_input_exists_can_be_opened(tmp_path):
    """Stages are not a wizard. Coming back to correct a contract after the world is built is
    the ordinary case, so what cannot be skipped is the input, not the order."""
    from harness.chat import Conversation
    from harness.tools import accept_contract

    empty = Conversation(out=tmp_path)
    blocked = empty.reachable()
    assert blocked["reception"] == ""
    assert "where its source lives" in blocked["understand"]
    assert "needs a contract" in blocked["build"]
    # Without a contract, every later stage says so — not "needs a world", which would send
    # somebody to build one against nothing.
    assert "needs a contract" in blocked["scenarios"]
    assert "needs a contract" in blocked["run"]

    root, contract, _catalogue = _built_environment(tmp_path / "built")
    accept_contract(contract.model_dump(), root)
    ready = Conversation(out=root)
    open_now = ready.reachable()
    assert open_now["build"] == "", open_now
    assert open_now["scenarios"] == "", open_now
    assert "needs scenarios" in open_now["run"]


def test_reception_can_hand_over_in_the_turn_that_finds_the_agent(tmp_path):
    """The source is read off the reception stage after its turn ends, so within that turn the
    conversation does not know it yet. Without allowing for that, the stage that has just
    succeeded is told it has produced nothing and the handoff is refused."""
    from harness.chat import Conversation
    from harness.sources import RepoSource

    conversation = Conversation(out=tmp_path)
    conversation.stage_name = "reception"
    assert conversation.next_stage() is None, "nothing pointed at yet"

    # what point_at_agent does, mid-turn
    conversation._found["source"] = RepoSource(name="x", root=tmp_path)
    assert conversation.next_stage() == "understand"


def test_the_turn_that_finds_the_agent_also_opens_the_next_stage(tmp_path, monkeypatch):
    """Allowing that handoff is not enough: the stage it opens is built from the source, so the
    source has to be on the conversation before the hop, not after it. Otherwise the hop raises,
    the turn is lost, and the source is never taken up at all — every later message arrives back
    at reception, which has no tools to do anything with it."""
    import asyncio

    from harness.chat import Conversation
    from harness.sources import RepoSource

    from harness import chat as chat_module

    said: list[str] = []

    class Stage:
        spent_usd = 0.0

        def __init__(self, name: str) -> None:
            self.name = name

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def say(self, message, on_event=None):
            said.append(f"{self.name}: {message}")

        def grant(self, *_, **__):
            pass

    found: dict = {}
    monkeypatch.setattr(
        chat_module.reception_stage, "open_stage", lambda **_: (Stage("reception"), found)
    )
    monkeypatch.setattr(chat_module.reception_stage, "opening", lambda: "which agent")
    monkeypatch.setattr(
        chat_module.understand_stage,
        "open_stage",
        lambda *_, **__: (Stage("understand"), {}),
    )
    monkeypatch.setattr(chat_module.understand_stage, "opening", lambda _: "read the agent")

    conversation = Conversation(out=tmp_path, workspace=tmp_path)

    async def turn():
        await conversation.open_quietly()
        # what reception's turn does when one message both names the agent and asks for the next
        # thing: it points, then hands the request on.
        found["source"] = RepoSource(name="x", root=tmp_path)
        conversation._handoff["request"] = "read it and tell me what it can do"
        await conversation.say("test the voice agent at /x, and tell me what it can do")

    asyncio.run(turn())

    assert conversation.source is not None, "the turn that pointed never landed"
    assert conversation.stage_name == "understand"
    assert any("read it and tell me what it can do" in one for one in said), said


def test_the_build_skill_documents_every_method_a_handler_can_call():
    """A handler gets `db` and nothing else, so if the skill does not say what `db` offers the
    model guesses — and the guess is sqlite's cursor API, which fails on the smoke call."""
    import inspect

    from harness.world.runtime import Db

    skill = load_skill("build-environment")
    methods = [
        name for name, _ in inspect.getmembers(Db, inspect.isfunction)
        if not name.startswith("_")
    ]
    assert methods, "Db should have methods to document"
    for name in methods:
        assert f"db.{name}(" in skill, f"the build skill never shows db.{name}()"
    # and it warns off the API the model actually reaches for by default
    assert "fetchone" in skill


def test_a_crashed_handler_is_told_what_a_handler_actually_has(tmp_path):
    """An error naming the failure without naming the API produces the same wrong guess again.
    Three identical attempts at one handler is what that cost on a real run."""
    import asyncio

    from harness.world import tools as world_tools

    root, contract = _saved_world(tmp_path)
    server, _world = world_tools.world_tools(contract, root)
    instance = server.get("instance") if isinstance(server, dict) else server

    async def define(source):
        return await _call_tool(
            instance, "define_handler", {"tool_name": "add", "source": source}
        )

    # the mistake a model actually makes: sqlite's cursor API
    said = asyncio.run(define(
        "def handle(args, db):\n"
        "    return db.execute('SELECT 1').fetchone()\n"
    ))
    assert "crashed on its smoke call" in said
    assert "db.query(" in said and "db.one(" in said and "db.execute(" in said
    assert "fetchone" in said


# --- the store layer: engines, resets, and what a scenario lands on ----------------------


def test_a_reset_that_forgets_its_counters_is_caught_without_naming_one(tmp_path):
    """Rows going back is the easy half, and most wrong resets manage it.

    What they miss is the counter behind the rows, so the next scenario's first insert gets an id
    continuing from the last one, and a check naming a specific id then fails for a reason that
    has nothing to do with the agent. Rather than ask what a counter is called on this engine,
    the same change is run twice from the same starting point and the results compared.
    """
    from harness.world.stores import resolve
    from harness.world.stores.prove import prove_store

    def stood_up():
        store = resolve("sqlite")
        store.apply(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, who TEXT);"
            "INSERT INTO orders (who) VALUES ('ana'), ('bo');"
        )
        return store

    insert = "INSERT INTO orders (who) VALUES ('new')"
    assert not [one for one in prove_store(stood_up(), insert).results if not one.passed]

    # And with the counter deliberately left where it was, which is the bug itself.
    forgetful = stood_up()
    forgetful._reinstate = lambda counters: None
    failed = [one for one in prove_store(forgetful, insert).results if not one.passed]
    assert [one.name for one in failed] == ["ids do not drift"]
    assert "'id': 3" in failed[0].detail and "'id': 4" in failed[0].detail


def test_an_engine_nobody_taught_the_harness_is_refused_by_name():
    """Handing a ClickHouse agent a Postgres would produce a green suite about SQL it never runs."""
    from harness.world.stores import StoreError, resolve, supported

    with pytest.raises(StoreError) as refused:
        resolve("clickhouse")
    assert "clickhouse" in str(refused.value)
    for engine in supported():
        assert engine in str(refused.value)


def test_a_written_store_has_to_say_how_a_scenario_changes_it():
    """Reading an engine is not enough. Without add, amend and remove a suite has one world.

    A store that can be stood up and read but not changed lets every scenario run against the
    same base, and the per-scenario setup silently does nothing rather than failing.
    """
    from harness.world.stores import StoreError
    from harness.world.stores.written import register_written

    readable = (
        "def connect(dsn): return None\n"
        "def apply(db, script): pass\n"
        "def state(db): return {}\n"
        "def freeze(db): return {}, {}\n"
        "def restore(db, rows, counters): pass\n"
    )
    with pytest.raises(StoreError) as refused:
        register_written(engine="ledgerdb", image="x:1", container_port=1, code=readable)
    said = str(refused.value)
    assert "add" in said and "amend" in said and "remove" in said


def test_an_agent_that_holds_its_own_data_is_reached_by_its_own_loader():
    """Not by reading its files and rebuilding the structure, which would be a second
    implementation of the one thing this path exists to stop reimplementing."""
    from harness.world.stores import resolve

    called = []

    def load_data():
        called.append(True)
        return {"orders": {"o1": {"status": "pending"}}, "notes": ["first"]}

    store = resolve("in_process", loader=load_data)
    store.start()
    assert called, "the agent's own loader is what fills the store"

    # A keyed group reads back as records carrying the key, because that is the id a check names.
    assert store.records("orders") == [{"_id": "o1", "status": "pending"}]
    assert store.records("notes") == [{"value": "first"}]

    # What a scenario's setup lands on, in both shapes.
    kept = store.freeze()
    store.amend("orders", "o1", {"status": "cancelled"})
    store.add("orders", {"_id": "o2", "status": "pending"})
    assert store.state()["orders"] == [
        {"_id": "o1", "status": "cancelled"},
        {"_id": "o2", "status": "pending"},
    ]

    # And going back has to reproduce the agent's own structure, not merely the records.
    store.restore(kept)
    assert store.data == {"orders": {"o1": {"status": "pending"}}, "notes": ["first"]}


def test_emptying_a_store_is_something_restore_can_reproduce():
    """The check gate empties the world and insists every check notices, so a restore that cannot
    represent an empty store would make that gate impossible to run."""
    from harness.world.stores import Snapshot, resolve

    store = resolve("in_process", loader=lambda: {"orders": {"o1": {"status": "pending"}}})
    store.start()
    store.restore(Snapshot())
    assert store.state() == {"orders": []}
    # The group itself survives, because the agent's own code indexes into it.
    assert store.data == {"orders": {}}


def test_postgres_can_bind_to_an_external_scenario_database(monkeypatch):
    from harness.world.stores.postgres import PostgresStore

    external = "postgresql://alk@127.0.0.1:55432/alk"
    monkeypatch.setenv("ALK_POSTGRES_DSN", external)
    assert PostgresStore().dsn() == external


def test_postgres_preserves_native_arrays_when_writing_rows():
    from harness.world.stores.postgres import _adapt

    values = ["uberx", "comfort", "uberxl"]
    assert _adapt(values, "ARRAY") is values


def test_saving_a_world_that_already_lives_in_the_saved_file_does_not_hang(tmp_path):
    """An agent that connects by URI needs the world to be a real file, and the obvious file to
    give it is the one the world is saved to.

    SQLite retries a locked backup destination rather than refusing, so backing a database up onto
    its own file does not fail, it hangs, with no error and no timeout. The build then stops dead
    somewhere nobody is looking.
    """
    import threading

    from harness.world.runtime import GeneratedWorld
    from harness.world.snapshot import restore, save

    world = GeneratedWorld(tmp_path / "world.sqlite")
    world.store.apply("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT); INSERT INTO t (v) VALUES ('a');")

    done = threading.Event()

    def keep():
        save(world, tmp_path, sequences=[])
        done.set()

    worker = threading.Thread(target=keep, daemon=True)
    worker.start()
    assert done.wait(20), "saving a world onto its own file hung"
    assert len(restore(tmp_path).state()["t"]) == 1


def test_a_world_takes_the_agents_own_store_rather_than_retyping_it(tmp_path):
    """Seeding by hand means retyping somebody's data through a model, and what comes out is
    smaller and tidier than what went in: fewer rows, the awkward ones dropped, the accented names
    spelled the easy way. The agent's queries were written against the real thing."""
    import sqlite3

    from harness.world.runtime import GeneratedWorld

    theirs = tmp_path / "theirs.db"
    origin = sqlite3.connect(theirs)
    origin.executescript(
        "CREATE TABLE Customer (CustomerId INTEGER PRIMARY KEY, Name TEXT, Company TEXT);"
        "INSERT INTO Customer (Name, Company) VALUES ('Luis Goncalves', 'Embraer');"
        "INSERT INTO Customer (Name, Company) VALUES ('Leonie Kohler', NULL);"
    )
    origin.commit()
    origin.close()

    world = GeneratedWorld(":memory:")
    world.store.take(theirs)

    held = world.state()["Customer"]
    assert len(held) == 2
    # Their schema, not one inferred from the rows: the nullable column survives as null.
    assert held[1]["Company"] is None
    # And taking it is read-only on their side, so testing an agent never edits its data.
    assert theirs.stat().st_size > 0


def test_a_missing_store_names_the_root_and_what_is_under_it(tmp_path):
    """A message that says a path was wrong without saying what the right ones are turns one call
    into a search, over a filesystem this stage deliberately cannot list. That is how the same
    wrong guess gets made three times."""
    from harness.world.tools import _stores_here

    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "chinook.db").write_bytes(b"x" * 4096)
    (tmp_path / "agent.py").write_text("print('hi')", encoding="utf-8")

    said = _stores_here(str(tmp_path))
    assert str(tmp_path) in said, "the root itself has to be named"
    assert "nested/chinook.db" in said
    assert "4 KB" in said
    # A python file is not a store, and listing it would bury the one that is.
    assert "agent.py" not in said

    # And with no root at all, say that rather than reporting an empty directory.
    assert "not told where" in _stores_here("")


def test_a_tool_that_cannot_be_reached_has_a_way_out_that_is_recorded(tmp_path):
    """Without one there is no legitimate exit at all: define_handler refuses because the tool has
    an implementation, adopt_tool fails because that implementation needs something this
    environment does not have, and the only moves left are to give up or to lie."""
    from harness.amend import unreachable
    from harness.contract import AgentContract

    contract = AgentContract(
        agent="x",
        tools=[{"name": "look", "args": ["q"]}],
        real_use_cases=["a plain sentence"],
        tool_entrypoints=[
            {"tool": "look", "mode": "construct", "module": "vendor.tools", "callable": "Look._run"}
        ],
    )
    assert contract.adoptable("look")

    # Not without a reason: this is the only record that the tool was a stand-in.
    held, said = unreachable(contract, tmp_path, tool_name="look", why="  ")
    assert not held and "say why" in said

    held, said = unreachable(
        contract, tmp_path, tool_name="look", why="built by a framework that needs a live client"
    )
    assert held
    # The refusal is lifted, so a handler can be written.
    assert not contract.adoptable("look")
    # And the reason survives, on the contract, where a reader will find it.
    assert any("could not be reached" in one for one in contract.amendments)
    assert "live client" in contract.entry_for("look").notes

    kept = json.loads((tmp_path / "contract.json").read_text(encoding="utf-8"))
    assert kept["tool_entrypoints"][0]["mode"] == "generate"

    # And a tool that never had an implementation was never blocked, so there is nothing to record.
    again, said = unreachable(contract, tmp_path, tool_name="look", why="same reason")
    assert not again and "nothing is blocking" in said


def test_a_refusal_convention_written_with_escaped_quotes_still_matches():
    """A convention written for people gets quoted the way people quote, and a model writing JSON
    escapes those quotes. Left in, the backslash lands inside the marker, so "Error:" is looked for
    as 'Error:\\' and matches nothing. Every refusal is then recorded as a success, silently."""
    from harness.world.runtime import GeneratedWorld

    world = GeneratedWorld(":memory:")
    world.refusal_signature = (
        'sql_db_query and sql_db_schema return plain error strings beginning with \\"Error:\\" '
        '(e.g. \\"Error: (sqlite3.OperationalError) ...\\") on failure rather than raising'
    )
    assert world._markers(world.refusal_signature)[0] == "Error:"
    assert world._refused_by_value("Error: DML statements are not permitted.")
    assert not world._refused_by_value("[('AC/DC',), ('Accept',)]")

    # And the plain unescaped form, which is what a human writing the contract would put.
    world.refusal_signature = 'strings starting with "Error: "'
    assert world._refused_by_value("Error: no such order")


def test_emptying_a_world_survives_foreign_keys(tmp_path):
    """A real schema has references. Deleting table by table fails on the referenced ones, and a
    caller that swallows those failures believes it emptied a store still holding most of its data.

    The gate then reports every check as verifying nothing, because they are all still reading real
    rows, and somebody rewrites checks that were correct.
    """
    from harness.world.mutate import _empty, left
    from harness.world.runtime import GeneratedWorld

    world = GeneratedWorld(":memory:")
    world.store.apply(
        "CREATE TABLE Artist (ArtistId INTEGER PRIMARY KEY, Name TEXT);"
        "CREATE TABLE Album (AlbumId INTEGER PRIMARY KEY, Title TEXT, ArtistId INTEGER,"
        "  FOREIGN KEY (ArtistId) REFERENCES Artist (ArtistId));"
        "INSERT INTO Artist (ArtistId, Name) VALUES (1, 'AC/DC');"
        "INSERT INTO Album (AlbumId, Title, ArtistId) VALUES (1, 'Let There Be Rock', 1);"
    )
    assert sum(len(rows) for rows in world.state().values()) == 2

    _empty(world)
    assert left(world) == {}, "a referenced table has to be emptied too"


def test_a_mutation_that_does_not_land_accuses_nobody():
    """The gate accuses a check of verifying nothing when it stays green through damage. That is
    only fair if the damage happened."""
    from harness.world.mutate import EMPTIED, SILENCED, UNDAMAGED, blind, unnoticed
    from harness.world.runtime import GeneratedWorld

    class Stubborn(GeneratedWorld):
        def __init__(self):
            super().__init__(":memory:")
            self.store.apply("CREATE TABLE t (v TEXT); INSERT INTO t VALUES ('kept');")
            # A store that quietly refuses to empty, which is what a foreign key looked like.
            self.store.clear = lambda: None

    held = type("Outcome", (), {"held": True})()
    survived = unnoticed(
        "anywhere",
        [("reads_the_world", "def check(world):\n    return None\n")],
        run=lambda source, world: held,
        restore=lambda _root: Stubborn(),
    )

    assert survived[EMPTIED] == [], "nothing can be concluded from damage that did not happen"
    assert survived[UNDAMAGED] and "would not empty" in survived[UNDAMAGED][0]
    # The check stayed green through silencing, but that alone is not blindness.
    assert survived[SILENCED] == ["reads_the_world"]
    assert blind(survived) == []


def test_an_opening_line_in_the_agents_voice_falls_back_to_the_instruction():
    """The opening turn is the one with no conversation behind it, and a model asked to speak into
    that gap sometimes takes the other part.

    Both of these are real opening lines from a run: the agent then replied that no question had
    been asked, and the scenario failed for a reason that had nothing to do with the agent.
    """
    from harness.run.conversation import OPENING, _answered_as_the_agent

    assert _answered_as_the_agent("Sure! Let me find the database and make that update for you.")
    assert _answered_as_the_agent("I'd be happy to look that up! Let me check the database.")
    assert _answered_as_the_agent("What would you like to know about the database?")

    # What a person actually says, which must survive untouched.
    assert not _answered_as_the_agent("How many of your customers are based in Canada?")
    assert not _answered_as_the_agent(
        "I want all track prices changed to 0.99 for the promotion, can you update them?"
    )
    assert not _answered_as_the_agent("Which genre has the highest average listener rating?")

    # And the instruction that produces the opening says which part to play.
    assert "you speak first" in OPENING
    assert "not \nthe agent being contacted" in OPENING or "not the agent" in OPENING


def test_a_refusal_scenario_is_not_vacuous_because_its_evidence_is_what_was_said():
    """Where the right behaviour is to decline and touch nothing, every check about the world holds
    with nothing done, and the explanation is the only real evidence.

    Judged on that alone, the gate rejects exactly the scenarios that test a refusal. An agent that
    did nothing also said nothing, so a judged sub-goal cannot be passed by an empty run.
    """
    from harness.catalogue import Catalogue, SubGoal
    from harness.checks import Outcome
    from harness.prove import Proof

    catalogue = Catalogue(
        sub_goals=[
            SubGoal(name="no_dml_attempted", what="nothing was written", check="def check(w,c):\n    return None\n"),
            SubGoal(name="refused_clearly", what="it explained the refusal", judged="needs the reply read"),
        ]
    )
    assert catalogue.named("no_dml_attempted").deterministic()
    assert not catalogue.named("refused_clearly").deterministic()

    # The state check holds with nothing done, which on its own would read as vacuous.
    proof = Proof()
    proof.with_nothing = [Outcome("no_dml_attempted", True, "")]
    proof.weak = ["no_dml_attempted"]

    everything_weak = len(proof.weak) == len(proof.with_nothing)
    assert everything_weak
    judged = [
        name
        for name in ["no_dml_attempted", "refused_clearly"]
        if (found := catalogue.named(name)) is not None and not found.deterministic()
    ]
    assert judged == ["refused_clearly"]
    # Which is what spares the scenario.
    assert not (everything_weak and not judged)


def test_a_setup_or_ready_that_says_nothing_is_not_a_complaint():
    """The convention is that a complaint is a sentence. An empty string reads as "no complaint" to
    whoever wrote it, and taking it as a failure produces a rejection with no reason attached: the
    author is then sent hunting for a problem that is not there."""
    from harness.folder import _run

    for returning in ("''", "'   '", "None", "True"):
        outcome = _run(f"def ready(world):\n    return {returning}\n", "s/ready.py", "ready", None)
        assert outcome.ok, f"returning {returning} should read as holding"
        assert outcome.said == ""

    # A real complaint survives untouched.
    complained = _run(
        "def ready(world):\n    return 'no pending orders'\n", "s/ready.py", "ready", None
    )
    assert not complained.ok and complained.said == "no pending orders"

    # And False is a failure that says nothing, so the message says that rather than being blank.
    bare = _run("def ready(world):\n    return False\n", "s/ready.py", "ready", None)
    assert not bare.ok and "without saying what is wrong" in bare.said


def test_an_optional_field_may_be_null():
    """Filling a field that does not apply with null is what a model does, and it is not wrong: the
    alternative is inventing a value.

    Rejecting it costs a turn, and the rejection does not say which field was at fault. "None is
    not of type 'string'" is the whole message, on a tool with twenty properties.
    """
    import jsonschema
    from harness.tools import schema

    shape = schema({"name": str, "note": str, "size": int}, ["name"])

    # Required stays strict.
    assert shape["properties"]["name"]["type"] == "string"
    assert shape["properties"]["note"]["type"] == ["string", "null"]
    assert shape["properties"]["size"]["type"] == ["integer", "null"]

    jsonschema.validate({"name": "x", "note": None, "size": None}, shape)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"name": None}, shape)

    # A property given as a full fragment is left exactly as written.
    spelled = schema({"size": {"type": "string", "enum": ["S", "M"]}}, [])
    assert spelled["properties"]["size"] == {"type": "string", "enum": ["S", "M"]}


def test_an_agent_with_no_store_still_gets_a_world_that_holds_things():
    """An agent whose state lives in services and files has no store to declare collections in, so
    every collection the world needs is one the harness invents.

    Refusing the first record leaves that agent with a world that cannot hold anything at all, and
    there is no other way for the build to put its stand-in data somewhere.
    """
    from harness.world.runtime import GeneratedWorld

    world = GeneratedWorld(":memory:", kind="in_process")
    assert world.state() == {}

    world.put("reports", {"title": "first"})
    world.put("reports", {"title": "second"})
    assert len(world.state()["reports"]) == 2

    # A keyed collection stays keyed, so a scenario can name one record.
    world.put("sources", {"url": "a"}, key="s1")
    assert world.change("sources", "s1", {"url": "b"}) == 1
    assert world.state()["sources"] == [{"_id": "s1", "url": "b"}]

    assert world.drop("reports") == 2
    assert world.state()["reports"] == []


def test_a_handler_can_read_a_world_that_has_no_query_language():
    """An agent whose state lives in services and files gets a world whose collections the harness
    invented, and there is no dialect to write a SELECT in.

    A handler that could only issue SQL would be unable to read the world it was given at all,
    which is where the build stalls: seed works, then nothing can look at what was seeded.
    """
    from harness.world.runtime import GeneratedWorld

    world = GeneratedWorld(":memory:", kind="in_process")
    world.put("search_results", {"query": "solar", "title": "Solar in 2026", "rank": 1})
    world.put("search_results", {"query": "wind", "title": "Wind at sea", "rank": 1})

    world.handlers = {
        "search": (
            "def handle(args, db):\n"
            "    found = db.find('search_results', query=args['query'])\n"
            "    if not found:\n"
            "        raise ToolError('no results')\n"
            "    return [one['title'] for one in found]\n"
        )
    }
    assert world.call("search", {"query": "solar"}).result == ["Solar in 2026"]
    # And a handler can still say no, which is the behaviour worth testing.
    assert world.call("search", {"query": "nothing"}).refused

    # The collection names are reachable too, without knowing what kind of store this is.
    assert world.call(
        "search", {"query": "wind"}
    ).ok


def test_a_world_with_no_database_can_still_be_reverted():
    """Every probe and every smoke call takes a checkpoint first, so a world that cannot be
    checkpointed cannot be built at all: define_handler fails before the handler body even runs,
    and the error names the store rather than anything the author wrote."""
    from harness.world.runtime import GeneratedWorld

    world = GeneratedWorld(":memory:", kind="in_process")
    world.put("reports", {"title": "first"})

    held = world.checkpoint()
    world.put("reports", {"title": "second"})
    assert len(world.state()["reports"]) == 2

    world.revert(held)
    assert [one["title"] for one in world.state()["reports"]] == ["first"]

    # And defining a handler, which is what actually stalled: it checkpoints around the smoke call.
    world.handlers = {"look": "def handle(args, db):\n    return len(db.records('reports'))\n"}
    assert world.call("look", {}).result == 1


def test_a_world_with_no_database_still_counts_as_built(tmp_path):
    """Whether a stage is done is keyed on the manifest, not on a database file.

    Keyed on the database, an agent whose state lives in services and files stays "not built"
    forever: the world saves, scores 1.00, and the conversation still cannot leave the build stage
    because the file it is looking for was never going to exist.
    """
    from harness.chat import Conversation
    from harness.world.runtime import GeneratedWorld
    from harness.world.snapshot import save

    world = GeneratedWorld(":memory:", kind="in_process")
    world.put("reports", {"title": "first"})
    save(world, tmp_path, sequences=[])

    assert not (tmp_path / "world.sqlite").exists(), "this world has no database, by construction"
    assert Conversation(out=tmp_path).world_built

    # And a world that does have one is still built, which is the case that already worked.
    other = tmp_path / "sql"
    save(GeneratedWorld(":memory:"), other, sequences=[])
    assert Conversation(out=other).world_built


def test_a_storeless_world_comes_back_on_the_right_side_of_the_seam(tmp_path):
    """The store and the agent's own state are two different things, and both are written beside
    the world. Sharing one filename means whichever is written second wins.

    The symptom is quiet: the world reads correctly, because state() merges both sides, but the
    store is empty. The mutation gate then empties a store that was never holding anything and
    reports every check as verifying nothing.
    """
    from harness.world.mutate import _empty, left
    from harness.world.runtime import GeneratedWorld
    from harness.world.snapshot import restore, save

    world = GeneratedWorld(":memory:", kind="in_process")
    world.put("reports", {"title": "first"})
    world.put("reports", {"title": "second"})
    save(world, tmp_path, sequences=[])

    again = restore(tmp_path)
    assert again.store.state()["reports"], "the records belong to the store"
    assert again.state_object is None, "and not to the agent's own state, which it never had"
    assert len(again.state()["reports"]) == 2

    # Which is what lets the gate actually break this world.
    _empty(again)
    assert left(again) == {}


def test_dropping_a_scenario_removes_it_from_disk(tmp_path):
    """The folders are the truth and they are what gets read back. Writing the survivors without
    taking the others away means a dropped scenario returns on the next load, still failing, and
    dropping it appears to do nothing at all."""
    from harness.catalogue import Catalogue, SubGoal
    from harness.scenario import Scenario
    from harness.scenario_tools import load_scenarios, write_scenarios

    catalogue = Catalogue(
        sub_goals=[SubGoal(name="held", what="it held", check="def check(w,c):\n    return None\n")]
    )
    made = [
        Scenario(name="keeper", instruction="a thing happens", sub_goals=["held"]),
        Scenario(name="goner", instruction="another thing", sub_goals=["held"]),
    ]
    write_scenarios(made, tmp_path, catalogue)
    assert sorted(one.name for one in load_scenarios(tmp_path)) == ["goner", "keeper"]

    write_scenarios([made[0]], tmp_path, catalogue)
    assert [one.name for one in load_scenarios(tmp_path)] == ["keeper"]
    assert not (tmp_path / "scenarios" / "goner").exists()


def test_a_target_refuses_a_model_it_cannot_drive():
    """Handed a model it cannot speak to, this target does not fail: it produces a session that
    answers nothing, which arrives as a scenario with no turns, no calls and every check red.

    That reads exactly like an agent that ignored the person, and a whole suite is then wrong in a
    way nobody would think to question. It cost a full run to find.
    """
    from harness.run.targets import _drivable

    # What it runs on.
    _drivable(None)
    _drivable("claude-sonnet-4-6")
    _drivable("anthropic/claude-opus-4-7")

    with pytest.raises(RuntimeError) as refused:
        _drivable("vertex_ai/gemini-2.5-flash")
    assert "cannot run" in str(refused.value)
    # And it says where to go instead, rather than only saying no.
    assert "endpoint adapters" in str(refused.value)


def test_a_run_is_a_folder_that_can_be_read_back(tmp_path):
    """A session accumulates runs. One simulation over a suite is one run, kept whole, so runs can
    be compared instead of the next one overwriting the last."""
    from harness.run.grade import Result
    from harness.run.simulation import _write_case, every_run, read_run, run_root

    root = run_root(tmp_path, "run-1")
    folder = root / "a-scenario"
    folder.mkdir(parents=True)
    kept = Result(scenario="a-scenario", transcript="user: hi\nagent: hello")
    kept.calls_detail = [{"name": "look", "arguments": {"q": "x"}, "ok": True, "at": 1.5}]
    _write_case(folder, kept)
    (root / "run.json").write_text(
        json.dumps({"run_id": "run-1", "agent": "x", "scenarios": 1, "passed": 1}),
        encoding="utf-8",
    )

    listed = every_run(tmp_path)
    assert [one["run_id"] for one in listed] == ["run-1"]

    whole = read_run(tmp_path, "run-1")
    assert whole["scenarios"][0]["scenario"] == "a-scenario"
    assert "hello" in whole["scenarios"][0]["transcript"]
    # Down to a single call, which is what the harness is asked about when a run is questioned.
    assert whole["scenarios"][0]["calls_detail"][0]["name"] == "look"


def test_the_closing_line_is_kept():
    """The sentinel used to be the whole reply, and breaking on it threw the words away.

    Every conversation then ended on the agent's turn with nothing after it: a transcript that
    reads as cut off rather than finished, and no way to tell a person who left satisfied from
    one who was still waiting for an answer.
    """
    from harness.run.conversation import DONE, STUCK, customer_prompt

    said = "Thanks, that's exactly what I needed.\n[DONE]"
    closing = said.replace(DONE, "").replace(STUCK, "").strip()
    assert closing == "Thanks, that's exactly what I needed."

    # And a bare sentinel still ends it, without recording an empty turn.
    assert not "[DONE]".replace(DONE, "").replace(STUCK, "").strip()

    # The person is asked for that line, and told not to leave while the agent is waiting.
    from harness.contract import AgentContract
    from harness.scenario import Scenario

    asked = customer_prompt(
        Scenario(name="s", instruction="you want a thing", sub_goals=[]),
        AgentContract(agent="x", tools=[{"name": "t", "args": ["a"]}], real_use_cases=["a sentence"]),
    )
    assert "the one line you would actually say to end it" in asked
    assert "not the end of the conversation" in asked


def test_a_judged_sub_goal_becomes_a_named_platform_eval():
    """The sentence a sub-goal already is happens to be exactly what a custom eval wants, so it
    becomes one: created once, versioned, visible in the product rather than only in a run
    folder, and reusable against production traffic later without being rewritten."""
    from harness.run import platform_evals

    name = platform_evals.eval_name("text-to-sql", "refused_dml_clearly")
    assert name == "text-to-sql-refused_dml_clearly"
    # The agent is in the name because uniqueness is per organisation: a bare sub-goal name
    # would collide across every agent anybody tests.
    assert platform_evals.eval_name("other-agent", "refused_dml_clearly") != name
    # And the platform only accepts a restricted alphabet.
    assert platform_evals.eval_name("Drive Thru!", "no DML") == "drive-thru--no-dml"

    written = platform_evals.instructions_for(
        "the agent explained why it could not", "shop", ["never write to the database"]
    )
    # One variable, declared by being written: the platform extracts it from the instructions.
    assert "{{conversation}}" in written
    assert "never write to the database" in written


def test_a_verdict_is_read_however_it_arrives():
    """A pass comes back as a word or a number depending on the eval's output type, and reading
    only one shape would silently fail every eval configured the other way."""
    from harness.run.platform_evals import _passed

    assert _passed("Pass") and _passed("pass") and _passed("PASSED")
    assert _passed(True) and _passed(1.0) and _passed(0.5)
    assert not _passed("Fail") and not _passed(False) and not _passed(0.2)
    assert not _passed(None)


def test_the_platform_is_used_only_when_it_is_configured():
    """Without keys the harness judges here instead. A suite that cannot run without a platform
    account is a worse tool than one that degrades."""
    import os

    from harness.run import platform_evals

    kept = {name: os.environ.pop(name, None) for name in platform_evals.KEYS}
    try:
        assert not platform_evals.configured()
        os.environ["FI_API_KEY"] = "x"
        assert not platform_evals.configured(), "both keys are needed, not one"
        os.environ["FI_SECRET_KEY"] = "y"
        assert platform_evals.configured()
    finally:
        for name, value in kept.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value


def test_voice_suite_evals_use_the_documented_platform_inputs(monkeypatch):
    from harness.catalogue import default_suite_evals
    from harness.contract import AgentContract
    from harness.run import platform_evals
    from harness.run.conversation import Exchange, Transcript
    from harness.run.grade import judge_suite_evals
    from harness.scenario import Scenario

    calls = []

    def judge_builtin(name, inputs):
        calls.append((name, inputs))
        output = "Pass" if name == "customer_agent_task_completion" else {"choice": "4"}
        return {"output": output, "why": "verified", "model": "turing_flash"}

    monkeypatch.setattr(platform_evals, "configured", lambda: True)
    monkeypatch.setattr(platform_evals, "judge_builtin", judge_builtin)
    contract = AgentContract(
        agent="voice-agent",
        modality="voice",
        system_prompt_excerpt="Resolve support requests accurately.",
        tools=[{"name": "lookup", "args": ["order_id"]}],
        real_use_cases=["support"],
    )
    transcript = Transcript(
        exchanges=[Exchange("customer", "Where is my order?"), Exchange("agent", "It arrives tomorrow.")]
    )
    verdicts = judge_suite_evals(
        default_suite_evals(),
        Scenario(name="order-status", instruction="check an order", sub_goals=[]),
        transcript,
        contract,
    )

    assert calls == [
        (
            "customer_agent_task_completion",
            {
                "agent_prompt": "Resolve support requests accurately.",
                "conversation": "customer: Where is my order?\nagent: It arrives tomorrow.",
            },
        ),
        (
            "customer_agent_conversation_quality",
            {"conversation": "customer: Where is my order?\nagent: It arrives tomorrow."},
        ),
    ]
    assert all(verdict.holds for verdict in verdicts)


def test_suite_evals_do_not_run_for_non_voice_agents(monkeypatch):
    from harness.catalogue import default_suite_evals
    from harness.contract import AgentContract
    from harness.run import platform_evals
    from harness.run.conversation import Transcript
    from harness.run.grade import judge_suite_evals
    from harness.scenario import Scenario

    monkeypatch.setattr(platform_evals, "configured", lambda: True)
    monkeypatch.setattr(platform_evals, "judge_builtin", lambda *_args: pytest.fail("should not run"))
    contract = AgentContract(
        agent="chat-agent",
        modality="chat",
        tools=[{"name": "lookup", "args": ["order_id"]}],
        real_use_cases=["support"],
    )
    assert judge_suite_evals(
        default_suite_evals(),
        Scenario(name="status", instruction="check", sub_goals=[]),
        Transcript(),
        contract,
    ) == []
def test_webhook_binds_where_the_environment_says(monkeypatch):
    from harness.run.voice import WorldWebhook

    monkeypatch.setenv("HARNESS_WEBHOOK_HOST", "127.0.0.1")
    monkeypatch.setenv("HARNESS_WEBHOOK_PORT", "0")
    webhook = WorldWebhook()
    try:
        assert webhook._server.server_address[0] == "127.0.0.1"
    finally:
        webhook._server.server_close()


def test_webhook_arguments_beat_the_environment(monkeypatch):
    from harness.run.voice import WorldWebhook

    monkeypatch.setenv("HARNESS_WEBHOOK_PORT", "1")
    webhook = WorldWebhook(port=0)
    try:
        assert webhook.port != 1
    finally:
        webhook._server.server_close()


# --- environments cross-session listing -----------------------------------------------


def _environment_session(base, name, runs=()):
    from harness import sessions

    session = sessions.create(agent=name, base=base)
    (session.path / "contract.json").write_text(
        json.dumps({"agent": name, "one_liner": f"{name} does things"}), encoding="utf-8"
    )
    (session.path / "manifest.json").write_text(
        json.dumps({"tables": {}, "tools": ["lookup", "refund"]}), encoding="utf-8"
    )
    for run_id, (total, passed) in runs:
        folder = session.path / "runs" / run_id
        folder.mkdir(parents=True)
        (folder / "run.json").write_text(
            json.dumps({"run_id": run_id, "scenarios": total, "passed": passed}),
            encoding="utf-8",
        )
    return session


def test_environments_lists_only_sessions_with_a_world(tmp_path):
    from harness import sessions

    _environment_session(tmp_path, "support")
    sessions.create(agent="incomplete", base=tmp_path)  # no world
    rows = sessions.environments(tmp_path)
    assert [one["agent"] for one in rows] == ["support"]
    assert rows[0]["one_liner"] == "support does things"
    assert rows[0]["tools"] == 2


def test_environments_counts_simulation_runs_not_chat_runs(tmp_path):
    from harness import sessions

    _environment_session(tmp_path, "billing", runs=[("run-1", (3, 3)), ("run-2", (3, 1))])
    rows = sessions.environments(tmp_path)
    assert rows[0]["runs"] == 2
    assert rows[0]["runs_passed"] == 1


# --- reporting a run to the platform ---------------------------------------------------


def _reported_result(**over):
    from harness.run.grade import Checkpoint, Result

    result = Result(
        scenario=over.pop("scenario", "cancel-pending-order"),
        ended="finished",
        seconds=12.5,
        spent_usd=0.25,
        checkpoints=[
            Checkpoint(name="user_authenticated", kind="code", passed=True),
            Checkpoint(name="confirmation_obtained", kind="judged", passed=False, detail="never asked"),
        ],
    )
    result.exchanges = [
        {"speaker": "customer", "text": "cancel my order"},
        {"speaker": "agent", "text": "which one?"},
    ]
    result.calls_detail = [
        {"name": "cancel_pending_order", "arguments": {"order_id": "#W1"},
         "result": "cancelled", "ok": True, "refused": False, "error": "", "at": 1.0}
    ]
    for key, value in over.items():
        setattr(result, key, value)
    return result


def test_a_run_reaches_the_platform_as_transcript_rows_it_understands():
    from harness import platform

    rows = platform.segments_of(_reported_result())
    assert [r["speaker_role"] for r in rows] == [
        "user", "assistant", "tool_calls", "tool_call_result",
    ]
    # The platform derives its metrics from timings, so inventing them here would be
    # indistinguishable downstream from timings that were actually measured.
    assert all("start_time_ms" not in row for row in rows)


def test_sub_goals_travel_named_so_a_page_can_show_one_per_column():
    from harness import platform

    payload = platform.result_of(_reported_result())
    checks = payload["call_metadata"]["harness_checkpoints"]
    assert [c["name"] for c in checks] == ["user_authenticated", "confirmation_obtained"]
    assert [c["passed"] for c in checks] == [True, False]
    assert payload["call_metadata"]["harness_of"] == 2


def test_a_scenario_that_never_ran_is_not_reported_as_one_the_agent_failed():
    from harness import platform

    ran = platform.result_of(_reported_result())
    blocked = platform.result_of(_reported_result(problems=["the world was not ready"]))
    assert ran["status"] == "completed"
    assert blocked["status"] == "failed"
    assert "not ready" in blocked["error_message"]


def test_a_second_run_joins_the_same_test_rather_than_starting_another():
    from harness import platform

    class Recording:
        def __init__(self):
            self.provisioned = 0
            self.started = 0

        def provision(self, name, personas, modality="text"):
            self.provisioned += 1
            return {"run_test_id": "rt-1"}

        def start(self, run_test_id):
            self.started += 1
            return {"test_execution_id": f"te-{self.started}"}

        def batch(self, test_execution_id, count):
            return {"call_execution_ids": [f"ce-{n}" for n in range(count)]}

        def result(self, call_execution_id, payload):
            return {"status": "ingested"}

    api = Recording()
    first = platform.report([_reported_result()], [], name="s", platform=api)
    second = platform.report(
        [_reported_result()], [], name="s", run_test_id=first.run_test_id, platform=api
    )
    assert api.provisioned == 1
    assert (first.test_execution_id, second.test_execution_id) == ("te-1", "te-2")
    assert second.url == "/dashboard/simulate/test/rt-1/runs"


def test_reporting_says_when_the_platform_allocated_too_few_calls():
    from harness import platform

    class Short:
        def provision(self, name, personas, modality="text"):
            return {"run_test_id": "rt-1"}

        def start(self, run_test_id):
            return {"test_execution_id": "te-1"}

        def batch(self, test_execution_id, count):
            return {"call_execution_ids": ["ce-0"]}

        def result(self, call_execution_id, payload):
            return {"status": "ingested"}

    reported = platform.report(
        [_reported_result(scenario="a"), _reported_result(scenario="b")],
        [], name="s", platform=Short(),
    )
    assert reported.calls == {"a": "ce-0"}
    assert "allocated 1 calls for 2 scenarios" in " ".join(reported.problems)
