import { describe, expect, it } from "vitest";
import {
  sanitizeEvalMapping,
  serializeEvalConfig,
} from "./serializeEvalConfig";

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

  it("drops mapping entries the user cleared (empty-string paths)", () => {
    // Auto-mapped fields the user removed come through as "" — sending them
    // makes the eval runner treat the key as a required attribute with an
    // empty path and fail every row ("Required attribute '' ... not found").
    // No mapping at all must serialize as {} so context-only evals run.
    const payload = serializeEvalConfig({
      templateId: "template-1",
      name: "context_only",
      mapping: { agent_prompt: "", conversation: "   ", output: "answer" },
    });

    expect(payload.mapping).toEqual({ output: "answer" });
  });

  it("serializes an all-cleared mapping as an empty object", () => {
    const payload = serializeEvalConfig({
      templateId: "template-1",
      name: "context_only",
      mapping: { agent_prompt: "", conversation: "" },
    });

    expect(payload.mapping).toEqual({});
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

describe("sanitizeEvalMapping", () => {
  it("drops cleared fields and keeps real attribute paths", () => {
    expect(
      sanitizeEvalMapping({ a: "output.value", b: "", c: null, d: "   " }),
    ).toEqual({ a: "output.value" });
  });

  it("forwards a non-string value so the API rejects it by name", () => {
    // Dropping it here would silently delete the variable from the saved
    // config; the write gate answers it with a message instead.
    expect(sanitizeEvalMapping({ a: { value: "output.value" } })).toEqual({
      a: { value: "output.value" },
    });
  });
});
