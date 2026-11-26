import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// Path from futureagi/simulate/views/harness_environment.py (HarnessEnvironmentViewSet: capabilities). The rows'
// rules are futureagi/simulate/services/harness_readiness/workers.py.
const ENVIRONMENTS = '/simulate/api/harness-environments';

interface WorkerTool { tool: string; action: string | null; boundary: string }
interface WorkerRow {
  worker_id: string; status: string; reason_code: string;
  tools: WorkerTool[]; fix: { type: string } | null;
}
interface Report { workers: WorkerRow[] }

const byId = (report: Report) => Object.fromEntries(report.workers.map((one) => [one.worker_id, one]));

test('SIM-E2E-033: run readiness shows each worker\'s tools as Allowed, Blocked or Not enforced, never Supported', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-033', area: 'simulate',
    userGoal: 'A developer testing a multi-agent app sees, per worker, which tools the test world holds it to, '
      + 'and is told to rebuild an environment sealed before worker limits',
    steps: ['seed a certified Environment with two separately wired workers and ALK\'s root door.json, '
              + 'worker-limits.json and tool proxy files sealed (e2e/lib/harness_environment_seed.py, workers=sealed)',
            'seed one with the same workers sealed before worker limits (workers=old_seal)',
            'read each pairing\'s readiness (GET capabilities)'],
    backendChecks: ['the sealed pairing reads both workers Partial held_at_world_cooperative, the lookup worker\'s '
                      + 'lookup_order Allowed and issue_refund Blocked, and no worker Supported',
                    'the old seal reads both workers Untested sealed_before_worker_limits with a rebuild fix and '
                      + 'every tool Not enforced'],
  }),
}, async ({ actor }, testInfo) => {
  // Two seeds through the backend container (Django start-up each) and two API calls.
  test.setTimeout(120_000);
  const scope = { organizationId: actor.organizationId, workspaceId: actor.workspaceId };
  const sealed = seedHarnessEnvironment({ ...scope, workers: 'sealed' });
  const oldSeal = seedHarnessEnvironment({ ...scope, workers: 'old_seal' });
  await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({ ...scope, sealed, oldSeal }) });

  await test.step('api: a sealed pairing holds each worker on its own channel, cooperatively', async () => {
    const report = await actor.api.get<Report>(`${ENVIRONMENTS}/${sealed.environmentId}/capabilities/`);
    const rows = byId(report);
    expect([rows.order_lookup?.status, rows.order_lookup?.reason_code]).toEqual(['partial', 'held_at_world_cooperative']);
    expect(Object.fromEntries(rows.order_lookup.tools.map((one) => [one.tool, one.boundary])))
      .toEqual({ lookup_order: 'allowed', issue_refund: 'blocked' });
    expect(report.workers.every((one) => one.status !== 'supported')).toBe(true);
  });

  await test.step('api: an old seal reads each worker not known, with a rebuild fix', async () => {
    const report = await actor.api.get<Report>(`${ENVIRONMENTS}/${oldSeal.environmentId}/capabilities/`);
    for (const row of report.workers) {
      expect([row.status, row.reason_code, row.fix?.type])
        .toEqual(['untested', 'sealed_before_worker_limits', 'rebuild_environment']);
      expect(row.tools.every((one) => one.boundary === 'not_enforced')).toBe(true);
    }
  });
});
