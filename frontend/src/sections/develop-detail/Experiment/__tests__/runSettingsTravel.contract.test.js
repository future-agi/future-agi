// Run settings (picked rows, sample, per-row limits, error policy) reach the
// create call exactly as the New experiment wizard builds them, and Run is
// not blocked. The check reads the generated contract, as the wizard does:
// against a contract that does not declare these fields it fails, and the
// failure lists the "isn't available in this release" lines the wizard
// shows on Run.
import { describe, expect, it } from "vitest";
import { unknownFields } from "src/api/contracts/strictBody";
import { experimentBodyOfValues } from "../experimentBody";
import { emptyExperimentValues } from "../experimentFormValues";
import { runScopeBody, SCOPE_FIELDS } from "../runScopeBody";
import { wireExperimentBody } from "../serverBody";

const DATASET = "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01";
const Q = { id: "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51", name: "question" };
const ROWS = [
  "6a1b2c3d-0000-4000-8000-000000000011",
  "6a1b2c3d-0000-4000-8000-000000000012",
  "6a1b2c3d-0000-4000-8000-000000000013",
];

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

const form = (over = {}) => ({
  ...emptyExperimentValues(),
  name: "Run settings",
  promptConfig: [promptCard()],
  ...over,
});

// What the wizard builds and what it would send (not prototype mode).
const submit = (values) => {
  const body = experimentBodyOfValues(values, { datasetId: DATASET });
  return {
    body,
    wire: wireExperimentBody(body, { columns: [Q] }, false),
  };
};

// Run settings the create contract does not declare.
const refusedSettings = (body) =>
  unknownFields(runScopeBody(body), "ExperimentCreateV2").filter((field) =>
    SCOPE_FIELDS.includes(field),
  );

describe("run settings reach the create call", () => {
  it("sends 3 picked rows, a time limit and Leave out without blocking Run", () => {
    const { body, wire } = submit(
      form({
        rowIds: ROWS,
        errorPolicy: "exclude",
        promptConfig: [promptCard({ rowLimits: { timeout_s: 30 } })],
      }),
    );
    expect(wire.blocks.map((block) => block.text)).toEqual([]);
    expect(refusedSettings(body)).toEqual([]);
    expect(wire.body.row_ids).toEqual(ROWS);
    expect(wire.body.limits).toEqual({ timeout_s: 30 });
    expect(wire.body.error_policy).toBe("exclude");
    expect(wire.body.prompt_config[0]).not.toHaveProperty("row_limits");
  });

  it("sends a sample of 5 rows with seed 0 and no error policy", () => {
    const { body, wire } = submit(
      form({ sample: { on: true, n: 5, seed: 0 } }),
    );
    expect(wire.blocks.map((block) => block.text)).toEqual([]);
    expect(refusedSettings(body)).toEqual([]);
    expect(wire.body.sample).toEqual({ size: 5, seed: 0 });
    expect(wire.body).not.toHaveProperty("error_policy");
    expect(wire.body).not.toHaveProperty("row_ids");
  });
});
