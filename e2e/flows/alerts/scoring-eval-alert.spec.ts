import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// Endpoints pinned off frontend/src/utils/axios.js (endpoints.project.*):
//   createEvalTemplateV2, createEvalTaskConfig, createMonitor.
const PROJECT_PATH = '/tracer/project/';
const EVAL_TEMPLATE_PATH = '/model-hub/eval-templates/create-v2/';
const EVAL_CONFIG_PATH = '/tracer/custom-eval-config/';
const MONITOR_PATH = '/tracer/user-alerts/';

// The alerts list route (frontend/src/routes/sections/dashboard.jsx:1281). The
// list is org-wide and searchable by alert name (Actions.jsx:88 placeholder).
const ALERTS_URL = '/dashboard/alerts';

// Browser-side wait. Two name searches at UI_READY back to back can approach the
// 120s per-test default, so the test raises its own ceiling below.
const UI_READY = 60_000;

// Serializer-side messages the eval-alert validator returns
// (futureagi/tracer/serializers/monitor.py:100-123). A score eval measures the
// mean score, so a stored choice is rejected; a choices/pass-fail eval requires
// one.
const MSG_MUST_BE_EMPTY = 'must be empty for evals without predefined choices';
const MSG_REQUIRED = 'required for evals with predefined choices';

