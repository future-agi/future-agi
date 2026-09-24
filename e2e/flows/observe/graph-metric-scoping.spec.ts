import { request, type APIRequestContext } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import type { TestActor } from '../../lib/provisioning';
import { sendTrace } from '../../lib/otlp';
import { POLL, type StateProbe } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// playwright.config.ts sets expect timeout to 10s, which this flow's UI step can
// outrun: it waits on a cold metrics catalog, whose span-attribute discovery
// carries a 15s server-side budget on its own. Both other observe flows use the
// same constant for the same reason. The popover renders *empty* while the
// catalog is in flight, so an under-budgeted wait looks identical to a broken fix.
const UI_READY = 60_000;

interface CreatedId { result: { id: string } }
interface MetricsEnvelope {
  result: { metrics: Array<{ category: string; display_name: string }> };
}

/** Seed a trace under a fresh project name and return the project id the collector created. */
async function seedProject(
  req: APIRequestContext,
  actor: TestActor,
  probe: StateProbe,
  projectName: string,
): Promise<string> {
  const seeded = await sendTrace(req, {
    collectorUrl: E2E.collectorUrl,
    apiKey: actor.apiKey,
    secretKey: actor.secretKey,
    projectName,
  });
  await expect
    .poll(async () => {
      const rows = await probe.ch<{ n: string }>(
        'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}',
        { t: seeded.traceId },
      );
      return Number(rows[0].n);
    }, POLL.SPAN_VISIBLE)
    .toBe(seeded.spanIds.length);

  const [{ project_id: projectId }] = await probe.ch<{ project_id: string }>(
    'SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id = {t:String}',
    { t: seeded.traceId },
  );
  return projectId;
}

