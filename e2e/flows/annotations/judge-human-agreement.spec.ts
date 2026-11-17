import { test, expect } from "../../lib/fixtures";
import { sendTrace } from "../../lib/otlp";
import { E2E } from "../../lib/env";
import { POLL } from "../../lib/state-probe";
import { flowAnnotation } from "../../lib/flow-meta";

// model_hub/serializers/annotation_queues.py and views/annotation_queues.py.
const QUEUES = "/model-hub/annotation-queues/";
// model_hub/serializers/{develop_annotations,scores}.py.
const LABELS = "/model-hub/annotations-labels/";
const SCORES = "/model-hub/scores/bulk/";
// stack/gateway.e2e.yaml and docker-compose.distributed.yml: worker-side address.
const MODEL = "gpt-4o-mini";
const GATEWAY = "http://agentcc-gateway:8080/v1";
const KEY = "local-dev-only-shared-secret-replace-me";
const UI_READY = 60_000;
type Created = { result: { id: string } };
type Agreement = {
  overall_agreement: number | null;
  labels: Record<string, unknown>;
  annotator_pairs: unknown[];
  judge_vs_human: null | {
    evaluator_name: string;
    overall_agreement: number | null;
    total_comparisons: number;
    labels: Record<string, unknown>;
  };
};

