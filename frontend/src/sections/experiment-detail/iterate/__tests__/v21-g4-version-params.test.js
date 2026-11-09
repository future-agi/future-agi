// v2.1 g4 (knob:prompt:version, knob:prompt:models): a model row filled
// with the model's defaults before the prompt version loaded takes the
// version's temperature and max tokens; a row the user changed keeps its
// values. (The card's popover offers only the params the engine reads.)
import { describe, expect, it } from "vitest";
import { versionParamsUpdate } from "src/sections/develop-detail/Experiment/StepperComponentExperiment/NewModelRenderWithParamsTool";

const DEFAULTS = { temperature: 0.7, maxTokens: 1024, topP: 1 };
const VERSION = { temperature: 0.3, max_tokens: 400 };

describe("versionParamsUpdate", () => {
  it("replaces model defaults with the version's params", () => {
    expect(
      versionParamsUpdate({ ...DEFAULTS }, DEFAULTS, VERSION),
    ).toMatchObject({ temperature: 0.3, maxTokens: 400, topP: 1 });
  });

  it("keeps a row the user changed", () => {
    expect(
      versionParamsUpdate({ ...DEFAULTS, temperature: 1.5 }, DEFAULTS, VERSION),
    ).toBeNull();
  });

  it("no change when the row already has the version's params", () => {
    expect(
      versionParamsUpdate(
        { temperature: 0.3, maxTokens: 400 },
        { temperature: 0.3, maxTokens: 400 },
        VERSION,
      ),
    ).toBeNull();
    expect(versionParamsUpdate(null, DEFAULTS, VERSION)).toBeNull();
    expect(versionParamsUpdate({ ...DEFAULTS }, DEFAULTS, null)).toBeNull();
  });
});
