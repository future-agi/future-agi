import { describe, expect, it, vi } from "vitest";
import { forbiddenRuntimeWords } from "src/sections/skills/runtime/runtimeCopy";
import {
  OBSERVE_SKILL_COPY,
  SKILL_COLUMN_IDS,
  buildSkillGroupItems,
  chipFilterItems,
  filtersSignature,
  hasGroupedSkillItems,
  hasSkillFilter,
  hydrateFiltersWithSkills,
  isSkillFilterItem,
  nextSkillGroup,
  normalizeSkillItem,
  parseLinkSort,
  parseSkillPhrase,
  parseSkillSource,
  reconcileSkillItems,
  serializeFiltersWithSkills,
  skillGroupChipText,
  skillGroups,
  sourceMetricPhrase,
  validateSkillGroups,
} from "../skillFilters";

const SKILL = "11111111-1111-4111-8111-111111111111";
const V3 = "33333333-3333-4333-8333-333333333333";
const V4 = "44444444-4444-4444-8444-444444444444";
const CTX = "55555555-5555-4555-8555-555555555555";
const EVENT = "66666666-6666-4666-8666-666666666666";

const item = (column, op, value, extra = {}) => ({
  column_id: column,
  ...extra,
  filter_config: { col_type: "SKILL", filter_type: column === "skill.version" ? "number" : "text", filter_op: op, filter_value: value },
});
const latency = {
  column_id: "latency",
  filter_config: { col_type: "SYSTEM_METRIC", filter_type: "number", filter_op: "greater_than", filter_value: 100 },
};

describe("normalizeSkillItem", () => {
  it("keeps a canonical item and drops the default group", () => {
    expect(normalizeSkillItem(item("skill.version_id", "equals", V3, { group: 0 }))).toEqual(item("skill.version_id", "equals", V3));
  });

  it("keeps a non-default group and the server's display name", () => {
    expect(normalizeSkillItem(item("skill.id", "equals", SKILL, { group: 2, display_name: "refund-policy" }))).toEqual({
      ...item("skill.id", "equals", SKILL),
      group: 2,
      display_name: "refund-policy",
    });
  });

  it("maps the panel's one-item list to an operator the column accepts", () => {
    expect(normalizeSkillItem(item("skill.digest", "in", [`sha256:${"a".repeat(64)}`])).filter_config).toMatchObject({
      filter_op: "equals",
      filter_value: `sha256:${"a".repeat(64)}`,
    });
    expect(normalizeSkillItem(item("skill.evidence", "equals", "delivered")).filter_config).toMatchObject({
      filter_op: "in",
      filter_value: ["delivered"],
    });
    expect(normalizeSkillItem(item("skill.version_id", "equals", [V3, V4])).filter_config).toMatchObject({
      filter_op: "in",
      filter_value: [V3, V4],
    });
  });

  it("rejects what the server would reject", () => {
    expect(normalizeSkillItem(item("skill.unknown", "equals", "x"))).toBeNull();
    expect(normalizeSkillItem(item("skill.digest", "contains", "x"))).toBeNull();
    expect(normalizeSkillItem(item("skill.load_result", "equals", "maybe"))).toBeNull();
    expect(normalizeSkillItem(item("skill.root_status", "equals", "ERROR"))).toBeNull();
    expect(normalizeSkillItem(item("skill.evidence", "in", ["guessed"]))).toBeNull();
    expect(normalizeSkillItem(item("skill.version", "equals", "three"))).toBeNull();
    expect(normalizeSkillItem(item("skill.id", "equals", ""))).toBeNull();
    expect(normalizeSkillItem(item("skill.id", "equals", SKILL, { group: -1 }))).toBeNull();
    expect(normalizeSkillItem(item("skill.id", "equals", SKILL, { group: 1.5 }))).toBeNull();
    expect(normalizeSkillItem(latency)).toBeNull();
  });

  it("allows Ran after loading only at span grain", () => {
    expect(normalizeSkillItem(item("skill.in_scope", "equals", V3))).toBeNull();
    expect(normalizeSkillItem(item("skill.in_scope", "equals", V3), { grain: "span" })).not.toBeNull();
  });

  it("knows every §6.3 column", () => {
    expect(SKILL_COLUMN_IDS).toEqual(expect.arrayContaining([
      "skill.id", "skill.version_id", "skill.name", "skill.version", "skill.requested_label", "skill.digest",
      "skill.evidence", "skill.agent", "skill.service", "skill.context_id", "skill.context_version_id",
      "skill.binding_id", "skill.in_scope", "skill.load_result", "skill.root_status",
    ]));
  });
});

