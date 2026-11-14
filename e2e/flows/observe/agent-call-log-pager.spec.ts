import { test, expect } from '../../lib/existing-customer';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedCallExecutions } from '../../lib/simulate-seed';
import { PAGER_VIEWPORT, UI_READY, openObserveList, pagerButton, readPager, selectPageSize, waitForCurrentPage } from '../../lib/pager';

// Runs as the existing customer: the Agent Definition page is one an org
// created after the Simulate cutoff cannot open (#3502). Its pager neighbours
// stay on the ordinary actor in list-pagination.spec.ts.
test.use({ viewport: PAGER_VIEWPORT });

test('OBS-E2E-034: the agent call-log pager (a plain DRF-paginated, non-cursor screen) still paginates and reaches its last row', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-034', area: 'observe',
    userGoal: "A developer browsing an agent version's call logs gets a working pager even though this screen has no cursor `has_more` contract",
    steps: ["seed 12 completed CallExecution rows for one fresh AgentDefinition/AgentVersion directly through the backend (no simulate/voice infra runs in this harness — see e2e/lib/simulate-seed.ts)",
            "open the agent's Call Logs tab for that version",
            'see all 12 calls on one page at the default 25 per page',
            'switch to 10 per page and confirm more than one page is offered',
            'walk forward to the last page'],
    backendChecks: ['the seeded CallExecution rows are scoped to the seeded AgentVersion, status=completed, non-empty eval_outputs — exactly what AgentVersionCallExecutionView filters for',
                    'AgentVersionCallExecutionView honours the page_size the grid sends'],
  }),
}, async ({ page, actor }, testInfo) => {
  await testInfo.attach('existing-customer', { contentType: 'application/json', body: JSON.stringify({ email: actor.email, organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
  test.setTimeout(120_000);
  const CALL_EXECUTION_COUNT = 12;
  const seed = seedCallExecutions({
    organizationId: actor.organizationId,
    workspaceId: actor.workspaceId,
    count: CALL_EXECUTION_COUNT,
  });
  await openObserveList(
    page,
    `/dashboard/simulate/agent-definitions/${seed.agentDefinitionId}?activeTab=CallLogs&version=${seed.agentVersionId}`,
  );

  const callRows = page.locator('.clean-data-table:visible .ag-row');
  // The grid sends page_size, as it does to the trace lists. The view used to
  // read only `limit` and served 10 rows whatever was asked, while the grid
  // counted pages at 25 and ran out of pages early. At the default 25 all 12
  // calls must fit on one page.
  await expect(callRows).toHaveCount(CALL_EXECUTION_COUNT, { timeout: UI_READY });
  expect((await readPager(page)).nextDisabled).toBe(true);

  await selectPageSize(page, '10');
  await expect(callRows).toHaveCount(10, { timeout: UI_READY });
  await waitForCurrentPage(page, 1);

  const pager = await readPager(page);
  // PAG-05: this screen's response has no `has_more` field at all — a plain
  // DRF `count`/`next`/`previous` payload (ExtendedPageNumberPagination,
  // futureagi/tfc/utils/pagination.py) — yet the shared CursorGridPagination
  // still offers a real second page for it.
  expect(pager.numbers.length).toBeGreaterThan(1);
  expect(pager.nextDisabled).toBe(false);

  // The grid prefetches the next page ahead of the visible one, so this click
  // is served from that cache rather than guaranteed to hit the wire — wait
  // on the pager's own DOM state, not a response (same reasoning as the
  // cached-page legs in OBS-E2E-028/OBS-E2E-032).
  await pagerButton(page, 'Next page').click();
  await waitForCurrentPage(page, 2);

  await expect(callRows).toHaveCount(seed.callExecutionCount - 10, { timeout: UI_READY });
  const finalPager = await readPager(page);
  expect(finalPager.nextDisabled).toBe(true);
});
