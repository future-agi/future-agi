import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Alert surfaces, pinned off frontend/src/utils/axios.js (endpoints.project.*).
const MONITOR_PATH = '/tracer/user-alerts/';
const MONITOR_LOG_PATH = '/tracer/user-alert-logs/';
const monitorDetailsPath = (id: string) => `/tracer/user-alerts/${id}/details/`;

// Link params the alert sheet writes, pinned off
// frontend/src/sections/projects/Alerts/alertTraceLink.js.
const DATE_PARAM = 'primaryTraceDateFilter';
const FILTER_PARAM = 'observeLinkFilter';

// Sheet hooks that already exist in product code — no new data-testid needed.
// AlertsSheetView.jsx:422 for the header button; the row action's label is the
// menu item text rendered by useAlertSheetView.jsx.
const HEADER_VIEW_TRACE = '[data-alert-sheet-action="view-trace"]';
const ROW_VIEW_TRACE = 'View trace';
// FilterChips.jsx:133 — the chip strip that makes the carried scope visible.
const CHIP = '[data-filter-chip-column]';

// Fixed by lib/otlp.ts: sendTrace emits a root plus one child carrying
// fi.span.kind='llm', which is the only observation_type this harness can seed.
const ROOT_SPAN = 'e2e.root';

// Browser-side waits. Sized like OBS-E2E-001's: the local stack slows several
// fold under parallel specs, and this flow makes five separate navigations into
// the trace list, whose query runs a membership subquery per filter row.
const UI_READY = 60_000;

// POST /tracer/user-alerts/ answers with a message string, not the created row
// (tracer/views/monitor.py:574), so the id is read back from PG by name.
interface MonitorRow {
  id: string;
}

/** ISO-8601 for the wire; the log serializer takes DateTimeField. */
const iso = (d: Date) => d.toISOString();

