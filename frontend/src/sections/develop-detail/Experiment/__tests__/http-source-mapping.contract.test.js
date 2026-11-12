// HTTP admission regressions: authored before the adapter/contract integration.
import { afterEach, describe, expect, it } from "vitest";
import {
  hydrateHttpSourceValues,
  serverExperimentBody,
  withHttpSourceSettings,
} from "../serverBody";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { experimentBodyOfValues } from "../experimentBody";
import { emptyExperimentValues } from "../experimentFormValues";
import { getExperimentDefaultValue } from "../utils";
import { flattenRuns } from "../runPlan";
import { autoMapVariables } from "../variableMapping";
import { buildRequest } from "src/sections/common/RunSources/endpoint/requestModel";

afterEach(() => setLaunchOverrideForTest(null));

const Q = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51";
const OTHER = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e52";
const columns = [{ id: Q, name: "question" }, { id: OTHER, name: "other" }];
const adapt = (entry, isEditing = false) => serverExperimentBody({
  name: "Endpoint regression",
  ...(!isEditing ? { dataset_id: "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01" } : {}),
  prompt_config: [entry], user_eval_metrics: [],
}, { columns, isEditing, initial: { name: "Endpoint regression" } });
const sources = [
  { target_type: "prompt", model: "test-model", prompt_id: Q, prompt_version: OTHER },
  { target_type: "agent", agent_id: Q, agent_version: OTHER },
];

describe("source-aware real backend mapping", () => {
  it("retains HTTP settings and explicit remaps once its real contract is registered", () => {
    const entry = {
      target_type: "http", http: {
        url: "https://fixture.example/answer", method: "POST",
        headers: [{ name: "Authorization", secret: "TOKEN" }],
        body_template: '{"q":"{{question}}"}', output_path: "$.reply",
      },
      variable_mapping: { question: OTHER },
    };
    const result = adapt(entry);
    expect(result.blocks).toEqual([]);
    expect(result.body.prompt_config[0]).toEqual(entry);
  });
  for (const isEditing of [false, true]) {
    it.each(sources)(`removes only same-name no-ops for $target_type; editing=${isEditing}`, (source) => {
      const result = adapt({ ...source, variable_mapping: { question: Q } }, isEditing);
      expect(result.blocks).toEqual([]);
      expect(result.body.prompt_config[0]).not.toHaveProperty("variable_mapping");
    });
    it.each(sources)(`still blocks non-HTTP remaps/constants/paths; editing=${isEditing}`, (source) => {
      for (const value of [OTHER, `${Q}::$.value`, { kind: "constant", value: "fixed" }]) {
        const result = adapt({ ...source, variable_mapping: { question: value } }, isEditing);
        expect(result.blocks.some((block) => block.field === "prompt_config[].variable_mapping")).toBe(true);
      }
    });
  }
});

const http = {
  url: "https://fixture.example/{{question}}", method: "POST",
  headers: [{ name: "Authorization", secret_ref: "TOKEN" }],
  body_template: '{"q":"{{question}}","empty":"{{empty}}"}',
  output_path: '$.items["-1"].reply', streaming: false, audio: "text",
  timeout_seconds: 23,
};

