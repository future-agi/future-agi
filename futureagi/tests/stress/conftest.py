"""Fixtures for the eval-read stress suite (design doc
internal-docs/eval-task-redesign/DATA-READ-OPTIMIZATION-DESIGN.md).

CH data comes only from the loadgen binary (``$LOADGEN_BIN``, built from
fi-collector/cmd/loadgen); PG control-plane rows come only from
``eval_task_factory``. The suite skips with a visible reason when
``LOADGEN_BIN`` is unset.

Database: loadgen's chwriter pins ``?database=default``, so the session
fixture hard-resets the test CH's ``default`` database, applies the full v2
schema sequence there (the same ``apply_schema`` mechanism the root conftest
uses for ``test_tfc`` — 020 removes the 90-day spans TTL that would drop the
back-dated loadgen rows, 015/017/018 create the curated tables loadgen's
best-effort writes need), and repoints ``settings.CLICKHOUSE_V2`` at
``default`` for the session.

Dataset scale: base sizes follow the task brief (target 10k traces × 8,
noise 30k × 8 mixed); ``STRESS_SCALE`` (default 0.1) scales trace counts so a
default run seeds ≈100k spans — the design doc's CI-tier size. Scale 1.0
reproduces the brief-verbatim sizes (needs several GB of free disk: the mixed
noise project carries ~10% voice traces with 1.2 MiB transcripts).

Seed reuse: set ``STRESS_REUSE_SEED=1`` to skip the drop + re-ingest + OPTIMIZE
when the CH ``default`` DB already holds this scale's seeded data intact (fast
local re-runs, no colima disk growth). Unset (the CI default) always rebuilds.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import clickhouse_connect
import pytest
from django.conf import settings

from tests.stress import ch_asserts

# Re-exported engine/cost stubs (same objects tracer/tests uses) so drain
# scenarios run `run_entry` end-to-end without live LLM or billing calls.
from tracer.tests.conftest import stub_cost_log, stub_run_eval  # noqa: F401

_SCALE = float(os.environ.get("STRESS_SCALE", "0.1"))

STRESS_ORG_ID = "5712e5ac-0000-4000-8000-000000000000"
TARGET_PROJECT_ID = "5712e5ac-0000-4000-8000-000000000001"
NOISE_PROJECT_ID = "5712e5ac-0000-4000-8000-000000000002"
AGENT_DEEP_PROJECT_ID = "5712e5ac-0000-4000-8000-000000000003"
VOICE_PROJECT_ID = "5712e5ac-0000-4000-8000-000000000004"

# Covers loadgen's fixed reproducibility anchor (--end 2026-01-01, 30d back).
# Historical tasks must carry this window: the UI builders' default is
# now-30d, which excludes the back-dated seed rows.
SEED_WINDOW = ("2025-11-01T00:00:00Z", "2026-02-01T00:00:00Z")

_CURATED_TABLES = ("spans", "traces", "trace_sessions", "end_users")


@dataclass(frozen=True)
class Manifest:
    project_id: str
    trace_ids: list
    root_span_id_by_trace: dict
    span_count: int
    session_ids: list
    observation_type_counts: dict

    @classmethod
    def from_file(cls, path: Path) -> Manifest:
        data = json.loads(path.read_text())
        return cls(
            project_id=data["project_id"],
            trace_ids=data["trace_ids"],
            root_span_id_by_trace=data["root_span_id_by_trace"],
            span_count=data["span_count"],
            session_ids=data["session_ids"],
            observation_type_counts=data["observation_type_counts"],
        )


@dataclass(frozen=True)
class StressDataset:
    target: Manifest
    noise: Manifest
    agent_deep: Manifest
    voice: Manifest


# Opt-in reuse: with STRESS_REUSE_SEED set, if the CH `default` DB already holds
# the expected per-project span counts (from a prior run's cached manifests at
# this scale) the fixture skips the DROP + loadgen re-ingest + OPTIMIZE and reads
# the cached manifests instead. Cheap re-runs, no colima disk growth. CI leaves
# it unset and always seeds fresh.
_SEED_CACHE_DIR = Path.home() / ".cache" / "fi-stress-seed"
_DATASET_NAMES = ("target", "noise", "agent_deep", "voice")


def _reuse_enabled() -> bool:
    return os.environ.get("STRESS_REUSE_SEED", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _cache_dir() -> Path:
    return _SEED_CACHE_DIR / f"scale-{_SCALE}"


def _load_cached_manifests() -> dict | None:
    d = _cache_dir()
    if not all((d / f"{name}.json").exists() for name in _DATASET_NAMES):
        return None
    try:
        return {name: Manifest.from_file(d / f"{name}.json") for name in _DATASET_NAMES}
    except Exception:
        return None


def _save_cached_manifests(mans: dict) -> None:
    d = _cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    for name, m in mans.items():
        (d / f"{name}.json").write_text(
            json.dumps(
                {
                    "project_id": m.project_id,
                    "trace_ids": m.trace_ids,
                    "root_span_id_by_trace": m.root_span_id_by_trace,
                    "span_count": m.span_count,
                    "session_ids": m.session_ids,
                    "observation_type_counts": m.observation_type_counts,
                }
            )
        )


def _ch_data_matches(host: str, cfg: dict, cached: dict) -> bool:
    """True iff every cached project's live span count matches CH — i.e. the
    seeded data is intact and safe to reuse without re-ingesting."""
    client = clickhouse_connect.get_client(
        host=host,
        port=cfg["http_port"],
        username=cfg["user"],
        password=cfg["password"] or "",
        database="default",
    )
    try:
        for m in cached.values():
            cnt = client.query(
                "SELECT count() FROM spans WHERE project_id = %(p)s AND is_deleted = 0",
                parameters={"p": m.project_id},
            ).result_rows[0][0]
            if int(cnt) != m.span_count:
                return False
        return True
    except Exception:
        return False
    finally:
        client.close()


def _reset_cached_dict_clients() -> None:
    from tracer.services.clickhouse.v2 import (
        end_user_dict_reader,
        trace_session_dict_reader,
    )

    trace_session_dict_reader._reset_client()
    end_user_dict_reader._reset_client()


@pytest.fixture(scope="session")
def _stress_ch(request):
    """Hard-reset + v2-schema the CH ``default`` database and point the v2
    readers at it for the session."""
    # Guard: the DROP DATABASE below is destructive — reject mixed sessions
    # before any CH operation so non-stress tests are never affected.
    stress_root = str(Path(__file__).resolve().parent)
    non_stress = [
        item
        for item in request.session.items
        if not str(item.fspath).startswith(stress_root)
    ]
    if non_stress:
        pytest.exit(
            "stress suite rebuilds the ClickHouse `default` database — run it "
            "standalone: uv run pytest tests/stress/ "
            f"(found non-stress tests in this session: {non_stress[0].nodeid})",
            returncode=4,
        )

    loadgen_bin = os.environ.get("LOADGEN_BIN")
    if not loadgen_bin:
        pytest.skip(
            "LOADGEN_BIN not set — build fi-collector/cmd/loadgen and export "
            "LOADGEN_BIN=<path> to run the stress suite"
        )

    from tracer.services.clickhouse.v2 import apply_schema, get_v2_config

    cfg = get_v2_config()
    host = "localhost" if cfg["host"] == "clickhouse" else cfg["host"]

    # Reuse: skip the destructive rebuild when the seeded data is already present
    # and intact for this scale (see _reuse_enabled / _ch_data_matches).
    cached = _load_cached_manifests() if _reuse_enabled() else None
    reused = bool(cached) and _ch_data_matches(host, cfg, cached)

    if not reused:
        admin = clickhouse_connect.get_client(
            host=host,
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database="system",
        )
        try:
            admin.command("DROP DATABASE IF EXISTS default")
            admin.command("CREATE DATABASE default")
        finally:
            admin.close()

        rc = apply_schema.main(
            [
                "--schema-dir",
                str(
                    Path(__file__).resolve().parents[2]
                    / "tracer/services/clickhouse/v2/schema"
                ),
                "--ch-host",
                host,
                "--ch-http-port",
                str(cfg["http_port"]),
                "--ch-user",
                cfg["user"],
                "--ch-password",
                cfg["password"] or "",
                "--ch-database",
                "default",
            ]
        )
        if rc != 0:
            pytest.fail(f"v2 schema apply to CH database 'default' failed (rc={rc})")

    prior = settings.CLICKHOUSE_V2.get("CH25_DATABASE")
    settings.CLICKHOUSE_V2["CH25_DATABASE"] = "default"
    _reset_cached_dict_clients()
    ch_asserts.capture_baseline()
    try:
        yield {
            "bin": loadgen_bin,
            "ch_url": f"http://{host}:{cfg['http_port']}",
            "reused": reused,
            "manifests": cached if reused else None,
        }
    finally:
        if prior is None:
            settings.CLICKHOUSE_V2.pop("CH25_DATABASE", None)
        else:
            settings.CLICKHOUSE_V2["CH25_DATABASE"] = prior
        _reset_cached_dict_clients()


@pytest.fixture(scope="session")
def loadgen_run(_stress_ch, tmp_path_factory):
    """Run the loadgen binary against the stress CH; returns the parsed
    Manifest. Deterministic for a fixed (seed, sizes, end) tuple."""
    manifest_dir = tmp_path_factory.mktemp("loadgen-manifests")
    seq = itertools.count()

    def _run(
        project_id: str,
        *,
        traces: int,
        shape: str,
        seed: int,
        spans_per_trace: int = 8,
        sessions: int = 4,
        batch_size: int | None = None,
        end: str | None = None,
        time_range: str | None = None,
        trickle: int | None = None,
    ) -> Manifest:
        out = manifest_dir / f"{shape}-{seed}-{next(seq)}.json"
        cmd = [
            _stress_ch["bin"],
            "--ch-url",
            _stress_ch["ch_url"],
            "--project-id",
            project_id,
            "--org-id",
            STRESS_ORG_ID,
            "--traces",
            str(traces),
            "--spans-per-trace",
            str(spans_per_trace),
            "--sessions",
            str(sessions),
            "--shape",
            shape,
            "--seed",
            str(seed),
            "--manifest",
            str(out),
        ]
        if batch_size is not None:
            cmd += ["--batch-size", str(batch_size)]
        if end is not None:
            cmd += ["--end", end]
        if time_range is not None:
            cmd += ["--time-range", time_range]
        if trickle is not None:
            cmd += ["--trickle", str(trickle)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if res.returncode != 0:
            pytest.fail(f"loadgen exited {res.returncode}: {res.stderr[-2000:]}")
        return Manifest.from_file(out)

    return _run


@pytest.fixture(scope="session")
def stress_dataset(loadgen_run, _stress_ch) -> StressDataset:
    """Seed the four read-only stress projects once per session.

    Voice-bearing shapes get a smaller --batch-size: at the default 10k-span
    batch a mixed batch carries ~150 MB of transcript JSON in one POST, which
    exceeds chwriter's 30s request timeout.

    time_range="24h" collapses all seed rows into ≤2 daily partitions so CH
    index granules (8 192 rows) can actually prune on project_id; without it
    rows scatter across ~30 partitions at ~2 300/part — below one granule.
    """
    if _stress_ch.get("reused"):
        m = _stress_ch["manifests"]
        return StressDataset(
            target=m["target"],
            noise=m["noise"],
            agent_deep=m["agent_deep"],
            voice=m["voice"],
        )

    n = lambda base: max(1, int(base * _SCALE))  # noqa: E731
    target = loadgen_run(
        TARGET_PROJECT_ID,
        traces=n(10_000),
        sessions=n(50),
        shape="llm",
        seed=42,
        time_range="24h",
    )
    noise = loadgen_run(
        NOISE_PROJECT_ID,
        traces=n(30_000),
        sessions=n(500),
        shape="mixed",
        seed=43,
        batch_size=1000,
        time_range="24h",
    )
    agent_deep = loadgen_run(
        AGENT_DEEP_PROJECT_ID,
        traces=n(200),
        sessions=max(2, n(20)),
        shape="agent-deep",
        seed=44,
        batch_size=1000,
        time_range="24h",
    )
    voice = loadgen_run(
        VOICE_PROJECT_ID,
        traces=max(4, n(40)),
        sessions=4,
        shape="voice",
        seed=45,
        batch_size=200,
        time_range="24h",
    )

    # Merge all parts so granules are dense before budget tests run.  Without
    # this, freshly-ingested rows sit in many small parts each below one
    # 8 192-row granule, making granule pruning impossible.
    from tracer.services.clickhouse.v2 import get_v2_config

    cfg = get_v2_config()
    host = "localhost" if cfg["host"] == "clickhouse" else cfg["host"]
    admin = clickhouse_connect.get_client(
        host=host,
        port=cfg["http_port"],
        username=cfg["user"],
        password=cfg["password"] or "",
        database="default",
    )
    try:
        admin.command("OPTIMIZE TABLE spans FINAL")
        admin.command("OPTIMIZE TABLE trace_sessions FINAL")
        admin.command("OPTIMIZE TABLE traces FINAL")
        # OPTIMIZE returns while projection-materialize mutations and part
        # merges can still be rewriting parts; a budget read mid-rewrite sees
        # inflated read_rows. Wait for the storage to settle.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            busy = admin.command(
                "SELECT (SELECT count() FROM system.mutations WHERE NOT is_done)"
                " + (SELECT count() FROM system.merges)"
            )
            if int(busy) == 0:
                break
            time.sleep(0.5)
    finally:
        admin.close()

    dataset = StressDataset(
        target=target, noise=noise, agent_deep=agent_deep, voice=voice
    )
    # Persist manifests so a later STRESS_REUSE_SEED run can skip the reseed.
    _save_cached_manifests(
        {
            "target": target,
            "noise": noise,
            "agent_deep": agent_deep,
            "voice": voice,
        }
    )
    return dataset


@pytest.fixture
def eval_task_factory(db, organization, workspace):
    """Persist an EvalTask (+ its CustomEvalConfigs) against a seeded CH
    project. Creates the PG Project row keyed to the seeded project id.

    Historical tasks default to a filter window covering the seed data
    (SEED_WINDOW); pass explicit ``filters`` to override — include a
    ``date_range`` or the builders' now-30d default excludes the seed rows.
    """
    from model_hub.models.ai_model import AIModel
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
    from tracer.models.project import Project

    def _make(
        project_id: str,
        row_type: str,
        *,
        filters: dict | None = None,
        sampling_rate: float = 100.0,
        run_type: str = RunType.HISTORICAL,
        spans_limit: int = 1_000_000,
        n_evals: int = 1,
        custom_eval: bool = False,
    ) -> EvalTask:
        project, _ = Project.objects.get_or_create(
            id=project_id,
            defaults={
                "name": f"stress-{project_id[-4:]}",
                "organization": organization,
                "workspace": workspace,
                "model_type": AIModel.ModelTypes.GENERATIVE_LLM,
                "trace_type": "observe",
                "config": [
                    {"id": "input", "name": "Input", "is_visible": True},
                    {"id": "output", "name": "Output", "is_visible": True},
                ],
            },
        )
        template_config = {"type": "pass_fail", "criteria": "stress"}
        if custom_eval:
            # Flip on the custom-eval branch in _process_trace_mapping /
            # _process_session_mapping: a missing attribute resolves to "" instead
            # of raising, which is what the lean-first heavy-field regression needs.
            template_config["custom_eval"] = True
        template = EvalTemplate.objects.create(
            name=f"stress-eval-{uuid.uuid4().hex[:8]}",
            description="stress",
            organization=organization,
            workspace=workspace,
            config=template_config,
        )
        configs = [
            CustomEvalConfig.objects.create(
                name=f"stress-cfg-{i}-{uuid.uuid4().hex[:6]}",
                project=project,
                eval_template=template,
                config={"threshold": 0.8},
                mapping={"input": "input", "output": "output"},
                filters={},
            )
            for i in range(n_evals)
        ]
        if filters is None:
            filters = (
                {"date_range": list(SEED_WINDOW)}
                if run_type == RunType.HISTORICAL
                else {}
            )
        task = EvalTask.objects.create(
            project=project,
            name=f"stress-task-{uuid.uuid4().hex[:6]}",
            filters=filters,
            sampling_rate=sampling_rate,
            spans_limit=spans_limit,
            run_type=run_type,
            status=EvalTaskStatus.PENDING,
            row_type=row_type,
        )
        task.evals.add(*configs)
        return task

    return _make
