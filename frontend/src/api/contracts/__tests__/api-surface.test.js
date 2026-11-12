import { describe, expect, it } from "vitest";

import {
  API_SURFACE_CONTRACT,
  API_SURFACE_PATHS,
} from "../api-surface.generated";
import {
  apiPath,
  getContractedApiMethods,
  isContractedApiPath,
  isApiContractExceptionPath,
  uncontractedApiPath,
} from "../api-surface";
import { API_CONTRACT_EXCEPTIONS } from "../api-contract-exceptions";

describe("api surface contract", () => {
  it("generates annotation and model-hub endpoint coverage from Swagger", () => {
    expect(API_SURFACE_CONTRACT.swaggerVersion).toBe("2.0");
    expect(API_SURFACE_CONTRACT.groups["model-hub"]).toHaveProperty(
      "/model-hub/annotation-tasks/",
    );
    expect(API_SURFACE_CONTRACT.groups["model-hub"]).toHaveProperty(
      "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
    );
    expect(API_SURFACE_CONTRACT.groups["model-hub"]).toHaveProperty(
      "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/{thread_id}/resolve/",
    );
    expect(API_SURFACE_CONTRACT.groups["model-hub"]).toHaveProperty(
      "/model-hub/scores/bulk/",
    );
    expect(API_SURFACE_CONTRACT.groups["model-hub"]).toHaveProperty(
      "/model-hub/dataset/{dataset_id}/annotation-summary/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/project-version/add_annotations/",
    );
  });

  it("generates tracer, filter, and observe endpoint coverage from Swagger", () => {
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/trace/list_traces/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/observation-span/list_spans/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/trace-session/list_sessions/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/trace/list_voice_calls/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty("/tracer/users/");
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/observation-span/root-spans/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/observation-span/get_evaluation_details/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/trace/{id}/tags/",
    );
    expect(API_SURFACE_CONTRACT.groups.tracer).toHaveProperty(
      "/tracer/dashboard/{dashboard_pk}/widgets/{id}/query/",
    );
  });

  it("builds only registered paths and preserves method metadata", () => {
    expect(
      apiPath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
        { queue_id: "queue one", id: "item/1" },
      ),
    ).toBe(
      "/model-hub/annotation-queues/queue%20one/items/item%2F1/annotate-detail/",
    );
    expect(
      getContractedApiMethods("/model-hub/annotation-queues/{id}/"),
    ).toEqual(["delete", "get", "patch", "put"]);
    expect(getContractedApiMethods("/model-hub/annotation-tasks/")).toEqual([
      "get",
    ]);
    expect(getContractedApiMethods("/tracer/trace/{id}/tags/")).toEqual([
      "patch",
    ]);
    expect(getContractedApiMethods("/usage/v2/add-addon/")).toEqual([
      "delete",
      "post",
      "put",
    ]);
    expect(getContractedApiMethods("/usage/v2/usage-overview/")).toEqual([
      "get",
    ]);
    expect(
      apiPath("/usage/v2/invoices/{invoice_id}/", { invoice_id: "invoice/1" }),
    ).toBe("/usage/v2/invoices/invoice%2F1/");
    expect(getContractedApiMethods("/v1/internal/licenses")).toEqual(["post"]);
    expect(getContractedApiMethods("/falcon-ai/conversations/")).toEqual([
      "get",
      "post",
    ]);
    expect(isContractedApiPath("/not-in-openapi/")).toBe(false);
    expect(() => apiPath("/not-in-openapi/")).toThrow(
      "API path is not in generated contract",
    );
    expect(() => apiPath("/tracer/trace/{id}/tags/")).toThrow(
      'Missing API path param "id"',
    );
  });

  it("checks every exception exactly once and bounds the entire agentcc group", () => {
    const entries = Object.entries(API_CONTRACT_EXCEPTIONS);
    const gateway = entries.filter(([, meta]) =>
      meta.reason.includes("Gateway console"),
    );
    const credits = entries.filter(([path]) =>
      path.startsWith("/agentcc/managed-credits/"),
    );
    const dsx = entries.filter(
      ([, meta]) => meta.reason === "dsx prototype (PRODUCT-SPEC §6.2)",
    );
    const gatedRowRead = entries.filter(
      ([path]) => path === "/model-hub/datasets/{dataset_id}/rows/{row_id}/",
    );
    expect(gatedRowRead).toHaveLength(1);
    expect(gatedRowRead[0][1]).toMatchObject({
      group: "model-hub",
      status: "active_uncontracted",
      reason:
        "gated by DSX_DATASET_ROW_SOURCE_ENABLED, off by default; not in the public surface while off",
      methods: ["get"],
    });
    const checked = [...gateway, ...credits, ...dsx, ...gatedRowRead].map(
      ([path]) => path,
    );
    expect(checked.sort()).toEqual(Object.keys(API_CONTRACT_EXCEPTIONS).sort());
    expect(new Set(checked).size).toBe(checked.length);
    expect(
      entries.filter(([, meta]) => meta.group === "agentcc").length,
    ).toBeLessThanOrEqual(35);
    for (const [path] of entries) expect(isContractedApiPath(path)).toBe(false);
    expect(credits.map(([path]) => path)).toEqual([
      "/agentcc/managed-credits/reconciliation/",
    ]);
  });

  it("lets only pending gateway console endpoints bypass the contract", () => {
    expect(
      API_CONTRACT_EXCEPTIONS["/agentcc/managed-credits/reconciliation/"],
    ).toMatchObject({
      group: "agentcc",
      status: "active_uncontracted",
      next: expect.stringContaining("apiPath()"),
    });
    const exceptions = Object.entries(API_CONTRACT_EXCEPTIONS).filter(
      ([, meta]) => meta.reason.includes("Gateway console"),
    );
    expect(
      Object.values(API_CONTRACT_EXCEPTIONS).filter(
        (meta) => meta.group === "agentcc",
      ).length,
    ).toBeLessThanOrEqual(35);
    for (const [template, meta] of exceptions) {
      expect(template).toMatch(/^\/agentcc\//);
      expect(isContractedApiPath(template)).toBe(false);
      expect(meta).toEqual({
        group: "agentcc",
        status: "active_uncontracted",
        reason: expect.stringContaining("Gateway console"),
        next: expect.stringContaining("apiPath()"),
      });
    }
  });

  it.each([
    ["/model-hub/datasets/{dataset_id}/runs/", ["get"]],
    ["/model-hub/datasets/{dataset_id}/results/", ["get"]],
    ["/model-hub/datasets/{dataset_id}/results/analysis/", ["get"]],
    ["/model-hub/datasets/{dataset_id}/versions/", ["get", "post"]],
    ["/model-hub/datasets/{dataset_id}/versions/{version_id}/", ["get"]],
    ["/model-hub/datasets/{dataset_id}/baseline/", ["delete", "get", "put"]],
    ["/agentcc/managed-credits/", ["get"]],
    ["/agentcc/managed-credits/entries/", ["get"]],
    ["/agentcc/managed-credits/statement/", ["get"]],
    ["/agentcc/managed-credits/checkout/", ["post"]],
    ["/agentcc/managed-credits/checkout/{session_id}/", ["get"]],
    ["/agentcc/managed-credits/settings/", ["get", "put"]],
    ["/agentcc/managed-credits/grant/", ["post"]],
    ["/agentcc/managed-credits/adjust/", ["post"]],
    ["/agentcc/managed-credits/refund/", ["post"]],
    // The workspace's decision-model listing is served and documented.
    ["/agentcc/decisions/models/", ["get"]],
  ])(
    "requires the published contract for %s instead of an exception",
    (path, methods) => {
      expect(isContractedApiPath(path)).toBe(true);
      expect(getContractedApiMethods(path)).toEqual(methods);
      expect(isApiContractExceptionPath(path)).toBe(false);
      expect(() => uncontractedApiPath(path)).toThrow(
        "API contract exception path is not registered",
      );
    },
  );

  it("keeps the endpoint registry fully backed by generated contracts", () => {
    // The remaining exceptions are Datasets + Experiments prototype
    // endpoints (PRODUCT-SPEC §6.3 + R2-SPEC §6: I1, S1, IM1 + R3 G0 §B10:
    // task compare, task review + R4: 15 r4 paths + the interface contract: 15 collaboration, review and
    // access paths + the interface contract: dataset query and star + the interface contract implementation: import jobs and
    // saved sources + the interface contract: the eval version flip preview + the interface contract: 12 results
    // analysis paths + KH: record a run + the interface contract: 12 evaluator-type and judge
    // quality paths); none of them is in the generated surface.
    const prototypePaths = Object.entries(API_CONTRACT_EXCEPTIONS)
      .filter(
        ([, meta]) => meta.reason === "dsx prototype (PRODUCT-SPEC \u00a76.2)",
      )
      .map(([template]) => template);
    expect(prototypePaths.sort()).toEqual(
      [
        "/model-hub/datasets/{dataset_id}/result-reviews/",
        "/model-hub/datasets/{dataset_id}/rows/{row_id}/runs/",
        // Split CRUD is source-contracted (F50); version list/detail are generated.
        "/model-hub/datasets/{dataset_id}/versions/{version_id}/changes/",
        "/model-hub/datasets/{dataset_id}/versions/{version_id}/table/",
        "/model-hub/datasets/imports/",
        // the interface contract (lane w3-rows-in A7): import jobs, saved sources.
        "/model-hub/datasets/imports/jobs/{job_id}/",
        "/model-hub/datasets/{dataset_id}/sources/",
        "/model-hub/datasets/{dataset_id}/sources/{source_id}/",
        "/model-hub/datasets/{dataset_id}/sources/{source_id}/refresh/",
        // shared-edit(F06): resume is contracted now; see the run-control test.
        "/model-hub/experiments/v2/{experiment_id}/runs/",
        "/model-hub/experiments/v2/{experiment_id}/runs/{run_id}/results/",
        "/model-hub/experiments/v2/{experiment_id}/runs/{run_id}/session/",
        "/model-hub/datasets/{dataset_id}/task-compare/",
        "/model-hub/datasets/{dataset_id}/task-compare/review/",
        // R4 (implementation; CONTRACT.md "Round 4").
        "/model-hub/r4/run-sources/catalog/",
        "/model-hub/r4/calls/{call_id}/",
        "/model-hub/r4/calls/{call_id}/audio/{channel}",
        "/model-hub/r4/connect/machines/",
        "/model-hub/r4/connect/machines/{machine_id}/",
        "/model-hub/r4/connect/tokens/",
        "/model-hub/r4/connect/sessions/{token}/",
        "/model-hub/r4/connect/sessions/{token}/simulate/",
        "/model-hub/r4/connect/agents/{agent_id}/try/",
        "/model-hub/r4/runs/{run_id}/activity/",
        "/model-hub/r4/runs/{run_id}/control/",
        "/model-hub/r4/runs/{run_id}/simulate-upload/",
        "/model-hub/r4/runs/{run_id}/rerun/",
        "/model-hub/r4/runs/{run_id}/paths/",
        "/model-hub/r4/datasets/{dataset_id}/voice-summary/",
        // + the interface contract: 15 paths (the interface contract §3; CONTRACT.md "the interface contract").
        "/model-hub/annotation-queues/{queue_id}/invites/",
        "/model-hub/annotation-queues/{queue_id}/invites/{invite_id}/",
        "/model-hub/audit/",
        "/model-hub/datasets/{dataset_id}/access/",
        "/model-hub/datasets/{dataset_id}/assignments/",
        "/model-hub/datasets/{dataset_id}/assignments/{assignment_id}/",
        "/model-hub/datasets/{dataset_id}/comments/",
        "/model-hub/datasets/{dataset_id}/corrections/",
        "/model-hub/datasets/{dataset_id}/corrections/{correction_id}/",
        "/model-hub/datasets/{dataset_id}/rows/{row_id}/comments/",
        "/model-hub/datasets/{dataset_id}/rows/{row_id}/comments/{comment_id}/",
        "/model-hub/datasets/{dataset_id}/translate/",
        "/model-hub/review-links/{token}/",
        "/model-hub/review-links/{token}/items/{item_id}/",
        "/model-hub/workspaces/{workspace_id}/sharing-policy/",
        // + the interface contract: 2 paths (the interface contract §3.2; CONTRACT.md "the interface contract").
        "/model-hub/datasets/query/",
        // the interface contract: the eval version flip preview.
        "/model-hub/eval-templates/{template_id}/versions/{version}/preview/",
        // + the interface contract: 12 paths (the interface contract §3.1 the interface contract; CONTRACT.md "the interface contract").
        "/model-hub/datasets/{dataset_id}/result-columns/",
        "/model-hub/datasets/{dataset_id}/result-columns/values/",
        "/model-hub/datasets/{dataset_id}/derived-metrics/",
        "/model-hub/datasets/{dataset_id}/classification/",
        "/model-hub/results/match/",
        "/model-hub/datasets/{dataset_id}/best-of/",
        "/model-hub/tags/",
        "/model-hub/tags/{tag_id}/",
        "/model-hub/experiments/v2/{experiment_id}/tags/",
        "/model-hub/leaderboards/",
        "/model-hub/leaderboards/{leaderboard_id}/",
        "/model-hub/experiments/v2/{experiment_id}/runs/{run_id}/traces/",
        // + HO D7: the 13th the interface contract path, the Expression column (the interface contract).
        "/model-hub/develops/{dataset_id}/add_expression_column/",
        // + KH: 1 path (KH-SCOPE §3.3; CONTRACT.md "KH").
        "/model-hub/experiments/v2/{experiment_id}/runs/{run_id}/record/",
        // + the interface contract: 12 paths (the interface contract §3.1 the interface contract; CONTRACT.md "the interface contract").
        // shared-edit(F69): Agreement is covered by the generated contract.
        "/model-hub/eval-templates/{template_id}/improve/",
        "/model-hub/eval-templates/create-remote/",
        "/model-hub/eval-templates/{template_id}/remote/",
        "/model-hub/eval-templates/remote/test/",
        "/model-hub/eval-templates/{template_id}/remote/score-run/",
        "/model-hub/eval-templates/remote/jobs/{job_id}/",
      ].sort(),
    );
    prototypePaths.forEach((template) =>
      expect(isContractedApiPath(template)).toBe(false),
    );
    // Keep the former prototype scenarios in this same exhaustive check.
    // f2c4e6b0c9 (708J REGEN-REGISTRY-01) moved both R3 saved-view routes
    // into the generated contract; they are not prototype exceptions.
    // W96B: the three Results GETs are contracted, but remain launch-held.
    for (const [template, methods] of [
      ["/model-hub/datasets/{dataset_id}/runs/", ["get"]],
      ["/model-hub/datasets/{dataset_id}/results/", ["get"]],
      ["/model-hub/datasets/{dataset_id}/results/analysis/", ["get"]],
      ["/model-hub/datasets/{dataset_id}/versions/", ["get", "post"]],
      ["/model-hub/datasets/{dataset_id}/versions/{version_id}/", ["get"]],
      ["/model-hub/datasets/{dataset_id}/baseline/", ["delete", "get", "put"]],
      ["/model-hub/r3/saved-views/", ["get", "post"]],
      ["/model-hub/r3/saved-views/{view_id}/", ["delete"]],
      // head-87 regen: row activity and add-results-to-dataset are published
      // (from-results under {dataset_id}); their exceptions are gone.
      ["/model-hub/datasets/{dataset_id}/rows/{row_id}/activity/", ["get"]],
      ["/model-hub/datasets/{dataset_id}/rows/from-results/", ["post"]],
    ]) {
      expect(isContractedApiPath(template)).toBe(true);
      expect(getContractedApiMethods(template)).toEqual(methods);
      expect(isApiContractExceptionPath(template)).toBe(false);
    }
    expect(isApiContractExceptionPath("/usage/v2/usage-overview/")).toBe(false);
    expect(() => uncontractedApiPath("/model-hub/legacy/")).toThrow(
      "API contract exception path is not registered",
    );
  });

  it("contracts the composed star, decision-tree and jury endpoints", () => {
    const methods = {
      "/model-hub/datasets/{dataset_id}/star/": ["delete", "post"],
      "/model-hub/eval-templates/create-dag/": ["post"],
      "/model-hub/eval-templates/{template_id}/dag/": ["get", "patch"],
      "/model-hub/eval-templates/dag/execute-adhoc/": ["post"],
      "/model-hub/eval-templates/{template_id}/dag/paths/": ["get"],
      "/model-hub/eval-templates/{template_id}/composite/jury/": ["get"],
    };
    for (const [path, expected] of Object.entries(methods)) {
      expect(getContractedApiMethods(path), path).toEqual(expected);
      expect(isApiContractExceptionPath(path), path).toBe(false);
    }
  });

  it("contracts the F06 run-control endpoints", () => {
    const methods = {
      "/model-hub/experiments/v2/{experiment_id}/resume/": ["post"],
      "/model-hub/experiments/v2/{experiment_id}/run-control/": ["get"],
    };
    for (const [path, expected] of Object.entries(methods)) {
      expect(getContractedApiMethods(path), path).toEqual(expected);
      expect(isApiContractExceptionPath(path), path).toBe(false);
    }
  });

  it("does not let generated Management API coverage accidentally shrink", () => {
    expect(API_SURFACE_CONTRACT.endpointCount).toBeGreaterThan(960);
    expect(Object.keys(API_SURFACE_PATHS).length).toBe(
      API_SURFACE_CONTRACT.endpointCount,
    );
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups["model-hub"]).length,
    ).toBeGreaterThan(360);
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups.tracer).length,
    ).toBeGreaterThan(152);
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups.accounts).length,
    ).toBeGreaterThan(75);
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups.simulate).length,
    ).toBeGreaterThan(100);
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups.agentcc).length,
    ).toBeGreaterThan(100);
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups.usage).length,
    ).toBeGreaterThan(55);
    expect(
      Object.keys(API_SURFACE_CONTRACT.groups["falcon-ai"]).length,
    ).toBeGreaterThan(15);
  });
});
