import { describe, expect, it } from "vitest";
import {
  selectedConfigErrors,
  hasDuplicateCheckIdentity,
} from "./guardrailChecks";

const moderation = (config, threshold = 0.8) =>
  selectedConfigErrors("content-moderation", config, threshold);

describe("selected configuration readiness R4", () => {
  it.each(["prompt-injection", "injection-detector"])(
    "checks original %s constructor input",
    (name) => {
      const categories = [
        "structured_role_injection",
        "instruction_override",
        "role_manipulation",
        "system_prompt_extraction",
        "delimiter_injection",
        "encoding_bypass",
      ];
      for (const config of [
        undefined,
        {},
        { categories: [] },
        { categories, sensitivity: "HIGH" },
        { sensitivity: "Medium" },
        { sensitivity: "low", extra: [42] },
      ]) {
        expect(selectedConfigErrors(name, config)).toEqual({});
      }
      for (const config of [
        { categories: [42] },
        { categories: ["unknown"] },
        { categories: [categories[0], 42] },
        { categories: null },
        { sensitivity: null },
        { sensitivity: 42 },
        { sensitivity: " high " },
        { sensitivity: "unknown" },
        null,
        [],
      ]) {
        expect(
          Object.keys(selectedConfigErrors(name, config)).length,
        ).toBeGreaterThan(0);
      }
    },
  );

  it.each([0, -1, 0.5, 50.5, true, null, "3", Infinity, NaN, 2 ** 63])(
    "rejects invalid category match count %s without truncation",
    (category_threshold) => {
      expect(moderation({ category_threshold }, 0)).toHaveProperty(
        "category_threshold",
      );
    },
  );

  it.each([
    [{}, 0.8, true],
    [{}, 1, false],
    [{ categories: [], category_threshold: 3 }, 0.8, true],
    [{ categories: ["toxicity"], category_threshold: 50 }, 0.3, false],
    [{ categories: ["toxicity"], category_threshold: 50 }, 0.29, true],
    [{ categories: ["toxicity"], category_threshold: 1000 }, 0.01, true],
    [{ categories: ["toxicity"], category_threshold: 1000 }, 0.015, false],
    [{ categories: ["sexual"], category_threshold: 20 }, 0.65, false],
    [{ categories: ["sexual"], category_threshold: 20 }, 0.64, true],
    [
      {
        categories: ["toxicity", "sexual"],
        category_threshold: 16,
        weights: { sexual: 100 },
      },
      0.9,
      true,
    ],
    [
      {
        categories: ["toxicity", "sexual"],
        category_threshold: 20,
        weights: { sexual: 3 },
      },
      0.675,
      false,
    ],
    [
      {
        categories: ["toxicity", "sexual"],
        category_threshold: 20,
        weights: { sexual: 3 },
      },
      0.674,
      true,
    ],
    [
      {
        categories: ["toxicity", "toxicity", "sexual"],
        category_threshold: 20,
      },
      0.7,
      false,
    ],
    [{ category_threshold: 2 ** 62 }, 0, true],
    [
      { weights: { toxicity: 0, sexual: -1, unused: { keep: true } } },
      0.8,
      true,
    ],
    [{ weights: { toxicity: "1" } }, 0.8, false],
    [{ weights: { toxicity: true } }, 0.8, false],
    [{ weights: { toxicity: Infinity } }, 0.8, false],
    [{ weights: { toxicity: 1e308, sexual: 1e308 } }, 0.8, false],
    [{ weights: null }, 0.8, false],
  ])(
    "uses actual maximum score and strict outer threshold %#",
    (config, threshold, valid) => {
      expect(Object.keys(moderation(config, threshold)).length === 0).toBe(
        valid,
      );
    },
  );

  it.each([
    ["pii-detector", "pii-detection"],
    ["pii-detection", "pii-detection"],
    ["injection-detector", "prompt-injection"],
    ["secrets-detector", "secret-detection"],
    ["custom-static", "custom-static"],
  ])(
    "detects canonical collisions %s/%s regardless of enabled or order",
    (first, second) => {
      for (const enabled of [true, false]) {
        const checks = [
          { name: first, enabled },
          { name: second, enabled: false },
        ];
        expect(hasDuplicateCheckIdentity(checks)).toBe(true);
        expect(hasDuplicateCheckIdentity([...checks].reverse())).toBe(true);
      }
      expect(
        hasDuplicateCheckIdentity([{ name: first }, { name: "unrelated" }]),
      ).toBe(false);
    },
  );
});
