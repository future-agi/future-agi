// An Edit save sends the run settings a person changed in the wizard (the
// metadata rows, Errored tries in scores, the time limit on the runs) as the
// experiment's settings for its next run: only a changed one, and only where
// the edit contract (ExperimentUpdateV2) declares it; elsewhere Save says
// why, and nothing is dropped without a word. Clearing a setting is a
// change too. A prompt or agent run added on Edit starts with the stored
// limits, so adding one changes none. The sample is never sent on an edit.
// The contract is set per
// case, with and without the settings, so this holds on both sides of a
// contract regeneration.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { acceptedFields } from "src/api/contracts/strictBody";
import { experimentBodyOfValues } from "../experimentBody";
import { serverExperimentBody } from "../serverBody";
import { editSavePlan } from "../editSave";
import { emptyExperimentValues } from "../experimentFormValues";
import {
  EDIT_RUN_SETTINGS,
  cardAddedOnEdit,
  editSettingLocked,
  withStoredRunSettings,
} from "../editRunSettings";

// The show-unreleased switch, read as the product modules read it.
const unreleased = vi.hoisted(() => ({ shown: true }));
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});

// The generated contract with an edit definition that declares the run
// settings or not. Every reader of the contract (the accepted fields, the
// unknown and the dropped nested fields) sees the same definition.
const contract = vi.hoisted(() => ({ declares: false }));
vi.mock(
  "src/api/contracts/openapi-contract.generated",
  async (importOriginal) => {
    const actual = await importOriginal();
    const base = actual.OPENAPI_CONTRACT;
    const settings = ["metadata", "error_policy", "limits"];
    const update = base.definitions.ExperimentUpdateV2;
    const create = base.definitions.ExperimentCreateV2.properties;
    const without = Object.fromEntries(
      Object.entries(update.properties).filter(
        ([field]) => !settings.includes(field),
      ),
    );
    const declared = {
      ...without,
      metadata: create.metadata,
      error_policy: { ...create.error_policy, "x-nullable": true },
      limits: create.limits,
    };
    const definitions = { ...base.definitions };
    Object.defineProperty(definitions, "ExperimentUpdateV2", {
      enumerable: true,
      get: () => ({
        ...update,
        properties: contract.declares ? declared : without,
      }),
    });
    return { ...actual, OPENAPI_CONTRACT: { ...base, definitions } };
  },
);

const LAUNCH = { launch: true, experimentsBasic: true, madeReal: [] };
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

// What the experiment holds for its next run, as its detail reads it.
const DETAIL = {
  id: "e0e0e0e0-0000-4000-8000-000000000001",
  name: "Exp 1",
  description: "",
  metadata: { team: "evals", source: "sdk" },
  error_policy: "zero",
  limits: { timeout_s: 30 },
  sample: { size: 2, seed: 17 },
};

// The Edit form as it opens: two runs, filled from the detail.
const opened = (detail = DETAIL) =>
  withStoredRunSettings(
    {
      ...emptyExperimentValues(),
      name: "Exp 1",
      promptConfig: [
        promptCard(),
        promptCard({
          runId: "run-b",
          promptVersion: "1b2c3d4e-0000-4000-8000-000000000003",
        }),
      ],
    },
    detail,
  );

const withLimits = (values, ...limits) => ({
  ...values,
  promptConfig: values.promptConfig.map((card, i) => ({
    ...card,
    rowLimits: limits[i] ?? limits[0],
  })),
});

// Runs the Configuration step adds on Edit: no run id yet.
const newPrompt = () =>
  promptCard({
    runId: undefined,
    promptVersion: "1b2c3d4e-0000-4000-8000-000000000004",
  });
const newAgent = () => ({
  experimentType: "llm",
  targetType: "agent",
  promptId: null,
  promptVersion: null,
  agentId: "6a6a6a6a-0000-4000-8000-000000000001",
  agentVersion: "6a6a6a6a-0000-4000-8000-000000000002",
  name: "Support agent",
  outputFormat: "string",
  isDraft: false,
  variables: [],
  variableMapping: {},
});

// The form with a run added the way the Configuration step adds it on Edit.
const withAdded = (values, card) => ({
  ...values,
  promptConfig: [
    ...values.promptConfig,
    cardAddedOnEdit(card, values.runSettingsBase),
  ],
});

const editOf = (values) =>
  serverExperimentBody(
    experimentBodyOfValues(values, { isEditing: true, datasetId: DATASET }),
    {
      isEditing: true,
      initial: { name: "Exp 1", description: "" },
      columns: [Q],
    },
  );

const fieldsOf = (out) => out.blocks.map((block) => block.field).sort();

beforeEach(() => {
  setLaunchOverrideForTest(LAUNCH);
  unreleased.shown = true;
  contract.declares = false;
});
afterEach(() => {
  setLaunchOverrideForTest(null);
  unreleased.shown = true;
  contract.declares = false;
});

