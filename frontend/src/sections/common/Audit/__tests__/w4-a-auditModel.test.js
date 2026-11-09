// Audit words (W4-7, A2): sentences, diff rows, day groups, filters.
import { describe, expect, it } from "vitest";
import {
  actionChoices,
  auditParams,
  dayLabel,
  diffRows,
  groupByDay,
  sentenceOf,
  sinceFor,
} from "../auditModel";

const NOW = Date.parse("2026-10-22T15:00:00");
const at = (h) => new Date(NOW - h * 3600_000).toISOString();

describe("auditModel", () => {
  it("sentences and diff rows", () => {
    expect(
      sentenceOf({
        actor: { name: "Maya Chen" },
        action_label: "changed access",
      }),
    ).toBe("Maya Chen changed access");
    expect(sentenceOf({ actor: null, action: "x.y" })).toBe("Someone x.y");
    const server = [{ field: "Access", before: "Edit and run", after: "View" }];
    expect(diffRows({ diff: server })).toBe(server);
    expect(
      diffRows({
        changes: {
          before: { level: "edit_run" },
          after: { level: "view", rows: ["Row 1", "Row 2"] },
        },
      }),
    ).toEqual([
      { field: "level", before: "edit_run", after: "view" },
      { field: "rows", before: "—", after: "Row 1, Row 2" },
    ]);
  });

  it("day groups keep the order and name today and yesterday", () => {
    expect(dayLabel(at(1), NOW)).toBe("Today");
    expect(dayLabel(at(24), NOW)).toBe("Yesterday");
    expect(dayLabel("2026-10-15T10:00:00", NOW)).toBe("Thu 15 Oct");
    const g = groupByDay(
      [
        { id: "a", at: at(1) },
        { id: "b", at: at(2) },
        { id: "c", at: at(24) },
      ],
      NOW,
    );
    expect(g.map((x) => [x.label, x.entries.map((e) => e.id)])).toEqual([
      ["Today", ["a", "b"]],
      ["Yesterday", ["c"]],
    ]);
  });

  it("filters: groups with entries only; params for the server", () => {
    const groups = [
      {
        key: "versions",
        label: "Versions",
        actions: ["dataset.version_saved"],
        count: 6,
      },
      {
        key: "labels",
        label: "Labels",
        actions: ["dataset.label_moved"],
        count: 0,
      },
      {
        key: "access",
        label: "Access",
        actions: ["dataset.access_changed", "dataset.access_requested"],
        count: 2,
      },
    ];
    expect(actionChoices(groups).map((c) => c.label)).toEqual([
      "Versions (6)",
      "Access (2)",
    ]);
    expect(sinceFor("all", NOW)).toBeNull();
    expect(sinceFor("7", NOW)).toBe(
      new Date(NOW - 7 * 86400_000).toISOString(),
    );
    expect(
      auditParams({
        scope: "dataset:x",
        groupKey: "access",
        groups,
        actor: "u1",
        period: "30",
        now: NOW,
      }),
    ).toEqual({
      scope: "dataset:x",
      action: "dataset.access_changed,dataset.access_requested",
      actor: "u1",
      since: new Date(NOW - 30 * 86400_000).toISOString(),
    });
    expect(
      auditParams({
        scope: "dataset:x",
        groupKey: "all",
        groups,
        actor: null,
        period: "all",
        now: NOW,
      }),
    ).toEqual({ scope: "dataset:x" });
  });
});
