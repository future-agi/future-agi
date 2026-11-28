import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedFailedEvalTask } from '../../lib/simulate-seed';

const REASON = 'Evaluation task row selection exceeded its read budget. Narrow the time range and retry.';
const UI_READY = 60_000;

test('EVAL-E2E-022: read a failed task’s selector diagnosis and clear it with Resume', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-022', area: 'evals',
    userGoal: 'See why a task failed before evaluating any rows and resume it without a stale diagnosis.',
    steps: ['hover over the seeded task’s Failed badge', 'open its Logs and read the same reason in the alert',
      'click Resume and see the alert disappear from the refreshed Logs'],
    backendChecks: ['Resume succeeds for the seeded task; its refreshed Logs and its Postgres row both have a null failure reason.'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(210_000); // Seed plus a first paint each for the list, Logs and Resume.
  page.setDefaultTimeout(UI_READY);
  const taskName = `e2e-failed-task-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const { result } = await actor.api.post<{ result: { project_id: string } }>('/tracer/project/', {
    name: `${taskName}-project`, trace_type: 'observe', model_type: 'GenerativeLLM',
  });
  const { taskId } = seedFailedEvalTask({ projectId: result.project_id, taskName,
    organizationId: actor.organizationId, workspaceId: actor.workspaceId });

  await page.goto('/dashboard/tasks');
  const row = page.getByRole('row').filter({ has: page.getByText(taskName, { exact: true }) });
  await row.getByText('Failed', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toHaveText(REASON);
  await row.getByText(taskName, { exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/dashboard/tasks/${taskId}$`));
  await page.getByRole('tab', { name: 'Logs', exact: true }).click();
  const alert = page.getByRole('alert').filter({ hasText: REASON });
  await expect(alert).toBeVisible({ timeout: UI_READY });

  const taskResponse = (path: string, method: string) => page.waitForResponse(response => {
    const url = new URL(response.url());
    return response.request().method() === method && url.pathname === path && url.searchParams.get('eval_task_id') === taskId;
  }, { timeout: UI_READY });
  const [resumed, refreshed] = await Promise.all([
    taskResponse('/tracer/eval-task/unpause_eval_task/', 'POST'),
    taskResponse('/tracer/eval-task/get_eval_task_logs/', 'GET'),
    page.getByRole('button', { name: 'Resume', exact: true }).click(),
  ]);
  expect(resumed.status()).toBe(200);
  expect(refreshed.status()).toBe(200);
  const logs = (await refreshed.json()).result;
  expect(logs).toMatchObject({ failure_reason: null, total_count: 0 });
  expect(['pending', 'running', 'completed']).toContain(logs.status);
  await expect(page.getByText('Total Spans', { exact: true })).toBeVisible(); // Not the loading skeleton.
  await expect(alert).toHaveCount(0);
  expect(await probe.pg<{ failure_reason: string | null }>(
    'SELECT failure_reason FROM tracer_eval_task WHERE id=$1 AND project_id=$2', [taskId, result.project_id],
  )).toEqual([{ failure_reason: null }]);
});
