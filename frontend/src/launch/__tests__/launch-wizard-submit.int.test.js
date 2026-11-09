// L6 x L2 integration (L6 fix 2, item 1): with experimentsBasic ON, an
// unsupported New experiment setting can never be submitted, and a held
// (stale, copied, door-preselected) non-default value is rejected explicitly,
// never dropped without a word. L6 hides each control while it holds its
// default (src/launch/wizardSettings.js) and shows it again once it holds a
// value; L2 derives the body dev reads and blocks Run on every field dev
// refuses (serverBody.js). Together, for each hidden setting:
//   - untouched: the control is absent and the body dev reads has no such
//     field and no block (Run works);
//   - held: the control shows (so the value can be seen and cleared), the
//     body dev reads still has no such field, and Run is blocked with the
//     setting's own reason on the setting's own step.
// Needs L2's serverBody.js / experimentBody.js: it runs on the integration
// (L6 + L2), not on either lane alone.
import { afterEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { wizardSettingShown } from "src/launch/wizardSettings";
import { emptyExperimentValues } from "src/sections/develop-detail/Experiment/experimentFormValues";
import { experimentBodyOfValues } from "src/sections/develop-detail/Experiment/experimentBody";
import { wireExperimentBody } from "src/sections/develop-detail/Experiment/serverBody";
import { EXPERIMENT_STEP } from "src/sections/develop-detail/Experiment/constants";

const DATASET = "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01";
const Q = { id: "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51", name: "question" };
const BASIC = { launch: true, experimentsBasic: true, madeReal: [] };

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

const plain = (over = {}) => ({
  ...emptyExperimentValues(),
  name: "L6 x L2",
  promptConfig: [promptCard()],
  ...over,
});

const wire = (raw) =>
  wireExperimentBody(
    experimentBodyOfValues(raw, { datasetId: DATASET }),
    { columns: [Q] },
    false,
  );

// setting -> [held form values, the body field dev refuses, its step]
const HELD = {
  description: [
    { description: "copied notes" },
    "description",
    "CONFIGURATION",
  ],
  version: [
    { datasetVersionId: "9a8b7c6d-0000-4000-8000-000000000003" },
    "dataset_version_id",
    "DATA",
  ],
  splits: [{ splitIds: ["split-a"] }, "split_ids", "DATA"],
  theseRows: [{ rowIds: [Q.id] }, "row_ids", "DATA"],
  sample: [{ sample: { on: true, n: 2, seed: 7 } }, "sample", "DATA"],
  rowLimits: [
    { promptConfig: [promptCard({ rowLimits: { tokens: 500 } })] },
    "limits",
    "CONFIGURATION",
  ],
  moreSettings: [
    {
      startFrom: {
        experimentId: "e0e0e0e0-0000-4000-8000-000000000004",
        preset: "same-runs",
      },
    },
    "preset",
    "CONFIGURATION",
  ],
};

const heldOf = (setting, raw) => {
  switch (setting) {
    case "description":
      return Boolean(String(raw.description ?? "").trim());
    case "version":
      return Boolean(raw.datasetVersionId);
    case "splits":
      return (raw.splitIds ?? []).length > 0;
    case "theseRows":
      return (raw.rowIds ?? []).length > 0;
    case "sample":
      return Boolean(raw.sample?.on);
    case "rowLimits":
      return Object.values(raw.promptConfig?.[0]?.rowLimits ?? {}).some(
        (v) => v != null && v !== "",
      );
    case "moreSettings":
      return Boolean(raw.startFrom?.experimentId);
    default:
      throw new Error(setting);
  }
};

afterEach(() => {
  vi.unstubAllEnvs();
  setLaunchOverrideForTest(null);
});

const qualifyScope = () => {
  // Enabled-path controls use the existing separately compiled census profile.
  vi.stubEnv("VITE_DSX_CENSUS_B_TEST", "true");
  setLaunchOverrideForTest({ ...BASIC, madeReal: ["exp.new.scope"] });
};

describe("L6 x L2: unsupported settings cannot be submitted", () => {
  it("untouched: every hidden control is absent and the body dev reads runs as is", () => {
    setLaunchOverrideForTest(BASIC);
    const raw = plain();
    Object.keys(HELD).forEach((setting) => {
      expect(heldOf(setting, raw), setting).toBe(false);
      expect(wizardSettingShown(setting, heldOf(setting, raw)), setting).toBe(
        false,
      );
    });
    const out = wire(raw);
    expect(out.blocks).toEqual([]);
    [
      "description",
      "dataset_version_id",
      "split_ids",
      "row_ids",
      "sample",
      "trials",
      "max_concurrency",
      "preset",
    ].forEach((k) => expect(out.body, k).not.toHaveProperty(k));
    expect(out.body.prompt_config[0]).not.toHaveProperty("row_limits");
    for (const field of ["metadata", "limits", "error_policy"])
      expect(out.body).not.toHaveProperty(field);
    expect(wizardSettingShown("theseRows", false)).toBe(false);
    expect(wizardSettingShown("sample", false)).toBe(false);
  });

  it.each([
    ["theseRows", { rowIds: [Q.id] }, "row_ids", [Q.id]],
    [
      "sample",
      { sample: { on: true, n: 2, seed: 7 } },
      "sample",
      { size: 2, seed: 7 },
    ],
  ])(
    "transports %s only under independently qualified F05",
    (setting, over, field, value) => {
      qualifyScope();
      const raw = plain(over);
      expect(wizardSettingShown(setting, heldOf(setting, raw))).toBe(true);
      const out = wire(raw);
      expect(out.blocks).toEqual([]);
      expect(out.body[field]).toEqual(value);
    },
  );

  it("transports split_ids independently of held F05 without injecting policy", () => {
    setLaunchOverrideForTest(BASIC);
    const raw = plain({ splitIds: [Q.id] });
    expect(wizardSettingShown("splits", heldOf("splits", raw))).toBe(true);
    const out = wire(raw);
    expect(out.blocks).toEqual([]);
    expect(out.body.split_ids).toEqual([Q.id]);
    for (const field of [
      "metadata",
      "limits",
      "error_policy",
      "row_ids",
      "sample",
    ])
      expect(out.body).not.toHaveProperty(field);
  });

  it("transports an identical row policy only under independently qualified F05", () => {
    qualifyScope();
    const out = wire(plain(HELD.rowLimits[0]));
    expect(out.blocks).toEqual([]);
    expect(out.body.limits).toEqual({ tokens: 500 });
    expect(out.body.error_policy).toBe("zero");
    expect(out.body.prompt_config[0]).not.toHaveProperty("row_limits");
  });

  Object.entries(HELD)
    .filter(([setting]) => setting !== "splits")
    .forEach(([setting, [over, field, step]]) => {
      it(`held ${setting}: the control shows and Run is blocked on ${field}, never sent`, () => {
        setLaunchOverrideForTest(BASIC);
        const raw = plain(over);
        const held = heldOf(setting, raw);
        expect(held).toBe(true);
        expect(wizardSettingShown(setting, held)).toBe(true);
        const out = wire(raw);
        const block = out.blocks.find((b) => b.field === field);
        expect(block, `${setting}: ${JSON.stringify(out.blocks)}`).toBeTruthy();
        expect(block.step).toBe(EXPERIMENT_STEP[step]);
        expect(block.generic).toBe(false);
        const top = field.replace(/^prompt_config\[\]\./, "");
        if (field.startsWith("prompt_config[]."))
          expect(out.body.prompt_config[0]).not.toHaveProperty(top);
        else expect(out.body).not.toHaveProperty(top);
      });
    });

  it("a stale form blocks held F05 and unsupported settings, preserving independent split_ids", () => {
    setLaunchOverrideForTest(BASIC);
    const raw = plain(
      Object.values(HELD).reduce(
        (acc, [over]) => ({
          ...acc,
          ...over,
          promptConfig: over.promptConfig ?? acc.promptConfig,
        }),
        {},
      ),
    );
    const out = wire(raw);
    const fields = out.blocks.map((b) => b.field);
    expect(fields.sort()).toEqual(
      // Picked rows take precedence over the sample draft in experimentExtras.
      [
        "description",
        "dataset_version_id",
        "preset",
        "row_ids",
        "limits",
      ].sort(),
    );
    expect(fields).not.toContain("prompt_config[].row_limits");
    expect(out.body.prompt_config[0]).not.toHaveProperty("row_limits");
    for (const field of ["metadata", "limits", "error_policy"])
      expect(out.body).not.toHaveProperty(field);
    expect(out.body.split_ids).toEqual(HELD.splits[0].splitIds);
    [
      "description",
      "dataset_version_id",
      "row_ids",
      "sample",
      "preset",
    ].forEach((k) => expect(out.body, k).not.toHaveProperty(k));
  });
});