test('OBS-E2E-003: graph metric picker lists only the current project\'s evals', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-003',
    area: 'observe',
    userGoal:
      'A developer picking a metric to graph sees only the evals attached to the project they are viewing',
    steps: [
      'seed two projects in one org',
      'attach a distinctly-named eval to each',
      "open the first project's Observe graph view",
      'open the metric picker',
      'search the picker for the seeded eval names',
      'read the EVALS section',
    ],
    backendChecks: [
      "the metrics catalog scoped to each project returns that project's eval template and not its sibling's",
      'the frontend requests the catalog with project_ids set to the project being viewed',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Two collector round-trips (SPAN_VISIBLE, 15s each), four API creates, two
  // cold catalog builds, an SPA boot and three UI_READY-budgeted waits. The
  // harness precedent is 240s for OBS-E2E-002 and 300s for OBS-E2E-001, both
  // lighter than this. Under-budgeting means the outer timeout fires first and
  // hides which assertion actually ran out.
  test.setTimeout(240_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const mineName = `e2e-scope-mine-${suffix}`;
  const otherName = `e2e-scope-other-${suffix}`;

  const mineProjectId = await seedProject(req, actor, probe, `e2e-scope-a-${suffix}`);
  const otherProjectId = await seedProject(req, actor, probe, `e2e-scope-b-${suffix}`);
  expect(mineProjectId).not.toBe(otherProjectId);

  await test.step('attach one distinctly-named eval to each project', async () => {
    for (const [name, projectId] of [
      [mineName, mineProjectId],
      [otherName, otherProjectId],
    ] as const) {
      // eval_type 'code' needs no model and never calls a provider: this flow
      // only cares that the template is attached, never that it runs. The name
      // must satisfy ^[a-z0-9_-]+$ with no leading/trailing/consecutive
      // separators, so keep the suffix lowercase alphanumeric.
      const template = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
        name,
        eval_type: 'code',
        // Required: the view rejects a non-draft code eval with no body
        // ("Code is required for code-type evaluations."). `instructions` is
        // ignored for code evals. Never executed — the e2e stack does not start
        // code-executor.
        code: 'def main(**kwargs):\n    return True',
        code_language: 'python',
        output_type: 'pass_fail',
      });
      await actor.api.post<CreatedId>('/tracer/custom-eval-config/', {
        project: projectId,
        eval_template: template.result.id,
        name,
        config: { mapping: {} },
        mapping: {},
        error_localizer: false,
      });
    }
  });

  await test.step('the scoped catalog separates them (API lane)', async () => {
    const evalNames = async (projectId: string) => {
      const body = await actor.api.get<MetricsEnvelope>('/tracer/dashboard/metrics/', {
        project_ids: projectId,
      });
      return body.result.metrics
        .filter((m) => m.category === 'eval_metric')
        .map((m) => m.display_name);
    };

    // Asserted symmetrically rather than against the unscoped catalog: the
    // unscoped cache key is shared with every other flow in this worker and
    // lives 60s, so reading it here would flake. Each project seeing its own
    // eval and not its sibling's proves the same thing without touching it.
    const mine = await evalNames(mineProjectId);
    expect(mine).toContain(mineName);
    expect(mine).not.toContain(otherName);

    const other = await evalNames(otherProjectId);
    expect(other).toContain(otherName);
    expect(other).not.toContain(mineName);
  });

  await test.step("the picker shows only this project's eval (UI lane)", async () => {
    // `selectedTab` is the only real URL key here: viewMode lives in Zustand and
    // already defaults to "graph", and `tab` is not a URL key.
    await page.goto(`/dashboard/observe/${mineProjectId}/llm-tracing?selectedTab=trace`, {
      waitUntil: 'domcontentloaded',
    });

    // The picker intentionally lazy-loads its bounded catalog when opened. Arm
    // both waits immediately before the click: one proves the canonical eval
    // page is attempted, while the other accepts the successful canonical page
    // or the rollout-only legacy page after a typed catalog-not-ready response.
    // The E2E stack deliberately leaves the property-catalog read gate off, so
    // requiring the canonical attempt itself to return 200 would reject the
    // compatibility path that this UI is designed to use.
    const pickerTrigger = page.getByTestId('graph-metric-picker-trigger').first();
    await expect(pickerTrigger).toBeVisible({ timeout: UI_READY });
    const canonicalAttempt = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname.endsWith('/tracer/dashboard/metrics/') &&
        url.searchParams.get('project_ids') === mineProjectId &&
        url.searchParams.get('cursor_mode') === 'true' &&
        url.searchParams.get('per_eval_config') === 'true' &&
        url.searchParams.get('category') === 'eval_metric'
      );
    }, { timeout: UI_READY });
    const readyCatalog = page.waitForResponse((response) => {
      const url = new URL(response.url());
      const canonicalEvalPage =
        url.searchParams.get('cursor_mode') === 'true' &&
        url.searchParams.get('category') === 'eval_metric';
      const legacyFallbackPage =
        !url.searchParams.has('cursor_mode') &&
        url.searchParams.get('exclude_custom_attributes') === 'true';
      return (
        url.pathname.endsWith('/tracer/dashboard/metrics/') &&
        url.searchParams.get('project_ids') === mineProjectId &&
        url.searchParams.get('per_eval_config') === 'true' &&
        response.ok() &&
        (canonicalEvalPage || legacyFallbackPage)
      );
    }, { timeout: UI_READY });
    await pickerTrigger.click();
    await Promise.all([canonicalAttempt, readyCatalog]);

    await expect(page.getByPlaceholder('Search metrics...')).toBeVisible({ timeout: UI_READY });

    // Typing narrows to the minted names only, so neither assertion can be
    // satisfied by an unrelated row and neither needs a scroll. The picker
    // filters on the label, which is the EvalTemplate name verbatim.
    await page.getByPlaceholder('Search metrics...').fill('e2e-scope-');

    // The positive assertion gates the negative one: without it, toHaveCount(0)
    // would pass vacuously against a picker that never finished loading.
    await expect(page.getByRole('button', { name: mineName })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('button', { name: otherName })).toHaveCount(0);
  });

  await req.dispose();
});
