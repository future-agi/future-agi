import { describe, it, expect } from "vitest";
import {
  CHARTS_SECTION,
  defaultPrefs,
  legacyToLayout,
  mergePrefs,
  prefsStorageKey,
  readPrefs,
  writePrefs,
} from "../leaderboardPrefs";
import {
  GROUP_BY_COLUMN,
  NAME_COLUMN,
  columnCatalog,
  columnOfGroup,
  gridRoomFor,
  migrateLegacyColumns,
} from "../leaderboardColumns";
import {
  hideColumn,
  isDefaultLayout,
  pinColumn,
  resolveColumns,
} from "src/sections/common/ux/oneview/columnLayout";

const memoryStorage = () => {
  const data = new Map();
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
  };
};

describe("leaderboard prefs", () => {
  it("round-trips through storage under fa.expList.<id>", () => {
    const storage = memoryStorage();
    const prefs = mergePrefs(defaultPrefs(), {
      columns: { cost: false },
      charts: { open: false, cards: ["scores", "cost"] },
      showDeltas: false,
    });
    writePrefs("DS", prefs, storage);
    expect(storage.getItem(prefsStorageKey("DS"))).toContain('"cost":false');
    expect(readPrefs("DS", storage)).toEqual(prefs);
  });

  it("drops junk and survives broken storage", () => {
    expect(
      mergePrefs(defaultPrefs(), {
        columns: { cost: "no" },
        charts: { cards: ["scores", "nope"] },
      }),
    ).toMatchObject({ columns: {}, charts: { open: true, cards: ["scores"] } });
    const throwing = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(readPrefs("DS", throwing)).toEqual(defaultPrefs());
    expect(() => writePrefs("DS", defaultPrefs(), throwing)).not.toThrow();
  });
});

describe("column catalog", () => {
  const defs = [
    {
      key: "eval:correctness",
      name: "Correctness",
      kind: "eval",
      is_dataset_eval: true,
    },
    {
      key: "eval:groundedness",
      name: "Groundedness",
      kind: "eval",
      is_dataset_eval: true,
    },
    { key: "eval:tone", name: "Tone", kind: "eval", is_dataset_eval: true },
    { key: "eval:task_completion", name: "Task completion", kind: "eval" },
    { key: "human:quality", name: "Quality", kind: "human" },
    { key: "ops:latency", name: "Latency", kind: "system" },
  ];
  const runs = [
    { metadata: { source: "ci", branch: "main" }, metrics: { preference: {} } },
  ];

  // R4 (lane Rb, MUST 7) changed the default set: run, Success rate, Cost
  // per task, Time per task, Updated; evals, target, status one click away.
  it("opens on ONE default set (R4): run, headline numbers, Updated", () => {
    const cols = columnCatalog(defs, runs);
    const resolved = resolveColumns(cols, {});
    expect(resolved.filter((c) => !c.hidden).map((c) => c.id)).toEqual([
      NAME_COLUMN,
      "success_rate",
      "cost_per_task",
      "latency_p50",
      "updated",
    ]);
    // Hidden by default, one click away (`N hidden`).
    const hidden = resolved.filter((c) => c.hidden).map((c) => c.id);
    [
      "pass_k",
      "eval:correctness",
      "eval:groundedness",
      "where_ran",
      "target",
      "status",
      "eval:tone",
      "eval:task_completion",
      "human:quality",
      "win_rate",
      "cost",
      "latency_p99",
      "tokens",
      "dataset",
      "errors",
      "trials",
      "examples",
      "created_by",
      "created",
      "description",
      "meta:branch",
    ].forEach((id) => expect(hidden).toContain(id));
    // IT-R3 G2 P3: the header names the statistic.
    expect(cols.find((c) => c.id === "latency_p50").label).toBe("Time (p50)");
    expect(cols.find((c) => c.id === "cost").label).toBe("Total cost");
    expect(cols.map((c) => c.id)).not.toContain("meta:source");
  });

  it("locks and freezes the Experiment column (A2-06)", () => {
    const cols = columnCatalog(defs, runs);
    const name = resolveColumns(cols, {}).find((c) => c.id === NAME_COLUMN);
    expect(name).toMatchObject({ locked: true, pinned: "left", hidden: false });
    expect(resolveColumns(cols, {})[0].id).toBe(NAME_COLUMN);
    // Never hidden; the user can unfreeze it.
    expect(hideColumn({}, cols, NAME_COLUMN)).toEqual({});
    const unfrozen = pinColumn({}, cols, NAME_COLUMN, null);
    expect(
      resolveColumns(cols, unfrozen).find((c) => c.id === NAME_COLUMN).pinned,
    ).toBe(null);
  });

  it("maps header-menu Group by to the board's group values", () => {
    const cols = columnCatalog(defs, runs);
    const groupable = cols.filter((c) => c.groupable).map((c) => c.id);
    expect(groupable).toEqual(
      expect.arrayContaining(["experiment", "dataset", "target"]),
    );
    expect(GROUP_BY_COLUMN.dataset).toBe("dataset_version");
    expect(columnOfGroup("target_type")).toBe("target");
    expect(columnOfGroup("model")).toBe(null);
  });
});

