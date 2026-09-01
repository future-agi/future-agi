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

  it("sends the selected version as pinned_version_id", () => {
    expect(
      serializeEvalConfig({
        templateId: "template-1",
        name: "quality_check",
        versionId: "version-7",
      }),
    ).toMatchObject({ pinned_version_id: "version-7" });
  });

  it("omits pinned_version_id when no version was selected", () => {
    // System evals never pin, and the backend treats an absent key as
    // "pin the template default" - sending null would mean "unpin".
    for (const versionId of [null, undefined, ""]) {
      expect(
        serializeEvalConfig({
          templateId: "template-1",
          name: "quality_check",
          versionId,
        }),
      ).not.toHaveProperty("pinned_version_id");
    }
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
