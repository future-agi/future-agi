import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import { wizardSettingShown } from "src/launch/wizardSettings";
import {
  UNAVAILABLE_ROW_LIMITS_TEXT,
  rowLimitStepIssues,
} from "src/sections/common/RunSettings/rowLimitsModel";
import { emptyExperimentValues } from "../experimentFormValues";
import { experimentBodyOfValues } from "../experimentBody";
import { runScopeBody } from "../runScopeBody";
import { serverExperimentBody } from "../serverBody";

// Both states of the show-unreleased switch are pinned in every build. The
// switch is read two ways: launchSwitch's own gates read the test override
// (showUnreleased), and product modules read the exported constant. Each
// state sets both the same way; the export follows this flag.
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

// Exercise both the currently published contract and the cloud-regenerated OFF
// contract. The real nested-field validator remains in use in both arms.
const schema = vi.hoisted(() => ({ scopeEnabled: true }));
const fields = ["row_ids", "sample", "metadata", "error_policy", "limits"];
vi.mock("src/api/contracts/strictBody", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    unknownFields: (body, definition) => [
      ...actual.unknownFields(body, definition),
      ...(!schema.scopeEnabled
        ? fields.filter((field) => Object.hasOwn(body, field))
        : []),
    ],
  };
});

// madeReal is empty on purpose: madeReal.js marks the run-settings surface as
// released, which would show it with the switch off too and leave no hold to
// test. With nothing claimed, the switch alone decides whether it is held.
const LAUNCH = { launch: true, experimentsBasic: true, madeReal: [] };

beforeEach(() => {
  vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "false");
  schema.scopeEnabled = true;
});
afterEach(() => {
  setLaunchOverrideForTest(null);
  unreleased.shown = true;
  vi.unstubAllEnvs();
});

const id = "11111111-1111-4111-8111-111111111111";

// Each run setting as the wizard holds it, and as the create body sends it.
const SETTINGS = [
  ["row_ids", [id], [id]],
  ["sample", { n: 1, seed: 17 }, { size: 1, seed: 17 }],
  ["metadata", { purpose: "stale draft" }, { purpose: "stale draft" }],
  ["error_policy", "exclude", "exclude"],
  ["limits", { timeout_s: 5 }, { timeout_s: 5 }],
];

const basicCreates = () => {
  for (const card of [
    {
      targetType: "prompt",
      promptId: id,
      promptVersion: id,
      model: [{ value: "gpt-4o-mini", providers: "openai" }],
    },
    { targetType: "agent", agentId: id, agentVersion: id, name: "Graph" },
  ]) {
    const body = experimentBodyOfValues(
      {
        ...emptyExperimentValues(),
        name: "Basic experiment",
        promptConfig: [card],
      },
      { datasetId: id },
    );
    const result = serverExperimentBody(body);
    expect(result.body.prompt_config).toHaveLength(1);
    expect(result.blocks).toEqual([]);
    for (const field of fields) expect(result.body).not.toHaveProperty(field);
  }
};

describe("switch off: run settings are held while basic creates run", () => {
  beforeEach(() => {
    setLaunchOverrideForTest({ ...LAUNCH, showUnreleased: false });
    unreleased.shown = false;
  });

  it("hides scope but keeps stale controls visible to clear", () => {
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown("exp.new.scope")).toBe(false);
    for (const setting of ["rowLimits", "sample", "theseRows"]) {
      expect(wizardSettingShown(setting)).toBe(false);
      expect(wizardSettingShown(setting, true)).toBe(true);
    }
  });

  it.each([true, false])(
    "does not block basic creates with schema ON=%s",
    (enabled) => {
      schema.scopeEnabled = enabled;
      basicCreates();
    },
  );

  it.each(SETTINGS)(
    "refuses a held %s even when the contract advertises it",
    (field, value) => {
      const body = { [field]: value };
      const original = structuredClone(body);
      const result = serverExperimentBody(body);
      expect(result.blocks.map((block) => block.field)).toContain(field);
      expect(result.body).not.toHaveProperty(field);
      expect(body).toEqual(original);
    },
  );

  it.each([true, false])(
    "sends no error policy by default and holds a chosen one, schema ON=%s",
    (enabled) => {
      schema.scopeEnabled = enabled;
      const plain = serverExperimentBody({});
      expect(plain.blocks).toEqual([]);
      expect(plain.body).not.toHaveProperty("error_policy");
      const chosen = serverExperimentBody({ error_policy: "zero" });
      expect(chosen.blocks.map((block) => block.field)).toContain(
        "error_policy",
      );
      expect(chosen.body).not.toHaveProperty("error_policy");
      expect(
        serverExperimentBody(
          { error_policy: "zero" },
          { isEditing: true },
        ).blocks.map((block) => block.field),
      ).toContain("error_policy");
    },
  );

  it("does not silently discard per-run limits", () => {
    const result = serverExperimentBody({
      prompt_config: [{ row_limits: { timeout_s: 5 } }],
    });
    expect(result.blocks.map((block) => block.field)).toContain("limits");
    expect(result.body).not.toHaveProperty("limits");
  });

  it("does not put split selection under the run-settings hold", () => {
    const result = serverExperimentBody({ split_ids: [id] });
    expect(result.body.split_ids).toEqual([id]);
    expect(result.blocks).toEqual([]);
    expect(result.body).not.toHaveProperty("error_policy");
  });
});