describe("default columns fit the window (P5, A2-07)", () => {
  const defs = [
    { key: "eval:a", name: "A", kind: "eval", is_dataset_eval: true },
    { key: "eval:b", name: "B", kind: "eval", is_dataset_eval: true },
  ];
  const shownAt = (mainWidth, windowWidth) =>
    resolveColumns(
      columnCatalog(defs, [], { room: gridRoomFor(mainWidth, windowWidth) }),
      {},
    )
      .filter((c) => !c.hidden)
      .map((c) => c.id);

  it("1280 (main 1080): the whole R4 default set fits", () => {
    expect(gridRoomFor(1080, 1280)).toBe(754);
    expect(shownAt(1080, 1280)).toEqual([
      NAME_COLUMN,
      "success_rate",
      "cost_per_task",
      "latency_p50",
      "updated",
    ]);
  });

  it("wider windows show the same default set (the name column takes the room)", () => {
    expect(shownAt(1240, 1440)).toEqual(shownAt(1080, 1280));
    expect(shownAt(1720, 1920)).toEqual(shownAt(1080, 1280));
  });

  it("never touches columns that are hidden by default; unknown room = full set", () => {
    const all = columnCatalog(defs, []);
    expect(columnCatalog(defs, [], { room: undefined })).toEqual(all);
    const tiny = columnCatalog(defs, [], { room: 10 });
    expect(tiny.find((c) => c.id === NAME_COLUMN).defaultVisible).toBe(true);
    expect(tiny.find((c) => c.id === "cost").defaultVisible).toBe(false);
  });
});

describe("migrating the old Columns choices (A2-07)", () => {
  const defs = [
    { key: "eval:a", name: "A", kind: "eval", is_dataset_eval: true },
    { key: "eval:b", name: "B", kind: "eval", is_dataset_eval: true },
    { key: "eval:c", name: "C", kind: "eval", is_dataset_eval: true },
  ];
  const cols = columnCatalog(defs, []);

  it("keeps only real differences from the new default", () => {
    const legacy = {
      cost: true, // hidden now: shown on purpose -> kept
      target: false, // already the default (hidden) -> dropped
      updated: false, // hidden on purpose -> kept
      experiment: false, // locked -> dropped
      "eval:gone": true, // unknown -> dropped
    };
    const next = migrateLegacyColumns(legacy, cols, {});
    expect(next).toEqual({ visible: { cost: true, updated: false } });
    expect(isDefaultLayout(cols, next)).toBe(false);
    const shown = resolveColumns(cols, next)
      .filter((c) => !c.hidden)
      .map((c) => c.id);
    expect(shown).toContain("cost");
    expect(shown).not.toContain("updated");
  });

  it("never overrides what the layout already says; no-op returns null", () => {
    expect(
      migrateLegacyColumns({ cost: true }, cols, { visible: { cost: false } }),
    ).toBe(null);
    expect(migrateLegacyColumns({ target: false }, cols, {})).toBe(null);
    expect(migrateLegacyColumns(null, cols, {})).toBe(null);
  });

  it("moves charts and deltas into sections / extras", () => {
    const legacy = mergePrefs(defaultPrefs(), {
      columns: { tokens: true },
      charts: { open: false, cards: ["scores", "cost"] },
      showDeltas: false,
    });
    expect(legacyToLayout(legacy, cols, {})).toEqual({
      columns: { visible: { tokens: true } },
      sections: { [CHARTS_SECTION]: false },
      extras: { chartCards: ["scores", "cost"], showDeltas: false },
    });
    // Defaults move nothing; existing layout choices win.
    expect(legacyToLayout(defaultPrefs(), cols, {})).toBe(null);
    expect(
      legacyToLayout(legacy, cols, {
        columns: { visible: { tokens: false } },
        sections: { [CHARTS_SECTION]: true },
        extras: { chartCards: ["errors"], showDeltas: true },
      }),
    ).toBe(null);
  });
});