describe("Edit opens with what the experiment holds", () => {
  it("fills the error policy, the metadata rows, each run's time limit and the sample", () => {
    const values = opened();
    expect(values.errorPolicy).toBe("zero");
    // `source` is not a row the form shows; it is kept for the save.
    expect(values.metadata).toEqual([{ key: "team", value: "evals" }]);
    values.promptConfig.forEach((card) =>
      expect(card.rowLimits).toEqual({ timeout_s: 30 }),
    );
    expect(values.sample).toEqual({ on: true, n: 2, seed: 17 });
  });

  it("an experiment with no settings opens with the defaults", () => {
    const values = opened({
      id: DETAIL.id,
      name: "Exp 1",
      description: "",
      metadata: {},
    });
    expect(values.errorPolicy).toBeNull();
    expect(values.metadata).toEqual([]);
    values.promptConfig.forEach((card) =>
      expect(card.rowLimits ?? {}).toEqual({}),
    );
  });

  it("stored limits holding one this release refuses stay as they are", () => {
    contract.declares = true;
    const values = opened({
      ...DETAIL,
      limits: { tokens: 500, timeout_s: 30 },
    });
    values.promptConfig.forEach((card) =>
      expect(card.rowLimits ?? {}).toEqual({}),
    );
    const base = values.runSettingsBase;
    expect(editSettingLocked("limits", { isEditing: true, base })).toBe(true);
    const out = editOf(values);
    expect(out.blocks).toEqual([]);
    expect(out.body).not.toHaveProperty("limits");
  });

  it("stored metadata the rows show trimmed is not a change", () => {
    const out = editOf(
      opened({ ...DETAIL, metadata: { " team ": "evals", source: "sdk" } }),
    );
    expect(out.blocks).toEqual([]);
    expect(out.body).not.toHaveProperty("metadata");
  });

  it("a detail without run settings leaves the form as it was and sends none", () => {
    const values = {
      ...emptyExperimentValues(),
      name: "Exp 1",
      errorPolicy: "exclude",
      promptConfig: [promptCard()],
    };
    const detail = { id: DETAIL.id, name: "Exp 1", description: "" };
    expect(withStoredRunSettings(values, detail)).toEqual(values);
    contract.declares = true;
    const out = editOf(withStoredRunSettings(values, detail));
    EDIT_RUN_SETTINGS.forEach((field) =>
      expect(out.body).not.toHaveProperty(field),
    );
  });
});

describe("a run added on Edit", () => {
  it("takes its own copy of the stored limits", () => {
    const base = opened().runSettingsBase;
    const card = cardAddedOnEdit(newAgent(), base);
    expect(card.rowLimits).toEqual({ timeout_s: 30 });
    expect(card.rowLimits).not.toBe(base.limits);
  });

  it.each([
    ["no stored limits", newPrompt(), { metadata: {}, limits: {} }],
    ["stored limits kept as they are", newPrompt(), { metadata: {} }],
    ["a create", newAgent(), undefined],
    [
      "a run of another kind",
      { experimentType: "llm", targetType: "http", http: {} },
      { limits: { timeout_s: 30 } },
    ],
    [
      "a run with limits of its own",
      { ...newPrompt(), rowLimits: { timeout_s: 90 } },
      { limits: { timeout_s: 30 } },
    ],
  ])("is left as it was with %s", (_label, card, base) => {
    expect(cardAddedOnEdit(card, base)).toBe(card);
  });
});

