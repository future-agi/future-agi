import { test, expect } from '../lib/error-feed-fixtures';
import { ErrorFeedHarness } from '../lib/error-feed-harness';

test('private source harness qualifies collector publication and production Temporal evaluation', async ({ scopeActors, scopeProbe }, info) => {
  test.setTimeout(360_000);
  const harness = new ErrorFeedHarness(scopeActors, scopeProbe, info);
  const project = await harness.project('source-qualification', 1);
  const published = await harness.publish(project);
  expect(published.issue?.status).toBe('for_review');
  expect(published.source_reports).toHaveLength(1);
  expect(published.source_reports[0].occurrence_ids).toHaveLength(1);
  const evaluated = await harness.evaluate(project, 10);
  expect(evaluated.result.status).toBe('completed');
  expect(evaluated.result.result.status).toBe('completed');
  expect(evaluated.result.result.result.evaluated).toBe(1);
  const rows = await scopeProbe.pg<{ status: string; status_origin: string; state: { reason: { current_count: number } } }>(
    'SELECT status, status_origin, escalation_state AS state FROM tracer_trace_error_group WHERE id=$1', [published.issue!.id]);
  expect(rows).toHaveLength(1);
  expect(rows[0].status).toBe('for_review');
  expect(rows[0].status_origin).toBe('system');
  expect(rows[0].state.reason.current_count).toBe(1);
});

test('session completion and trace breadth use real collector evidence', async ({ scopeActors, scopeProbe }, info) => {
  test.setTimeout(360_000);
  const harness = new ErrorFeedHarness(scopeActors, scopeProbe, info);
  const externalSession = `${scopeActors.prefix}-session`;
  const project = await harness.project('session-source-contract', 1, -20, externalSession);
  await harness.ingest(project.name, 1, harness.at(-10), externalSession);
  let sessionId = '';
  await expect.poll(async () => {
    const rows = await scopeProbe.ch<{ trace_session_id: string }>(
      `SELECT trace_session_id FROM trace_sessions FINAL
       WHERE project_id={project:UUID} AND external_session_id={session:String} AND is_deleted=0`,
      { project: project.id, session: externalSession });
    if (rows.length === 1) sessionId = rows[0].trace_session_id;
    return rows.length;
  }, { timeout: 60_000 }).toBe(1);
  const result = await harness.qualifySession(project, sessionId);
  expect(new Date(result.occurred_at).toISOString()).toBe(harness.at(-10));
  expect(result.provenance).toBe('session_end');
  expect(result.traces_count).toBe(2);
});
