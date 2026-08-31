"""Run a stage from a terminal.

This is one renderer over the stage loop, not the product. It prints events as lines and reads
follow-ups from stdin; a browser front end subscribes to the same events and draws them as a
transcript beside the artifact. Keeping the terminal a renderer rather than the interface is what
makes the second one cheap.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from .build import open_stage as build_stage
from .build import opening as build_opening
from .chat import open_conversation
from .config import (
    DEFAULT_MODEL,
    artifact_dir,
    chosen_model,
    credentials_hint,
    permission_gate,
)
from .scenarios import load as load_written
from .scenarios import open_stage as scenario_stage
from .scenarios import opening as scenario_opening
from .session import TEXT, Event
from .world.snapshot import saved as world_saved
from .run.targets import supported as target_kinds
from .sources import resolve, supported
from .understand import load, open_stage, opening


def _render(event: Event) -> None:
    line = event.line()
    if event.kind == TEXT:
        print(line, end="", flush=True)
    else:
        print(f"\n{line}", flush=True)


async def _prompt(question: str) -> str:
    return (await asyncio.to_thread(input, question)).strip()


async def _ask_operator(_tool_name: str, payload: dict[str, Any], _context: Any) -> Any:
    """Render the model's clarifying questions and return the operator's answers."""
    from claude_agent_sdk.types import PermissionResultAllow

    answers: dict[str, Any] = {}
    for question in payload.get("questions", []):
        print(f"\n\n  {question.get('header', '?')}: {question.get('question', '')}")
        options = question.get("options", []) or []
        for index, option in enumerate(options, start=1):
            print(
                f"    {index}. {option.get('label')} - {option.get('description', '')}"
            )
        raw = await _prompt("  > ")
        chosen = raw
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            chosen = options[int(raw) - 1].get("label", raw)
        answers[question.get("question", "")] = chosen
    print()
    return PermissionResultAllow(
        updated_input={"questions": payload.get("questions", []), "answers": answers}
    )


async def _understand(args: argparse.Namespace) -> int:
    source = resolve(args.kind, name=args.name, root=args.path)
    stage, destination = open_stage(
        source,
        out=Path(args.out) if args.out else None,
        # Unattended, there is nobody to answer, so the model records what it could not
        # resolve in open_questions rather than blocking on a prompt nobody will see.
        ask=permission_gate(_ask_operator) if args.interactive else None,
    )

    print(f"agent: {source.name}  ({source.kind})")
    print(f"model: {chosen_model()}")
    print(f"out:   {destination}\n")

    await _converse(
        stage,
        opening(source),
        interactive=args.interactive,
        until=lambda: load(destination) is not None,
        nudge=(
            "Nothing was saved: you finished without calling submit_contract. Call it now "
            "with the contract you worked out."
        ),
    )

    contract = load(destination)
    if contract is None:
        print("\nNo contract was submitted.", file=sys.stderr)
        return 1
    print(
        f"\ncontract: {len(contract.tools)} tools, "
        f"{len(contract.hard_constraints)} rules, "
        f"{len(contract.real_use_cases)} use cases, "
        f"{len(contract.open_questions)} open questions"
    )
    print(f"spent:    ${stage.spent_usd:.4f}")
    return 0


async def _converse(
    stage,
    opening_message: str,
    *,
    interactive: bool,
    until=None,
    nudge: str = "",
) -> None:
    """Say the opening, then keep the stage open for corrections.

    The same shape for every stage. A world is usually right on the second look, and the point
    of holding the session open is that correcting it is the next thing said rather than a
    rebuild from nothing.

    ``until``/``nudge`` guard the unattended case. The commonest way an unattended stage fails
    is finishing all the work and never calling the tool that saves it — the whole contract
    written out as prose, submitted to nobody. One mechanical reminder costs a turn; rerunning
    the stage costs everything it just did.
    """
    async with stage:
        await stage.say(opening_message, on_event=_render)
        if not interactive and until is not None and nudge and not until():
            await stage.say(nudge, on_event=_render)
        while interactive:
            try:
                said = await _prompt("\nyou  ")
            except (EOFError, KeyboardInterrupt):
                break
            if not said or said in {"q", "quit", "exit"}:
                break
            await stage.say(said, on_event=_render)


async def _build(args: argparse.Namespace) -> int:
    destination = Path(args.out) if args.out else artifact_dir(args.name)
    contract = load(destination)
    if contract is None:
        print(f"No contract at {destination}. Run `understand` first.", file=sys.stderr)
        return 1

    print(f"agent: {contract.agent}  ({len(contract.tools)} tools)")
    print(f"model: {chosen_model()}")
    print(f"out:   {destination}\n")

    stage, _ = build_stage(
        contract,
        out=destination,
        ask=permission_gate(_ask_operator) if args.interactive else None,
    )
    await _converse(
        stage,
        build_opening(contract),
        interactive=args.interactive,
        until=lambda: world_saved(destination),
        nudge=(
            "Nothing was saved: you finished without calling save_world. Call check_world, "
            "fix what it names, then save_world."
        ),
    )

    if not world_saved(destination):
        print("\nNo world was saved.", file=sys.stderr)
        return 1
    print(f"\nworld: {destination}")
    print(f"spent: ${stage.spent_usd:.4f}")
    return 0


async def _scenarios(args: argparse.Namespace) -> int:
    destination = Path(args.out) if args.out else artifact_dir(args.name)
    contract = load(destination)
    if contract is None:
        print(f"No contract at {destination}. Run `understand` first.", file=sys.stderr)
        return 1
    if not world_saved(destination):
        print(f"No world at {destination}. Run `build` first.", file=sys.stderr)
        return 1

    # With a suite already written, the target is what is there. Somebody who comes back to
    # change one scenario is not asking for a different number of them.
    existing = len(load_written(destination))
    wanted = args.count or existing or 10

    print(
        f"agent: {contract.agent}  "
        + (f"({existing} scenarios, loaded)" if existing else f"(writing {wanted})")
    )
    print(f"model: {chosen_model()}")
    print(f"out:   {destination}\n")

    stage, _ = scenario_stage(
        contract,
        out=destination,
        wanted=wanted,
        ask=permission_gate(_ask_operator) if args.interactive else None,
    )
    await _converse(
        stage,
        scenario_opening(contract, wanted, existing),
        interactive=args.interactive,
        until=lambda: bool(load_written(destination)),
        nudge=(
            "Nothing was saved: you finished without calling save_scenarios. Submit anything "
            "still unsubmitted, then call save_scenarios."
        ),
    )

    written = load_written(destination)
    if not written:
        print("\nNo scenarios were saved.", file=sys.stderr)
        return 1
    print(f"\nscenarios: {len(written)} in {destination / 'scenarios.json'}")
    print(f"spent:     ${stage.spent_usd:.4f}")
    return 0


async def _live(args: argparse.Namespace) -> int:
    """The run stage as a conversation: it decides what to run and reads what came back."""
    from .run.stage import load as load_results
    from .run.stage import open_stage as run_stage
    from .run.stage import opening as run_opening

    destination = Path(args.out) if args.out else artifact_dir(args.name)
    contract = load(destination)
    written = load_written(destination)
    if contract is None or not written:
        print(
            f"Need a contract and scenarios at {destination}. Run `understand`, `build` and "
            "`scenarios` first.",
            file=sys.stderr,
        )
        return 1

    print(f"agent: {contract.agent}  ({len(written)} scenarios)")
    print(f"model: {chosen_model()}")
    print(f"out:   {destination}\n")

    stage, _ = run_stage(
        contract,
        out=destination,
        ask=permission_gate(_ask_operator) if args.interactive else None,
    )
    await _converse(
        stage, run_opening(contract, destination), interactive=args.interactive
    )

    results = load_results(destination)
    passed = sum(1 for record in results if record["passed"])
    print(f"\nruns:  {passed} of {len(results)} passed, in {destination / 'runs.json'}")
    print(f"spent: ${stage.spent_usd:.4f}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    from .run import run_suite
    from .run.grade import summarise

    destination = Path(args.out) if args.out else artifact_dir(args.name)
    contract = load(destination)
    written = load_written(destination)
    if contract is None or not written:
        print(
            f"Need a contract and scenarios at {destination}. Run `understand`, `build` "
            "and `scenarios` first.",
            file=sys.stderr,
        )
        return 1

    chosen = [s for s in written if s.name in args.only] if args.only else written
    if not chosen:
        print(f"No scenario matching {args.only}.", file=sys.stderr)
        return 1

    print(f"agent: {contract.agent}  ({len(chosen)} scenarios, target {args.target})")
    print(f"model: {chosen_model()}")
    print(f"out:   {destination}\n")

    def overheard(exchange: Any) -> None:
        if args.quiet:
            return
        print(f"    {exchange.speaker:8} {exchange.text}", flush=True)

    def show(result: Any) -> None:
        # Just the verdict as it lands. The detail is in the summary at the end, and printing
        # it in both places means every failure is read twice.
        print(result.line(), flush=True)

    results = await run_suite(
        chosen,
        contract,
        destination,
        target=args.target,
        model=args.model,
        on_result=show,
        on_exchange=overheard,
    )
    print("\n" + summarise(results))
    print(f"\nspent: ${sum(result.spent_usd for result in results):.4f}")
    return 0 if all(result.passed for result in results) else 2


async def _chat(args: argparse.Namespace) -> int:
    """One conversation for the whole thing: point at an agent and keep talking."""
    conversation = open_conversation(
        name=args.name or "",
        path=args.path or "",
        kind=args.kind,
        out=Path(args.out) if args.out else None,
        ask=permission_gate(_ask_operator),
    )
    print(f"model:       {chosen_model()}")
    print(credentials_hint())
    print("\nSay what you want. Enter on its own moves to the next stage; 'q' ends.\n")

    await conversation.start(on_event=_render)
    while True:
        try:
            said = await _prompt(f"\nyou ({conversation.stage_name})  ")
        except (EOFError, KeyboardInterrupt):
            break
        if said in {"q", "quit", "exit"}:
            break
        if not said:
            entered = await conversation.advance(on_event=_render)
            if entered is None:
                print(
                    "\n  [nothing to move on to yet; this stage has not produced its artifact]"
                )
            continue
        await conversation.say(said, on_event=_render)
    await conversation.close()
    print(f"\nspent: ${conversation.spent_usd:.4f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-harness", description=__doc__)
    # Talking to it is the way in, so that is what happens when you just start it.
    sub = parser.add_subparsers(dest="stage", required=False)

    understand = sub.add_parser(
        "understand", help="read an agent and produce its contract"
    )
    understand.add_argument("--name", required=True, help="what to call this agent")
    understand.add_argument("--path", required=True, help="where the agent is")
    understand.add_argument(
        "--kind", default="repo", choices=supported(), help="how the agent is supplied"
    )
    understand.add_argument("--out", default=None, help="artifact directory")
    understand.add_argument(
        "--once",
        dest="interactive",
        action="store_false",
        help="run unattended instead of staying open for corrections",
    )
    understand.add_argument("--model", default=DEFAULT_MODEL, help=argparse.SUPPRESS)
    understand.set_defaults(run=_understand, interactive=True)

    world = sub.add_parser("build", help="build the world from an agent's contract")
    world.add_argument("--name", required=True, help="which agent")
    world.add_argument("--out", default=None, help="artifact directory")
    world.add_argument(
        "--once",
        dest="interactive",
        action="store_false",
        help="run unattended instead of staying open for corrections",
    )
    world.set_defaults(run=_build, interactive=True)

    scenarios = sub.add_parser(
        "scenarios", help="write the scenarios to test the agent with"
    )
    scenarios.add_argument("--name", required=True, help="which agent")
    scenarios.add_argument("--out", default=None, help="artifact directory")
    scenarios.add_argument(
        "--count",
        type=int,
        default=None,
        help="how many scenarios to write (defaults to however many already exist)",
    )
    scenarios.add_argument(
        "--once",
        dest="interactive",
        action="store_false",
        help="run unattended instead of staying open for corrections",
    )
    scenarios.set_defaults(run=_scenarios, interactive=True)

    live = sub.add_parser(
        "live", help="run the scenarios against the real agent, as a conversation"
    )
    live.add_argument("--name", required=True, help="which agent")
    live.add_argument("--out", default=None, help="artifact directory")
    live.add_argument(
        "--once",
        dest="interactive",
        action="store_false",
        help="run unattended instead of staying open",
    )
    live.set_defaults(run=_live, interactive=True)

    runs = sub.add_parser("run", help="run the scenarios and grade what happened")
    runs.add_argument("--name", required=True, help="which agent")
    runs.add_argument("--out", default=None, help="artifact directory")
    runs.add_argument(
        "--target",
        default="local",
        choices=target_kinds(),
        help="where the agent under test runs",
    )
    runs.add_argument(
        "--only", nargs="*", default=None, help="run only these scenarios, by name"
    )
    runs.add_argument("--model", default=None, help="model for the run")
    runs.add_argument(
        "--quiet",
        action="store_true",
        help="only the verdicts, without the conversations as they happen",
    )
    runs.set_defaults(run=_run)

    chat = sub.add_parser(
        "chat",
        help="one conversation: understand, build the world, write the scenarios",
    )
    # Nothing is required. Which agent, where it lives and how many scenarios are all things
    # you say; naming one here is a shortcut back into work already in progress.
    chat.add_argument("--name", default=None, help=argparse.SUPPRESS)
    chat.add_argument("--path", default=None, help=argparse.SUPPRESS)
    chat.add_argument(
        "--kind", default="repo", choices=supported(), help=argparse.SUPPRESS
    )
    chat.add_argument("--out", default=None, help=argparse.SUPPRESS)
    chat.set_defaults(run=_chat)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "run", None) is None:
        args = parser.parse_args([*(argv or []), "chat"])
    return asyncio.run(args.run(args))


if __name__ == "__main__":
    raise SystemExit(main())
