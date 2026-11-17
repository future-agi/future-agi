"""Cooperative run control: drain writes, retain history, resume in place."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


class ControlledRun:
    def __init__(self):
        self._control_enabled = False
        self._control_stopped = False
        self._control_drained = False
        self._control_finished = False
        self._control_identity = {}

    @workflow.signal(name="set_run_stopped")
    def set_run_stopped(self, stopped: bool):
        if self._control_enabled and not self._control_finished:
            self._control_stopped = stopped
            if not stopped:
                self._control_drained = False

    @workflow.query(name="run_control")
    def run_control(self) -> dict:
        if not self._control_enabled:
            status = "UNAVAILABLE"
        elif self._control_finished:
            status = "COMPLETED"
        elif self._control_stopped:
            status = "STOPPED" if self._control_drained else "STOPPING"
        else:
            status = "RUNNING"
        return {**self._control_identity, "status": status}

    async def bind_control(self, experiment_id: str, config_id: str, kind: str):
        # Old histories continue without introducing non-deterministic commands.
        if not workflow.patched("cooperative-run-control-v1"):
            return
        self._control_enabled = True
        info = workflow.info()
        self._control_identity = {
            "experiment_id": experiment_id,
            "config_id": config_id,
            "kind": kind,
            "workflow_id": info.workflow_id,
            "workflow_run_id": info.run_id,
        }
        await workflow.execute_activity(
            "bind_experiment_run_activity",
            self._control_identity,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

    async def control_checkpoint(self, pending=()):
        if not self._control_stopped:
            return
        # Never report STOPPED while a prompt, graph sink or eval can still write.
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        while self._control_stopped:
            await self.persist_control("STOPPED")
            if self._control_stopped:
                self._control_drained = True
                await workflow.wait_condition(lambda: not self._control_stopped)
            self._control_drained = False
            await self.persist_control("RUNNING")

    async def persist_control(self, phase):
        if self._control_enabled:
            await workflow.execute_activity(
                "persist_experiment_run_state_activity",
                {**self._control_identity, "phase": phase},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

    async def complete_control(self):
        await self.control_checkpoint()
        self._control_finished = True
        await self.persist_control("COMPLETED")
