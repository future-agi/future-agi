import { test, expect } from "../../lib/fixtures";
import { flowAnnotation } from "../../lib/flow-meta";
import {
  seedNative,
  browserErrors,
  shot,
  UI_READY,
  LIST,
  SESSIONS,
  type TracePage,
  type SessionPage,
} from "./native-source";
import { nativeCaptureStages } from "./native-capture-stages";

test.use({ viewport: { width: 1440, height: 900 } });

test(
  "OBS-E2E-054: native source accounting retains exact usage and known, zero and unknown costs",
  {
    tag: ["@flow"],
    annotation: flowAnnotation({
      id: "OBS-E2E-054",
      area: "observe",
      userGoal:
        "A developer trusts native trace accounting and can filter and sort costs without treating unknown as zero",
      steps: [
        "read parent-only native trace and session list coverage, deliver children later, then replay identical IDs",
        "ingest synthetic Copilot and Gemini trees plus a legacy control, including duplicate delivery",
        "verify exact source identities, normalized counters and session grouping in storage",
        "read exact trace membership and known, zero and unknown costs in the desktop grid",
        "filter by trace name and by zero or positive cost",
        "sort positive costs and the full set both ways, replaying each captured API request",
      ],
      backendChecks: [
        "root-only, late-child and replay lists keep observed request counts 0, 3, 3 with coverage unknown and unchanged source summary presence",
        "source IDs, parents, normalization profiles, summary counters and sanitized event attributes persist exactly",
        "trace API totals and grid distinguish zero from unknown costs",
        "session fallback and explicit identities match exact source membership",
        "filtered and sorted API, replay and DOM IDs match independent expected sets and order, with exact counts and no lower-bound marker",
      ],
    }),
  },
  async ({ page, request, actor, probe }, testInfo) => {
    // Eight SPAN_VISIBLE polls (120s), 20 UI_READY action chains, 90s I/O headroom.
    test.setTimeout(120_000 + 20 * UI_READY + 90_000);
    page.setDefaultTimeout(UI_READY);
    const checkBrowser = browserErrors(page, testInfo);
    try {
      // Pin list-specific UI readback, not merely a detail warning. The stored
      // source totals remain 42925/146/17 while observed requests change 0→3.
      for await (const stage of nativeCaptureStages(
        request,
        actor,
        probe,
        testInfo,
      )) {
        await test.step(`native capture lists ${stage.stage}`, async () => {
          const coverage = {
            scope: "observed_requests",
            source_summary_present: true,
            request_count: stage.count,
            coverage_unknown: true,
          };
          const traces = await actor.api.get<TracePage>(LIST, {
            project_id: stage.projectId,
            page_number: 0,
            page_size: 25,
            filters: "[]",
          });
          expect(traces.result.table.map((r) => r.trace_id).sort()).toEqual(
            [stage.traceId, stage.legacyTraceId].sort(),
          );
          expect(traces.result.trace_capture_coverage).toEqual({
            [stage.projectId]: { [stage.traceId]: coverage },
          });
          const row = traces.result.table.find(
            (r) => r.trace_id === stage.traceId,
          )!;
          expect([row.total_tokens, row.cost]).toEqual([
            stage.tokens,
            stage.cost,
          ]);
          const legacy = traces.result.table.find(
            (r) => r.trace_id === stage.legacyTraceId,
          )!;
          expect([legacy.total_tokens, legacy.cost]).toEqual([10, 0.05]);
          const sessions = await actor.api.get<SessionPage>(SESSIONS, {
            project_id: stage.projectId,
            page_number: 0,
            page_size: 25,
          });
          const session = sessions.result.table.find(
            (r) => r.session_id === stage.sessionId,
          )!;
          expect([
            session.total_tokens,
            session.total_cost,
            session.total_traces_count,
          ]).toEqual([stage.tokens, stage.cost, 1]);
          expect(session.capture_coverage).toEqual(coverage);
          await testInfo.attach(`capture-lists-${stage.stage}`, {
            body: JSON.stringify({ traces, sessions }),
            contentType: "application/json",
          });
          const costLabel =
            stage.cost === 0
              ? "Observed-only · $0.00"
              : "Observed-only · Unknown";
          const tokenLabel =
            stage.tokens === 0
              ? "Observed-only · 0 tokens"
              : "Observed-only · 43,071 tokens";
          for (const [path, rowId, costCol, tokenCol] of [
            ["llm-tracing?tab=traces", stage.traceId, "cost", "total_tokens"],
            ["sessions", stage.sessionId, "total_cost", "total_tokens"],
          ]) {
            await page.goto(`/dashboard/observe/${stage.projectId}/${path}`, {
              waitUntil: "domcontentloaded",
            });
            const identityCol =
              costCol === "cost" ? "trace_name" : "session_id";
            await expect(
              page.locator(
                `.clean-data-table:visible .ag-row[row-id="${rowId}"] [col-id="${identityCol}"]`,
              ),
            ).toBeVisible({ timeout: UI_READY });
            // Session tokens are hidden by default (helper.py); use the actual
            // Display picker and keep only three columns to avoid virtualization.
            await page
              .getByRole("button", { name: "Display", exact: true })
              .click();
            await page.getByText("View columns", { exact: true }).click();
            const popup = page.locator(".MuiPopover-paper:visible").filter({
              has: page.getByPlaceholder("Search", { exact: true }),
            });
            const labels =
              costCol === "cost"
                ? ["Trace Name", "Tokens", "Total Cost"]
                : ["Session Id", "Total Tokens", "Total Cost"];
            await expect(
              popup.getByText(labels[0], { exact: true }),
            ).toBeVisible({ timeout: UI_READY });
            const all = popup.getByRole("checkbox", {
              name: "Toggle Select all",
              exact: true,
            });
            await all.check();
            await all.uncheck();
            for (const label of labels)
              await popup
                .getByText(label, { exact: true })
                .locator("..")
                .getByRole("checkbox")
                .check();
            await expect(
              popup.getByRole("checkbox", { checked: true }),
            ).toHaveCount(3, { timeout: UI_READY });
            await page.keyboard.press("Escape");
            await page.keyboard.press("Escape");
            await expect(
              page.getByText("View columns", { exact: true }),
            ).toBeHidden({ timeout: UI_READY });
            const cost = page.locator(
              `.clean-data-table:visible .ag-row[row-id="${rowId}"] [col-id="${costCol}"]`,
            );
            await expect(cost).toHaveText(costLabel, { timeout: UI_READY });
            await expect(
              page.locator(
                `.clean-data-table:visible .ag-row[row-id="${rowId}"] [col-id="${tokenCol}"]`,
              ),
            ).toHaveText(tokenLabel, { timeout: UI_READY });
            await cost.getByText(costLabel, { exact: true }).hover();
            await expect(page.getByRole("tooltip")).toHaveText(
              `${costLabel}. Capture coverage unknown. ${stage.count} observed requests. Source summaries are nonadditive; observed amounts do not prove complete capture.`,
              { timeout: UI_READY },
            );
            await shot(page, testInfo, `capture-${stage.stage}-${costCol}`);
          }
        });
      }
      const { cases, stored, sessions, projectId } = await seedNative(
        request,
        actor,
        probe,
        testInfo,
      );
      await test.step("storage exact identities, profiles, usage, summary and event truth", async () => {
        for (const c of cases) {
          const rows = stored.filter((s) => s.trace_id === c.traceId);
          expect(
            rows.map((s) => s.parent_span_id),
            c.key,
          ).toEqual(c.parents);
          if (c.key !== "legacy")
            expect(
              rows.map((s) => s.observation_type),
              c.key,
            ).toEqual(c.kinds);
          expect(
            rows.map((s) => s.profile),
            c.key,
          ).toEqual(c.spanIds.map(() => c.profile));
          expect(
            rows.map((s) => s.prompt_tokens),
            c.key,
          ).toEqual(c.prompt);
          expect(
            rows.map((s) => s.completion_tokens),
            c.key,
          ).toEqual(c.completion);
          expect(
            rows.reduce((n, s) => n + s.total_tokens, 0),
            c.key,
          ).toBe(c.total);
          if (c.key.startsWith("copilot"))
            expect([
              rows[0].contribution,
              rows[0].source_prompt,
              rows[0].source_completion,
            ]).toEqual(["none", 42925, 146]);
          if (c.key.startsWith("gemini")) {
            expect([rows[1].input, rows[1].output]).toEqual([
              `${c.name}-input`,
              `${c.name}-output`,
            ]);
            expect(rows[3].span_events).not.toContain(
              "FORBIDDEN-SYNTHETIC-EVENT-TEXT",
            );
            expect(JSON.parse(rows[3].span_events)[0].attributes).toEqual({
              "exception.type": "SyntheticToolError",
            });
          }
        }
      });
      await test.step("session fallback and explicit identity membership", async () => {
        expect(sessions.map((s) => s.external_session_id).sort()).toEqual(
          [...new Set(cases.map((c) => c.session))].sort(),
        );
        for (const c of cases) {
          const sid = sessions.find(
            (s) => s.external_session_id === c.session,
          )?.trace_session_id;
          expect(
            [
              ...new Set(
                stored
                  .filter((s) => s.trace_id === c.traceId)
                  .map((s) => s.trace_session_id),
              ),
            ],
            c.key,
          ).toEqual([sid]);
        }
      });
      await test.step("API trace list exact sets and literal usage/cost totals", async () => {
        const data = await actor.api.get<TracePage>(LIST, {
          project_id: projectId,
          page_number: 0,
          page_size: 25,
          filters: "[]",
        });

        expect(data.result.table.map((r) => r.trace_id).sort()).toEqual(
          cases.map((c) => c.traceId).sort(),
        );
        for (const c of cases) {
          const row = data.result.table.find((r) => r.trace_id === c.traceId)!;
          expect(row?.total_tokens, c.key).toBe(c.total);
          const capture =
            data.result.trace_capture_coverage?.[projectId]?.[c.traceId];
          if (c.key.startsWith("copilot"))
            expect(capture).toEqual({
              scope: "observed_requests",
              source_summary_present: true,
              request_count: 3,
              coverage_unknown: true,
            });
          else expect(capture).toBeUndefined();
          if (c.cost === null) expect(row?.cost, c.key).toBeNull();
          else expect(row?.cost, c.key).toBeCloseTo(c.cost, 8);
        }
      });
      await test.step("UI exact trace set and grid known zero unknown costs", async () => {
        await page.goto(
          `/dashboard/observe/${projectId}/llm-tracing?tab=traces`,
          { waitUntil: "domcontentloaded" },
        );
        const gridIds = () =>
          page
            .locator('.clean-data-table:visible .ag-row [col-id="trace_name"]')
            .evaluateAll((es) =>
              es
                .map((e) => e.closest(".ag-row")?.getAttribute("row-id"))
                .sort(),
            );
        await expect
          .poll(gridIds, { timeout: UI_READY })
          .toEqual(cases.map((c) => c.traceId).sort());
        await page
          .locator(".clean-data-table:visible .ag-center-cols-viewport")
          .hover();
        await page.mouse.wheel(1800, 0);
        // Pin each rendered cost after the complete row set has loaded.
        await expect(
          page.locator(
            `.clean-data-table:visible .ag-row[row-id="${cases[0].traceId}"] [col-id="cost"]`,
          ),
        ).toBeVisible({ timeout: UI_READY });
        await shot(page, testInfo, "trace-list-costs");

        for (const c of cases) {
          const text = await page
            .locator(
              `.clean-data-table:visible .ag-row[row-id="${c.traceId}"] [col-id="cost"]`,
            )
            .innerText();

          // CostCell.jsx: amounts 0.01..0.99 render four decimals, unlike the drawer.
          const gridExpected =
            c.cost === null
              ? "-"
              : c.cost === 0
                ? "$0.00"
                : c.cost === 0.6
                  ? "$0.6000"
                  : c.cost === 0.5
                    ? "$0.5000"
                    : "$0.0500";
          expect(text, c.key).toBe(
            c.key.startsWith("copilot")
              ? `Observed-only · ${c.cost === null ? "Unknown" : gridExpected}`
              : gridExpected,
          );
        }
      });
      await test.step("UI applied trace name filter exact membership", async () => {
        const c = cases[0];
        await page.goto(
          `/dashboard/observe/${projectId}/llm-tracing?tab=traces`,
          { waitUntil: "domcontentloaded" },
        );
        await page.getByRole("button", { name: "Filter", exact: true }).click();
        await page
          .getByRole("button", { name: "Property", exact: true })
          .first()
          .click();
        // TraceFilterPanel.jsx DEFAULT_FIELDS uses name (mapped to trace_name on wire).
        await page.locator('[data-filter-property-option="name"]').click();
        await page.locator('[data-filter-value-trigger="name"]').click();
        await page.getByPlaceholder("Search values...").fill(c.name);
        const pending = page.waitForResponse(
          (r) =>
            r.url().includes(LIST) &&
            r.request().method() !== "OPTIONS" &&
            (r.request().postData()?.includes(c.name) ||
              decodeURIComponent(r.url()).includes(c.name)),
          { timeout: UI_READY },
        );
        await page
          .locator(`[data-filter-value-option="${c.name}"][role="checkbox"]`)
          .click();
        await page.keyboard.press("Escape");
        await expect(page.getByPlaceholder("Search values...")).toBeHidden({
          timeout: UI_READY,
        });
        await page.keyboard.press("Escape");
        await expect(
          page.getByRole("tab", { name: "Basic", exact: true }),
        ).toBeHidden({ timeout: UI_READY });
        const response = await pending;
        expect(response.status()).toBe(200);
        const body: TracePage = await response.json();
        expect(body.result.table.map((r) => r.trace_id)).toEqual([c.traceId]);
        await expect(
          page.locator(
            '.clean-data-table:visible .ag-row [col-id="trace_name"]',
          ),
        ).toHaveText([c.name], {
          timeout: UI_READY,
        });
        await shot(page, testInfo, "applied-trace-filter");
      });
      const orderedGrid = () =>
        page
          .locator(
            ".clean-data-table:visible .ag-center-cols-container .ag-row[row-id]",
          )
          .evaluateAll((rows) =>
            rows
              .sort(
                (a, b) =>
                  Number(a.getAttribute("row-index")) -
                  Number(b.getAttribute("row-index")),
              )
              .map((r) => r.getAttribute("row-id")),
          );
      const closeFilterPopovers = async () => {
        // Each Escape closes the current real picker/menu layer; cleanup also runs
        // after assertion failure so a stale backdrop cannot block later sorts.
        for (let i = 0; i < 4; i++) {
          if ((await page.locator(".MuiPopover-paper:visible").count()) === 0)
            break;
          await page.keyboard.press("Escape");
        }
        await expect(page.locator(".MuiPopover-paper:visible")).toHaveCount(0, {
          timeout: UI_READY,
        });
      };
      for (const [op, label, keys] of [
        ["equals", "equals", ["copilot-zero"]],
        [
          "greater_than",
          "greater than",
          ["copilot-known-1", "copilot-known-2", "gemini-known", "legacy"],
        ],
      ] as const) {
        await test.step(`UI native cost ${op} zero exact membership count and API replay`, async () => {
          await page.goto(
            `/dashboard/observe/${projectId}/llm-tracing?tab=traces`,
            { waitUntil: "domcontentloaded" },
          );
          await expect
            .poll(async () => (await orderedGrid()).sort(), {
              timeout: UI_READY,
            })
            .toEqual(cases.map((c) => c.traceId).sort());
          try {
            await page
              .getByRole("button", { name: "Filter", exact: true })
              .click();
            await page
              .getByRole("button", { name: "Property", exact: true })
              .click();
            await page.getByPlaceholder("Search properties...").fill("Cost");
            const costOption = page.locator(
              '[data-filter-property-option="cost"]',
            );
            await expect(
              costOption.getByText("Cost", { exact: true }),
            ).toBeVisible({ timeout: UI_READY });
            await shot(page, testInfo, `native-cost-picker-${op}`);
            await costOption.click();
            const panel = page.locator(".MuiPopover-paper:visible").filter({
              has: page.getByRole("tab", { name: "Basic", exact: true }),
            });
            await expect(panel.getByRole("combobox")).toHaveCount(1);
            await panel.getByRole("combobox").click();
            await page
              .getByRole("option", { name: label, exact: true })
              .click();
            const pending = page.waitForResponse(
              (r) => {
                if (
                  new URL(r.url()).pathname !== LIST ||
                  r.request().method() !== "POST"
                )
                  return false;
                const q = r.request().postDataJSON();
                return JSON.parse(q.filters).some(
                  (f: {
                    column_id: string;
                    filter_config: { filter_op: string; filter_value: number };
                  }) =>
                    f.column_id === "cost" &&
                    f.filter_config.filter_op === op &&
                    f.filter_config.filter_value === 0,
                );
              },
              { timeout: UI_READY },
            );
            await page.getByPlaceholder("Value", { exact: true }).fill("0");
            await page.keyboard.press("Tab");
            const response = await pending;
            expect(response.status()).toBe(200);
            const body: TracePage = await response.json(),
              query = response.request().postDataJSON();
            const replay = await actor.api.post<TracePage>(LIST, query);
            await testInfo.attach(`cost-filter-${op}`, {
              body: JSON.stringify({
                query,
                result: body.result,
                replay: replay.result,
              }),
              contentType: "application/json",
            });
            const expected = cases
              .filter((c) => (keys as readonly string[]).includes(c.key))
              .map((c) => c.traceId)
              .sort();

            expect(body.result.table.map((r) => r.trace_id).sort()).toEqual(
              expected,
            );
            expect(replay.result.table.map((r) => r.trace_id).sort()).toEqual(
              expected,
            );
            expect(body.result.metadata.total_rows).toBe(keys.length);
            expect(body.result.metadata.total_rows_is_lower_bound).toBe(false);
            expect(replay.result.metadata.total_rows).toBe(keys.length);
            expect(replay.result.metadata.total_rows_is_lower_bound).toBe(
              false,
            );
            await closeFilterPopovers();
            await expect
              .poll(async () => (await orderedGrid()).sort(), {
                timeout: UI_READY,
              })
              .toEqual(expected);
            await shot(page, testInfo, `native-cost-filter-${op}`);
          } finally {
            await closeFilterPopovers();
          }
        });
      }
      // Sorted queries return exact counts and omit the bounded-scan marker.
      // Keep literal total_rows/IDs/order assertions; reject true or invalid markers.
      for (const direction of ["asc", "desc"] as const) {
        await test.step(`UI native positive cost ${direction} sort exact order and count`, async () => {
          await page
            .locator(".clean-data-table:visible .ag-center-cols-viewport")
            .hover();
          await page.mouse.wheel(1800, 0);
          const pending = page.waitForResponse(
            (r) => {
              if (
                new URL(r.url()).pathname !== LIST ||
                r.request().method() !== "POST"
              )
                return false;
              const sort = JSON.parse(
                r.request().postDataJSON().sort_params || "[]",
              )[0];
              return (
                sort?.column_id === "cost" && sort?.direction === direction
              );
            },
            { timeout: UI_READY },
          );
          await page
            .getByRole("button", { name: "Sort Total Cost", exact: true })
            .click();
          const response = await pending;
          expect(response.status()).toBe(200);
          const body: TracePage = await response.json(),
            query = response.request().postDataJSON(),
            replay = await actor.api.post<TracePage>(LIST, query);
          await testInfo.attach(
            `cost-sort-${direction}-${body.result.metadata.total_rows}`,
            {
              body: JSON.stringify({
                query,
                result: body.result,
                replay: replay.result,
              }),
              contentType: "application/json",
            },
          );
          const keys =
            direction === "asc"
              ? ["legacy", "gemini-known", "copilot-known-2", "copilot-known-1"]
              : [
                  "copilot-known-2",
                  "copilot-known-1",
                  "gemini-known",
                  "legacy",
                ];
          const expected = keys.map(
            (key) => cases.find((c) => c.key === key)!.traceId,
          );

          expect(body.result.table.map((r) => r.trace_id)).toEqual(expected);
          expect(replay.result.table.map((r) => r.trace_id)).toEqual(expected);
          expect(body.result.metadata.total_rows).toBe(4);
          expect([undefined, false]).toContain(
            body.result.metadata.total_rows_is_lower_bound,
          );
          expect(replay.result.metadata.total_rows).toBe(4);
          expect([undefined, false]).toContain(
            replay.result.metadata.total_rows_is_lower_bound,
          );
          await expect
            .poll(orderedGrid, { timeout: UI_READY })
            .toEqual(expected);
          await shot(page, testInfo, `native-positive-cost-sort-${direction}`);
        });
      }
      // Also exercise the full native set, including zero and both unknown costs.
      // Cost is ordered by the compiler, with start_time DESC as deterministic tie
      // breaker (observe_usage.py); ClickHouse default places NULL last both ways.
      await page.goto(
        `/dashboard/observe/${projectId}/llm-tracing?tab=traces`,
        { waitUntil: "domcontentloaded" },
      );
      await expect
        .poll(async () => (await orderedGrid()).sort(), { timeout: UI_READY })
        .toEqual(cases.map((c) => c.traceId).sort());
      for (const direction of ["asc", "desc"] as const) {
        await test.step(`UI native all costs ${direction} exact order null placement count and API replay`, async () => {
          await page
            .locator(".clean-data-table:visible .ag-center-cols-viewport")
            .hover();
          await page.mouse.wheel(1800, 0);
          const pending = page.waitForResponse(
            (r) => {
              if (
                new URL(r.url()).pathname !== LIST ||
                r.request().method() !== "POST"
              )
                return false;
              const sort = JSON.parse(
                r.request().postDataJSON().sort_params || "[]",
              )[0];
              return (
                sort?.column_id === "cost" && sort?.direction === direction
              );
            },
            { timeout: UI_READY },
          );
          await page
            .getByRole("button", { name: "Sort Total Cost", exact: true })
            .click();
          const response = await pending;
          expect(response.status()).toBe(200);
          const body: TracePage = await response.json(),
            query = response.request().postDataJSON(),
            replay = await actor.api.post<TracePage>(LIST, query);
          await testInfo.attach(
            `cost-sort-${direction}-${body.result.metadata.total_rows}`,
            {
              body: JSON.stringify({
                query,
                result: body.result,
                replay: replay.result,
              }),
              contentType: "application/json",
            },
          );
          expect(
            JSON.parse(query.filters).filter(
              (f: {
                column_id: string;
                filter_config: { filter_op: string; filter_value: number };
              }) => f.column_id === "cost",
            ),
          ).toEqual([]);
          const keys =
            direction === "asc"
              ? [
                  "copilot-zero",
                  "legacy",
                  "gemini-known",
                  "copilot-known-2",
                  "copilot-known-1",
                  "gemini-unknown",
                  "copilot-unknown",
                ]
              : [
                  "copilot-known-2",
                  "copilot-known-1",
                  "gemini-known",
                  "legacy",
                  "copilot-zero",
                  "gemini-unknown",
                  "copilot-unknown",
                ];
          const expected = keys.map(
            (key) => cases.find((c) => c.key === key)!.traceId,
          );

          for (const result of [body.result, replay.result]) {
            expect(result.table.map((r) => r.trace_id)).toEqual(expected);
            expect(result.metadata.total_rows).toBe(7);
            expect([undefined, false]).toContain(
              result.metadata.total_rows_is_lower_bound,
            );
            for (const c of cases) {
              const r = result.table.find((r) => r.trace_id === c.traceId)!;
              if (c.cost === null) expect(r.cost).toBeNull();
              else expect(r.cost).toBeCloseTo(c.cost, 8);
            }
          }
          await expect
            .poll(orderedGrid, { timeout: UI_READY })
            .toEqual(expected);
          await shot(page, testInfo, `native-all-cost-sort-${direction}`);
        });
      }
    } finally {
      await checkBrowser();
    }
  },
);
