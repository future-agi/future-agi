import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import { wizardSettingShown } from "src/launch/wizardSettings";
import { emptyExperimentValues } from "../experimentFormValues";
import { experimentBodyOfValues } from "../experimentBody";
import { runScopeBody } from "../runScopeBody";
import { serverExperimentBody } from "../serverBody";

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

beforeEach(() => {
  vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "false");
  schema.scopeEnabled = true;
  setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
});
afterEach(() => {
  setLaunchOverrideForTest(null);
  vi.unstubAllEnvs();
});

const id = "11111111-1111-4111-8111-111111111111";

describe("F05 stays held when F01/F02 are enabled", () => {
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
        for (const field of fields)
          expect(result.body).not.toHaveProperty(field);
      }
    },
  );

  it.each([
    ["row_ids", [id]],
    ["sample", { n: 1, seed: 17 }],
    ["metadata", { purpose: "stale draft" }],
    ["error_policy", "exclude"],
    ["error_policy", "zero"],
    ["limits", { tokens: 5 }],
  ])(
    "refuses stale %s even when the contract still advertises it",
    (field, value) => {
      const body = { [field]: value };
      const original = structuredClone(body);
      const result = serverExperimentBody(body);
      expect(result.blocks.map((block) => block.field)).toContain(field);
      expect(result.body).not.toHaveProperty(field);
      expect(body).toEqual(original);
    },
  );

  it("does not silently discard per-run limits", () => {
    const result = serverExperimentBody({
      prompt_config: [{ row_limits: { tokens: 5 } }],
    });
    expect(result.blocks.map((block) => block.field)).toContain("limits");
  });

  it("does not inject the F05 failed-row policy into an ordinary body", () => {
    expect(runScopeBody({ name: "Plain", metadata: {} })).toEqual({
      name: "Plain",
    });
  });

  it("does not put F50 split selection under the F05 hold", () => {
    const result = serverExperimentBody({ split_ids: [id] });
    expect(result.body.split_ids).toEqual([id]);
    expect(result.blocks).toEqual([]);
    expect(result.body).not.toHaveProperty("error_policy");
  });

  it("retains qualified scope behavior outside the launch build", () => {
    setLaunchOverrideForTest({ launch: false, experimentsBasic: true });
    expect(runScopeBody({})).toEqual({ error_policy: "zero" });
  });
});
