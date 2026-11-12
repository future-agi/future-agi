// exp.rename: the wizard's Edit save on the real backend. Dev's
// ExperimentUpdateV2 declares name and description (read from the generated
// contract, regenerated from the backend half's serializers), but refuses a
// body that mixes them with run settings. So the name and description travel
// on their own (`metadata`), only when they changed, and the run settings
// follow only when they changed: a rename alone starts no run.
import { describe, expect, it } from "vitest";
import {
  acceptedFields,
  unknownFields,
} from "src/api/contracts/strictBody";
import { experimentBodyOfValues } from "../experimentBody";
import { serverExperimentBody } from "../serverBody";
import { editSavePlan, editSettingsChanged } from "../editSave";
import { emptyExperimentValues } from "../experimentFormValues";

const DATASET = "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01";
const Q = { id: "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51", name: "question" };

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
  runId: "run-a",
  ...over,
});

const opened = () => ({
  ...emptyExperimentValues(),
  name: "Exp 1",
  description: "First try",
  promptConfig: [promptCard()],
});
const initial = { name: "Exp 1", description: "First try" };

const editWire = (values) =>
  serverExperimentBody(
    experimentBodyOfValues(values, { isEditing: true, datasetId: DATASET }),
    { isEditing: true, initial, columns: [Q] },
  );

describe("the contract the edit relies on", () => {
  it("ExperimentUpdateV2 declares name and description", () => {
    expect(acceptedFields("ExperimentUpdateV2")).toEqual(
      expect.arrayContaining(["name", "description", "prompt_config"]),
    );
  });
});

describe("serverExperimentBody (edit)", () => {
  it("a new name travels as metadata, never in the run-settings body", () => {
    const wire = editWire({ ...opened(), name: "Exp renamed" });
    expect(wire.blocks).toEqual([]);
    expect(wire.metadata).toEqual({ name: "Exp renamed" });
    expect(wire.body).not.toHaveProperty("name");
    expect(wire.body).not.toHaveProperty("description");
    expect(wire.body.prompt_config).toHaveLength(1);
    expect(unknownFields(wire.metadata, "ExperimentUpdateV2")).toEqual([]);
  });

  it("clearing the description is a change; an unchanged name is not sent", () => {
    const wire = editWire({ ...opened(), description: "" });
    expect(wire.metadata).toEqual({ description: "" });
    expect(wire.blocks).toEqual([]);
  });

  it("nothing changed: no metadata, the run-settings body as before", () => {
    const wire = editWire(opened());
    expect(wire.metadata).toEqual({});
    expect(wire.blocks).toEqual([]);
    expect(wire.body).not.toHaveProperty("name");
  });

  it("a create has no metadata and keeps a blank description at home", () => {
    const wire = serverExperimentBody(
      experimentBodyOfValues(
        { ...opened(), description: "  " },
        { datasetId: DATASET },
      ),
      { columns: [Q] },
    );
    expect(wire.metadata).toBeNull();
    expect(wire.body).toHaveProperty("name", "Exp 1");
    expect(wire.body).not.toHaveProperty("description");
  });
});

describe("editSavePlan", () => {
  it("a rename alone sends the name and no run settings", () => {
    const current = { ...opened(), name: "Exp renamed" };
    const plan = editSavePlan(editWire(current), {
      original: opened(),
      current,
      datasetId: DATASET,
    });
    expect(plan).toEqual({ metadata: { name: "Exp renamed" }, config: null });
  });

  it("a rename with a changed run sends both, the name first", () => {
    const current = {
      ...opened(),
      name: "Exp renamed",
      promptConfig: [
        promptCard({ model: [{ value: "gpt-4.1", providers: "openai" }] }),
      ],
    };
    expect(
      editSettingsChanged(opened(), current, { datasetId: DATASET }),
    ).toBe(true);
    const wire = editWire(current);
    const plan = editSavePlan(wire, {
      original: opened(),
      current,
      datasetId: DATASET,
    });
    expect(plan.metadata).toEqual({ name: "Exp renamed" });
    expect(plan.config).toBe(wire.body);
    expect(plan.config).not.toHaveProperty("name");
  });

  it("an evaluation change counts as a run-settings change", () => {
    const current = {
      ...opened(),
      description: "Second try",
      userEvalMetrics: [{ name: "Tone", templateId: "t-1" }],
    };
    const plan = editSavePlan(editWire(current), {
      original: opened(),
      current,
      datasetId: DATASET,
    });
    expect(plan.metadata).toEqual({ description: "Second try" });
    expect(plan.config).not.toBeNull();
  });

  it("an HTTP endpoint's stored settings count too (headers included)", () => {
    const endpointCard = (team) => ({
      experimentType: "llm",
      targetType: "http",
      name: "Support endpoint",
      variables: [],
      variableMapping: {},
      http: {
        url: "https://fixture.example/run",
        method: "POST",
        bodyTemplate: "{}",
        outputPath: "$.reply",
        headers: [{ name: "X-Team", value: team }],
      },
      runId: "run-h",
    });
    const original = { ...opened(), promptConfig: [endpointCard("a")] };
    const renamed = { ...original, name: "Exp renamed" };
    expect(
      editSettingsChanged(original, renamed, { datasetId: DATASET }),
    ).toBe(false);
    expect(
      editSettingsChanged(
        original,
        { ...renamed, promptConfig: [endpointCard("b")] },
        { datasetId: DATASET },
      ),
    ).toBe(true);
  });

  it("no metadata: the run-settings body goes as before", () => {
    const wire = { body: { prompt_config: [] }, metadata: {} };
    expect(editSavePlan(wire, { original: opened(), current: opened() })).toEqual(
      { metadata: null, config: wire.body },
    );
    // Prototype mode answers without metadata: the whole body, once.
    const proto = { body: { name: "Exp 2", prompt_config: [] }, blocks: [] };
    expect(editSavePlan(proto)).toEqual({ metadata: null, config: proto.body });
  });
});
