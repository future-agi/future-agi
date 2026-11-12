// W9 A1 (W9-SCOPE §5): metadata rows, YAML (a parse error names its line),
// the several-datasets patch, and the one-PUT-per-dataset writer with its
// toasts.
import { describe, expect, it, vi } from "vitest";
import {
  bulkCopy,
  bulkPatch,
  fromYaml,
  fullPatch,
  keysAcross,
  mapFromRows,
  metadataChips,
  rowsOf,
  toYaml,
  typedValue,
  valueInput,
} from "../metadataModel";
import {
  errorSentence,
  saveInSequence,
  sequenceToast,
} from "src/api/develop/dataset-org";

describe("rows", () => {
  it("types what the user typed", () => {
    expect(typedValue("2")).toBe(2);
    expect(typedValue("-1.5")).toBe(-1.5);
    expect(typedValue("true")).toBe(true);
    expect(typedValue("false")).toBe(false);
    expect(typedValue('"2"')).toBe("2");
    expect(typedValue("billing")).toBe("billing");
    expect(valueInput("2")).toBe('"2"');
    expect(valueInput(2)).toBe("2");
    expect(valueInput("billing")).toBe("billing");
  });

  it("builds a map and names the first problem", () => {
    expect(
      mapFromRows([
        { key: "domain", value: "billing" },
        { key: "tier", value: "2" },
        { key: "", value: "" },
      ]),
    ).toEqual({ metadata: { domain: "billing", tier: 2 }, error: null });
    expect(mapFromRows([{ key: "Domain", value: "x" }]).error).toBe(
      "Metadata key “Domain” must start with a lowercase letter and use only lowercase letters, digits, “.”, “_” or “-” (40 characters at most).",
    );
    expect(
      mapFromRows([
        { key: "a", value: "1" },
        { key: "a", value: "2" },
      ]).error,
    ).toBe("“a” is listed twice.");
    expect(mapFromRows([{ key: "notes", value: "x".repeat(201) }]).error).toBe(
      "The value of “notes” is longer than 200 characters.",
    );
    expect(rowsOf({ b: 1, a: "x" })).toEqual([
      { key: "a", value: "x" },
      { key: "b", value: 1 },
    ]);
  });
});

describe("YAML", () => {
  it("round-trips a map", () => {
    const m = { team: "support", domain: "billing", tier: 2, live: true };
    const text = toYaml(m);
    expect(text).toBe("domain: billing\nlive: true\nteam: support\ntier: 2\n");
    expect(fromYaml(text)).toEqual({ metadata: m, error: null });
    expect(fromYaml("team: ops\n").metadata).toEqual({ team: "ops" });
    expect(fromYaml("")).toEqual({ metadata: {}, error: null });
  });

  it("names the line of a parse error", () => {
    const res = fromYaml("domain: billing\nteam: [ops\n");
    expect(res.metadata).toBeNull();
    expect(res.error).toMatch(/^Line \d+: /);
  });

  it("names the line of a rule problem", () => {
    expect(fromYaml("domain: billing\nteam:\n  - a\n").error).toBe(
      "Line 2: “team” must be text, a number or true/false, not a list.",
    );
    expect(fromYaml("- a\n- b\n").error).toBe(
      "Metadata must be key: value lines, one per key.",
    );
  });
});

describe("patches", () => {
  it("several datasets: set keys and remove keys", () => {
    expect(bulkPatch([{ key: "domain", value: "billing" }], ["old"])).toEqual({
      patch: { domain: "billing", old: null },
      error: null,
    });
    expect(bulkPatch([{ key: "a", value: "1" }], ["a"]).error).toBe(
      "“a” is both set and removed.",
    );
    expect(bulkPatch([], []).error).toBe("Set or remove at least one key.");
    expect(bulkCopy(3)).toBe(
      "Keys you set are added or replaced on each of the 3 datasets; other keys stay.",
    );
  });

  it("one dataset: deleted keys are sent as null", () => {
    expect(fullPatch({ a: 1, b: 2 }, { b: 3, c: 4 })).toEqual({
      a: null,
      b: 3,
      c: 4,
    });
  });

  it("lists keys across datasets and builds chips", () => {
    expect(
      keysAcross([{ metadata: { a: 1, b: 2 } }, { metadata: { b: 3 } }, {}]),
    ).toEqual([
      { key: "a", count: 1 },
      { key: "b", count: 2 },
    ]);
    expect(
      metadataChips({ domain: "billing", team: "ops", tier: 2 }, 1),
    ).toEqual({
      chips: ["domain: billing"],
      more: 2,
    });
  });
});

describe("one PUT per dataset, in sequence", () => {
  const ds = [
    { id: "a", name: "Golden" },
    { id: "b", name: "Refund policy QA" },
    { id: "c", name: "Tasks" },
  ];

  it("saves each in order and reports success", async () => {
    const order = [];
    const put = vi.fn(async (id, body) => order.push([id, body]));
    const res = await saveInSequence(
      ds,
      (d) => ({ metadata: { by: d.id } }),
      put,
    );
    expect(order).toEqual([
      ["a", { metadata: { by: "a" } }],
      ["b", { metadata: { by: "b" } }],
      ["c", { metadata: { by: "c" } }],
    ]);
    expect(sequenceToast(res, "Saved metadata on")).toEqual({
      variant: "success",
      message: "Saved metadata on 3 datasets",
    });
    expect(sequenceToast(res, "Moved", " to support/billing").message).toBe(
      "Moved 3 datasets to support/billing",
    );
  });

  it("names the first failure with the server's sentence", async () => {
    const put = vi.fn(async (id) => {
      if (id === "b")
        throw {
          response: {
            data: { result: "A dataset can have up to 30 metadata keys." },
          },
        };
    });
    const res = await saveInSequence(ds, () => ({}), put);
    expect(res.saved.map((d) => d.id)).toEqual(["a", "c"]);
    expect(sequenceToast(res, "Saved metadata on")).toEqual({
      variant: "warning",
      message:
        "Saved on 2 of 3 · Refund policy QA: A dataset can have up to 30 metadata keys.",
    });
    const one = await saveInSequence([ds[1]], () => ({}), put);
    expect(sequenceToast(one, "Saved metadata on")).toEqual({
      variant: "error",
      message: "A dataset can have up to 30 metadata keys.",
    });
    expect(errorSentence(new Error("Network Error"))).toBe("Network Error");
    expect(errorSentence({ result: "Dataset not found" })).toBe(
      "Dataset not found",
    );
  });
});
