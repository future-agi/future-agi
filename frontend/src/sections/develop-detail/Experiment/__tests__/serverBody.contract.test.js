// the capability contract: the block-Run list is DERIVED. This test builds the body the
// New experiment form sends (experimentBody.js, the code RunExperiment uses)
// with every setting moved off its default, diffs it against dev's strict
// serializers as the generated contract states them at this head
// (futureagi/model_hub/tests/test_dsx_contract_parity.py proves the contract
// equals the serializers), and checks that every field dev would refuse or
// drop blocks Run with its own reason. No field list is written here: the
// floor below only pins the fields the critic named, so a regression that
// sends one of them silently fails.
import { describe, expect, it } from "vitest";
import {
  droppedNestedFields,
  unknownFields,
} from "src/api/contracts/strictBody";
import { experimentBody, experimentBodyOfValues } from "../experimentBody";
import {
  DEV_ROWS_AT_ONCE,
  fieldIdOf,
  serverExperimentBody,
  wireExperimentBody,
  withServerBlocks,
} from "../serverBody";
import { SERVER_BLOCK_COPY } from "../serverBlockCopy";
import { emptyExperimentValues } from "../experimentFormValues";
import { EXPERIMENT_STEP } from "../constants";

const DATASET = "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01";
const COLUMNS = [
  { id: "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51", name: "question" },
  { id: "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e52", name: "prompt" },
];
const [Q, P] = COLUMNS;

const promptCard = (over = {}) => ({
  experimentType: "llm",
  targetType: "prompt",
  promptId: "1b2c3d4e-0000-4000-8000-000000000001",
  promptVersion: "1b2c3d4e-0000-4000-8000-000000000002",
  model: [{ value: "gpt-4o-mini", providers: "openai" }],
  modelParams: {},
  configuration: { toolChoice: "auto", tools: [] },
  variables: ["question"],
  variableMapping: { question: Q.id },
  ...over,
});

const dsxBody = (raw, opts = {}) =>
  experimentBodyOfValues(raw, { datasetId: DATASET, ...opts });

// Every setting the wizard offers, off its default.
const everything = () => ({
  ...emptyExperimentValues(),
  name: "Exp L2",
  description: "notes for the team",
  datasetVersionId: "9a8b7c6d-0000-4000-8000-000000000003",
  splitIds: ["split-a"],
  rowIds: ["row-1", "row-2"],
  trials: 3,
  maxConcurrency: 4,
  metadata: [{ key: "team", value: "evals" }],
  errorPolicy: "exclude",
  trialReducer: { kind: "at_least", n: 2 },
  record: false,
  startFrom: {
    experimentId: "e0e0e0e0-0000-4000-8000-000000000004",
    preset: "same-runs",
    carryOver: true,
    versionMoved: true,
  },
  promptConfig: [
    promptCard({
      // {{question}} reads column `prompt`: a remap dev cannot do.
      variableMapping: { question: P.id },
      toolSetup: {
        versionTools: [
          { name: "lookup", schema: { type: "object", properties: {} } },
        ],
      },
      rowLimits: { tokens: 500 },
    }),
    {
      experimentType: "llm",
      targetType: "http",
      http: { url: "https://example.test/run", method: "POST" },
      variables: [],
      variableMapping: {},
    },
  ],
});

// What the form sends that dev's serializers refuse (400) or drop (silent):
// derived from the contract, never listed.
const derivedBlockList = (body, definition = "ExperimentCreateV2") => [
  ...unknownFields(body, definition),
  ...droppedNestedFields(body, definition).map((d) => fieldIdOf(d.path)),
];