test(
  'ALERT-E2E-001: a fired alert opens the traces from that fire’s own window',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'ALERT-E2E-001',
      area: 'alerts',
      userGoal:
        'An on-call engineer opens a fired alert and lands on the traces from that fire’s own time window',
      steps: [
        'send an OTLP trace with the org API key',
        'create a monitor on the auto-created project, filtered to LLM spans',
        'plant two fires: one whose window brackets the trace, one a day earlier that contains nothing',
        'open the alert from the alerts list',
        'use the row action on the fire that brackets the trace',
        'use the row action on the empty earlier fire',
        'use the header button, which stands for every fire',
        'narrow the issue grid by trigger type and use the header button again',
      ],
      backendChecks: [
        'both seeded spans present in CH `spans` (FINAL) under the auto-created project',
        'both planted fires stored in PG tracer_useralertmonitorlog with the exact windows sent',
        'the alert details endpoint reports window_start/window_end as the min/max across every fire',
        'the details window is unchanged when the issue list is narrowed by trigger type',
        'the row action carries that fire’s own window and the trace list returns the seeded trace',
        'a fire whose window excludes the trace returns an empty trace list',
        'the header button carries the window spanning every fire',
        'the monitor’s span-type filter travels in the link and renders as a chip',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    // SPAN_VISIBLE (15s) then five trace-list navigations at UI_READY (60s)
    // each = 315s worst case, well past the config's 120s default.
    test.setTimeout(330_000);

    const req = await request.newContext();
    const stamp = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const projectName = `e2e-alert-${stamp}`;
    const monitorName = `e2e-alert-monitor-${stamp}`;
    const MATCH_FIRE = `fire-a-${stamp}`;
    const EMPTY_FIRE = `fire-b-${stamp}`;

    const seeded = await sendTrace(req, {
      collectorUrl: E2E.collectorUrl,
      apiKey: actor.apiKey,
      secretKey: actor.secretKey,
      projectName,
      rootName: ROOT_SPAN,
    });
    await testInfo.attach('seeded-trace', {
      body: JSON.stringify(seeded),
      contentType: 'application/json',
    });

    const projectId = await test.step('storage: seeded spans land under the new project', async () => {
      await expect
        .poll(async () => {
          const rows = await probe.ch<{ n: string }>(
            'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}',
            { t: seeded.traceId },
          );
          return Number(rows[0].n);
        }, POLL.SPAN_VISIBLE)
        .toBe(seeded.spanIds.length);

      const projects = await probe.pg<{ id: string }>(
        'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
        [projectName, actor.organizationId],
      );
      expect(projects).toHaveLength(1);
      return projects[0].id;
    });

    // The window each fire claims to have measured. There is no clock control in
    // the harness, so the fires are planted already aged rather than waited out:
    // FIRE_MATCH brackets the spans just seeded, FIRE_EMPTY sits a day earlier
    // and contains nothing.
    const now = new Date();
    const fireMatchStart = new Date(now.getTime() - 60 * 60 * 1000);
    const fireMatchEnd = new Date(now.getTime() + 60 * 60 * 1000);
    const fireEmptyStart = new Date(now.getTime() - 25 * 60 * 60 * 1000);
    const fireEmptyEnd = new Date(now.getTime() - 24 * 60 * 60 * 1000);

    const { monitorId, matchLogId, emptyLogId } = await test.step(
      'seed: a monitor on the project and two fires with disjoint windows',
      async () => {
        // Body shape from UserAlertMonitorSerializer (fields = "__all__") with
        // organization/workspace/created_by injected server-side by
        // tracer/views/monitor.py:536; values mirror a monitor created through
        // the UI. `filters` is the same JSON the alert filter panel stores.
        await actor.api.post(MONITOR_PATH, {
          name: monitorName,
          project: projectId,
          metric_type: 'count_of_errors',
          threshold_type: 'static',
          threshold_operator: 'greater_than',
          critical_threshold_value: 1,
          alert_frequency: 60,
          auto_threshold_time_window: 10080,
          notification_emails: ['oncall@example.com'],
          filters: { observation_type: ['llm'] },
        });

        const monitors = await probe.pg<MonitorRow>(
          'SELECT id FROM tracer_useralertmonitor WHERE name = $1 AND organization_id = $2 AND deleted = false',
          [monitorName, actor.organizationId],
        );
        expect(monitors).toHaveLength(1);
        const id = monitors[0].id;

        // UserAlertMonitorLogWriteRequestSerializer (serializers/monitor.py:388).
        const fire = async (
          type: string,
          message: string,
          start: Date,
          end: Date,
        ) => {
          // Plain DRF ModelViewSet.create (tracer/views/monitor.py:955), so the
          // body is the serialized row itself, not the _gm success envelope.
          const res = await actor.api.post<{ id: string }>(MONITOR_LOG_PATH, {
            alert: id,
            type,
            message,
            time_window_start: iso(start),
            time_window_end: iso(end),
          });
          return res.id;
        };

        // The marker leads the message: the Issue cell truncates with an
        // ellipsis, so anything at the end never reaches the DOM to match on.
        const matchId = await fire(
          'critical',
          `${MATCH_FIRE} window brackets the seeded trace`,
          fireMatchStart,
          fireMatchEnd,
        );
        const emptyId = await fire(
          'warning',
          `${EMPTY_FIRE} window is a day earlier and holds nothing`,
          fireEmptyStart,
          fireEmptyEnd,
        );
        return { monitorId: id, matchLogId: matchId, emptyLogId: emptyId };
      },
    );

    await testInfo.attach('seeded-alert', {
      body: JSON.stringify({ monitorId, matchLogId, emptyLogId, projectId }),
      contentType: 'application/json',
    });

    await test.step('storage: both fires stored with the windows sent', async () => {
      const rows = await probe.pg<{
        id: string;
        time_window_start: Date;
        time_window_end: Date;
      }>(
        'SELECT id, time_window_start, time_window_end FROM tracer_useralertmonitorlog WHERE alert_id = $1 ORDER BY created_at DESC',
        [monitorId],
      );
      expect(rows).toHaveLength(2);
      const byId = new Map(rows.map((r) => [r.id, r]));
      expect(new Date(byId.get(matchLogId)!.time_window_start).toISOString()).toBe(
        iso(fireMatchStart),
      );
      expect(new Date(byId.get(emptyLogId)!.time_window_end).toISOString()).toBe(
        iso(fireEmptyEnd),
      );
    });

    await test.step('API: the rule-level window spans every fire, whatever the issue list shows', async () => {
      const details = await actor.api.get<{
        result: { window_start: string; window_end: string };
      }>(monitorDetailsPath(monitorId));
      // Aggregated over every fire: the earliest start and the latest end.
      expect(new Date(details.result.window_start).toISOString()).toBe(
        iso(fireEmptyStart),
      );
      expect(new Date(details.result.window_end).toISOString()).toBe(
        iso(fireMatchEnd),
      );

      // The regression this replaced: the header used to read the newest row out
      // of logs.results, so narrowing the issue list moved its window. The
      // aggregate is taken before that filter, so it must not move.
      const narrowed = await actor.api.get<{
        result: { window_start: string; window_end: string; logs: { results: unknown[] } };
      }>(monitorDetailsPath(monitorId), { type: 'warning' });
      expect(narrowed.result.logs.results).toHaveLength(1);
      expect(narrowed.result.window_start).toBe(details.result.window_start);
      expect(narrowed.result.window_end).toBe(details.result.window_end);
    });

    // Open the sheet from whatever page we are on. Only the first call pays for
    // a full document load; the later ones come back through history, because
    // each reload re-runs the auth bootstrap and this flow visits the sheet four
    // times.
    const openSheet = async () => {
      if (new URL(page.url()).pathname !== '/dashboard/alerts') {
        if (page.url() === 'about:blank') {
          await page.goto('/dashboard/alerts', { waitUntil: 'domcontentloaded' });
        } else {
          await page.goBack();
          await expect(page).toHaveURL(/\/dashboard\/alerts/, { timeout: UI_READY });
        }
      }
      await page.getByPlaceholder('Search').first().fill(monitorName);
      await expect(page.getByText(monitorName).first()).toBeVisible({
        timeout: UI_READY,
      });
      await page.getByText(monitorName).first().click();
      await expect(page.locator(HEADER_VIEW_TRACE)).toBeVisible({
        timeout: UI_READY,
      });
    };

    /** The window the link carries, as the two local-time strings the picker uses. */
    const linkWindow = () => {
      const params = new URLSearchParams(new URL(page.url()).search);
      const raw = params.get(DATE_PARAM);
      return raw ? (JSON.parse(raw).dateFilter as string[]) : null;
    };

    const traceNames = () =>
      page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');

    await test.step('UI: the row action carries that fire’s window and finds the trace', async () => {
      await openSheet();
      await page
        .locator('.ag-row')
        .filter({ hasText: MATCH_FIRE })
        .first()
        .locator('button')
        .first()
        .click();
      await page.getByText(ROW_VIEW_TRACE, { exact: true }).first().click();
      await expect(page).toHaveURL(new RegExp(DATE_PARAM), { timeout: UI_READY });

      await expect(traceNames()).toHaveText([ROOT_SPAN], { timeout: UI_READY });
      expect(linkWindow()).not.toBeNull();
    });

    const matchWindow = linkWindow();

    await test.step('UI: the monitor’s span-type filter travels and is visible as a chip', async () => {
      const params = new URLSearchParams(new URL(page.url()).search);
      const rows = JSON.parse(params.get(FILTER_PARAM) ?? '[]') as {
        column_id: string;
      }[];
      // node_type is the trace list's name for the monitor's observation_type;
      // filters.py:319 maps both to the same column.
      expect(rows.map((r) => r.column_id)).toEqual(['node_type']);
      await expect(page.locator(CHIP)).toHaveCount(1, { timeout: UI_READY });
    });

    await test.step('UI: a fire whose window excludes the trace returns nothing', async () => {
      await openSheet();
      await page
        .locator('.ag-row')
        .filter({ hasText: EMPTY_FIRE })
        .first()
        .locator('button')
        .first()
        .click();
      await page.getByText(ROW_VIEW_TRACE, { exact: true }).first().click();
      await expect(page).toHaveURL(new RegExp(DATE_PARAM), { timeout: UI_READY });

      // A different fire must produce a different window, not merely a page that
      // happens to be empty.
      expect(linkWindow()).not.toEqual(matchWindow);
      await expect(traceNames()).toHaveCount(0, { timeout: UI_READY });
    });

    await test.step('UI: the header button spans every fire and finds the trace', async () => {
      await openSheet();
      await page.locator(HEADER_VIEW_TRACE).click();
      await expect(page).toHaveURL(new RegExp(DATE_PARAM), { timeout: UI_READY });

      const spanning = linkWindow();
      expect(spanning).not.toBeNull();
      // Starts no later than the earlier fire and ends no earlier than the later
      // one — the union, not either fire's own window.
      expect(new Date(spanning![0]).getTime()).toBeLessThanOrEqual(
        fireEmptyStart.getTime(),
      );
      expect(new Date(spanning![1]).getTime()).toBeGreaterThanOrEqual(
        fireMatchEnd.getTime(),
      );
      await expect(traceNames()).toHaveText([ROOT_SPAN], { timeout: UI_READY });
    });

    await req.dispose();
  },
);
