from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from fi.alk import simulate


def main() -> int:
    parser = argparse.ArgumentParser(description="Run chat simulation acceptance")
    parser.add_argument("--output", default="artifacts/simulation-acceptance/chat.json")
    parser.add_argument("--failure-isolation-probe", action="store_true")
    args = parser.parse_args()

    endpoint = os.environ.get("CHAT_TARGET_URL", "").strip()
    if not endpoint:
        print(json.dumps({"status": "missing_setup", "missing_env": ["CHAT_TARGET_URL"]}, indent=2))
        return 2
    protocol = os.environ.get("CHAT_TARGET_PROTOCOL", "openai_chat").strip()
    model = os.environ.get("CHAT_TARGET_MODEL", "agent-learning-target").strip()
    wrapper = simulate.HTTPAgentWrapper(
        endpoint=endpoint,
        protocol=protocol,
        model=model,
        api_key_env="CHAT_TARGET_API_KEY",
    )
    names = ["healthy-a", "crash", "healthy-b"] if args.failure_isolation_probe else ["customer-a", "customer-b"]
    scenario = simulate.Scenario(
        name="chat-acceptance",
        dataset=[
            simulate.Persona(
                persona={"name": name, "role": "customer"},
                situation="My delivery is late and I need its current status.",
                outcome="The delivery status and next action are confirmed.",
            )
            for name in names
        ],
    )

    async def target(agent_input):
        if args.failure_isolation_probe and agent_input.persona.get("name") == "crash":
            raise RuntimeError("intentional acceptance probe failure")
        return await wrapper.call(agent_input)

    report = asyncio.run(
        simulate.LocalTextEngine().run(
            scenario=scenario,
            agent_callback=target,
            max_turns=4,
            min_turns=2,
        )
    )
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    statuses = [
        str(result.metadata.get("status") or "completed")
        for result in report.results
    ]
    print(
        json.dumps(
            {
                "status": "passed" if statuses.count("failed") <= int(args.failure_isolation_probe) else "failed",
                "case_statuses": statuses,
                "report": str(output),
            },
            indent=2,
        )
    )
    if args.failure_isolation_probe:
        return 0 if statuses == ["completed", "failed", "completed"] else 1
    return 0 if all(status == "completed" for status in statuses) else 1


if __name__ == "__main__":
    raise SystemExit(main())