describe("the derived block-Run list (create)", () => {
  const body = dsxBody(everything());
  const derived = new Set(derivedBlockList(body));
  const { body: wire, blocks } = serverExperimentBody(body, {
    columns: COLUMNS,
  });
  const blocked = new Set(blocks.map((b) => b.field));

  it("covers at least the fields the critic named (a floor, not the list)", () => {
    [
      "description",
      "dataset_version_id",
      "trials",
      "max_concurrency",
      "trial_reducer",
      "record",
      "carry_over_from_experiment_id",
      "preset",
      "prompt_config[].variable_mapping",
      "prompt_config[].target_type",
      "prompt_config[].tools",
      "prompt_config[].row_limits",
    ].forEach((f) => expect(derived, f).toContain(f));
  });

  it("blocks Run on every derived field, each with its own reason", () => {
    // A run of a kind dev cannot start blocks once (its target_type); its
    // other keys (the endpoint's url...) add no second reason.
    const kindBlocked = new Set(
      droppedNestedFields(body, "ExperimentCreateV2")
        .filter((d) => d.key === "target_type" && d.value !== "prompt")
        .map((d) => d.path.slice(0, -1).join(".")),
    );
    const expected = new Set([
      ...unknownFields(body, "ExperimentCreateV2"),
      ...droppedNestedFields(body, "ExperimentCreateV2")
        .filter(
          (d) =>
            d.key === "target_type" ||
            !kindBlocked.has(d.path.slice(0, -1).join(".")),
        )
        .filter((d) => !(d.key === "target_type" && d.value === "prompt"))
        .map((d) => fieldIdOf(d.path)),
    ]);
    expect(expected.size).toBeGreaterThan(10);
    expect([...blocked].sort()).toEqual([...expected].sort());
    blocks.forEach((b) => {
      expect(b.generic, `${b.field} has no words in serverBlockCopy`).toBe(
        false,
      );
      expect(SERVER_BLOCK_COPY[b.field]).toBeTruthy();
      expect(b.text.length).toBeGreaterThan(20);
    });
  });

  it("never sends a field dev refuses or drops", () => {
    expect(unknownFields(wire, "ExperimentCreateV2")).toEqual([]);
    expect(droppedNestedFields(wire, "ExperimentCreateV2")).toEqual([]);
  });

  it("puts each block on the step where its setting lives", () => {
    const step = Object.fromEntries(blocks.map((b) => [b.field, b.step]));
    expect(step.description).toBe(EXPERIMENT_STEP.CONFIGURATION);
    expect(step.split_ids).toBeUndefined();
    expect(wire.split_ids).toEqual(body.split_ids);
    expect(step["prompt_config[].variable_mapping"]).toBe(EXPERIMENT_STEP.DATA);
    expect(step.trials).toBe(EXPERIMENT_STEP.REVIEW);
    expect(step.error_policy).toBeUndefined();
    expect(wire.error_policy).toBe("exclude");
  });

  it("names the remap and the run kind in plain words", () => {
    const text = (f) => blocks.find((b) => b.field === f)?.text;
    expect(text("prompt_config[].variable_mapping")).toBe(
      "{{question}} reads column “prompt”. Mapping a variable isn't supported here: the server fills {{question}} using the template path “question”.",
    );
    expect(text("prompt_config[].target_type")).toBe(
      "Endpoint runs aren't supported here. Remove that run, or use a prompt or an agent.",
    );
    expect(text("trials")).toBe(
      "3 tries per row aren't supported here: each row runs once. Set Trials to 1 to run.",
    );
  });

  it("derives sample too (the wizard sends it only without picked rows)", () => {
    const raw = {
      ...everything(),
      rowIds: [],
      sample: { on: true, n: 20, seed: 7 },
    };
    const b = dsxBody(raw);
    expect(unknownFields(b, "ExperimentCreateV2")).not.toContain("sample");
    const scoped = serverExperimentBody(b, { columns: COLUMNS });
    expect(scoped.blocks.map((x) => x.field)).not.toContain("sample");
    expect(scoped.body.sample).toEqual({ size: 20, seed: 7 });
  });
});