describe("as shipped: run settings show and travel as set", () => {
  beforeEach(() => {
    setLaunchOverrideForTest(LAUNCH);
    unreleased.shown = true;
  });

  it("shows the run settings", () => {
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown("exp.new.scope")).toBe(true);
    for (const setting of ["rowLimits", "sample", "theseRows"])
      expect(wizardSettingShown(setting)).toBe(true);
  });

  it.each([true, false])(
    "does not block basic creates with schema ON=%s",
    (enabled) => {
      schema.scopeEnabled = enabled;
      basicCreates();
    },
  );

  it.each(SETTINGS)("sends %s as set", (field, value, sent) => {
    const body = { [field]: value };
    const original = structuredClone(body);
    const result = serverExperimentBody(body);
    expect(result.blocks.map((block) => block.field)).not.toContain(field);
    expect(result.body[field]).toEqual(sent);
    expect(body).toEqual(original);
  });

  it.each([true, false])(
    "sends no error policy by default and zero only as a choice, schema ON=%s",
    (enabled) => {
      schema.scopeEnabled = enabled;
      const plain = serverExperimentBody({});
      expect(plain.blocks).toEqual([]);
      expect(plain.body).not.toHaveProperty("error_policy");
      const chosen = serverExperimentBody({ error_policy: "zero" });
      if (enabled) {
        expect(chosen.blocks).toEqual([]);
        expect(chosen.body.error_policy).toBe("zero");
      } else {
        // A contract without the field blocks a chosen policy with its reason.
        expect(chosen.blocks.map((block) => block.field)).toContain(
          "error_policy",
        );
        expect(chosen.body).not.toHaveProperty("error_policy");
      }
      expect(
        serverExperimentBody(
          { error_policy: "zero" },
          { isEditing: true },
        ).blocks.map((block) => block.field),
      ).toContain("error_policy");
    },
  );

  it("sends identical per-run time limits as the experiment's limits", () => {
    const result = serverExperimentBody({
      prompt_config: [{ row_limits: { timeout_s: 5 } }],
    });
    expect(result.blocks.map((block) => block.field)).not.toContain("limits");
    expect(result.body.limits).toEqual({ timeout_s: 5 });
    expect(result.body.prompt_config[0]).not.toHaveProperty("row_limits");
  });

  it.each([{ tokens: 5 }, { messages: 2 }, { cost_usd: 0.05 }])(
    "refuses a %j limit with the release line, and accepts a time limit alone",
    (limits) => {
      expect(rowLimitStepIssues({ rowLimits: limits })).toEqual([
        UNAVAILABLE_ROW_LIMITS_TEXT,
      ]);
      expect(rowLimitStepIssues({ rowLimits: { timeout_s: 5 } })).toEqual([]);
    },
  );

  it("does not put split selection under the run settings", () => {
    const result = serverExperimentBody({ split_ids: [id] });
    expect(result.body.split_ids).toEqual([id]);
    expect(result.blocks).toEqual([]);
    expect(result.body).not.toHaveProperty("error_policy");
  });
});

describe("in any build", () => {
  it("does not inject a failed-row policy into an ordinary body", () => {
    expect(runScopeBody({ name: "Plain", metadata: {} })).toEqual({
      name: "Plain",
    });
  });

  it("adds no error policy outside the launch build either", () => {
    setLaunchOverrideForTest({ launch: false, experimentsBasic: true });
    expect(runScopeBody({})).toEqual({});
    expect(runScopeBody({ error_policy: "exclude" })).toEqual({
      error_policy: "exclude",
    });
  });
});