describe("validateSkillGroups", () => {
  it("accepts one correlated group and a second group", () => {
    expect(validateSkillGroups([
      item("skill.id", "equals", SKILL),
      item("skill.version_id", "equals", V3),
      item("skill.agent", "equals", "refund-agent"),
      item("skill.id", "equals", SKILL, { group: 1 }),
      item("skill.load_result", "equals", "failed", { group: 1 }),
      latency,
    ]).ok).toBe(true);
  });

  it("rejects root status on its own", () => {
    expect(validateSkillGroups([item("skill.root_status", "equals", "error")]).reason).toBe("root_status_alone");
  });

  it("rejects a version number without a skill in its group", () => {
    expect(validateSkillGroups([
      item("skill.id", "equals", SKILL),
      item("skill.version", "equals", 3, { group: 1 }),
    ]).reason).toBe("version_without_skill");
  });

  it("rejects version, digest and evidence in a failed group", () => {
    expect(validateSkillGroups([
      item("skill.id", "equals", SKILL),
      item("skill.version_id", "equals", V3),
      item("skill.load_result", "equals", "failed"),
    ]).reason).toBe("failed_group_column");
  });
});

describe("hydrateFiltersWithSkills", () => {
  const hydrateOthers = vi.fn((filters, createId) => filters.map((filter) => ({ ...filter, id: createId() })));
  let next = 0;
  const createId = () => `id-${(next += 1)}`;

  it("keeps order and groups, and hands other items to the shared hydrator", () => {
    const { rows, rejected } = hydrateFiltersWithSkills(
      [item("skill.version_id", "equals", V3), latency, item("skill.id", "equals", SKILL, { group: 1 })],
      { createId, hydrateOthers },
    );
    expect(rejected).toBe(0);
    expect(rows.map((row) => [row.column_id, row.group])).toEqual([
      ["skill.version_id", undefined],
      ["latency", undefined],
      ["skill.id", 1],
    ]);
    expect(rows.every((row) => row.id)).toBe(true);
    expect(hydrateOthers).toHaveBeenCalledWith([latency], createId);
  });

  it("rejects a whole link when one skill item is unreadable, so the cohort never widens", () => {
    const { rows, rejected } = hydrateFiltersWithSkills(
      [item("skill.version_id", "equals", V3), item("skill.root_status", "equals", "broken"), latency],
      { createId, hydrateOthers, strict: true },
    );
    expect(rejected).toBe(1);
    expect(rows).toEqual([]);
  });

  it("rejects a link whose groups break the server's rules", () => {
    const { rows } = hydrateFiltersWithSkills([item("skill.root_status", "equals", "error")], { createId, hydrateOthers, strict: true });
    expect(rows).toEqual([]);
  });

  it("drops only the skill items from a stored list it cannot read", () => {
    const { rows } = hydrateFiltersWithSkills(
      [item("skill.version_id", "equals", V3), item("skill.digest", "contains", "x"), latency],
      { createId, hydrateOthers },
    );
    expect(rows.map((row) => row.column_id)).toEqual(["latency"]);
  });
});

describe("serializeFiltersWithSkills", () => {
  it("leaves lists without grouped skill items to the shared serializer", () => {
    const serializeOthers = vi.fn((filters) => filters.map((filter) => ({ ...filter, serialized: true })));
    const list = [item("skill.version_id", "equals", V3), latency];
    expect(serializeFiltersWithSkills(list, serializeOthers)).toEqual(list.map((filter) => ({ ...filter, serialized: true })));
    expect(serializeOthers).toHaveBeenCalledWith(list);
  });

  it("keeps group on grouped skill items", () => {
    const serializeOthers = vi.fn((filters) => filters);
    const out = serializeFiltersWithSkills(
      [item("skill.id", "equals", SKILL), item("skill.id", "equals", SKILL, { group: 1, id: "row-1" }), latency],
      serializeOthers,
    );
    expect(serializeOthers).toHaveBeenCalledWith([latency]);
    expect(out).toEqual([latency, item("skill.id", "equals", SKILL), { ...item("skill.id", "equals", SKILL), group: 1 }]);
  });
});