describe("HTTP-only admission and faithful readback", () => {
  it.each([false, true])("preserves every HTTP mapping and setting; editing=%s", (isEditing) => {
    const entry = {
      target_type: "http", http,
      variable_mapping: {
        question: Q, remap: OTHER, path: `${Q}::$.value`,
        constant: { kind: "constant", value: "" }, empty: { kind: "empty" },
      },
    };
    const before = JSON.stringify(entry);
    const result = adapt(entry, isEditing);
    expect(result.blocks).toEqual([]);
    expect(result.body.prompt_config[0]).toEqual(entry);
    expect(JSON.stringify(entry)).toBe(before);
  });

  it.each(["external", "code", "traces", "harness", "mcp_tool", "voice", "HTTP"])(
    "does not lift the guard for %s", (target_type) => {
      for (const isEditing of [false, true]) {
        expect(adapt({ target_type, http }, isEditing).blocks.some(
          (block) => block.field === "prompt_config[].target_type",
        )).toBe(true);
      }
    },
  );

  it("a new A2A run is admitted, but never with HTTP settings", () => {
    const created = adapt({ target_type: "a2a", http });
    // The A2A run itself is no longer refused on create...
    expect(created.blocks.some((block) => block.field === "prompt_config[].target_type")).toBe(false);
    // ...but HTTP settings on it block Run and never travel: the server's A2A
    // settings take no http key and would refuse the run.
    expect(created.blocks.map((block) => block.text)).toContain(
      "This A2A agent run carries settings of another run type, which an A2A agent doesn't take. Remove the run and add the A2A agent again, then run.",
    );
    expect(created.body.prompt_config[0]).not.toHaveProperty("http");
    // Edit reads saved A2A runs back as HTTP sources, so an A2A entry there still blocks.
    expect(adapt({ target_type: "a2a", http }, true).blocks.some(
      (block) => block.field === "prompt_config[].target_type",
    )).toBe(true);
  });

  it("does not treat an unproven mapping as a prompt default", () => {
    const result = serverExperimentBody({
      name: "Missing columns", prompt_config: [{ ...sources[0], variable_mapping: { question: Q } }],
    });
    expect(result.blocks.some((b) => b.field === "prompt_config[].variable_mapping")).toBe(true);
  });

  it.each(sources)("blocks HTTP settings on $target_type", (source) => {
    expect(adapt({ ...source, http }).blocks.some((b) => b.field === "prompt_config[].http")).toBe(true);
  });

  it("sends an HTTP create's error policy only when one was chosen", () => {
    const body = { name: "Defaults", metadata: {}, prompt_config: [{ target_type: "http", http }] };
    const result = serverExperimentBody(body);
    expect(result.blocks).toEqual([]);
    expect(result.body).not.toHaveProperty("error_policy");
    expect(result.body).not.toHaveProperty("metadata");
    for (const error_policy of ["zero", "exclude"]) {
      const chosen = serverExperimentBody({ ...body, error_policy });
      expect(chosen.blocks).toEqual([]);
      expect(chosen.body.error_policy).toBe(error_policy);
    }
    // An edit never changes the stored policy.
    expect(serverExperimentBody({ ...body, error_policy: "zero" }, { isEditing: true }).blocks.some(
      (b) => b.field === "error_policy",
    )).toBe(true);
  });

  it("translates the HTTP edit source identity without dropping its mapping", () => {
    const result = adapt({ target_type: "http", run_id: Q, http, variable_mapping: { question: OTHER } }, true);
    expect(result.blocks).toEqual([]);
    expect(result.body.prompt_config[0]).toEqual({
      target_type: "http", id: Q, http, variable_mapping: { question: OTHER },
    });
  });

  it.each([false, true])("hydrates classic HTTP readback without the optional run catalog; editing=%s", (isEditing) => {
    const detail = { http_configs: [{ id: Q, order: 1, target_type: "http", name: "Saved endpoint", http,
      variable_mapping: { question: OTHER, empty: { kind: "empty" } } }] };
    const original = { name: "Saved", promptConfig: [{ targetType: "prompt", name: "Prompt" }] };
    const before = JSON.stringify({ detail, original });
    const hydrated = hydrateHttpSourceValues(original, detail, { isEditing });
    const card = hydrated.promptConfig[1];
    expect(card).toMatchObject({ targetType: "http", name: "Saved endpoint",
      http: { ...http, headers: [{ name: "Authorization", secret: "TOKEN" }], bodyTemplate: http.body_template, outputPath: http.output_path },
      variableMapping: detail.http_configs[0].variable_mapping,
    });
    expect(card.variables).toEqual(["question", "empty"]);
    if (isEditing) expect(card.runId).toBe(Q);
    else expect(card).not.toHaveProperty("runId");
    const raw = { ...hydrated, promptConfig: [card] };
    const payload = withHttpSourceSettings(experimentBodyOfValues(raw, { isEditing, datasetId: OTHER }), raw.promptConfig);
    const result = serverExperimentBody(payload, { isEditing, columns, initial: { name: "Saved" } });
    expect(result.blocks).toEqual([]);
    // SRC S1b: an unchanged saved source is sent back exactly as read
    // (its secret_ref alias included); a duplicate sends the one editor alias.
    expect(result.body.prompt_config[0].http).toEqual(isEditing
      ? http
      : { ...http, headers: [{ name: "Authorization", secret: "TOKEN" }] });
    expect(result.body.prompt_config[0].variable_mapping).toEqual(detail.http_configs[0].variable_mapping);
    if (isEditing) expect(result.body.prompt_config[0]).toMatchObject({ id: Q, name: "Saved endpoint" });
    else expect(result.body.prompt_config[0]).not.toHaveProperty("id");
    expect(JSON.stringify({ detail, original })).toBe(before);
  });

  it("replaces a canonical HTTP card instead of duplicating it on reload", () => {
    const detail = { http_configs: [{ id: Q, order: 0, target_type: "http", http, variable_mapping: {} }] };
    const value = { promptConfig: [{ targetType: "http", runId: Q, http: { url: "stale" } }] };
    expect(hydrateHttpSourceValues(value, detail, { isEditing: true }).promptConfig).toHaveLength(1);
  });
});