test(
  "ANNOT-E2E-005: a completed queue compares its selected judge with human labels",
  {
    tag: ["@flow"],
    annotation: flowAnnotation({
      id: "ANNOT-E2E-005",
      area: "annotations",
      userGoal:
        "Select a queue evaluator and read completed judge-versus-human agreement without changing human-human agreement",
      steps: [
        "mint a project, two traces, a Pass/Fail label, a queue and a mock-backed evaluator through supported APIs",
        "select the evaluator in queue Settings, save and reload",
        "annotate the two spans through the browser with Pass and Fail",
        "run the evaluator on the project and complete the queue through Settings",
        "read exactly 50.0% agreement and two comparable item-label pairs",
      ],
      backendChecks: [
        "the exact minted project and source spans retain their organization and workspace identities",
        "the browser-selected evaluator persists on the exact queue in the detail API and Postgres after reload",
        "the two human Scores and completed evaluator results retain exact queue, span, label and verdict identities in Postgres and latest ClickHouse",
        "the completed agreement API returns exactly one match in two comparisons while human-human fields remain unchanged",
      ],
    }),
  },
  async ({ page, request, actor, probe }, testInfo) => {
    // SPAN_VISIBLE 15 + EVAL_RESULT 90 + CDC_VISIBLE 180 + 9 UI_READY 540 + 75 headroom.
    test.setTimeout(900_000);
    page.setDefaultTimeout(UI_READY);
    const prefix = `e2e-annot5-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const seeds: Awaited<ReturnType<typeof sendTrace>>[] = [];
    for (const outcome of ["Pass", "Fail"])
      seeds.push(
        await sendTrace(request, {
          collectorUrl: E2E.collectorUrl,
          apiKey: actor.apiKey,
          secretKey: actor.secretKey,
          projectName: prefix,
          rootName: `${prefix}-${outcome}-root`,
          childName: `${prefix}-${outcome}`,
          resourceAttributes: { project_type: "observe" },
        }),
      );
    await testInfo.attach("source-identities", {
      body: JSON.stringify(seeds),
      contentType: "application/json",
    });
    await expect
      .poll(
        () =>
          probe.ch(
            "SELECT id FROM spans FINAL WHERE trace_id IN ({a:String}, {b:String}) ORDER BY id",
            { a: seeds[0].traceId, b: seeds[1].traceId },
          ),
        POLL.SPAN_VISIBLE,
      )
      .toEqual(
        seeds
          .flatMap((s) => s.spanIds)
          .sort()
          .map((id) => ({ id })),
      );
    const projects = await probe.pg<{ id: string }>(
      "SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2 AND workspace_id = $3",
      [prefix, actor.organizationId, actor.workspaceId],
    );
    expect(projects).toHaveLength(1);
    const projectId = projects[0].id;
    expect(
      await probe.ch(
        "SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id IN ({a:String}, {b:String})",
        { a: seeds[0].traceId, b: seeds[1].traceId },
      ),
    ).toEqual([{ project_id: projectId }]);

    // Eval-template/create-v2, custom-eval-config and eval-task serializers;
    // echo judge recipe also exercised by EVAL-E2E-001. Only the LLM children map fi.span.kind.
    await actor.api.post("/model-hub/custom_models/create/", {
      model_provider: "openai",
      model_name: MODEL,
      input_token_cost: 0,
      output_token_cost: 0,
      config_json: { key: KEY, api_base: GATEWAY },
    });
    const template = await actor.api.post<Created>(
      "/model-hub/eval-templates/create-v2/",
      {
        name: `${prefix}-template`,
        eval_type: "llm",
        model: MODEL,
        output_type: "pass_fail",
        pass_threshold: 0.5,
        instructions: `Reply with exactly this JSON: {"result": "Pass", "explanation": "${prefix} saw {{output}}"}`,
      },
    );
    const evaluator = await actor.api.post<Created>(
      "/tracer/custom-eval-config/",
      {
        project: projectId,
        eval_template: template.result.id,
        name: `${prefix}-judge`,
        model: MODEL,
        mapping: { output: "fi.span.kind" },
        config: { mapping: { output: "fi.span.kind" } },
        error_localizer: false,
      },
    );
    const label = await actor.api.post<Created>(LABELS, {
      name: `${prefix}-label`,
      project: projectId,
      type: "categorical",
      allow_notes: false,
      settings: {
        multi_choice: false,
        options: [{ label: "Pass" }, { label: "Fail" }],
        auto_annotate: false,
        rule_prompt: "",
        strategy: null,
      },
    });
    const queue = await actor.api.post<{ id: string; name: string }>(QUEUES, {
      name: `${prefix}-queue`,
      project_id: projectId,
      label_ids: [label.result.id],
    });
    await actor.api.post(`${QUEUES}${queue.id}/update-status/`, {
      status: "active",
    });
    const added = await actor.api.post<{
      result: { added: number; duplicates: number; errors: unknown[] };
    }>(`${QUEUES}${queue.id}/items/add-items/`, {
      project_id: projectId,
      items: seeds.map((s) => ({
        source_type: "observation_span",
        source_id: s.spanIds[1],
      })),
    });
    expect(added.result).toMatchObject({ added: 2, duplicates: 0, errors: [] });
    const items = await probe.pg<{ id: string; observation_span_id: string }>(
      "SELECT id, observation_span_id FROM model_hub_queueitem WHERE queue_id = $1 AND deleted = false",
      [queue.id],
    );
    expect(items.map((i) => i.observation_span_id).sort()).toEqual(
      seeds.map((s) => s.spanIds[1]).sort(),
    );
    await testInfo.attach("queue-identities", {
      body: JSON.stringify({
        projectId,
        queue,
        label: label.result.id,
        evaluator: evaluator.result.id,
        template: template.result.id,
        items,
      }),
      contentType: "application/json",
    });
    // queue-detail-view.jsx routes and Settings' labelled MUI select/save controls.
    const queueUrl = `/dashboard/annotations/queues/${queue.id}`;
    await test.step("UI: select evaluator, save and reload Settings", async () => {
      await page.goto(queueUrl);
      await page.getByRole("tab", { name: "Settings", exact: true }).click();
      await page.getByLabel("Evaluator (Judge)").click();
      await page
        .getByRole("option", { name: `${prefix}-judge`, exact: true })
        .click();
      const saved = page.waitForResponse(
        (r) =>
          new URL(r.url()).pathname === `${QUEUES}${queue.id}/` &&
          r.request().method() === "PATCH",
        { timeout: UI_READY },
      );
      await page
        .getByRole("button", { name: "Save Changes", exact: true })
        .click();
      expect((await saved).status()).toBe(200);
      await page.reload();
      await page.getByRole("tab", { name: "Settings", exact: true }).click();
      await expect(page.getByLabel("Evaluator (Judge)")).toHaveText(
        `${prefix}-judge`,
        { timeout: UI_READY },
      );
      const detail = await actor.api.get<{ custom_eval_config: string }>(
        `${QUEUES}${queue.id}/`,
      );
      expect(detail.custom_eval_config).toBe(evaluator.result.id);
      expect(
        await probe.pg(
          "SELECT custom_eval_config_id FROM model_hub_annotationqueue WHERE id = $1",
          [queue.id],
        ),
      ).toEqual([{ custom_eval_config_id: evaluator.result.id }]);
    });

    for (const [index, value] of ["Pass", "Fail"].entries()) {
      await test.step(`UI: annotate ${value}`, async () => {
        // SpanGrid/AnnotationSidebarContent, same native path as ANNOT-E2E-003.
        await page.goto(
          `/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=spans`,
        );
        await page
          .locator('.ag-row [col-id="span_name"]')
          .getByText(`${prefix}-${value}`, { exact: true })
          .click();
        await page
          .getByRole("button", { name: "Actions", exact: true })
          .click();
        await page
          .getByRole("menuitem", { name: "Annotate", exact: true })
          .click();
        const drawer = page
          .locator(".MuiDrawer-paper:visible")
          .filter({ has: page.getByText(queue.name, { exact: true }) });
        await expect(drawer).toHaveCount(1, { timeout: UI_READY });
        const heading = drawer.getByText(`${prefix}-label*`, { exact: true });
        await heading
          .locator("..")
          .locator("..")
          .getByText(value, { exact: true })
          .click();
        const saved = page.waitForResponse(
          (r) =>
            new URL(r.url()).pathname === SCORES &&
            r.request().method() === "POST",
          { timeout: UI_READY },
        );
        await drawer.getByRole("button", { name: /^(Save|Update)/ }).click();
        const response = await saved;
        expect(response.status()).toBe(200);
        expect(response.request().postDataJSON()).toMatchObject({
          source_type: "observation_span",
          source_id: seeds[index].spanIds[1],
          queue_item_id: items.find(
            (i) => i.observation_span_id === seeds[index].spanIds[1],
          )!.id,
          scores: [{ label_id: label.result.id, value: { selected: [value] } }],
        });
        expect((await response.json()).result.errors).toEqual([]);
      });
    }
    const human = await actor.api.get<{ result: Agreement }>(
      `${QUEUES}${queue.id}/agreement/`,
    );
    expect(human.result.judge_vs_human).toBeNull();
    expect(human.result.overall_agreement).toBeNull();
    const now = Date.now();
    const task = await actor.api.post<Created>("/tracer/eval-task/", {
      name: `${prefix}-task`,
      project: projectId,
      evals: [evaluator.result.id],
      filters: {
        project_id: projectId,
        date_range: [
          new Date(now - 3600000).toISOString(),
          new Date(now + 3600000).toISOString(),
        ],
      },
      run_type: "historical",
      row_type: "spans",
      spans_limit: 100000,
      sampling_rate: 100,
    });
    await testInfo.attach("eval-task", {
      body: JSON.stringify(task.result),
      contentType: "application/json",
    });
    await expect
      .poll(
        async () =>
          (
            await actor.api.get<{ status: string }>(
              `/tracer/eval-task/${task.result.id}/`,
            )
          ).status,
        POLL.EVAL_RESULT,
      )
      .toBe("completed");
    const evaluations = await probe.pg<{
      id: string;
      observation_span_id: string;
      status: string;
      output_bool: boolean;
      eval_explanation: string;
    }>(
      `SELECT id, observation_span_id, status, output_bool, eval_explanation FROM tracer_eval_logger
     WHERE eval_task_id = $1 AND observation_span_id = ANY($2) AND deleted = false ORDER BY observation_span_id`,
      [task.result.id, seeds.map((s) => s.spanIds[1])],
    );
    expect(
      evaluations.map((e) => ({
        span: e.observation_span_id,
        status: e.status,
        verdict: e.output_bool,
        reason: e.eval_explanation,
      })),
    ).toEqual(
      seeds
        .map((s) => ({
          span: s.spanIds[1],
          status: "completed",
          verdict: true,
          reason: `${prefix} saw llm`,
        }))
        .sort((a, b) => a.span.localeCompare(b.span)),
    );
    const scores = await probe.pg<{
      id: string;
      observation_span_id: string;
      queue_item_id: string;
      value: { selected: string[] };
    }>(
      `SELECT id, observation_span_id, queue_item_id, value FROM model_hub_score WHERE label_id = $1 AND deleted = false ORDER BY observation_span_id`,
      [label.result.id],
    );
    expect(
      scores.map((s) => ({
        span: s.observation_span_id,
        item: s.queue_item_id,
        value: s.value,
      })),
    ).toEqual(
      seeds
        .map((s, i) => ({
          span: s.spanIds[1],
          item: items.find((q) => q.observation_span_id === s.spanIds[1])!.id,
          value: { selected: [i === 0 ? "Pass" : "Fail"] },
        }))
        .sort((a, b) => a.span.localeCompare(b.span)),
    );
    await expect
      .poll(
        async () => ({
          evals: await probe.ch(
            `SELECT id, observation_span_id, status, toUInt8(output_bool) AS verdict, eval_explanation
      FROM tracer_eval_logger FINAL WHERE eval_task_id = {t:String} AND status = 'completed' ORDER BY observation_span_id`,
            { t: task.result.id },
          ),
          scores: (
            await probe.ch<{
              id: string;
              observation_span_id: string;
              queue_item_id: string;
              value: string;
            }>(
              "SELECT id, observation_span_id, queue_item_id, value FROM model_hub_score FINAL WHERE label_id = {l:UUID} AND deleted = false ORDER BY observation_span_id",
              { l: label.result.id },
            )
          ).map((s) => ({ ...s, value: JSON.parse(s.value) })),
        }),
        POLL.CDC_VISIBLE,
      )
      .toEqual({
        evals: evaluations.map(({ output_bool, ...e }) => ({
          ...e,
          verdict: Number(output_bool),
        })),
        scores,
      });

    await test.step("UI: complete the queue and read agreement", async () => {
      await page.goto(queueUrl);
      await page.getByRole("tab", { name: "Settings", exact: true }).click();
      await page.getByLabel("Status", { exact: true }).click();
      await page.getByRole("option", { name: /Completed/ }).click();
      const completed = page.waitForResponse(
        (r) =>
          new URL(r.url()).pathname === `${QUEUES}${queue.id}/update-status/` &&
          r.request().method() === "POST",
        { timeout: UI_READY },
      );
      await page
        .getByRole("button", { name: "Save Changes", exact: true })
        .click();
      expect((await completed).status()).toBe(200);
      await page.getByRole("tab", { name: "Agreement", exact: true }).click();
      await expect(
        page.getByText(`Evaluator: ${prefix}-judge`, { exact: true }),
      ).toBeVisible({ timeout: UI_READY });
      const judge = page
        .getByText("Judge vs Human Agreement", { exact: true })
        .locator("..");
      await expect(
        judge
          .getByRole("row")
          .filter({ hasText: `${prefix}-label` })
          .getByRole("cell"),
      ).toHaveText([`${prefix}-label`, "categorical", "50.0%", "2"], {
        timeout: UI_READY,
      });
      await expect(
        page.getByText(
          "Need at least 2 annotators per item to calculate agreement",
          { exact: true },
        ),
      ).toBeVisible({ timeout: UI_READY });
    });
    const final = await actor.api.get<{ result: Agreement }>(
      `${QUEUES}${queue.id}/agreement/`,
    );
    expect(final.result.judge_vs_human).toEqual({
      evaluator_name: `${prefix}-judge`,
      overall_agreement: 0.5,
      total_comparisons: 2,
      labels: {
        [label.result.id]: {
          label_name: `${prefix}-label`,
          label_type: "categorical",
          judge_human_agreement: 0.5,
          total_comparisons: 2,
          comparable: true,
        },
      },
    });
    const { judge_vs_human: _before, ...humanBefore } = human.result;
    const { judge_vs_human: _after, ...humanAfter } = final.result;
    expect(humanAfter).toEqual(humanBefore);
    await testInfo.attach("agreement", {
      body: JSON.stringify({ before: human.result, after: final.result }),
      contentType: "application/json",
    });
  },
);
