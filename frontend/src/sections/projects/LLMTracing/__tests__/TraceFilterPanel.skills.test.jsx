import { describe, expect, it } from "vitest";
import { buildApiFilterFromPanelRow } from "src/api/contracts/filter-contract";
import { apiFilterToPanelRow } from "src/sections/projects/observeFilterConfig";
import {
  getSkillFilterFields,
  mergeTraceFilterProperties,
  skillValueOptions,
} from "../TraceFilterPanel";
import {
  SKILL_COLUMNS,
  reconcileSkillItems,
  skillItemError,
  validateSkillGroups,
} from "../skillFilters";

const SKILL = "11111111-1111-4111-8111-111111111111";
const V3 = "33333333-3333-4333-8333-333333333333";

describe("Skills fields in the filter panel (SKILL-TRACES §7.1)", () => {
  it("offers the §7.1 fields on traces, plus Ran after loading on spans", () => {
    expect(getSkillFilterFields("trace").map((field) => field.value)).toEqual([
      "skill.id",
      "skill.version_id",
      "skill.requested_label",
      "skill.agent",
      "skill.service",
      "skill.evidence",
      "skill.load_result",
      "skill.digest",
    ]);
    expect(getSkillFilterFields("spans").map((field) => field.value)).toContain("skill.in_scope");
    expect(getSkillFilterFields("users")).toEqual([]);
    expect(getSkillFilterFields("trace").map((field) => field.label)).toEqual([
      "Skill",
      "Version",
      "Asked for",
      "Agent",
      "Service",
      "Evidence",
      "Load result",
      "Contents",
    ]);
  });

  it("only narrows to operators the server accepts for each column", () => {
    const panelToServer = { in: ["in", "equals"], not_in: ["not_in", "not_equals"] };
    getSkillFilterFields("spans").forEach((field) => {
      field.operators.forEach((op) => {
        expect(panelToServer[op].some((serverOp) => SKILL_COLUMNS[field.value].ops.includes(serverOp))).toBe(true);
      });
    });
  });

  it("adds them to the trace catalog only while the flag is on", () => {
    const ids = (skillTraces) =>
      mergeTraceFilterProperties({ tab: "trace", source: "traces", skillTraces }).map((property) => property.id);
    expect(ids(false)).not.toContain("skill.id");
    expect(ids(true)).toContain("skill.id");
    const skill = mergeTraceFilterProperties({ tab: "trace", source: "traces", skillTraces: true }).find((property) => property.id === "skill.version_id");
    expect(skill).toMatchObject({ category: "skills", apiColType: "SKILL", operators: ["in", "not_in"], runtimeValues: "skill.version_id", allowCustomValue: false });
    expect(mergeTraceFilterProperties({ tab: "trace", source: "sessions", skillTraces: true }).some((property) => property.id === "skill.id")).toBe(false);
  });

  it("round-trips a panel row into a valid SKILL item", () => {
    const row = {
      field: "skill.version_id",
      fieldName: "Version",
      fieldCategory: "skills",
      fieldType: "string",
      apiColType: "SKILL",
      operator: "in",
      value: [V3],
    };
    const fromPanel = buildApiFilterFromPanelRow(row);
    const [item] = reconcileSkillItems([], [fromPanel]);
    expect(skillItemError(item)).toBeNull();
    expect(item.filter_config).toEqual({ col_type: "SKILL", filter_type: "text", filter_op: "in", filter_value: [V3] });
  });

  it("keeps a linked group through the panel's row round trip", () => {
    const linked = [
      { column_id: "skill.id", group: 1, filter_config: { col_type: "SKILL", filter_type: "text", filter_op: "equals", filter_value: SKILL } },
      { column_id: "skill.root_status", group: 1, filter_config: { col_type: "SKILL", filter_type: "text", filter_op: "equals", filter_value: "error" } },
    ];
    const roundTrip = linked.map(apiFilterToPanelRow).map(buildApiFilterFromPanelRow);
    const out = reconcileSkillItems(linked, roundTrip);
    expect(out).toEqual(linked);
    expect(validateSkillGroups(out).ok).toBe(true);
  });
});

describe("skillValueOptions", () => {
  it("maps skill names to ids with the production version", () => {
    expect(skillValueOptions({ values: [{ skillId: SKILL, name: "refund-policy", productionVersion: 3 }] }, "skill.name")).toEqual([
      { value: SKILL, label: "refund-policy · production now: v3" },
    ]);
  });

  it("labels versions with the labels they hold today", () => {
    expect(skillValueOptions([{ value: V3, version: 3, labelsNow: ["production"] }], "skill.version_id")).toEqual([
      { value: V3, label: "v3 (production now)" },
    ]);
  });

  it("keeps plain values and drops unusable entries", () => {
    expect(skillValueOptions({ values: [{ value: "refund-agent" }, null, {}] }, "skill.agent")).toEqual([
      { value: "refund-agent", label: "refund-agent" },
    ]);
    expect(skillValueOptions({ values: [{ name: "no id" }] }, "skill.name")).toEqual([]);
  });
});