describe.each([
  ["declares the run settings", true],
  ["does not declare the run settings", false],
])("the edit contract %s", (_label, declares) => {
  beforeEach(() => {
    contract.declares = declares;
  });

  // Travels where the contract declares it, else blocks Save on it.
  const expectSent = (out, field, value) => {
    if (declares) {
      expect(out.blocks).toEqual([]);
      expect(out.body).toHaveProperty(field, value);
    } else {
      expect(fieldsOf(out)).toEqual([field]);
      expect(out.body).not.toHaveProperty(field);
    }
  };

  it("reads the contract as set for the case", () => {
    EDIT_RUN_SETTINGS.forEach((field) =>
      expect(acceptedFields("ExperimentUpdateV2").includes(field)).toBe(
        declares,
      ),
    );
  });

  it("an unchanged edit sends no run setting and keeps each run's limits at home", () => {
    const out = editOf(opened());
    expect(out.blocks).toEqual([]);
    [...EDIT_RUN_SETTINGS, "sample"].forEach((field) =>
      expect(out.body).not.toHaveProperty(field),
    );
    expect(out.body.prompt_config).toHaveLength(2);
    out.body.prompt_config.forEach((entry) =>
      expect(entry).not.toHaveProperty("row_limits"),
    );
  });

  it("a changed error policy travels", () => {
    expectSent(
      editOf({ ...opened(), errorPolicy: "exclude" }),
      "error_policy",
      "exclude",
    );
  });

  it("returning the error policy to the default sends null", () => {
    expectSent(
      editOf({ ...opened(), errorPolicy: null }),
      "error_policy",
      null,
    );
  });

  it("a new time limit on every run travels as the experiment's limits", () => {
    const out = editOf(withLimits(opened(), { timeout_s: 45 }));
    expectSent(out, "limits", { timeout_s: 45 });
    out.body.prompt_config.forEach((entry) =>
      expect(entry).not.toHaveProperty("row_limits"),
    );
  });

  it("clearing the time limit on every run sends empty limits", () => {
    expectSent(editOf(withLimits(opened(), { timeout_s: null })), "limits", {});
  });

  it("different limits on two runs stay on each run and block Save", () => {
    const out = editOf(
      withLimits(opened(), { timeout_s: 45 }, { timeout_s: 30 }),
    );
    expect(fieldsOf(out)).toContain("prompt_config[].row_limits");
    expect(out.body).not.toHaveProperty("limits");
  });

  it.each([
    ["a prompt", newPrompt],
    ["an agent", newAgent],
  ])(
    "%s run added on Edit starts with the stored time limit and Save sends no limits",
    (_kind, card) => {
      const values = withAdded(opened(), card());
      expect(values.promptConfig.at(-1).rowLimits).toEqual({ timeout_s: 30 });
      const out = editOf(values);
      expect(out.blocks).toEqual([]);
      expect(out.body).not.toHaveProperty("limits");
      expect(out.body.prompt_config).toHaveLength(3);
      out.body.prompt_config.forEach((entry) =>
        expect(entry).not.toHaveProperty("row_limits"),
      );
    },
  );

  it("clearing the time limit on every run, the added ones too, still sends empty limits", () => {
    const values = withAdded(withAdded(opened(), newPrompt()), newAgent());
    const out = editOf(withLimits(values, { timeout_s: null }));
    expect(out.body.prompt_config).toHaveLength(4);
    expectSent(out, "limits", {});
  });

  it("changed metadata travels with the keys the form does not show", () => {
    expectSent(
      editOf({ ...opened(), metadata: [{ key: "team", value: "evals-2" }] }),
      "metadata",
      { team: "evals-2", source: "sdk" },
    );
  });

  it("removing every metadata row is a change", () => {
    expectSent(editOf({ ...opened(), metadata: [] }), "metadata", {
      source: "sdk",
    });
  });

  it("the sample is never sent on an edit", () => {
    const out = editOf({
      ...opened(),
      sample: { on: true, n: 5, seed: 1 },
    });
    expect(out.body).not.toHaveProperty("sample");
    expect(fieldsOf(out)).not.toContain("sample");
  });

  it("each Edit control is open exactly where the contract declares it", () => {
    const base = opened().runSettingsBase;
    EDIT_RUN_SETTINGS.forEach((field) => {
      expect(editSettingLocked(field, { isEditing: true, base })).toBe(
        !declares,
      );
      expect(editSettingLocked(field, { isEditing: false })).toBe(false);
    });
  });

  it("an Edit with no stored settings to compare with keeps every control read-only", () => {
    // Its detail carries no run settings: a change would never be sent.
    EDIT_RUN_SETTINGS.forEach((field) =>
      expect(editSettingLocked(field, { isEditing: true, base: null })).toBe(
        true,
      ),
    );
  });
});

describe("saving an Edit", () => {
  beforeEach(() => {
    contract.declares = true;
  });

  it("a rename with a changed setting saves the name, then the setting", () => {
    const current = {
      ...opened(),
      name: "Exp renamed",
      errorPolicy: "exclude",
    };
    const plan = editSavePlan(editOf(current), {
      original: opened(),
      current,
      datasetId: DATASET,
    });
    expect(plan.metadata).toEqual({ name: "Exp renamed" });
    expect(plan.config).not.toBeNull();
    expect(plan.config.error_policy).toBe("exclude");
    expect(plan.config).not.toHaveProperty("name");
  });

  it("a rename alone still sends no run settings", () => {
    const current = { ...opened(), name: "Exp renamed" };
    const plan = editSavePlan(editOf(current), {
      original: opened(),
      current,
      datasetId: DATASET,
    });
    expect(plan.metadata).toEqual({ name: "Exp renamed" });
    expect(plan.config).toBeNull();
  });
});

describe("while the launch switch holds run settings", () => {
  beforeEach(() => {
    contract.declares = true;
    setLaunchOverrideForTest({ ...LAUNCH, showUnreleased: false });
    unreleased.shown = false;
  });

  it("an unchanged edit blocks nothing", () => {
    expect(editOf(opened()).blocks).toEqual([]);
  });

  it.each([
    ["error_policy", (v) => ({ ...v, errorPolicy: null })],
    ["metadata", (v) => ({ ...v, metadata: [] })],
    ["limits", (v) => withLimits(v, { timeout_s: null })],
  ])(
    "clearing %s blocks Save with its reason, never silently",
    (field, change) => {
      const out = editOf(change(opened()));
      expect(fieldsOf(out)).toEqual([field]);
      expect(out.body).not.toHaveProperty(field);
      expect(out.blocks[0].text).toMatch(/back to what/);
    },
  );
});
