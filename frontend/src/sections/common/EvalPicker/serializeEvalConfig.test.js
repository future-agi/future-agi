import { describe, expect, it } from "vitest";
import { serializeEvalConfig } from "./serializeEvalConfig";

describe("serializeEvalConfig", () => {
  it("emits runtime overrides only inside config.run_config", () => {
    const payload = serializeEvalConfig({
      templateId: "template-1",
      name: "quality_check",
      model: "turing_large",
      mapping: { output: "answer" },
      pass_threshold: 0.8,
      check_internet: true,
      knowledge_bases: ["kb-1"],
      error_localizer_enabled: true,
    });

    expect(payload).toMatchObject({
      template_id: "template-1",
      name: "quality_check",
      model: "turing_large",
      mapping: { output: "answer" },
      error_localizer: true,
      filters: [],
      config: {
        run_config: {
          pass_threshold: 0.8,
          check_internet: true,
          knowledge_bases: ["kb-1"],
          error_localizer_enabled: true,
        },
      },
    });
    expect(payload).not.toHaveProperty("pass_threshold");
    expect(payload).not.toHaveProperty("check_internet");
    expect(payload).not.toHaveProperty("knowledge_bases");
  });

  it("keeps canonical filter lists unchanged", () => {
    const filters = [
      {
        column_id: "duration",
        filter_config: {
          filter_type: "number",
          filter_op: "greater_than",
          filter_value: 10,
        },
      },
    ];

    expect(
      serializeEvalConfig({
        templateId: "template-1",
        name: "quality_check",
        filters,
      }).filters,
    ).toBe(filters);
  });
});