test(
  'ALERT-E2E-002: a scoring-eval alert measures the score while a labelled eval alerts on a chosen label',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'ALERT-E2E-002',
      area: 'alerts',
      userGoal:
        'A user sets up an alert on a scoring evaluation and it measures the score itself, while a labelled evaluation still alerts on a chosen label',
      steps: [
        'seed a scoring eval (with labels) and a choices eval on a new project',
        'create an alert on the scoring eval with no chosen label',
        'try to create an alert on the scoring eval with a label',
        'try to create an alert on the choices eval with no label',
        'create an alert on the choices eval with a valid label',
        'open the alerts list and read each alert’s Alert Type',
      ],
      backendChecks: [
        'scoring-eval alert with no label is created (POST /tracer/user-alerts/ 201)',
        'scoring-eval alert with a label is rejected: must be empty for evals without predefined choices',
        'choices-eval alert with no label is rejected: required for evals with predefined choices',
        'choices-eval alert with a valid label is created',
        'PG tracer_useralertmonitor: the scoring monitor’s threshold_metric_value is NULL, the choices monitor’s is the label, both org-scoped',
        'alerts list shows the scoring alert’s Alert Type as the bare eval name and the choices alert’s as "name (label)"',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    // Only UI_READY waits, but two sequential name searches can reach ~120s
    // alongside seeding; give this test its own ceiling.
    test.setTimeout(180_000);

    const stamp = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const scoreEvalName = `e2e-eval-score-${stamp}`;
    const choiceEvalName = `e2e-eval-choice-${stamp}`;
    const scoreAlertName = `e2e-alert-score-${stamp}`;
    const choiceAlertName = `e2e-alert-choice-${stamp}`;
    const CHOICE_LABEL = 'frequently';

    const seeded = await test.step(
      'seed: a project with a scoring eval (labelled) and a choices eval',
      async () => {
        // ProjectSerializer requires model_type (futureagi/tracer/models/project.py).
        const project = await actor.api.post<{ result: { project_id: string } }>(
          PROJECT_PATH,
          {
            name: `e2e-alert-proj-${stamp}`,
            trace_type: 'observe',
            model_type: 'GenerativeLLM',
          },
        );
        const projectId = project.result.project_id;

        // create-v2 maps output_type -> config.output: 'percentage' -> "score",
        // 'deterministic' -> "choices"; choice_scores becomes the template's
        // choices list (futureagi/model_hub/views/separate_evals.py:2974-3037).
        // So the scoring template lands as output="score" WITH labels — the
        // exact TH-7788 shape.
        const mkTemplate = async (
          name: string,
          outputType: 'percentage' | 'deterministic',
        ) =>
          (
            await actor.api.post<{ result: { id: string } }>(EVAL_TEMPLATE_PATH, {
              name,
              eval_type: 'llm',
              instructions: 'Rate the response. {{input}}',
              model: 'turing_large',
              output_type: outputType,
              pass_threshold: 0.5,
              choice_scores:
                outputType === 'percentage'
                  ? { Complete: 1.0, Partial: 0.5, Incomplete: 0.0 }
                  : { never: 0.0, occasionally: 0.3, frequently: 0.7, always: 1.0 },
            })
          ).result.id;

        // CustomEvalConfigSerializer (futureagi/tracer/serializers/custom_eval_config.py).
        // The alert's Alert Type text is this config's name (get_metric_name).
        const mkConfig = async (templateId: string, name: string) =>
          (
            await actor.api.post<{ result: { id: string } }>(EVAL_CONFIG_PATH, {
              eval_template: templateId,
              name,
              project: projectId,
              config: {},
              mapping: {},
              filters: {},
              error_localizer: false,
            })
          ).result.id;

        const scoreTpl = await mkTemplate(scoreEvalName, 'percentage');
        const choiceTpl = await mkTemplate(choiceEvalName, 'deterministic');
        const scoreCfgId = await mkConfig(scoreTpl, scoreEvalName);
        const choiceCfgId = await mkConfig(choiceTpl, choiceEvalName);
        return { projectId, scoreCfgId, choiceCfgId };
      },
    );
    await testInfo.attach('seeded', {
      body: JSON.stringify({ ...seeded, scoreAlertName, choiceAlertName }),
      contentType: 'application/json',
    });

    // Wire body from UserAlertMonitorSerializer; values mirror a monitor created
    // through the UI (threshold_type "static", numeric critical<warning).
    const monitorBody = (name: string, metric: string, tmv?: string) => ({
      name,
      project: seeded.projectId,
      metric_type: 'evaluation_metrics',
      metric,
      threshold_type: 'static',
      threshold_operator: 'less_than',
      critical_threshold_value: 0.4,
      warning_threshold_value: 0.6,
      alert_frequency: 5,
      notification_emails: ['oncall@example.com'],
      filters: {},
      ...(tmv ? { threshold_metric_value: tmv } : {}),
    });

    await test.step('API: a scoring-eval alert is created with no chosen label', async () => {
      await actor.api.post(MONITOR_PATH, monitorBody(scoreAlertName, seeded.scoreCfgId));
    });

    await test.step('API: a scoring-eval alert with a label is rejected', async () => {
      await expect(
        actor.api.post(
          MONITOR_PATH,
          monitorBody(`${scoreAlertName}-bad`, seeded.scoreCfgId, 'Incomplete'),
        ),
      ).rejects.toThrow(new RegExp(MSG_MUST_BE_EMPTY));
    });

    await test.step('API: a choices-eval alert with no label is rejected', async () => {
      await expect(
        actor.api.post(
          MONITOR_PATH,
          monitorBody(`${choiceAlertName}-bad`, seeded.choiceCfgId),
        ),
      ).rejects.toThrow(new RegExp(MSG_REQUIRED));
    });

    await test.step('API: a choices-eval alert is created with a valid label', async () => {
      await actor.api.post(
        MONITOR_PATH,
        monitorBody(choiceAlertName, seeded.choiceCfgId, CHOICE_LABEL),
      );
    });

    await test.step('storage: the scoring monitor stores no choice, the choices monitor stores the label', async () => {
      const rows = await probe.pg<{
        name: string;
        threshold_metric_value: string | null;
      }>(
        `SELECT name, threshold_metric_value FROM tracer_useralertmonitor
         WHERE name IN ($1, $2) AND organization_id = $3 AND deleted = false
         ORDER BY name`,
        [choiceAlertName, scoreAlertName, actor.organizationId],
      );
      // ORDER BY name: 'e2e-alert-choice-…' sorts before 'e2e-alert-score-…'.
      expect(rows).toHaveLength(2);
      expect(rows[0].name).toBe(choiceAlertName);
      expect(rows[0].threshold_metric_value).toBe(CHOICE_LABEL);
      expect(rows[1].name).toBe(scoreAlertName);
      expect(rows[1].threshold_metric_value).toBeNull();
    });

    await test.step('UI: the alerts list shows the score alert bare and the choices alert suffixed', async () => {
      await page.goto(ALERTS_URL, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(/\/dashboard\/alerts/, { timeout: UI_READY });

      const search = page.getByPlaceholder('Search').first();
      const alertTypeOf = (alertName: string) =>
        page
          .locator('.MuiDataGrid-row', {
            has: page.locator(`[data-alert-row-name="${alertName}"]`),
          })
          .locator('[data-field="metricType"]');

      await search.fill(scoreAlertName);
      await expect(
        page.locator(`[data-alert-row-name="${scoreAlertName}"]`),
      ).toBeVisible({ timeout: UI_READY });
      // Bare eval name, no " (label)" suffix — the TH-7788 display fix.
      await expect(alertTypeOf(scoreAlertName)).toHaveText(scoreEvalName, {
        timeout: UI_READY,
      });

      await search.fill(choiceAlertName);
      await expect(
        page.locator(`[data-alert-row-name="${choiceAlertName}"]`),
      ).toBeVisible({ timeout: UI_READY });
      await expect(alertTypeOf(choiceAlertName)).toHaveText(
        `${choiceEvalName} (${CHOICE_LABEL})`,
        { timeout: UI_READY },
      );
    });
  },
);
