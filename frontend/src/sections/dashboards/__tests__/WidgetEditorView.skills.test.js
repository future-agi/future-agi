import { describe, expect, it, vi } from "vitest";
import {
  SKILL_BREAKDOWN_OPTIONS,
  isSkillLoadAttributeBreakdown,
  withSkillBreakdownOptions,
} from "../WidgetEditorView";

vi.mock("react-apexcharts", () => ({ default: () => null }));

describe("skill breakdowns in the widget editor (SKILL-TRACES §6.6)", () => {
  const catalog = [{ id: "model", name: "Model", type: "system" }];

  it("adds Skill, Skill version and Skill agent to the breakdown picker while the flag is on", () => {
    const options = withSkillBreakdownOptions(catalog, { pickerMode: "breakdown", enabled: true });
    expect(options.map((option) => option.id)).toEqual(["model", "skill", "skill_version", "skill_agent"]);
    expect(SKILL_BREAKDOWN_OPTIONS.every((option) => option.type === "system" && option.source === "traces")).toBe(true);
  });

  it("leaves metrics, filters, other categories and the flag-off picker alone", () => {
    expect(withSkillBreakdownOptions(catalog, { pickerMode: "metric", enabled: true })).toBe(catalog);
    expect(withSkillBreakdownOptions(catalog, { pickerMode: "breakdown", enabled: false })).toBe(catalog);
    expect(withSkillBreakdownOptions(catalog, { pickerMode: "breakdown", enabled: true, pickerCategory: "eval_metric" })).toBe(catalog);
  });

  it("filters by the picker search and never duplicates a catalog option", () => {
    expect(withSkillBreakdownOptions(catalog, { pickerMode: "breakdown", enabled: true, search: "agent" }).map((option) => option.id))
      .toEqual(["model", "skill_agent"]);
    const served = [...catalog, { id: "skill_version", name: "Skill version", type: "system" }];
    expect(withSkillBreakdownOptions(served, { pickerMode: "breakdown", enabled: true }).filter((option) => option.id === "skill_version")).toHaveLength(1);
  });

  it("warns only for the scalar fi.skill.version_id attribute breakdown", () => {
    expect(isSkillLoadAttributeBreakdown({ id: "fi.skill.version_id", type: "custom_attribute" })).toBe(true);
    expect(isSkillLoadAttributeBreakdown({ id: "skill_version", type: "system" })).toBe(false);
  });
});
