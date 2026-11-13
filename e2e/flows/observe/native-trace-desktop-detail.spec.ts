import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedNative, browserErrors, shot, UI_READY, ids, walk, type TraceDetail } from './native-source';

test.use({ viewport: { width: 1440, height: 900 } });

test(
  'OBS-E2E-057: native tree, model, tool and timeline details preserve source truth',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'OBS-E2E-057',
      area: 'observe',
      userGoal:
        'A developer inspects native model and tool work on a normal desktop with truthful costs and sanitized events',
      steps: [
        'open known, zero, unknown and legacy traces from the real grid',
        'select root and model nodes and inspect source IDs and canonical token and cost summaries',
        'inspect Gemini tool output and sanitized exception attributes',
        'switch to timeline and back to tree using the display controls',
      ],
      backendChecks: [
        'trace detail contains exact source IDs, parents, token contributions and LLM call counts',
        'trace and selected model costs preserve known, zero and unknown semantics',
        'Copilot links and exception types and Gemini sanitized events survive in the detail API',
        'tool output and exception type render in the selected node; unexpected browser and HTTP errors fail',
      ],
    }),
  },
  async ({ page, request, actor, probe }, testInfo) => {
    // Two SPAN_VISIBLE polls (30s), 8 UI_READY action chains, 90s I/O headroom.
    test.setTimeout(30_000 + 8 * UI_READY + 90_000);
    page.setDefaultTimeout(UI_READY);
    const checkBrowser = browserErrors(page, testInfo);
    try {
      const { cases, projectId } = await seedNative(request, actor, probe, testInfo);
      for (const key of [
        'copilot-known-1',
        'copilot-zero',
        'copilot-unknown',
        'gemini-known',
        'gemini-unknown',
        'legacy',
      ]) {
        const c = cases.find((x) => x.key === key)!;
        await test.step(`UI/API ${key} tree and canonical summary`, async () => {
          await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces`, { waitUntil: 'domcontentloaded' });
          const pending = page.waitForResponse(
            (r) => new URL(r.url()).pathname === `/tracer/trace/${c.traceId}/` && r.request().method() === 'GET',
            { timeout: UI_READY },
          );
          await page.locator(`.clean-data-table:visible .ag-row[row-id="${c.traceId}"] [col-id="trace_name"]`).click();
          const response = await pending;
          expect(response.status()).toBe(200);
          const body: TraceDetail = await response.json();
          await testInfo.attach(`detail-${key}`, {
            body: JSON.stringify(body.result),
            contentType: 'application/json',
          });
          expect(body.result.summary.total_tokens).toBe(c.total);
          if (key.startsWith('copilot')) expect(body.result.capture_coverage).toEqual({
            scope: 'observed_requests', source_summary_present: true, request_count: 3, coverage_unknown: true,
          });
          else expect(body.result.capture_coverage).toBeUndefined();
          if (c.cost === null) expect(body.result.summary.total_cost).toBeNull();
          else expect(body.result.summary.total_cost).toBeCloseTo(c.cost, 8);
          expect(body.result.summary.total_prompt_tokens).toBe(c.prompt.reduce((n, v) => n + v, 0));
          expect(body.result.summary.total_completion_tokens).toBe(c.completion.reduce((n, v) => n + v, 0));
          expect(body.result.summary.span_type_counts.llm).toBe(
            key.startsWith('copilot') ? 3 : key.startsWith('gemini') ? 2 : 1,
          );
          const detailSpans = walk(body.result.observation_spans).sort((a, b) => a.id.localeCompare(b.id));
          expect(detailSpans.map((s) => s.id)).toEqual(c.spanIds.slice().sort());
          expect(detailSpans.map((s) => s.parent_span_id ?? '')).toEqual(c.parents);
          expect(detailSpans.map((s) => s.prompt_tokens)).toEqual(c.prompt);
          expect(detailSpans.map((s) => s.completion_tokens)).toEqual(c.completion);
          if (key.startsWith('copilot')) {
            expect(detailSpans[0].span_events[0].attributes).toEqual({ 'exception.type': 'Timeout' });
            expect(detailSpans[0].span_attributes['fi.otel.links'][0].span_id).toBe(ids[1]);
          }
          if (key.startsWith('gemini')) {
            expect(detailSpans[3].span_events[0].attributes).toEqual({ 'exception.type': 'SyntheticToolError' });
            expect(JSON.stringify(detailSpans[3].span_events)).not.toContain('FORBIDDEN-SYNTHETIC-EVENT-TEXT');
          }
          const drawer = page
            .locator('.MuiDrawer-paper:visible')
            .filter({ has: page.getByText(c.traceId, { exact: true }) });
          await expect(drawer.getByText(ids[0], { exact: true })).toBeVisible({ timeout: UI_READY });
          await shot(page, testInfo, `${key}-tree`);
          await drawer.getByText(c.name, { exact: true }).first().click();
          await expect(drawer.getByText('Select a span to view details', { exact: true })).toBeVisible({
            timeout: UI_READY,
          });
          // formatters.js abbreviates >=10000; retain literal independent oracle.
          await expect(
            drawer.getByText(`${c.total === 43071 ? '43.1K' : c.total} ${key.startsWith('copilot') ? 'observed tokens' : 'tokens'}`, { exact: true }),
          ).toBeVisible({ timeout: UI_READY });
          await shot(page, testInfo, `${key}-summary`);
          expect(
            await drawer.getByText(`${key.startsWith('copilot') ? 'Observed cost: ' : ''}${c.display}`, { exact: true }).first().isVisible(),
            `${key} summary must show ${c.display}`,
          ).toBe(true);
          const childName = key.startsWith('copilot') ? 'chat' : key.startsWith('gemini') ? 'llm_call' : 'e2e.llm-call';
          await drawer.getByText(childName, { exact: true }).first().click();
          await expect(drawer.getByText(ids[1], { exact: true })).toBeVisible({ timeout: UI_READY });
          const expectedChildCost = key.includes('unknown')
            ? null
            : key === 'copilot-zero'
              ? 0
              : key.startsWith('copilot')
                ? 0.1
                : key.startsWith('gemini')
                  ? 0.25
                  : 0.05;
          const selectedChild = detailSpans.find((s) => s.id === ids[1])!;
          if (expectedChildCost === null) {
            expect(selectedChild.cost).toBeNull();
            expect(await drawer.getByText(/^Cost : /).count(), 'unknown span must not claim a zero cost').toBe(0);
          } else {
            expect(selectedChild.cost).toBeCloseTo(expectedChildCost, 8);
            const expectedCostLabel =
              expectedChildCost === 0
                ? '$0.00'
                : expectedChildCost === 0.1
                  ? '$0.10'
                  : expectedChildCost === 0.25
                    ? '$0.25'
                    : '$0.05';
            await expect(drawer.getByText(`Cost : ${expectedCostLabel}`, { exact: true })).toBeVisible({
              timeout: UI_READY,
            });
          }
          await shot(page, testInfo, `${key}-model-detail`);
        });
        if (key === 'gemini-known')
          await test.step('UI Gemini tool detail and timeline', async () => {
            const drawer = page
              .locator('.MuiDrawer-paper:visible')
              .filter({ has: page.getByText(c.traceId, { exact: true }) });
            await drawer.getByText('tool_call', { exact: true }).first().click();
            await expect(drawer.getByText(ids[3], { exact: true })).toBeVisible({ timeout: UI_READY });
            await expect(drawer.getByText(`${c.name}-tool-result`, { exact: true })).toBeVisible({ timeout: UI_READY });
            await shot(page, testInfo, 'gemini-tool-detail');
            await drawer.getByRole('tab', { name: 'Events', exact: true }).click();
            await expect(drawer.getByText('SyntheticToolError', { exact: true })).toBeVisible({ timeout: UI_READY });
            expect(await drawer.getByText('FORBIDDEN-SYNTHETIC-EVENT-TEXT', { exact: true }).count()).toBe(0);
            await shot(page, testInfo, 'gemini-sanitized-event');
            await drawer.getByRole('tab', { name: 'Preview', exact: true }).click();
            await drawer.getByRole('button', { name: 'Display', exact: true }).click();
            await page.getByRole('tab', { name: 'Timeline View', exact: true }).click();
            await page.keyboard.press('Escape');
            await expect(page.getByRole('tab', { name: 'Timeline View', exact: true })).toBeHidden({
              timeout: UI_READY,
            });
            await expect(drawer.getByText('Trace Timeline', { exact: true })).toBeVisible({ timeout: UI_READY });
            await shot(page, testInfo, 'gemini-timeline');
            // The timeline intentionally has no no-selection summary. Restore tree
            // mode through the real control before later tree-summary assertions.
            await drawer.getByRole('button', { name: 'Display', exact: true }).click();
            await page.getByRole('tab', { name: 'Trace View', exact: true }).click();
            await page.keyboard.press('Escape');
            await expect(page.getByRole('tab', { name: 'Trace View', exact: true })).toBeHidden({
              timeout: UI_READY,
            });
            await expect(drawer.getByText('Trace Tree', { exact: true })).toBeVisible({ timeout: UI_READY });
          });
      }
    } finally {
      await checkBrowser();
    }
  },
);