describe("a form dev can run as it is", () => {
  const raw = {
    ...emptyExperimentValues(),
    name: "Exp plain",
    promptConfig: [promptCard()],
  };
  const body = dsxBody(raw);
  const { body: wire, blocks } = serverExperimentBody(body, {
    columns: COLUMNS,
  });

  it("has no blocks: 1 try, 10 rows at once, {{question}} from `question`", () => {
    expect(DEV_ROWS_AT_ONCE).toBe(10);
    expect(raw.maxConcurrency).toBe(DEV_ROWS_AT_ONCE);
    expect(blocks).toEqual([]);
  });

  it("sends only what dev declares, with the run's own fields intact", () => {
    expect(Object.keys(wire).sort()).toEqual([
      "column_id",
      "dataset_id",
      "error_policy",
      "experiment_type",
      "metadata",
      "name",
      "prompt_config",
      "user_eval_metrics",
    ]);
    expect(wire.prompt_config).toHaveLength(1);
    expect(wire.prompt_config[0]).toMatchObject({
      prompt_id: raw.promptConfig[0].promptId,
      prompt_version: raw.promptConfig[0].promptVersion,
      model: "gpt-4o-mini",
      output_format: "string",
    });
    expect(wire.prompt_config[0]).not.toHaveProperty("target_type");
    expect(wire.prompt_config[0]).not.toHaveProperty("variable_mapping");
    expect(unknownFields(wire, "ExperimentCreateV2")).toEqual([]);
    expect(droppedNestedFields(wire, "ExperimentCreateV2")).toEqual([]);
  });

  it("keeps the eval metrics the validated form sends", () => {
    const metric = {
      template_id: "7e6d5c4b-0000-4000-8000-000000000005",
      name: "Answer relevance",
      config: { mapping: { output: "output" } },
      model: "turing_flash",
      error_localizer: false,
      kb_id: null,
    };
    const full = experimentBody(
      {
        experimentType: "llm",
        promptConfig: body.prompt_config,
        userEvalMetrics: [metric],
        columnId: null,
      },
      raw,
      { datasetId: DATASET },
    );
    const out = serverExperimentBody(full, { columns: COLUMNS });
    expect(out.blocks).toEqual([]);
    expect(out.body.user_eval_metrics).toEqual([metric]);
  });
});

describe("edit (ExperimentUpdateV2: no name, no description)", () => {
  const initial = { name: "Exp 1", description: "" };
  const raw = {
    ...emptyExperimentValues(),
    name: "Exp 1",
    promptConfig: [promptCard({ runId: "run-a" })],
  };

  it("sends no name or description when they did not change", () => {
    const b = dsxBody(raw, { isEditing: true });
    expect(b).toHaveProperty("name", "Exp 1");
    const out = serverExperimentBody(b, {
      isEditing: true,
      initial,
      columns: COLUMNS,
    });
    expect(out.definition).toBe("ExperimentUpdateV2");
    expect(out.blocks).toEqual([]);
    expect(out.body).not.toHaveProperty("name");
    expect(out.body).not.toHaveProperty("description");
    expect(unknownFields(out.body, "ExperimentUpdateV2")).toEqual([]);
  });

  it("blocks Save when the name or description changed", () => {
    const b = dsxBody(
      { ...raw, name: "Exp renamed", description: "why" },
      { isEditing: true },
    );
    const out = serverExperimentBody(b, {
      isEditing: true,
      initial,
      columns: COLUMNS,
    });
    expect(out.blocks.map((x) => x.field).sort()).toEqual([
      "description",
      "name",
    ]);
    expect(out.blocks.find((x) => x.field === "name").text).toBe(
      "Renaming an experiment isn't supported here. Keep the name “Exp 1” to save.",
    );
  });
});

describe("prototype mode and the step issues", () => {
  it("prototype mode sends the whole DSX body and blocks nothing", () => {
    const body = dsxBody(everything());
    const out = wireExperimentBody(body, { columns: COLUMNS }, true);
    expect(out.body).toBe(body);
    expect(out.blocks).toEqual([]);
  });

  it("merges each block into its step's issues once", () => {
    const issues = [["Name is required"], [], [], []];
    const merged = withServerBlocks(issues, [
      { step: EXPERIMENT_STEP.REVIEW, text: "a" },
      { step: EXPERIMENT_STEP.REVIEW, text: "a" },
      { step: EXPERIMENT_STEP.DATA, text: "b" },
    ]);
    expect(merged).toEqual([["Name is required"], ["b"], [], ["a"]]);
    expect(issues[3]).toEqual([]);
  });
});
