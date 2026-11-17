// An edit's run settings (metadata, error policy, time limit) travel when the
// edit contract (ExperimentUpdateV2) declares them, and block Save with their
// reason when it does not. The contract shape is set per case, so this holds
// on both sides of a contract regeneration. The rows an experiment runs on
// are chosen when it is created: a sample never travels on an edit.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { serverExperimentBody } from "../serverBody";

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

// The edit contract without the run settings, or declaring them. Every other
// definition and the nested-field validator stay as generated.
const contract = vi.hoisted(() => ({
  updateDeclares: false,
  editable: ["metadata", "error_policy", "limits"],
}));
vi.mock("src/api/contracts/strictBody", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    unknownFields: (body, definition) => {
      const unknown = actual.unknownFields(body, definition);
      if (definition !== "ExperimentUpdateV2") return unknown;
      if (contract.updateDeclares)
        return unknown.filter((field) => !contract.editable.includes(field));
      const sent = contract.editable.filter(
        (field) => body?.[field] !== undefined,
      );
      return [...new Set([...unknown, ...sent])].sort();
    },
  };
});

const LAUNCH = { launch: true, experimentsBasic: true, madeReal: [] };
const SETTINGS = { team: "evals" };

beforeEach(() => {
  setLaunchOverrideForTest(LAUNCH);
  unreleased.shown = true;
  contract.updateDeclares = false;
});
afterEach(() => {
  setLaunchOverrideForTest(null);
  unreleased.shown = true;
});

const editOf = (body, initial = { name: "Exp 1", description: "" }) =>
  serverExperimentBody(body, { isEditing: true, initial });

describe("edit contract without run settings", () => {
  it.each([
    ["metadata", SETTINGS],
    ["error_policy", "zero"],
    ["limits", { timeout_s: 30 }],
    ["sample", { size: 2, seed: 17 }],
  ])("blocks Save on %s and sends none of it", (field, value) => {
    const out = editOf({ [field]: value });
    expect(out.definition).toBe("ExperimentUpdateV2");
    expect(out.blocks.map((block) => block.field)).toContain(field);
    expect(out.body).not.toHaveProperty(field);
  });
});

describe("edit contract with run settings", () => {
  beforeEach(() => {
    contract.updateDeclares = true;
  });

  it("sends metadata, error policy and a time limit as set", () => {
    const body = {
      metadata: SETTINGS,
      error_policy: "zero",
      limits: { timeout_s: 30 },
    };
    const original = structuredClone(body);
    const out = editOf(body);
    expect(out.blocks).toEqual([]);
    expect(out.body).toEqual(original);
    expect(out.metadata).toEqual({});
    expect(body).toEqual(original);
  });

  it("still blocks a sample: an edit keeps the rows", () => {
    const out = editOf({
      error_policy: "exclude",
      sample: { size: 2, seed: 17 },
    });
    expect(out.blocks.map((block) => block.field)).toEqual(["sample"]);
    expect(out.body).toEqual({ error_policy: "exclude" });
  });

  it("keeps a new name apart from the run settings", () => {
    const out = editOf({ name: "Exp renamed", error_policy: "zero" });
    expect(out.blocks).toEqual([]);
    expect(out.metadata).toEqual({ name: "Exp renamed" });
    expect(out.body).toEqual({ error_policy: "zero" });
  });

  it("holds the settings while the launch switch hides them", () => {
    setLaunchOverrideForTest({ ...LAUNCH, showUnreleased: false });
    unreleased.shown = false;
    const out = editOf({ metadata: SETTINGS, error_policy: "zero" });
    expect(out.blocks.map((block) => block.field).sort()).toEqual([
      "error_policy",
      "metadata",
    ]);
    expect(out.body).toEqual({});
  });
});
