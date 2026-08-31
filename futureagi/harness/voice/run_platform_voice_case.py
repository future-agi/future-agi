"""Run a voice acceptance case AND submit its report to the Future AGI platform.

Wraps ``run_voice_case`` machinery: builds inputs, executes the simulation,
converts the legacy ``TestReport`` into a ``SimulationReport``, then hands
it to ``FutureAGIResultSink`` which POSTs to the ALK ingestion endpoints.

Env prerequisites (in addition to whatever the case itself needs):

  FI_BASE_URL        e.g. http://localhost:8000
  FI_API_KEY         org API key
  FI_SECRET_KEY      org secret key
  FI_RUN_TEST_ID     target RunTest on the platform to receive results

Usage:
  uv run --extra livekit python oss/simulation-acceptance/run_platform_voice_case.py 2.1.1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from voice_cases import CASES, build_inputs, missing_env

from fi.alk import simulate
from fi.simulate.artifacts import ArtifactManifest
from fi.simulate.results import FutureAGIResultSink
from fi.simulate.runtime import (
    SimulationReport,
    SimulationSpec,
    new_run_id,
)
from fi.simulate.runtime.run import RunStatus
from fi.simulate.runtime.spec import (
    AgentEndpointSpec,
    EnvironmentSpec,
    SimulatorPolicySpec,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one voice acceptance case and submit to platform"
    )
    parser.add_argument("case_id", choices=sorted(CASES))
    parser.add_argument("--output-root", default="artifacts/simulation-acceptance")
    args = parser.parse_args()

    for key in ("FI_BASE_URL", "FI_API_KEY", "FI_SECRET_KEY", "FI_RUN_TEST_ID"):
        if not os.environ.get(key, "").strip():
            print(
                json.dumps(
                    {
                        "case_id": args.case_id,
                        "status": "missing_platform_env",
                        "missing": key,
                    },
                    indent=2,
                )
            )
            return 2

    case = CASES[args.case_id]
    missing = missing_env(case)
    if missing:
        print(
            json.dumps(
                {
                    "case_id": case.case_id,
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
    output_dir = (
        Path(args.output_root).expanduser().resolve() / run_id / case.case_id
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(timezone.utc)
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
            min_turn_messages=6,
            max_seconds=inputs.max_seconds,
            connect_timeout=60,
            readiness_timeout=120,
            cleanup_timeout=30,
            conversation_direction=inputs.conversation_direction,
            agent_first_silence_timeout_seconds=30,
        )
    )
    ended_at = datetime.now(timezone.utc)

    legacy_report_path = output_dir / "report.json"
    legacy_report_path.write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )

    sim_spec = _build_spec(case_id=case.case_id, run_id=run_id, scenario=inputs.scenario)
    sim_report = SimulationReport.from_legacy(
        report,
        run_id=run_id,
        spec_hash=sim_spec.spec_hash,
        status=RunStatus.COMPLETED,
        started_at=started_at,
        ended_at=ended_at,
        artifacts=ArtifactManifest(run_id=run_id),
    )

    sink = FutureAGIResultSink(root=args.output_root)
    sink.prepare(sim_spec)
    sink.write_report(sim_report)  # writes local + submits via HTTP

    submission_path = sink.run_directory / "submission.json"
    submission = json.loads(submission_path.read_text(encoding="utf-8"))

    print(
        json.dumps(
            {
                "case_id": case.case_id,
                "run_id": run_id,
                "output_dir": str(output_dir),
                "submission_path": str(submission_path),
                "submission_status": submission.get("status"),
                "submission_reason": submission.get("reason"),
                "test_execution_id": submission.get("test_execution_id"),
                "submitted_call_executions": submission.get(
                    "submitted_call_executions"
                ),
                "failed_call_executions": submission.get("failed_call_executions"),
            },
            indent=2,
        )
    )

    if submission.get("status") != "submitted":
        return 1
    if submission.get("failed_call_executions"):
        return 1
    return 0


def _build_spec(*, case_id: str, run_id: str, scenario) -> SimulationSpec:
    return SimulationSpec(
        run_id=run_id,
        environment=EnvironmentSpec(
            adapter="livekit",
            world_kind="voice",
            config={"case_id": case_id},
        ),
        target=AgentEndpointSpec(adapter="callable"),
        simulator=SimulatorPolicySpec(adapter="synthetic_user"),
        scenario=scenario,
        metadata={"case_id": case_id, "source": "run_platform_voice_case"},
    )


if __name__ == "__main__":
    sys.exit(main())
