import { describe, expect, it } from "vitest";
import {
  buildSummaryConfig,
  getSummaryTemplateId,
  resolveSummarySelection,
} from "../summaryConfig";

describe("summaryConfig", () => {
  it("round-trips saved custom summary templates through UI selection state", () => {
    const templateId = "6ea6f892-c6b7-47cf-a74a-f89ce7f1f47a";
    const selection = resolveSummarySelection({
      type: "custom",
      custom: "Group failures by root cause.",
      template_id: templateId,
    });

    expect(selection).toBe(`custom:${templateId}`);
    expect(getSummaryTemplateId(selection)).toBe(templateId);
    expect(
      buildSummaryConfig(selection, {
        customSummary: "Group failures by root cause.",
      }),
    ).toEqual({
      type: "custom",
      custom: "Group failures by root cause.",
      template_id: templateId,
    });
  });

  it("preserves built-in and disabled summary selections", () => {
    expect(resolveSummarySelection({ type: "long" })).toBe("long");
    expect(buildSummaryConfig("short")).toEqual({ type: "short" });
    expect(resolveSummarySelection({ type: null })).toBeNull();
    expect(buildSummaryConfig(null)).toEqual({ type: null });
  });
});
