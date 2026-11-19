import { describe, expect, it, vi } from "vitest";
import { SKILL_VERSION_SPLIT_SUPPORTED, skillVersionSplitParams } from "../GraphSection";

vi.mock("react-apexcharts", () => ({ default: () => null }));

const skill = {
  column_id: "skill.version_id",
  filter_config: { col_type: "SKILL", filter_type: "text", filter_op: "equals", filter_value: "33333333-3333-4333-8333-333333333333" },
};

describe("Split by skill version (SKILL-TRACES §7.1)", () => {
  it("sends the contracted split only after support is mounted", () => {
    expect(SKILL_VERSION_SPLIT_SUPPORTED).toBe(true);
    expect(skillVersionSplitParams({ enabled: true, filters: [skill] })).toEqual({ split_by: "skill_version" });
  });

  it("asks for the split only with a Skill filter, once supported", () => {
    expect(skillVersionSplitParams({ enabled: true, filters: [skill], supported: true })).toEqual({ split_by: "skill_version" });
    expect(skillVersionSplitParams({ enabled: true, filters: [], supported: true })).toEqual({});
    expect(skillVersionSplitParams({ enabled: false, filters: [skill], supported: true })).toEqual({});
  });
});
