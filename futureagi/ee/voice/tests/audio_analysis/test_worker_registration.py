"""R11/R14: the CPU queue has an explicit concurrency budget in every worker."""

import pytest


def test_audio_worker_configuration(settings):
    from tfc.temporal.common.worker import audio_worker_options

    settings.VOICE_AUDIO_METRICS_WORKER_MAX_CONCURRENT_ACTIVITIES = 2
    assert audio_worker_options("tasks_audio")["max_concurrent_activities"] == 2
    assert audio_worker_options("tasks_l") == {}


def test_embedded_audio_queue_is_capped(monkeypatch, settings):
    from tfc.temporal import embedded
    from tfc.temporal.common import registry

    settings.VOICE_AUDIO_METRICS_WORKER_MAX_CONCURRENT_ACTIVITIES = 2
    monkeypatch.setattr(registry, "get_all_queues", lambda: ["tasks_l", "tasks_audio"])
    monkeypatch.setattr(registry, "get_all_workflows", lambda: [])
    monkeypatch.setattr(registry, "get_all_activities", lambda: [])
    monkeypatch.setattr(registry, "get_workflows_for_queue", lambda _: [])
    monkeypatch.setattr(registry, "get_activities_for_queue", lambda _: [])
    plan = next(p for p in embedded._worker_plans() if p.queue == "tasks_audio")
    assert plan.max_activities == 2


def test_orphan_sweeper_preserves_unrelated_recent_and_symlinked_files(
    tmp_path, settings
):
    import os
    import time

    from ee.voice.services.audio_worker import sweep_audio_temp_files

    settings.VOICE_AUDIO_TMP_DIR = str(tmp_path)
    old = tmp_path / "audio-analysis-old"
    old.mkdir()
    (old / "decoded").write_bytes(b"fixture")
    recent = tmp_path / "audio-analysis-recent"
    recent.write_bytes(b"recent")
    unrelated = tmp_path / "keep"
    unrelated.write_bytes(b"keep")
    symlink = tmp_path / "audio-analysis-link"
    symlink.symlink_to(unrelated)
    os.utime(old, (time.time() - 90000,) * 2)
    sweep_audio_temp_files()
    assert not old.exists()
    assert recent.exists() and unrelated.exists() and symlink.is_symlink()


@pytest.mark.asyncio
async def test_audio_workflow_sandbox_prepares_without_django_or_dsp_imports():
    from temporalio import workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from ee.voice.temporal.workflows.audio_analysis_workflow import (
        AudioAnalysisWorkflow,
    )

    SandboxedWorkflowRunner().prepare_workflow(
        workflow._Definition.must_from_class(AudioAnalysisWorkflow)
    )