describe("reconcileSkillItems", () => {
  it("restores an unchanged item exactly after the filter panel round trip", () => {
    const previous = [item("skill.id", "equals", SKILL, { group: 1 }), item("skill.root_status", "equals", "error", { group: 1 })];
    const fromPanel = [item("skill.id", "in", [SKILL]), item("skill.root_status", "in", ["error"]), latency];
    expect(reconcileSkillItems(previous, fromPanel)).toEqual([...previous, latency]);
  });

  it("normalizes an edited item and drops one the server would reject", () => {
    const out = reconcileSkillItems([], [item("skill.digest", "in", ["sha256:x"]), item("skill.digest", "contains", "y")]);
    expect(out).toEqual([item("skill.digest", "equals", "sha256:x")]);
  });

  it("returns other lists untouched", () => {
    const list = [latency];
    expect(reconcileSkillItems([], list)).toBe(list);
  });
});

describe("groups", () => {
  it("groups skill items with their indices and finds the next group", () => {
    const list = [latency, item("skill.id", "equals", SKILL), item("skill.agent", "equals", "a"), item("skill.id", "equals", SKILL, { group: 3 })];
    expect(skillGroups(list).map((group) => [group.group, group.indices])).toEqual([[0, [1, 2]], [3, [3]]]);
    expect(nextSkillGroup(list)).toBe(4);
    expect(nextSkillGroup([latency])).toBe(0);
    expect(hasGroupedSkillItems(list)).toBe(true);
    expect(hasSkillFilter([latency])).toBe(false);
    expect(isSkillFilterItem({ column_id: "x", filterConfig: { colType: "skill" } })).toBe(true);
  });

  it("changes the signature when the person edits a filter", () => {
    const list = [item("skill.version_id", "equals", V3)];
    expect(filtersSignature(list)).toBe(filtersSignature([{ ...list[0], id: "row" }]));
    expect(filtersSignature(list)).not.toBe(filtersSignature([item("skill.version_id", "equals", V4)]));
  });
});

describe("skillGroupChipText", () => {
  const labels = { "skill.id": { [SKILL]: "refund-policy" }, "skill.version_id": { [V3]: "v3" } };

  it("reads as one Skill chip", () => {
    expect(skillGroupChipText([
      item("skill.id", "equals", SKILL),
      item("skill.version_id", "equals", V3),
      item("skill.requested_label", "equals", "production"),
      item("skill.agent", "equals", "refund-agent"),
    ], labels).text).toBe("refund-policy v3 · Asked for production · Agent: refund-agent");
  });

  it("says No recorded load of, with the tooltip about apps that can't record", () => {
    const chip = skillGroupChipText([item("skill.id", "equals", SKILL), item("skill.version_id", "not_equals", V3)], labels);
    expect(chip.negative).toBe(true);
    expect(chip.text).toBe("No recorded load of refund-policy v3");
    expect(chip.tooltip).toContain("can't record loads");
  });

  it("shows failed loads and failed requests in their words", () => {
    expect(skillGroupChipText([item("skill.id", "equals", SKILL), item("skill.load_result", "equals", "failed")], labels).text)
      .toBe("refund-policy · load failed");
    expect(skillGroupChipText([item("skill.id", "equals", SKILL, { display_name: "refund-policy" }), item("skill.version_id", "equals", V3, { display_name: "v3" }), item("skill.root_status", "equals", "error")]).text)
      .toBe("refund-policy v3 · failed requests");
  });

  it("falls back to a short id, never a mangled one", () => {
    expect(skillGroupChipText([item("skill.version_id", "equals", V3)]).text).toBe("33333333…");
  });

  it("leads a connected-app link with its context", () => {
    expect(skillGroupChipText([
      item("skill.context_id", "equals", CTX, { display_name: "support-agent" }),
      item("skill.binding_id", "equals", EVENT),
    ]).text).toBe("support-agent · Connected app: 66666666…");
  });
});

describe("buildSkillGroupItems", () => {
  it("serializes the chip's fields into one group", () => {
    const items = buildSkillGroupItems({ skillId: SKILL, versionIds: [V3, V4], agent: "refund-agent", evidence: ["delivered"], group: 2 });
    expect(items.map((entry) => [entry.column_id, entry.filter_config.filter_op, entry.group])).toEqual([
      ["skill.id", "equals", 2],
      ["skill.version_id", "in", 2],
      ["skill.agent", "equals", 2],
      ["skill.evidence", "in", 2],
    ]);
    expect(validateSkillGroups(items).ok).toBe(true);
  });

  it("hides Version, Evidence and Contents when Load result is Failed", () => {
    const items = buildSkillGroupItems({ skillId: SKILL, versionIds: [V3], evidence: ["delivered"], digest: "sha256:x", loadResult: "failed" });
    expect(items.map((entry) => entry.column_id)).toEqual(["skill.id", "skill.load_result"]);
    expect(validateSkillGroups(items).ok).toBe(true);
  });

  it("builds a No recorded load chip", () => {
    const items = buildSkillGroupItems({ skillId: SKILL, negate: true });
    expect(items[0].filter_config.filter_op).toBe("not_equals");
  });

  it("returns nothing when nothing identifies a load", () => {
    expect(buildSkillGroupItems({ loadResult: "failed" })).toEqual([]);
  });
});

