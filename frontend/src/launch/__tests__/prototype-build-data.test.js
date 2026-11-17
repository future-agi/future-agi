import { afterEach, describe, expect, it, vi } from "vitest";
import * as preset from "src/test-fixtures/build-data/decisionPreset";
import * as fixture from "src/test-fixtures/build-data/harnessDefaults";
import { GLOSSARY as fixtureGlossary } from "src/test-fixtures/build-data/glossary";

const load = async (environment, enabled, mode = "production") => {
  vi.resetModules();
  vi.stubEnv("MODE", mode);
  vi.stubEnv("VITE_ENVIRONMENT", environment);
  vi.stubEnv("VITE_PROTOTYPE_MODE", enabled);
  const [decisions, glossary, defaults, values, harness] = await Promise.all([
    import("src/sections/common/DecisionJudges/decisionModels"),
    import("src/sections/common/ux/glossary"),
    import("src/sections/develop-detail/Experiment/harnessDefaults"),
    import("src/sections/develop-detail/Experiment/experimentFormValues"),
    import("src/sections/common/RunSources/snippets/prototypeHarness"),
  ]);
  return { decisions, glossary, defaults, values, harness };
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("local fixture build boundary", () => {
  it.each([
    ["production", "false"],
    ["production", "true"],
    ["local", "false"],
  ])("omits fixture data for %s / %s", async (environment, enabled) => {
    const { decisions, glossary, defaults, values, harness } = await load(
      environment,
      enabled,
    );
    for (const model of Object.keys(preset.PRICES)) {
      expect(decisions.DECISION_PRICES[model]).toBeUndefined();
      expect(decisions.RESOLVED_MODEL[model]).toBeUndefined();
      expect(decisions.decisionModelOf(model)).toBeNull();
      expect(
        decisions.pickerRowsOf({ data: [] }).some((row) => row.model === model),
      ).toBe(false);
    }
    expect(glossary.glossaryEntry("harness")).toBeNull();
    expect(glossary.searchGlossary("harness")).toEqual([]);
    expect(defaults.HARNESSES).toEqual([]);
    expect(defaults.SEED_INSTRUCTIONS).toBe("");
    expect(values.DEFAULT_HARNESS.name).toBe("");
    expect(values.DEFAULT_HARNESS.model).toBe("");
    expect(harness.HARNESS_FILE).toBeNull();
    expect(harness.UPLOAD_LINE).toBeNull();
    expect(() => harness.harnessScript({ entry: undefined, ids: {} })).toThrow(
      /unavailable/,
    );
    expect(() => harness.harnessTryCommand({})).toThrow(/unavailable/);
    expect(() => harness.steps({ entry: undefined })).toThrow(/unavailable/);

    // Shared providers and environment utilities must not be suppressed.
    expect(decisions.decisionModelOf("typesafe/jev-1.13.0").inputPerMTok).toBe(
      0.042,
    );
    expect(defaults.wordCount("one two")).toBe(2);
    expect(
      defaults.sandboxFrom({ image: "registry/team/image:1" }).sandbox,
    ).toBe("Docker · image:1");
    expect(
      harness.excludedToolsOf(
        { tool_names: ["a", "b"], disabled_tools: ["b"] },
        "gemini",
      ),
    ).toEqual(["b"]);
    const parsers = await import(
      "src/sections/common/RunSources/snippets/harnessParsers"
    );
    expect(parsers.parserOf("custom_command")).toBe("custom");
    expect(parsers.parserLines("custom").join("\n")).toContain(
      "def parse_try(lines, folder):",
    );
  });

  it.each([
    ["local", "true", "production"],
    ["production", "false", "test"],
  ])("preserves fixture behavior for %s / %s / %s", async (...args) => {
    const { decisions, glossary, defaults, values, harness } = await load(
      ...args,
    );
    for (const [model, price] of Object.entries(preset.PRICES)) {
      expect(decisions.DECISION_PRICES[model]).toBe(price);
      expect(decisions.decisionModelOf(model).model).toBe(model);
    }
    expect(glossary.glossaryEntry("harness")).toEqual(fixtureGlossary.harness);
    expect(defaults.SEED_INSTRUCTIONS).toBe(fixture.SEED_INSTRUCTIONS);
    expect(defaults.wordCount(defaults.SEED_INSTRUCTIONS)).toBe(412);
    expect(defaults.HARNESSES.map(({ value }) => value)).toEqual(
      fixture.HARNESSES.map(({ value }) => value),
    );
    expect(values.DEFAULT_HARNESS).toEqual(fixture.DEFAULT_HARNESS);
    expect(harness.harnessTryCommand({})).toContain(harness.HARNESS_FILE);
    expect(
      harness.harnessScript({ entry: { id: "custom_command" }, ids: {} }),
    ).toContain("def parse_try(lines, folder):");
  });
});
