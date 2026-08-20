from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from fi.alk import simulate
from fi.simulate.evaluation import evaluate_agent_report
from fi.simulate.runtime import new_run_id
from voice_cases import CASES, build_inputs, missing_env

LIVE_EVENT = "HARNESS_EXCHANGE "


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one voice acceptance matrix cell")
    parser.add_argument("case_id", choices=sorted(CASES))
    parser.add_argument("--output-root", default="artifacts/simulation-acceptance")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    case = CASES[args.case_id]
    missing = missing_env(case)
    if missing:
        print(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "description": case.description,
                    "status": "missing_setup",
                    "missing_env": missing,
                    "setup": case.setup,
                },
                indent=2,
            )
        )
        return 2

    run_id = new_run_id()
    inputs = build_inputs(case.case_id, run_id)
    min_turn_messages = int(os.environ.get("VOICE_MIN_TURN_MESSAGES", "6"))
    agent_first_silence = float(
        os.environ.get("VOICE_AGENT_FIRST_SILENCE_SECONDS", "30")
    )
    output_dir = Path(args.output_root).expanduser().resolve() / run_id / case.case_id
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = simulate.build_voice_run_manifest(
        name=f"acceptance-{case.case_id}",
        agent_definition=inputs.agent_definition,
        livekit_runtime=inputs.livekit_runtime,
        scenario=inputs.scenario,
        simulator=inputs.simulator,
        required_env=case.required_env,
        simulation_run_id=run_id,
        record_audio=True,
        recording_root=output_dir / "recordings",
        recording_case_directory=output_dir / "recordings",
        min_turn_messages=min_turn_messages,
        max_seconds=inputs.max_seconds,
        connect_timeout=60,
        readiness_timeout=120,
        cleanup_timeout=30,
        conversation_direction=inputs.conversation_direction,
        agent_first_silence_timeout_seconds=agent_first_silence,
    )
    manifest_path = simulate.write_manifest_file(
        manifest,
        output_dir / "manifest.json",
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "description": case.description,
                    "known_status": case.status,
                    "status": "dry_run_passed",
                    "manifest": str(manifest_path),
                    "setup": case.setup,
                },
                indent=2,
            )
        )
        return 0

    trigger = _start_livekit_outbound_trigger(case.case_id)
    async def on_exchange(_index: int, turn: dict) -> None:
        print(LIVE_EVENT + json.dumps(turn), flush=True)

    try:
        report = asyncio.run(
            simulate.run_voice_simulation(
                agent_definition=inputs.agent_definition,
                livekit_runtime=inputs.livekit_runtime,
                scenario=inputs.scenario,
                simulator=inputs.simulator,
                simulation_run_id=run_id,
                record_audio=True,
                recording_root=output_dir / "recordings",
                recording_case_directory=output_dir / "recordings",
                min_turn_messages=min_turn_messages,
                max_seconds=inputs.max_seconds,
                connect_timeout=60,
                readiness_timeout=120,
                cleanup_timeout=30,
                conversation_direction=inputs.conversation_direction,
                agent_first_silence_timeout_seconds=agent_first_silence,
                on_exchange=on_exchange,
            )
        )
        evaluation = evaluate_agent_report(report, attach=True)
    finally:
        _finish_livekit_outbound_trigger(trigger)
    report_path = output_dir / "report.json"
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    result = report.results[0]
    status = str(result.metadata.get("status") or "unknown")
    print(
        json.dumps(
            {
                "case_id": case.case_id,
                "description": case.description,
                "known_status": case.status,
                "status": status,
                "failure": result.metadata.get("failure"),
                "evaluation_passed": evaluation.passed,
                "evaluation_score": evaluation.score,
                "manifest": str(manifest_path),
                "report": str(report_path),
            },
            indent=2,
        )
    )
    return _result_exit_code(
        status=status,
        evaluation_passed=evaluation.passed,
    )


def _result_exit_code(*, status: str, evaluation_passed: bool) -> int:
    return 0 if status == "completed" and evaluation_passed else 1


def _start_livekit_outbound_trigger(case_id: str) -> subprocess.Popen | None:
    if case_id != "1.2.1":
        return None
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("trigger_livekit_outbound.py"))]
    )


def _finish_livekit_outbound_trigger(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