// SRC-S1-HTTP-R2-FE-COR-01/02/04: exercise initialization through the same
// auto-mapping, flattening and wire builders the mounted wizard uses.
const savedHttp = (overrides = {}) => ({
  id: Q, order: 0, target_type: "http", http,
  variable_mapping: { question: OTHER, empty: { kind: "empty" } },
  ...overrides,
});
const duplicate = (detail) => hydrateHttpSourceValues({
  ...emptyExperimentValues(),
  ...getExperimentDefaultValue(detail),
  name: `${detail.name} (copy)`,
}, detail);
const createWire = (values) => serverExperimentBody(withHttpSourceSettings(
  experimentBodyOfValues(values, { datasetId: OTHER }), values.promptConfig,
), { columns });

describe("HTTP duplicate form round-trip", () => {
  it.each([
    { run_scope: { split_ids: [Q] }, splits: [{ id: OTHER }] },
    { splits: [{ id: Q }] },
  ])("COR-01 retains the saved split from %j on the create wire", (scope) => {
    const values = duplicate({ name: "Scoped", ...scope, http_configs: [savedHttp()] });
    expect(values.splitIds).toEqual([Q]);
    expect(values.name).toBe("Scoped (copy)");
    const wire = createWire(values);
    expect(wire.blocks).toEqual([]);
    expect(wire.body.split_ids).toEqual([Q]);
  });

  it("COR-01 retains nondefault stored scope and scoring: unread settings block, run settings travel", () => {
    const values = duplicate({ name: "Scoped", http_configs: [savedHttp()],
      description: "Saved description", dataset_version: { id: Q }, trials: 3,
      run_scope: { split_ids: [Q], row_ids: [OTHER] },
      runs: [{ concurrency: 2, error_policy: "exclude", trial_reducer: { kind: "max" } }],
      metadata: { owner: "fixture", source: "ui" },
    });
    expect(values).toMatchObject({ description: "Saved description", datasetVersionId: Q,
      splitIds: [Q], rowIds: [OTHER], trials: 3, maxConcurrency: 2, errorPolicy: "exclude",
      trialReducer: { kind: "max" }, metadata: [{ key: "owner", value: "fixture" }],
    });
    const wire = createWire(values);
    const fields = wire.blocks.map((block) => block.field);
    for (const field of ["dataset_version_id", "trials", "max_concurrency", "trial_reducer"])
      expect(fields).toContain(field);
    // Picked rows, the error policy and metadata are run settings the create
    // contract declares: they travel as stored.
    expect(wire.body.row_ids).toEqual([OTHER]);
    expect(wire.body.error_policy).toBe("exclude");
    expect(wire.body.metadata).toEqual({ owner: "fixture" });
  });

  it.each([
    { orders: [0, 1, 2, 3], expected: ["model-a", "model-b", "http", "agent"] },
    { orders: [1, 3, 2, 0], expected: ["agent", "model-a", "http", "model-b"] },
  ])("COR-02 preserves global run order $expected", ({ orders, expected }) => {
    const detail = { name: "Interleaved", experiment_type: "llm",
      prompt_configs: ["model-a", "model-b"].map((model, index) => ({
        id: `${Q.slice(0, -1)}${index + 3}`, order: orders[index], model,
        prompt_id: Q, prompt_version: OTHER, prompt_name: "Shared prompt", model_params: {},
      })),
      agent_configs: [{ id: OTHER, order: orders[3], agent_id: Q, agent_version: OTHER, agent_name: "Agent" }],
      http_configs: [savedHttp({ order: orders[2] })],
    };
    const values = duplicate(detail);
    expect(flattenRuns(values.promptConfig).map((run) => run.model ?? run.targetType)).toEqual(expected);
    const wire = createWire(values);
    expect(wire.blocks).toEqual([]);
    expect(wire.body.prompt_config.map((entry) => entry.model ?? (entry.agent_id ? "agent" : entry.target_type))).toEqual(expected);
    expect(flattenRuns(values.promptConfig).map((run) => run.letter)).toEqual(["A", "B", "C", "D"]);
  });

  it.each([{ kind: "constant", value: "" }, { kind: "constant" }])(
    "COR-04 keeps the stored empty constant %j through auto-mapping and request preparation", (binding) => {
      const values = duplicate({ name: "Fixed empty", http_configs: [savedHttp({
        http: { ...http, url: "https://fixture.example/run", body_template: '{"q":"{{question}}"}' },
        variable_mapping: { question: binding },
      })] });
      const card = values.promptConfig[0];
      // This is RunExperiment's effect when same-name dataset columns arrive.
      card.variableMapping = autoMapVariables(card.variables, columns, card.variableMapping);
      expect(card.variableMapping).toEqual({ question: { kind: "empty" } });
      const wire = createWire(values);
      expect(wire.blocks).toEqual([]);
      expect(wire.body.prompt_config[0].variable_mapping).toEqual({ question: { kind: "empty" } });
      const entry = wire.body.prompt_config[0];
      const prepared = buildRequest({ endpoint: entry.http, mapping: entry.variable_mapping,
        values: { [Q]: "must not be sent" }, columns,
      });
      expect(JSON.parse(prepared.body)).toEqual({ q: "" });
    },
  );
});
