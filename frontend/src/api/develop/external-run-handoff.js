import {
  experimentRunRead,
  externalExperimentRunsCreate,
  getExperimentRunReadUrl,
  getExternalExperimentRunsCreateUrl,
  getExternalExperimentRunResultsIngestUrl,
  modelHubDatasetsVersionsRead,
} from "src/generated/api-contracts/api";

const resultOf = (response) => {
  if (response?.data?.status !== true || !response.data.result)
    throw new Error("The external run response is unavailable.");
  return response.data.result;
};
const object = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const freeze = (value) => {
  if (value && typeof value === "object") {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
};
const identity = ({ example_id, trial_number }) =>
  `${example_id}:${trial_number}`;

/** Read immutable inputs, never today's dataset rows or truncated result cells. */
async function versionInputs(datasetId, versionId, signal) {
  const items = new Map();
  let offset = 0;
  let total;
  const fail = () => {
    throw new Error("The pinned version inputs are incomplete.");
  };
  for (;;) {
    const page = resultOf(
      await modelHubDatasetsVersionsRead(
        datasetId,
        versionId,
        { limit: 500, offset },
        { signal },
      ),
    );
    total ??= page.version?.item_count;
    if (
      page.version?.id !== versionId ||
      page.version?.dataset !== datasetId ||
      !Number.isSafeInteger(total) ||
      total < 1 ||
      total > 10000 ||
      page.version.item_count !== total ||
      page.offset !== offset ||
      !Array.isArray(page.items) ||
      typeof page.has_more !== "boolean"
    )
      fail();
    page.items.forEach((item) => {
      if (!item.example_id || items.has(item.example_id) || item.input == null)
        fail();
      items.set(item.example_id, {
        example_id: item.example_id,
        input: item.input,
        expected: item.expected ?? null,
      });
    });
    offset += page.items.length;
    if (
      offset > total ||
      (page.has_more && (!page.items.length || offset >= total))
    )
      fail();
    if (!page.has_more) {
      if (offset !== total) fail();
      return items;
    }
  }
}

/** One caller-executed handoff. A read retry supplies the same newRunId. */
export async function loadExternalRunPlan({
  datasetId,
  runId,
  newRunId,
  scope,
  exampleIds = [],
  signal,
}) {
  const source = resultOf(await experimentRunRead(runId, { signal }));
  const plan = source.expected_items;
  if (
    source.id !== runId ||
    source.task_kind !== "external" ||
    !source.dataset_version_id ||
    !newRunId ||
    newRunId === runId ||
    !["all", "selected"].includes(scope) ||
    !Array.isArray(plan) ||
    !plan.length ||
    plan.length > 10000 ||
    plan.some(
      (item) =>
        !item?.example_id ||
        !Number.isSafeInteger(item.trial_number) ||
        item.trial_number < 1,
    ) ||
    new Set(plan.map(identity)).size !== plan.length ||
    !object(source.model_config) ||
    !object(source.metadata) ||
    !Array.isArray(source.evaluator_bindings) ||
    source.evaluator_bindings.some((binding) => !binding.metric_id)
  )
    throw new Error("This run has no external execution plan.");
  const selected = new Set(exampleIds);
  const expectedItems = plan.filter(
    (item) => scope === "all" || selected.has(item.example_id),
  );
  if (!expectedItems.length)
    throw new Error("No selected examples belong to this run.");
  const inputs = await versionInputs(
    datasetId,
    source.dataset_version_id,
    signal,
  );
  const examples = [...new Set(expectedItems.map((item) => item.example_id))];
  if (examples.some((id) => !inputs.has(id)))
    throw new Error("The pinned version inputs are incomplete.");
  const handoff = {
    sourceRunId: source.id,
    createUrl: getExternalExperimentRunsCreateUrl(),
    resultsUrl: getExternalExperimentRunResultsIngestUrl(newRunId),
    body: {
      run_id: newRunId,
      dataset_version_id: source.dataset_version_id,
      experiment_id: source.experiment_id ?? null,
      expected_items: expectedItems,
      evaluator_metric_ids: source.evaluator_bindings.map(
        (binding) => binding.metric_id,
      ),
      model_config: source.model_config,
      metadata: source.metadata,
    },
    inputs: examples.map((id) => inputs.get(id)),
  };
  // Clone before freezing: query data and the original run remain untouched.
  return freeze(JSON.parse(JSON.stringify(handoff)));
}

/** Creates a record only, using the existing authenticated transport. */
export async function createExternalRunRecord(plan) {
  const created = resultOf(await externalExperimentRunsCreate(plan.body));
  if (
    created.id !== plan.body.run_id ||
    created.dataset_version_id !== plan.body.dataset_version_id ||
    !Array.isArray(created.expected_items) ||
    JSON.stringify(created.expected_items.map(identity).sort()) !==
      JSON.stringify(plan.body.expected_items.map(identity).sort()) ||
    !Array.isArray(created.evaluator_bindings)
  )
    throw new Error("The external run response is unavailable.");
  return created;
}

/** No auth headers, source configuration or dataset input values in displayed text. */
export function externalRunRecipe(plan, created = false) {
  const first = plan.body.expected_items[0];
  return [
    "Use your existing authenticated REST client for this deployment and workspace.",
    "Load the downloaded handoff JSON. Keep its body unchanged on retries.",
    ...(created
      ? [
          `GET ${getExperimentRunReadUrl(plan.body.run_id)} and inspect result.evaluator_bindings.`,
          "This record was created in the UI. Do not replay creation from your code.",
        ]
      : [
          `POST ${plan.createUrl} with handoff.body.`,
          "Create retries must use the same authenticated principal as the original create.",
          "Inspect result.evaluator_bindings: a new run resolves current evaluator pins.",
        ]),
    "Execution happens in your code, not in Future AGI.",
    "For each body.expected_items identity, execute using the matching inputs entry.",
    `POST ${plan.resultsUrl} with:`,
    JSON.stringify(
      {
        items: [
          {
            ...first,
            receipt_key: `${plan.body.run_id}:${identity(first)}`,
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
  ].join("\n");
}
