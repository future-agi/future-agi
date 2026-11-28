import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { resolveBackendContainer, runSeedScript, seedSuffix } from './simulate-seed';

/**
 * Seeds one agent with two environment builds, for an agent's Environments tab: one
 * finished and linked to its test, and one that failed before its scenarios were
 * submitted, so it was never linked to a test (`run_test` is NULL) and holds its agent
 * only through the version it was built from (`agent_version`). See `OWNER` in
 * `futureagi/simulate/selectors/agent_environments.py`.
 *
 * A failed build needs the simulation runner and a sandbox to fail in; this stack
 * starts neither. So, following `seedCallExecutions` in `lib/simulate-seed.ts`, the
 * rows are written through the Django ORM inside the selected stack's backend
 * container. Job fields follow `STOP_JOURNEY_SEED_SCRIPT` there. Every row is in the
 * caller's own organization and workspace, so parallel workers never share them.
 */
export interface AgentBuildsSeed {
  agentDefinitionId: string;
  agentVersionId: string;
  runTestId: string;
  /** Finished and linked to `runTestId`. */
  linkedBuildId: string;
  /** Failed, with no test linked. */
  failedBuildId: string;
}

const SEED_SCRIPT = `
import json
import os
import uuid
from datetime import timedelta
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()
from django.utils import timezone

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import StatusType
from simulate.models import AgentDefinition, AgentVersion, HostedHarnessJob, RunTest, Scenarios


def job(organization, workspace, version, run_test, name, state, **fields):
    created = HostedHarnessJob.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        run_test=run_test,
        agent_version=version,
        name=name,
        run_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4().hex,
        request_digest=uuid.uuid4().hex,
        schema_version="1.6",
        seed=1,
        artifact_level="standard",
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(hours=1),
        scenario_count=1,
        payload={"source": {"kind": "archive"}, "metadata": {"name": name}},
    )
    HostedHarnessJob.no_workspace_objects.filter(id=created.id).update(state=state, **fields)
    return created


def main():
    organization = Organization.objects.get(id=os.environ["SEED_ORG_ID"])
    workspace = Workspace.objects.get(id=os.environ["SEED_WORKSPACE_ID"])
    suffix = os.environ["SEED_SUFFIX"]
    agent = AgentDefinition.objects.create(
        agent_name=f"e2e-agent5-agent-{suffix}",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="e2e AGENT-E2E-005 seed: an agent's builds on its Environments tab",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    version = AgentVersion.objects.create(
        agent_definition=agent,
        organization=organization,
        workspace=workspace,
        description=f"e2e AGENT-E2E-005 seed version {suffix}",
        commit_message="e2e AGENT-E2E-005 seed",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    scenario = Scenarios.objects.create(
        name=f"e2e-agent5-scenario-{suffix}",
        description="e2e AGENT-E2E-005 seed scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent,
        status=StatusType.COMPLETED.value,
    )
    run_test = RunTest.objects.create(
        name=f"e2e-agent5-run-{suffix}", agent_definition=agent, organization=organization, workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    linked = job(
        organization, workspace, version, run_test, f"e2e-agent5-linked-{suffix}",
        HostedHarnessJob.State.COMPLETED, current_stage="completed",
    )
    # Failed before its scenarios were submitted: no test was ever linked to it.
    failed = job(
        organization, workspace, version, None, f"e2e-agent5-failed-{suffix}",
        HostedHarnessJob.State.FAILED, current_stage="failed", terminal_at=timezone.now(),
    )
    print(json.dumps({
        "agentDefinitionId": str(agent.id),
        "agentVersionId": str(version.id),
        "runTestId": str(run_test.id),
        "linkedBuildId": str(linked.id),
        "failedBuildId": str(failed.id),
    }))


main()
`;

export function seedAgentBuilds(opts: { organizationId: string; workspaceId: string }): AgentBuildsSeed {
  const container = resolveBackendContainer();
  const suffix = seedSuffix();
  const dir = mkdtempSync(join(tmpdir(), 'e2e-agent5-'));
  const scriptPath = join(dir, `seed-${suffix}.py`);
  writeFileSync(scriptPath, SEED_SCRIPT);
  try {
    return runSeedScript(container, scriptPath, `agent-builds-seed-${suffix}.py`, {
      SEED_ORG_ID: opts.organizationId,
      SEED_WORKSPACE_ID: opts.workspaceId,
      SEED_SUFFIX: suffix,
    }) as AgentBuildsSeed;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
