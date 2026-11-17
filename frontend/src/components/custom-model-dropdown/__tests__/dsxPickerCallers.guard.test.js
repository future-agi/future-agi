// OK-B coverage guard: every DSX surface named by the census items reaches the
// managed-or-BYOK decision. Shared-picker callers inherit it from
// ShowModelDropdown, so none may opt out of the gate; the two surfaces with
// their own menus read the same adapter. RED on base 651ab5bb: the gate is
// `!option.isAvailable && requireUserApiKey` and no adapter exists.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";

const read = (file) => readFileSync(resolve(process.cwd(), file), "utf8");

const SHARED_PICKER_CALLERS = [
  ["R1-01 Run Prompt", "src/sections/develop-detail/RunPrompt/RunPrompt.jsx"],
  [
    "R1-03 Classification",
    "src/sections/develop-detail/AddColumn/Classification/Classification.jsx",
  ],
  [
    "R1-03 Extract Entities",
    "src/sections/develop-detail/AddColumn/ExtractEntities/ExtractEntities.jsx",
  ],
  [
    "R1-13 Experiment model variants",
    "src/sections/develop-detail/Experiment/StepperComponentExperiment/NewModelRenderWithParamsTool.jsx",
  ],
  [
    "R1-27 / R2-24 Run Optimization",
    "src/sections/develop-detail/Optimization/RunOptimization.jsx",
  ],
  [
    "R1-27 / R2-24 Dataset optimization drawer",
    "src/sections/develop-detail/DatasetOptimization/DatasetOptimizationDrawer.jsx",
  ],
  [
    "R2-27 Simulation optimizer",
    "src/sections/test-detail/CreateEditOptimization/CreateEditOptimizationForm.jsx",
  ],
  [
    "R2-13 Prompt workbench",
    "src/sections/workbench/createPrompt/Playground/ModelContainer.jsx",
  ],
];

const OWN_MENU_SURFACES = [
  [
    "R1-06 Dataset eval config model menu",
    "src/sections/develop-detail/Common/EvaluationConfigure/ModelSelectInput.jsx",
  ],
  [
    "R2-15 Evaluator model picker",
    "src/sections/evals/components/ModelSelector.jsx",
  ],
];

// The picker modules that make or display the eligibility decision. Only the
// adapter may spell the contract field names, so the real-contract swap is
// one file.
const PICKER_DIR = "src/components/custom-model-dropdown";
const PICKER_MODULES = [
  "common.js",
  "constants.js",
  "CustomModelDropdown.jsx",
  "CustomModelDropdownControl.jsx",
  "ShowModelDropdown.jsx",
];

describe("DSX model pickers reach the managed-or-BYOK decision", () => {
  it.each(SHARED_PICKER_CALLERS)(
    "%s mounts the shared picker without opting out of its gate",
    (_surface, file) => {
      const source = read(file);
      expect(source).toMatch(
        /from "src\/components\/custom-model-dropdown\/CustomModelDropdown(Control)?"/,
      );
      expect(source).not.toMatch(/requireUserApiKey=\{false\}/);
    },
  );

  it("the shared gate asks the adapter, not the bare isAvailable flag", () => {
    const source = read(`${PICKER_DIR}/ShowModelDropdown.jsx`);
    expect(source).toMatch(/pickerEligibility\(option\)/);
    expect(source).not.toMatch(/!option\.isAvailable && requireUserApiKey/);
    expect(source).not.toMatch(/requireUserApiKey && !isAvailable/);
  });

  it.each(OWN_MENU_SURFACES)(
    "%s reads eligibility through the adapter",
    (_surface, file) => {
      expect(read(file)).toMatch(
        /from "src\/sections\/develop-detail\/Common\/modelAccess"/,
      );
    },
  );

  it("only the adapter spells the contract field names", () => {
    const files = [
      ...PICKER_MODULES.map((name) => `${PICKER_DIR}/${name}`),
      ...OWN_MENU_SURFACES.map(([, file]) => file),
      "src/sections/develop-detail/Common/modelAccess.js",
    ];
    const spelled = files.filter((file) =>
      /["'](?:credential_mode|credentialMode|future_agi_credits|own_provider_key)["']/.test(read(file)),
    );
    expect(spelled).toEqual(["src/sections/develop-detail/Common/modelAccess.js"]);
  });
});
