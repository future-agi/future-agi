import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import {
  seedNative,
  browserErrors,
  shot,
  UI_READY,
  SESSIONS,
  type SessionPage,
  type SessionHistory,
} from './native-source';

test.use({ viewport: { width: 1440, height: 900 } });

test(
  'OBS-E2E-056: native sessions preserve exact membership, costs and trace navigation',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'OBS-E2E-056',
      area: 'observe',
      userGoal:
        'A developer trusts native session totals and opens the intended source trace from ordered conversation history',
      steps: [
        'read the exact six seeded session identities in the API and desktop grid',
        'open the two-turn Copilot session and navigate to its first source trace',
        'scroll the grid to inspect observed-only native costs, coverage unknown and exact request and trace counts',
        'open each remaining session and match its history card, source trace, tokens and cost',
      ],
      backendChecks: [
        'session list and history totals sum all request contributions, not parent summaries',
        'two-turn history has exactly the source chronology, 86142 tokens, two traces and 1.20 USD',
        'single-trace session API, card and header retain exact membership, token breakdown and known, zero or null cost',
        'View Trace opens the exact source trace, without unexpected browser or HTTP errors',
      ],
    }),
  },
  async ({ page, request, actor, probe }, testInfo) => {
    // Two SPAN_VISIBLE polls (30s), 7 UI_READY action chains, 90s I/O headroom.
    test.setTimeout(30_000 + 7 * UI_READY + 90_000);
    page.setDefaultTimeout(UI_READY);
    const checkBrowser = browserErrors(page, testInfo);
    try {
      const { cases, sessions, projectId } = await seedNative(request, actor, probe, testInfo);
      let sessionList: SessionPage['result'];
      await test.step('UI/API multi-turn Sessions and opening exact trace', async () => {
        await page.goto(`/dashboard/observe/${projectId}/sessions`, { waitUntil: 'domcontentloaded' });
        const data = await actor.api.get<SessionPage>(SESSIONS, {
          project_id: projectId,
          page_size: 25,
          page_number: 0,
        });
        sessionList = data.result;
        expect(data.result.table.map((r) => r.session_id).sort()).toEqual(
          sessions.map((s) => s.trace_session_id).sort(),
        );
        const multi = cases.slice(0, 2);
        const sid = sessions.find((s) => s.external_session_id === multi[0].session)!.trace_session_id;
        for (const session of sessions) {
          const members = cases.filter((c) => c.session === session.external_session_id);
          const row = data.result.table.find((r) => r.session_id === session.trace_session_id);
          expect(row?.total_tokens).toBe(members.reduce((n, c) => n + c.total, 0));
          if (members[0].key.startsWith('copilot')) expect(row?.capture_coverage).toEqual({
            scope: 'observed_requests', source_summary_present: true,
            request_count: members.length === 2 ? 6 : 3, coverage_unknown: true,
          });
          else expect(row?.capture_coverage).toBeUndefined();
          if (members.some((c) => c.cost === null)) expect(row?.total_cost).toBeNull();
          else
            expect(row?.total_cost).toBeCloseTo(
              members.reduce((n, c) => n + c.cost!, 0),
              8,
            );
        }
        const visible = () =>
          page
            .locator('.ag-row [col-id="session_id"]:visible')
            .evaluateAll((es) => es.map((e) => e.closest('.ag-row')?.getAttribute('row-id')).sort());
        await expect.poll(visible, { timeout: UI_READY }).toEqual(sessions.map((s) => s.trace_session_id).sort());
        await shot(page, testInfo, 'sessions-list');
        const pending = page.waitForResponse(
          (r) =>
            new URL(r.url()).pathname === `/tracer/trace-session/${sid}/query/` && r.request().method() !== 'OPTIONS',
          { timeout: UI_READY },
        );
        await page.locator(`.ag-row[row-id="${sid}"] [col-id="session_id"]:visible`).click();
        const response = await pending;
        expect(response.status()).toBe(200);
        const history: SessionHistory = await response.json();
        expect(history.result.session_metadata.total_tokens).toBe(86142);
        expect(history.result.session_metadata.total_traces).toBe(2);
        expect(history.result.session_metadata.total_cost).toBeCloseTo(1.2, 8);
        expect(history.result.response.map((r) => r.trace_id)).toEqual(multi.map((c) => c.traceId));
        const drawer = page
          .locator('.MuiDrawer-paper:visible')
          .filter({ has: page.getByRole('tablist', { name: 'session drawer tabs' }) });
        await expect(drawer.getByRole('tab', { name: 'Session History', exact: true })).toBeVisible({
          timeout: UI_READY,
        });
        await shot(page, testInfo, 'sessions-multi-turn');
        expect(
          await drawer.getByText('Observed Cost: $1.20', { exact: true }).isVisible(),
          'known two-turn session cost must be $1.20',
        ).toBe(true);
        const trace = page.waitForResponse(
          (r) => new URL(r.url()).pathname === `/tracer/trace/${multi[0].traceId}/` && r.request().method() === 'GET',
          { timeout: UI_READY },
        );
        await drawer.getByRole('button', { name: 'View Trace', exact: true }).first().click();
        expect((await trace).status()).toBe(200);
        await expect(page.getByText(multi[0].traceId, { exact: true }).last()).toBeVisible({ timeout: UI_READY });
        await shot(page, testInfo, 'session-opened-trace');
      });
      await test.step('Sessions grid known zero unknown costs and exact trace counts', async () => {
        await page.goto(`/dashboard/observe/${projectId}/sessions`, { waitUntil: 'domcontentloaded' });
        await expect(page.locator('.ag-row [col-id="session_id"]:visible')).toHaveCount(sessions.length, {
          timeout: UI_READY,
        });
        await page.locator('.clean-data-table:visible .ag-center-cols-viewport').hover();
        await page.mouse.wheel(1800, 0);

        for (const session of sessions) {
          const members = cases.filter((c) => c.session === session.external_session_id);
          const cost = members.some((c) => c.cost === null) ? null : members.reduce((n, c) => n + c.cost!, 0);
          // Literal UI expectations are independent of the shared formatter;
          // the API assertion separately protects the underlying numeric value.
          const labels: Record<string, string> = {
            'copilot-known-1': '$0.60', 'copilot-known-2': '$0.60',
            'copilot-zero': '$0.00', 'copilot-unknown': '-',
            'gemini-known': '$0.50', 'gemini-unknown': '-', legacy: '$0.05',
          };
          const label = members.length === 2 ? '$1.20' : labels[members[0].key];
          expect(typeof label).toBe('string');
          const cell = page.locator(`.ag-row[row-id="${session.trace_session_id}"] [col-id="total_cost"]:visible`);
          await expect(cell).toHaveText(members[0].key.startsWith('copilot')
            ? `Observed-only · ${cost === null ? 'Unknown' : label}` : label, { timeout: UI_READY });
          const row = sessionList.table.find((s) => s.session_id === session.trace_session_id)!;
          if (cost === null) expect(row.total_cost).toBeNull();
          else expect(row.total_cost).toBeCloseTo(cost, 8);
          expect(row.total_traces_count).toBe(members.length);
        }
        await shot(page, testInfo, 'sessions-grid-costs-all');
      });
      for (const key of ['copilot-zero', 'copilot-unknown', 'gemini-known', 'gemini-unknown', 'legacy']) {
        const c = cases.find((c) => c.key === key)!,
          sid = sessions.find((s) => s.external_session_id === c.session)!.trace_session_id;
        await test.step(`UI/API ${key} Session drawer exact cost tokens and membership`, async () => {
          await page.goto(`/dashboard/observe/${projectId}/sessions`, { waitUntil: 'domcontentloaded' });
          const pending = page.waitForResponse(
            (r) =>
              new URL(r.url()).pathname === `/tracer/trace-session/${sid}/query/` && r.request().method() !== 'OPTIONS',
            { timeout: UI_READY },
          );
          await page.locator(`.ag-row[row-id="${sid}"] [col-id="session_id"]:visible`).click();
          const response = await pending;
          expect(response.status()).toBe(200);
          const body: SessionHistory = await response.json();
          await testInfo.attach(`session-${key}`, {
            body: JSON.stringify(body.result),
            contentType: 'application/json',
          });
          expect(body.result.response.map((r) => r.trace_id)).toEqual([c.traceId]);
          expect(body.result.session_metadata.total_tokens).toBe(c.total);
          expect(body.result.session_metadata.total_traces).toBe(1);
          if (c.cost === null) expect(body.result.session_metadata.total_cost).toBeNull();
          else expect(body.result.session_metadata.total_cost).toBeCloseTo(c.cost, 8);
          const drawer = page
            .locator('.MuiDrawer-paper:visible')
            .filter({ has: page.getByRole('tablist', { name: 'session drawer tabs' }) });
          // TracesDrawer.jsx deliberately labels null session cost N/A, not $0.
          await expect(
            drawer.getByText(`${key.startsWith('copilot') ? 'Observed Cost' : 'Total Cost'}: ${c.cost === null ? 'N/A' : c.display}`, { exact: true }),
          ).toBeVisible({ timeout: UI_READY });
          // ShowTrace is a Grid container with the history card's View Trace button.
          // The auto-selected detail panel repeats this ID outside that card.
          const cards = drawer
            .locator('.MuiGrid-container')
            .filter({ has: page.getByRole('button', { name: 'View Trace', exact: true }) });
          const card = cards.filter({ has: page.getByText(c.traceId, { exact: true }) });
          await expect(card).toHaveCount(1, { timeout: UI_READY });
          // Pin the exact labelled Trace ID row in TraceCardRightSection's
          // history card, rather than a second detail-summary ID within the card.
          const identityRow = card.getByText('Trace ID:', { exact: true }).locator('..');
          await expect(identityRow.getByText(c.traceId, { exact: true })).toBeVisible({ timeout: UI_READY });
          const renderedIds = await cards
            .getByText('Trace ID:', { exact: true })
            .locator('..')
            .locator('p')
            .evaluateAll((es) =>
              es
                .map((e) => (e.textContent ?? '').trim())
                .filter((t) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(t)),
            );
          expect(renderedIds).toEqual([c.traceId]);
          expect(body.result.session_metadata.session_id).toBe(sid);
          const row = body.result.response.find((r) => r.trace_id === c.traceId)!;
          expect(row.system_metrics.total_tokens).toBe(c.total);
          expect(row.system_metrics.input_tokens).toBe(c.prompt.reduce((n, v) => n + v, 0));
          expect(row.system_metrics.output_tokens).toBe(c.completion.reduce((n, v) => n + v, 0));
          if (c.cost === null) expect(row.system_metrics.total_cost).toBeNull();
          else expect(row.system_metrics.total_cost).toBeCloseTo(c.cost, 8);
          await expect(drawer.getByText('Total Traces: 1', { exact: true })).toBeVisible({ timeout: UI_READY });

          await shot(page, testInfo, `${key}-session-drawer`);
        });
      }
    } finally {
      await checkBrowser();
    }
  },
);
