import { describe, expect, it } from "vitest";
import {
  deltaParts,
  deltaTooltip,
  filterVersions,
  restoreItems,
} from "../versionText";

describe("deltaParts / deltaTooltip (RV#31)", () => {
  it("hides zero parts", () => {
    expect(deltaParts({ added: 0, modified: 6, deleted: 2 })).toEqual([
      { key: "modified", glyph: "~", n: 6, word: "edited" },
      { key: "deleted", glyph: "−", n: 2, word: "removed" },
    ]);
    expect(deltaParts({ added: 16 }).map((p) => p.key)).toEqual(["added"]);
    expect(deltaParts({})).toEqual([]);
  });
  it("says it in words", () => {
    expect(deltaTooltip({ added: 10, modified: 2 })).toBe(
      "+10 rows added, ~2 rows edited",
    );
    expect(deltaTooltip({ split_changes: 3 })).toBe("3 split changes");
    expect(deltaTooltip({})).toBe("No row changes");
  });
});

describe("restoreItems (RV#30)", () => {
  it("lists what a restore changes", () => {
    expect(restoreItems({ added: 10, modified: 2, deleted: 0 }, 6)).toEqual([
      "10 rows added after it will be removed",
      "2 edited rows go back to their old values",
    ]);
    expect(restoreItems({ modified: 1, deleted: 1 }, 6)[0]).toBe(
      "1 edited row goes back to its old values",
    );
    expect(restoreItems({}, 6)).toEqual([]);
  });
});

describe("filterVersions (RV#32)", () => {
  const vs = [
    {
      number: 6,
      tags: [],
      description: "Added 10 rows",
      created_by: { id: "u1", name: "Ravi Menon" },
      experiments: [{ id: "e4" }],
    },
    {
      number: 5,
      tags: ["golden-v3"],
      description: "Corrected tool calls",
      created_by: { id: "u2", name: "Asha Rao" },
      experiments: [],
    },
    {
      number: 4,
      tags: ["golden-v2"],
      description: "Multi-intent",
      created_by: { id: "u3", name: "Meera Iyer" },
      experiments: [],
    },
  ];
  const n = (r) => r.map((v) => v.number);
  it("matches number, tag, text and author", () => {
    expect(n(filterVersions(vs, { q: "v5" }))).toEqual([5]);
    expect(n(filterVersions(vs, { q: "golden" }))).toEqual([5, 4]);
    expect(n(filterVersions(vs, { q: "asha" }))).toEqual([5]);
  });
  it("applies chips", () => {
    expect(n(filterVersions(vs, { chips: ["tagged"] }))).toEqual([5, 4]);
    expect(n(filterVersions(vs, { chips: ["used"] }))).toEqual([6]);
    expect(
      n(filterVersions(vs, { chips: ["mine"], me: { id: "u3" } })),
    ).toEqual([4]);
  });
});
