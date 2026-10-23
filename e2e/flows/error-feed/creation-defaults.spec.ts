import { test, expect } from '../../lib/error-feed-fixtures';
import { ErrorFeedHarness } from '../../lib/error-feed-harness';
import { flowAnnotation } from '../../lib/flow-meta';
import { expectFeedStatus, openIssue, expectUiStatus, storedIssue, listIssues } from '../../lib/error-feed-ui';

test('ESC-E2E-001: New issues require evidence before showing Escalating', {
  tag: ['@flow'], annotation: flowAnnotation({
    id: 'ESC-E2E-001', area: 'error-feed',
    userGoal: 'An engineer sees a newly detected issue start in review before growth is confirmed',
    steps: ['publish singleton and large initial scanner, F6 and evaluation clusters',
      'inspect each new issue and its filter results before confirmation', 'check its original occurrence timestamps'],
    backendChecks: ['PG stores the exact source memberships with system ownership, For review and revision zero',
      'collector timestamps remain original completion times',
      'detail/list/status-filter APIs and the browser show those exact issues For review'],
  }),
}, async ({ browser, scopeActors, scopeProbe }, info) => {
  // Provisioning + six publication cohorts + collector and browser budgets.
  test.setTimeout(1_200_000);
  const harness = new ErrorFeedHarness(scopeActors, scopeProbe, info);
  for (const source of ['scanner', 'f6', 'eval'] as const) {
    for (const number of [1, 25]) {
      await test.step(`${source}: initial ${number} occurrences`, async () => {
        const project = await harness.project(`new-${source}-${number}`, number);
        if (source === 'eval') {
          for (let index = 1; index < number; index++) {
            project.traces.push(await harness.ingest(project.name, 1, harness.at(-20)));
          }
        }
        const published = source === 'f6' ? await harness.publish(project)
          : await harness.publishLegacy(project, source);
        const issue = published.issue!;
        const saved = await storedIssue(scopeProbe, issue);
        expect(saved.status).toBe('for_review');
        expect(saved.status_origin).toBe('system');
        expect(Number(saved.status_revision)).toBe(0);
        const expectedIds = 'source_ids' in published ? published.source_ids
          : published.source_reports.flatMap(report => report.occurrence_ids);
        expect(expectedIds).toHaveLength(number);
        if (source === 'scanner') {
          const rows = await scopeProbe.pg<{ id: string; occurred_at: Date }>(
            'SELECT id, occurred_at FROM tracer_trace_scan_issue WHERE cluster_id=$1 AND deleted=false', [issue.id]);
          expect(rows.map(row => row.id).sort()).toEqual([...expectedIds].sort());
          expect(rows.every(row => row.occurred_at.toISOString() === harness.at(-20))).toBe(true);
        } else {
          const column = source === 'f6' ? 'finding_id' : 'eval_logger_id';
          const rows = await scopeProbe.pg<{ source_id: string; occurred_at: Date }>(
            `SELECT ${column} AS source_id, occurred_at FROM tracer_error_cluster_traces
             WHERE cluster_id=$1 AND deleted=false`, [issue.id]);
          expect(rows.map(row => row.source_id).sort()).toEqual([...expectedIds].sort());
          expect(rows.every(row => row.occurred_at.toISOString() === harness.at(-20))).toBe(true);
        }
        const row = await expectFeedStatus(scopeActors, project, issue, 'for_review');
        expect(row.status_origin).toBe('system');
        expect(await listIssues(scopeActors, project, 'escalating')).toEqual([]);
        // Qualify each adapter against real PG/ClickHouse provenance, not only
        // the creation default. One observation never confirms escalation.
        await harness.evaluate(project, 10);
        const evaluated = await storedIssue(scopeProbe, issue);
        expect(evaluated.status).toBe('for_review');
        expect(Number(evaluated.status_revision)).toBe(0);
        expect(evaluated.escalation_state.reason!.current_count).toBe(number);
        const { page, context } = await openIssue(browser, scopeActors, issue);
        await expectUiStatus(page, 'for_review');
        await context.close();
      });
    }
  }
});
