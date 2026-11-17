import { beforeEach, describe, expect, it, vi } from "vitest";
import { allowNaApplies } from "./allowNaModel";
import { buildEvalTemplateConfig } from "./evalPickerConfigUtils";
import { serializeEvalConfig } from "./serializeEvalConfig";
import { cardTestBodyOf } from "src/sections/develop-detail/Experiment/StepperComponentExperiment/evalCardModel-g7";

const mode = vi.hoisted(() => ({ prototype: false }));
vi.mock("src/utils/prototype", () => ({
  isPrototypeMode: () => mode.prototype,
}));

// Only prototype mocks implement allow_na. The real update contract rejects
// it, including when a previously saved prototype config is reopened.
describe.each([false, true])(
  "eval payload parity (prototype=%s)",
  (prototype) => {
    beforeEach(() => {
      mode.prototype = prototype;
    });

    it.each(["llm", "agent"])(
      "offers N/A only for prototype %s judges",
      (evalType) => {
        expect(allowNaApplies({ evalType })).toBe(prototype);
      },
    );

    it.each([
      { evalType: "code" },
      { evalType: "llm", isSystemEval: true },
      { evalType: "llm", decisionModel: "decision-judge" },
      { evalType: "llm", supportsNa: false },
    ])("does not widen the existing exclusions (%j)", (input) => {
      expect(allowNaApplies(input)).toBe(false);
    });

    it.each([true, false])(
      "cleans template and binding configs without mutation (stored=%s)",
      (allowNa) => {
        const template = Object.freeze({
          allow_na: !allowNa,
          retained: "template",
        });
        const binding = Object.freeze({
          allow_na: allowNa,
          retained: "binding",
        });
        for (const baseConfig of [template, { ...template, ...binding }]) {
          const config = buildEvalTemplateConfig({
            baseConfig,
            evalType: "llm",
            instructions: "Judge {{output}}",
            outputType: "percentage",
            passThreshold: 0.7,
            choiceScores: { Good: 1, Bad: 0 },
          });
          expect(config).toMatchObject({
            retained: baseConfig.retained,
            rule_prompt: "Judge {{output}}",
            output: "score",
            pass_threshold: 0.7,
            choice_scores: { Good: 1, Bad: 0 },
          });
          if (prototype) expect(config.allow_na).toBe(baseConfig.allow_na);
          else expect(config).not.toHaveProperty("allow_na");
        }
        expect(template.allow_na).toBe(!allowNa);
        expect(binding.allow_na).toBe(allowNa);
      },
    );

    it("cleans a legacy stored config at serialization, not just newly built configs", () => {
      const config = Object.freeze({
        allow_na: true,
        retained: "keep",
        run_config: { model: "saved-model" },
      });
      const body = serializeEvalConfig({
        templateId: "template-1",
        config,
        mapping: { output: "output-column" },
        pass_threshold: 0.8,
      });
      expect(body.config).toMatchObject({
        retained: "keep",
        run_config: { model: "saved-model", pass_threshold: 0.8 },
      });
      if (prototype) expect(body.config.allow_na).toBe(true);
      else expect(body.config).not.toHaveProperty("allow_na");
      expect(config.allow_na).toBe(true);
    });

    it("does not forward a legacy card's N/A flag to a real Test request", () => {
      const config = Object.freeze({
        allow_na: true,
        run_config: { model: "saved-model" },
      });
      const body = cardTestBodyOf({
        templateId: "template-1",
        card: { config },
        instructions: "Judge {{output}}",
        threshold: 0.8,
        outputType: "score",
      });
      expect(body.config).toMatchObject({
        rule_prompt: "Judge {{output}}",
        threshold: 0.8,
        output_type: "score",
        run_config: { model: "saved-model" },
      });
      if (prototype) expect(body.config.allow_na).toBe(true);
      else expect(body.config).not.toHaveProperty("allow_na");
      expect(config.allow_na).toBe(true);
    });
  },
);
