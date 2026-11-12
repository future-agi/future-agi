// R3 G0 §B10: taskCompareHref / readTaskCompareParams round-trip (C4 keys).
import { describe, expect, it } from "vitest";
import {
  parseTrials,
  readTaskCompareParams,
  serializeTrials,
  taskCompareHref,
} from "../analysisLinks";

const TS = "d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e06";
const B = "a6000000-0000-4000-8000-00000000002b";
const A = "a6000000-0000-4000-8000-00000000002a";
const D = "a6000000-0000-4000-8000-00000000002d";
const T26 = "b6000000-0000-4000-8000-000000000026";

const parse = (href) => readTaskCompareParams(new URL(href, "http://x").search);

describe("taskCompareHref", () => {
  it("builds the exit-walk URL", () => {
    const href = taskCompareHref({
      datasetId: TS,
      runIds: [B, A, D],
      baselineId: B,
      rowId: T26,
      depth: "steps",
      trials: { [B]: 1 },
    });
    expect(href).toBe(
      `/dashboard/develop/${TS}/compare/tasks?runs=${encodeURIComponent(`${B},${A},${D}`)}&baseline=${B}&row=${T26}&depth=steps&trial=${encodeURIComponent(`${B}:1`)}`,
    );
  });

  it("round-trips every key and omits defaults", () => {
    const opts = {
      datasetId: TS,
      runIds: [B, D],
      baselineId: B,
      rowId: T26,
      depth: "raw",
      trials: { [B]: 2, [D]: 3 },
      level: "all",
      diff: true,
      step: "m3",
      blind: true,
      rail: "differ",
      rtab: "workspace",
      fmt: "delta",
      from: `/dashboard/develop/${TS}/compare/matrix?runs=${B}`,
    };
    const p = parse(taskCompareHref(opts));
    expect(p).toMatchObject({
      runIds: [B, D],
      baselineId: B,
      rowId: T26,
      depth: "raw",
      trials: { [B]: 2, [D]: 3 },
      level: "all",
      diff: true,
      step: "m3",
      blind: true,
      rail: "differ",
      rtab: "workspace",
      fmt: "delta",
      from: opts.from,
      cards: false,
    });
    const bare = taskCompareHref({
      datasetId: TS,
      runIds: [B],
      rail: "all",
      level: "milestones",
      fmt: "abs",
      rtab: "waterfall",
    });
    expect(bare).toBe(`/dashboard/develop/${TS}/compare/tasks?runs=${B}`);
  });

  it("falls back silently on unknown values; > 4 lanes = Cards", () => {
    const p = readTaskCompareParams(
      "?runs=a,b,c,d,e,a&depth=wide&level=x&rail=zzz&rtab=nope&fmt=pct&step=q9&trial=a:x,b:2,:3",
    );
    expect(p.runIds).toEqual(["a", "b", "c", "d", "e"]);
    expect(p.cards).toBe(true);
    expect(p.depth).toBeNull();
    expect(p.level).toBe("milestones");
    expect(p.rail).toBe("all");
    expect(p.rtab).toBe("waterfall");
    expect(p.fmt).toBe("abs");
    expect(p.step).toBeNull();
    expect(p.trials).toEqual({ b: 2 });
    expect(readTaskCompareParams("").runIds).toEqual([]);
  });

  it("serialises trials from a map or a list", () => {
    expect(serializeTrials({ a: 1, b: 3 })).toBe("a:1,b:3");
    expect(serializeTrials([{ runId: "a", n: 2 }])).toBe("a:2");
    expect(serializeTrials({})).toBeNull();
    expect(parseTrials("a:1,b:3")).toEqual({ a: 1, b: 3 });
  });
});
