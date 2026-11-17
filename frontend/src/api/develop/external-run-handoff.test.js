import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createExternalRunRecord,
  externalRunRecipe,
  loadExternalRunPlan,
} from "./external-run-handoff";

const h = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: h.get, post: h.post },
}));
const source = {
  id: "source-run",
  task_kind: "external",
  dataset_version_id: "pinned-version",
  experiment_id: null,
  expected_items: [
    { example_id: "example-a", trial_number: 2 },
    { example_id: "example-a", trial_number: 7 },
    { example_id: "example-b", trial_number: 3 },
  ],
  evaluator_bindings: [{ metric_id: "metric-a", evaluator_version_id: "old" }],
  model_config: { model: "fixture", options: { temperature: 0 } },
  metadata: { code_revision: "revision-a" },
};
const inputs = [
  { example_id: "example-a", input: { question: "pinned A" }, expected: "A" },
  { example_id: "example-b", input: ["pinned B"], expected: null },
];
const response = (result) => ({ data: { status: true, result } });
const page = (offset, items, hasMore = false) =>
  response({
    version: { id: "pinned-version", dataset: "dataset", item_count: 2 },
    items,
    offset,
    limit: 500,
    has_more: hasMore,
  });
const args = {
  datasetId: "dataset",
  runId: "source-run",
  newRunId: "d5eeb619-51d9-4ea5-97bc-7f4409cbfd90",
  scope: "all",
};
const body = {
  run_id: args.newRunId,
  dataset_version_id: "pinned-version",
  experiment_id: null,
  expected_items: source.expected_items,
  evaluator_metric_ids: ["metric-a"],
  model_config: source.model_config,
  metadata: source.metadata,
};

beforeEach(() => {
  vi.resetAllMocks();
  h.get
    .mockResolvedValueOnce(response(source))
    .mockResolvedValueOnce(page(0, [inputs[0]], true))
    .mockResolvedValueOnce(page(1, [inputs[1]]));
});