describe("chipFilterItems", () => {
  it("adds the chip's version as a new group with readable names", () => {
    const existing = [item("skill.id", "equals", SKILL)];
    const items = chipFilterItems({ skill_id: SKILL, version_id: V3, version: 3, name: "refund-policy" }, existing);
    expect(items.map((entry) => [entry.column_id, entry.group, entry.display_name])).toEqual([
      ["skill.id", 1, "refund-policy"],
      ["skill.version_id", 1, "v3"],
    ]);
  });

  it("adds nothing for a load without a version id", () => {
    expect(chipFilterItems({ name: "x" })).toEqual([]);
  });
});

describe("URL parameters", () => {
  it("reads a §6.6 source object and refuses anything else", () => {
    expect(parseSkillSource(JSON.stringify({ skill_id: SKILL, version_id: V4, agent_key: ["p1", "svc", "refund-agent"], metric: "failed_requests", event_id: EVENT, side: "after" })))
      .toEqual({ skillId: SKILL, versionId: V4, contextId: null, bindingId: null, eventId: EVENT, metric: "failed_requests", side: "after", agentKey: ["p1", "svc", "refund-agent"], window: null });
    expect(parseSkillSource(JSON.stringify({ context_id: CTX, binding_id: EVENT, metric: "all" }))).toMatchObject({ contextId: CTX, bindingId: EVENT, skillId: null });
    expect(parseSkillSource(JSON.stringify({ skill_id: "javascript:alert(1)" }))).toBeNull();
    expect(parseSkillSource(JSON.stringify({ version_id: V3 }))).toBeNull();
    expect(parseSkillSource("{not json")).toBeNull();
    expect(parseSkillSource(JSON.stringify({ skill_id: SKILL, metric: "<b>", side: "sideways" }))).toMatchObject({ metric: "all", side: null });
  });

  it("reads the link sort", () => {
    expect(parseLinkSort(JSON.stringify({ column_id: "latency", direction: "desc" }))).toEqual({ column_id: "latency", direction: "desc" });
    expect(parseLinkSort(JSON.stringify({ column_id: "latency", direction: "sideways" }))).toBeNull();
    expect(parseLinkSort(JSON.stringify({ column_id: "x; drop", direction: "asc" }))).toBeNull();
  });

  it("names the metric in the source bar", () => {
    expect(sourceMetricPhrase("failed_requests")).toBe("failed requests");
    expect(sourceMetricPhrase("failed")).toBe("failed requests");
    expect(sourceMetricPhrase("slowest")).toBe("p95 latency");
    expect(sourceMetricPhrase("all")).toBe("traces");
  });
});

describe("parseSkillPhrase", () => {
  it("reads the AI input phrases", () => {
    expect(parseSkillPhrase("loaded skill refund-policy")).toEqual({ skillName: "refund-policy" });
    expect(parseSkillPhrase("skill refund-policy v3")).toEqual({ skillName: "refund-policy", version: 3 });
    expect(parseSkillPhrase("production version of refund-policy")).toEqual({ skillName: "refund-policy", requestedLabel: "production" });
    expect(parseSkillPhrase("agent refund-agent")).toEqual({ agent: "refund-agent" });
    expect(parseSkillPhrase("refund-policy failed to load")).toEqual({ skillName: "refund-policy", loadResult: "failed" });
    expect(parseSkillPhrase("latency over 2s")).toBeNull();
  });
});

describe("copy", () => {
  const strings = [];
  const collect = (value) => {
    if (typeof value === "string") strings.push(value);
    else if (typeof value === "function") {
      [{ names: ["a"], failed: 1, context: "c", version: 2, window: "24 h", label: "production", toVersion: 4 }, "x", 3].forEach((arg) => {
        try {
          strings.push(String(value(arg)));
        } catch {
          // A builder that needs a different argument shape is covered by the first call.
        }
      });
    } else if (value && typeof value === "object") Object.values(value).forEach(collect);
  };
  collect(OBSERVE_SKILL_COPY);

  it("never says Used, using, in use, Error rate or In scope", () => {
    expect(strings.length).toBeGreaterThan(40);
    strings.forEach((text) => expect(forbiddenRuntimeWords(text)).toEqual([]));
  });
});
