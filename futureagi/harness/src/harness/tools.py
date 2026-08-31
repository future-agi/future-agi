"""The tools the harness offers a session, and the gates behind them.

The model does judgement; these do the parts that must be exact. Validation lives inside the
tool rather than after the session, so a problem is returned into the conversation and fixed on
the next turn instead of surfacing once the session is already over.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from .contract import MODALITIES, AgentContract, validate_contract

CONTRACT_SERVER = "contract"


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


# validate_contract returns short codes: they are stable, testable, and the same string every
# time. What a code means is a separate question, and answering it here keeps the codes exact
# while the message the model reads says what to actually do.
_GUIDANCE = {
    "empty:agent": "the `agent` field is empty. A short lower-case name; it is only the "
    "artifact folder's label",
    "no-tools": "the `tools` field is empty. List the agent's real tools; nothing downstream "
    "can be built without them",
    "no-use-cases": "the `real_use_cases` field is empty — note the name, it is not "
    "`use_cases`. List the concrete situations this agent handles, from its tools and data",
    "no-arguments-on-any-tool": "every tool was recorded with no arguments, which means they "
    "were read and not written down. Put each tool's exact parameter names in args",
    "duplicate-tool-names": "the same tool is listed twice; keep one entry per tool",
    "types-for-unknown-args": "arg_types names an argument that is not in args. The names must "
    "match the source exactly",
}


def _advice(code: str) -> str:
    for key, said in _GUIDANCE.items():
        if code.startswith(key) or key in code:
            return f"{code} — {said}"
    return code


def _problems(problems: list[str], arrived: list[str] | None = None) -> dict[str, Any]:
    """Every problem at once, each with what to do about it.

    All of them together, never one at a time: a gate that reveals the next problem only after
    the last is fixed costs a full turn per problem and reads as though the rules are being
    invented as it goes.

    When the fields arrived under names this does not recognise, it says which names it got.
    Without that the answer is "agent is empty, there are no tools" about a submission that
    contained both, and the only way out is guessing at the packaging.
    """
    said = "Not accepted. Fix all of these and call submit_contract again:\n  - " + (
        "\n  - ".join(_advice(problem) for problem in problems)
    )
    unrecognised = arrived is not None and not any(
        key in arrived for key in ("agent", "tools", "real_use_cases")
    )
    if unrecognised:
        said += (
            f"\n\nWhat arrived was: {', '.join(arrived) or '(nothing)'}. None of those are "
            "contract fields, so the fields were probably nested inside something or sent as "
            "one JSON string. Send them as the tool's own top-level arguments — agent, tools, "
            "real_use_cases and the rest — not wrapped in an outer object."
        )
    return {
        "content": [{"type": "text", "text": said}],
        "is_error": True,
    }


_CONTRACT_KEYS = ("agent", "tools", "real_use_cases", "one_liner", "hard_constraints")


def _looks_like_a_contract(value: Any) -> bool:
    return isinstance(value, dict) and any(key in value for key in _CONTRACT_KEYS)


def unwrapped(payload: dict[str, Any]) -> dict[str, Any]:
    """The contract itself, however it was packaged.

    A contract is a nested thing being described, so it arrives wrapped — ``{"contract": {...}}``
    — or stringified, as JSON in a single argument, often enough to matter. In both the fields
    are present and correct and only the packaging is wrong. Rejecting that teaches nothing
    about the agent and costs a full turn, so it is unpacked; only an object that actually looks
    like a contract is unwrapped, so a real field that happens to hold a dict is never mistaken
    for an envelope.
    """
    if not isinstance(payload, dict):
        payload = {}
    if any(key in payload for key in ("agent", "tools", "real_use_cases")):
        return payload
    for value in payload.values():
        if _looks_like_a_contract(value):
            return value
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("```"):
                # Fenced JSON: the model wrote it as it would in a message.
                text = text.strip("`").removeprefix("json").strip()
            if not text.startswith("{"):
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if _looks_like_a_contract(parsed):
                return parsed
            for inner in parsed.values() if isinstance(parsed, dict) else []:
                if _looks_like_a_contract(inner):
                    return inner
    return payload


def accept_contract(payload: dict[str, Any], destination: Path) -> dict[str, Any]:
    """The gate itself: validate, and write only if it passes.

    A plain function rather than only a tool body, so the rule that decides whether a contract
    is usable can be exercised and reasoned about without standing up a session.
    """
    arrived = sorted(payload) if isinstance(payload, dict) else [type(payload).__name__]
    payload = unwrapped(payload)
    try:
        contract = AgentContract.model_validate(payload)
    except Exception as invalid:
        return _problems([f"schema:{invalid}"[:600]], arrived)

    problems = validate_contract(contract)
    if problems:
        return _problems(problems, arrived)

    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "contract.json"
    path.write_text(
        json.dumps(contract.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return _ok(
        f"Accepted and saved to {path}.\n"
        f"{len(contract.tools)} tools: {', '.join(sorted(contract.tool_names()))}\n"
        f"{len(contract.hard_constraints)} rules, "
        f"{len(contract.real_use_cases)} use cases, "
        f"{len(contract.open_questions)} open questions."
    )


def contract_tools(destination: Path) -> Any:
    """A server exposing ``submit_contract``, writing to ``destination`` on acceptance."""
    # Each of these is a nudge, not a wall: the first submission missing something that is
    # nearly always there gets sent back with directions, and a second submission is accepted.
    # A gate with no way through would permanently block the rare agent that genuinely lacks it,
    # and this stage cannot tell those two apart from the outside.
    nudged: set[str] = set()

    @tool(
        "submit_contract",
        "Submit the agent's testing contract: everything verifiably true about this agent, as "
        "one flat object. Every field is described in the schema; fill in what the source "
        "supports and leave the rest out.\n\n"
        "It is validated when you call it. If anything is wrong you get the whole list back at "
        "once, in terms of what to fix, and you submit again.",
        # Nothing required, and that is deliberate. This layer runs before the tool body, so
        # anything it rejects never reaches the code that could have understood it — a contract
        # sent inside a wrapper is complete and correct, and is unwrapped a few lines below, but
        # only if it gets there. accept_contract is the single gate; it reports every problem at
        # once and says what to do about each.
        #
        # The descriptions are the point of this block. The schema is shown to the model before
        # it calls anything, so what is written here is the difference between a correct first
        # call and a sequence of rejected guesses.
        schema(
            {
                "agent": {
                    "type": "string",
                    "description": "Short lower-case identifier, no spaces. Only a label for "
                    "the artifact folder.",
                },
                "one_liner": {
                    "type": "string",
                    "description": "One sentence: what this agent is for.",
                },
                "modality": {
                    "type": "string",
                    "enum": list(MODALITIES),
                    "description": "How a person reaches it, read from its runtime. A voice "
                    "session (LiveKit, telephony, TTS/STT) is voice; a text interface is chat; "
                    "a browser-driving agent is browser. This decides how it is later run.",
                },
                "conversational": {
                    "type": "boolean",
                    "description": "True if a person talks with it turn by turn. False for an "
                    "agent given one task and left to it.",
                },
                "system_prompt_excerpt": {
                    "type": "string",
                    "description": "The agent's own instructions, quoted. Often lives away from "
                    "the main agent file.",
                },
                "hard_constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Rules it must obey, in the source's own words. The agent "
                    "under test is told these and graded against them.",
                },
                "tools": {
                    "type": "array",
                    "description": "Every tool the agent really has. Everything downstream is "
                    "built from these, so a tool without its arguments cannot be tested.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "The exact callable name the model emits.",
                            },
                            "args": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Exact parameter names, in order.",
                            },
                            "arg_types": {
                                "type": "object",
                                "description": "Declared type per argument where the source "
                                'states one: {"recipient_ids": "list[str]"}.',
                            },
                            "arg_values": {
                                "type": "object",
                                "description": "Real permitted values per argument where it is "
                                "constrained to a set, an enum or a lookup: "
                                '{"priority": ["low", "normal", "urgent"]}.',
                            },
                            "description": {"type": "string"},
                        },
                        # Nothing required: a tool genuinely taking no arguments is ordinary,
                        # and requiring args here rejects the whole contract because of one.
                        # That every tool has none is the real defect, and validate_contract
                        # is where it is caught, with an explanation.
                    },
                },
                "data_schema": {
                    "type": "object",
                    "description": "The shape of the records the agent works on: which fields "
                    "each kind of record has.",
                },
                "base_environment": {
                    "type": "object",
                    "description": "Its real starting data, reproduced exactly — including "
                    "anything that looks like a mistake. The world is a replica, not a "
                    "corrected version.",
                },
                "dependencies": {
                    "type": "array",
                    "description": "Everything this agent reaches for that has to exist before "
                    "it can work, so the next stage knows what to build. A datastore, a service "
                    "it calls over HTTP, a file it reads, a queue it publishes to. The world is "
                    "a sandbox and nothing reaches outside it, so each of these is built inside "
                    "it — the agent's call goes to something real that happens to be ours.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "kind": {
                                "type": "string",
                                "description": "datastore, service, file, queue, or whatever "
                                "this actually is.",
                            },
                            "what": {
                                "type": "string",
                                "description": "What it holds or answers, and what the agent "
                                "needs from it.",
                            },
                            "used_by": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "The tools that cannot work without it.",
                            },
                        },
                    },
                },
                "real_use_cases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "What this agent is for, one plain sentence each. These are "
                    "capabilities, not test cases: 'cancel an order that has not shipped', not "
                    "a narrated situation with a customer, a name and an outcome. Scenarios are "
                    "written later, from these.",
                },
                "notes": {
                    "type": "string",
                    "description": "Free-form, yours. Anything else worth carrying forward: "
                    "quirks, traps, a plausible name that does not exist, an id that looks like "
                    "a typo but is real. Shown verbatim to every later stage.",
                },
                "open_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "What the source did not settle and you could not ask about.",
                },
                "implementation": {
                    "type": "string",
                    "enum": ["present", "absent", "partial"],
                    "description": "Whether the agent ships working code for its tools, as "
                    "opposed to only declaring them. The environment runs the agent's own code "
                    "wherever it exists, so this decides whether anything gets written for it.",
                },
                "tool_entrypoints": {
                    "type": "array",
                    "description": "How to reach the agent's own implementation of each tool. "
                    "One entry per tool that has code. Without this the environment has to write "
                    "a replacement, which tests our reading of the agent instead of the agent.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {
                                "type": "string",
                                "description": "The tool name, exactly as in `tools`.",
                            },
                            "mode": {
                                "type": "string",
                                "enum": ["import", "construct", "service", "generate"],
                                "description": "import: a module-level function or a method on a "
                                "class, reachable directly. construct: it hangs off an object "
                                "that has to be built first. service: it is already reachable "
                                "over HTTP. generate: there is no implementation, so one has to "
                                "be written. Choose generate only when nothing can be reached.",
                            },
                            "module": {
                                "type": "string",
                                "description": "Importable path as the agent's own code would "
                                "write it, e.g. package.module.file. Not a filesystem path.",
                            },
                            "callable": {
                                "type": "string",
                                "description": "What to call inside that module. May be dotted "
                                "to reach a method on a class, e.g. TheClass.the_method.",
                            },
                            "factory": {
                                "type": "string",
                                "description": "For construct: the expression that builds the "
                                "object, including whatever it needs to be constructed with.",
                            },
                            "first_arg": {
                                "type": "string",
                                "description": "If the callable takes the agent's own state as "
                                "its first argument, its name. Empty when the callable opens its "
                                "own connection instead.",
                            },
                            "notes": {
                                "type": "string",
                                "description": "Anything about reaching it that the fields above "
                                "do not carry, especially why a tool cannot be reached.",
                            },
                        },
                    },
                },
                "refusal_signature": {
                    "type": "string",
                    "description": "How this agent's own code says no in a value it returns "
                    "rather than by raising, described so it can be recognised, e.g. a string "
                    "beginning with a particular marker. Production code often reports failure "
                    "this way, and without this a refusal is recorded as a success, which hides "
                    "the behaviour most worth testing.",
                },
                "data_store": {
                    "type": "object",
                    "description": "What the agent's tools read and write, and how to point them "
                    "at a different one.",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "description": "postgres, clickhouse, mysql, sqlite, in_process for "
                            "state held in memory, or none.",
                        },
                        "configured_by": {
                            "type": "string",
                            "description": "How the code chooses its connection: the environment "
                            "variable it reads, the config file, or the constructor argument. "
                            "This is what makes substituting a store possible without editing "
                            "the agent, so say if it is hardcoded.",
                        },
                        "schema_from": {
                            "type": "string",
                            "description": "Where the schema comes from: its migrations, a DDL "
                            "file, its ORM models.",
                        },
                        "loaded_by": {
                            "type": "string",
                            "description": "The agent's own loader, if it has one that builds "
                            "its starting data, as module and callable.",
                        },
                        "loader_module": {
                            "type": "string",
                            "description": "The module that loader is imported from, so it can "
                            "be called rather than reimplemented.",
                        },
                        "version": {
                            "type": "string",
                            "description": "The engine version, where the agent pins one.",
                        },
                        "config_key": {
                            "type": "string",
                            "description": "Where a config file holds the connection instead, as "
                            "a dotted path such as database.url.",
                        },
                        "host": {
                            "type": "string",
                            "description": "The host the agent expects. Record it even when it "
                            "is hardcoded: a hardcoded name is not a dead end, it is a name our "
                            "store can answer to.",
                        },
                        "port": {
                            "type": "integer",
                            "description": "The port it expects.",
                        },
                        "database": {
                            "type": "string",
                            "description": "The database name it expects. Ours is created with "
                            "exactly this name rather than the agent being changed.",
                        },
                        "user": {
                            "type": "string",
                            "description": "The user it connects as.",
                        },
                        "password_from": {
                            "type": "string",
                            "description": "Where the password comes from, never the password "
                            "itself. A contract is written to disk and read by people, so a "
                            "secret in it outlives the run that needed it.",
                        },
                    },
                },
                "runtime": {
                    "type": "object",
                    "description": "What it takes to run the agent's code.",
                    "properties": {
                        "language": {"type": "string"},
                        "version": {"type": "string"},
                        "install": {
                            "type": "string",
                            "description": "Its own install command, e.g. from its lockfile or "
                            "requirements. Used as written rather than guessed at.",
                        },
                        "workdir": {
                            "type": "string",
                            "description": "Where in the source imports resolve from, if not the "
                            "root.",
                        },
                        "dockerfile": {
                            "type": "string",
                            "description": "Path to its own Dockerfile, if it has one. Theirs is "
                            "used in preference to anything written for it.",
                        },
                    },
                },
            },
            [],
        ),
    )
    async def submit_contract(args: dict[str, Any]) -> dict[str, Any]:
        payload = unwrapped(args)

        thin = [
            (
                "prompt",
                bool(payload.get("conversational", True))
                and not payload.get("hard_constraints")
                and not str(payload.get("system_prompt_excerpt") or "").strip(),
                "no hard_constraints and no system_prompt_excerpt, for a conversational agent. "
                "Its prompt usually exists and often lives away from the main agent file — "
                "search the whole source for a long instructions string before deciding there "
                "is none.",
            ),
            (
                "data",
                bool(payload.get("tools"))
                and not payload.get("data_schema")
                and not payload.get("base_environment"),
                "no data_schema and no base_environment, for an agent that has tools. The world "
                "every test runs against is built from exactly these two, so without them the "
                "next stage has no schema to create and no rows to seed, and every tool call it "
                "makes will refuse. Record the shape of each kind of record the tools read or "
                "write, and enough real rows to reach every branch those tools have — a "
                "representative sample for a large dataset, the whole thing for a small one.",
            ),
        ]
        # All of them together, and each only once. Nudging in sequence would cost a turn per
        # nudge and read as though the requirements were being invented one at a time.
        say = [said for key, when, said in thin if when and key not in nudged]
        nudged.update(key for key, when, _ in thin if when)
        if say:
            return _problems(
                say + ["If any of these genuinely does not apply, submit again as is."]
            )
        return accept_contract(payload, destination)

    return create_sdk_mcp_server(
        name=CONTRACT_SERVER, version="0.1.0", tools=[submit_contract]
    )


_JSON_TYPES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """A tool's inputs, described well enough to be filled in correctly the first time.

    Two things this exists for.

    **Required means required.** Handing the decorator a plain ``{name: type}`` mapping marks
    every parameter mandatory, so a tool with an optional field refuses any call that leaves it
    out — "Input validation error: 'seed' is a required property" — for a field the tool itself
    treats as optional.

    **A schema is documentation, not just validation.** It is shown to the model before it calls
    anything, so a property carrying only ``{"type": "array"}`` says nothing about what belongs
    in it, and the model discovers the shape by being rejected. That is a full turn per guess and
    it is avoidable: pass a full JSON-schema fragment instead of a bare type wherever the shape
    is not obvious from the name, and it is right on the first call.

        schema({"name": str,
                "size": {"type": "string", "enum": ["S", "M", "L"]}}, ["name"])
    """
    wanted = list(required)
    return {
        "type": "object",
        "properties": {
            name: dict(kind)
            if isinstance(kind, dict)
            else _typed(_JSON_TYPES.get(kind, "string"), optional=name not in wanted)
            for name, kind in properties.items()
        },
        "required": wanted,
    }


def _typed(kind: str, *, optional: bool) -> dict[str, Any]:
    """One property's type, letting an optional field be null.

    Filling a field that does not apply with null is what a model does, and it is not wrong: the
    alternative is inventing a value. Rejecting it costs a whole turn, and the rejection does not
    even say which field was at fault: "None is not of type 'string'" is the entire message.
    """
    return {"type": [kind, "null"]} if optional else {"type": kind}


def qualified(server: str, tool_name: str) -> str:
    """The name an in-process MCP tool is granted under."""
    return f"mcp__{server}__{tool_name}"


def brief(value: Any, limit: int = 1800) -> str:
    """What a call returned, shortened only when it has to be.

    Generous, and explicit when it cuts. A record from a real agent's data is long, and a reply
    trimmed silently in the middle of it reads as though the field being looked for is absent:
    the answer is then six more calls working around something that was there all along.

    Shared, because every stage that shows a caller what a tool answered has the same problem and
    they were not agreeing about it: one showed 1800 characters and said when it cut, the other
    showed 200 and said nothing, so the stage that most needs to read a record was the one that
    could not.
    """
    rendered = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(rendered) <= limit:
        return rendered
    return (
        rendered[:limit]
        + f"\n... cut here, {len(rendered) - limit} more characters. Ask for one record rather "
        "than many if you need the whole of it."
    )