describe("external execution plan", () => {
  it("copies the full pinned plan and every input page without changing the source", async () => {
    const before = JSON.stringify(source);
    const plan = await loadExternalRunPlan(args);
    expect(plan).toEqual({
      sourceRunId: "source-run",
      createUrl: "/model-hub/experiment-runs/",
      resultsUrl: `/model-hub/experiment-runs/${args.newRunId}/results/`,
      body,
      inputs,
    });
    expect(h.get.mock.calls.map(([url]) => url)).toEqual([
      "/model-hub/experiment-runs/source-run/",
      "/model-hub/datasets/dataset/versions/pinned-version/?limit=500&offset=0",
      "/model-hub/datasets/dataset/versions/pinned-version/?limit=500&offset=1",
    ]);
    expect(JSON.stringify(source)).toBe(before);
    expect(plan.body.model_config === source.model_config).toBe(false);
    expect(Object.isFrozen(plan.body.model_config.options)).toBe(true);
    expect(h.post.mock.calls).toEqual([]);
  });

  it("intersects selected examples and preserves every original trial identity", async () => {
    const plan = await loadExternalRunPlan({
      ...args,
      scope: "selected",
      exampleIds: ["example-b", "not-in-plan", "example-b"],
    });
    expect(plan.body).toEqual({
      ...body,
      expected_items: [{ example_id: "example-b", trial_number: 3 }],
    });
    expect(plan.inputs).toEqual([inputs[1]]);
  });

  it("keeps every selected trial without adding unplanned examples", async () => {
    h.get.mockReset();
    h.get
      .mockResolvedValueOnce(
        response({
          ...source,
          expected_items: source.expected_items.slice(0, 2),
        }),
      )
      .mockResolvedValueOnce(page(0, inputs));
    const plan = await loadExternalRunPlan({
      ...args,
      scope: "selected",
      exampleIds: ["example-a", "example-b"],
    });
    expect(plan.body.expected_items).toEqual([
      { example_id: "example-a", trial_number: 2 },
      { example_id: "example-a", trial_number: 7 },
    ]);
    expect(plan.inputs).toEqual([inputs[0]]);
  });

  it("refuses an empty selected scope rather than expanding it", async () => {
    await expect(
      loadExternalRunPlan({ ...args, scope: "selected", exampleIds: [] }),
    ).rejects.toThrow("No selected examples belong to this run.");
    expect(h.post.mock.calls).toEqual([]);
  });

  it.each([
    ["a repeated page", page(0, [inputs[1]])],
    ["missing inputs", page(1, [])],
    ["duplicate examples", page(1, [inputs[0]])],
    [
      "another dataset",
      response({
        ...page(1, [inputs[1]]).data.result,
        version: { id: "pinned-version", dataset: "other", item_count: 2 },
      }),
    ],
  ])("refuses %s", async (_, lastPage) => {
    h.get.mockReset();
    h.get
      .mockResolvedValueOnce(response(source))
      .mockResolvedValueOnce(page(0, [inputs[0]], true))
      .mockResolvedValueOnce(lastPage);
    await expect(loadExternalRunPlan(args)).rejects.toThrow(
      "The pinned version inputs are incomplete.",
    );
    expect(h.post.mock.calls).toEqual([]);
  });

  it("refuses a different source run", async () => {
    h.get.mockReset().mockResolvedValue(response({ ...source, id: "other" }));
    await expect(loadExternalRunPlan(args)).rejects.toThrow(
      "This run has no external execution plan.",
    );
  });

  it("preserves the UUID and exact request on retry and returns the new pins", async () => {
    const plan = await loadExternalRunPlan(args);
    const created = {
      ...source,
      id: args.newRunId,
      evaluator_bindings: [
        {
          metric_id: "metric-a",
          evaluator_version_id: "new",
          version_number: 4,
        },
      ],
    };
    h.post
      .mockRejectedValueOnce(new Error("connection interrupted"))
      .mockResolvedValueOnce(response(created));
    await expect(createExternalRunRecord(plan)).rejects.toThrow(
      "connection interrupted",
    );
    expect(await createExternalRunRecord(plan)).toEqual(created);
    expect(h.post.mock.calls).toEqual([
      [
        plan.createUrl,
        body,
        { headers: { "Content-Type": "application/json" } },
      ],
      [
        plan.createUrl,
        body,
        { headers: { "Content-Type": "application/json" } },
      ],
    ]);
    expect(h.post.mock.calls[0][1]).toEqual(h.post.mock.calls[1][1]);
  });

  it.each([
    [
      "caller-created",
      false,
      [
        "POST /model-hub/experiment-runs/ with handoff.body.",
        "Create retries must use the same authenticated principal as the original create.",
        "Inspect result.evaluator_bindings: a new run resolves current evaluator pins.",
      ],
    ],
    [
      "UI-created",
      true,
      [
        `GET /model-hub/experiment-runs/${args.newRunId}/ and inspect result.evaluator_bindings.`,
        "This record was created in the UI. Do not replay creation from your code.",
      ],
    ],
  ])(
    "keeps configuration out of the %s REST recipe",
    async (_, created, steps) => {
      const plan = await loadExternalRunPlan(args);
      expect(externalRunRecipe(plan, created)).toBe(
        [
          "Use your existing authenticated REST client for this deployment and workspace.",
          "Load the downloaded handoff JSON. Keep its body unchanged on retries.",
          ...steps,
          "Execution happens in your code, not in Future AGI.",
          "For each body.expected_items identity, execute using the matching inputs entry.",
          `POST /model-hub/experiment-runs/${args.newRunId}/results/ with:`,
          JSON.stringify(
            {
              items: [
                {
                  ...source.expected_items[0],
                  receipt_key: `${args.newRunId}:example-a:2`,
                  status: "completed",
                  output: "REPLACE_WITH_YOUR_ACTUAL_OUTPUT",
                  score_refs: [],
                },
              ],
            },
            null,
            2,
          ),
          "Replace the example output; never upload this placeholder.",
          "Send 1–100 results per batch (at most 2 MiB), using the planned example and trial IDs.",
          "For errors use status failed and error instead of a completed output.",
          "Any supplied scores must use the new run's evaluator pins.",
          "Persist each receipt and payload. Retry uploads unchanged; do not execute again to retry an upload.",
        ].join("\n"),
      );
      expect(h.post.mock.calls).toEqual([]);
    },
  );
});
