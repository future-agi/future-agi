import { describe, expect, it } from "vitest";
import {
  draftOfSources,
  proposalItemsOf,
  readSource,
} from "src/api/simulate-environments/_fixtures/sources";
import { readProgress, reviewDecisions, selectionResult } from "../proposals";

const read = (...names) =>
  names.map((name) =>
    readSource({ name, size: 1, origin: { kind: "upload" } }),
  );
const ids = (items) => items.map((item) => item.id);

describe("selectionResult", () => {
  it("keeps blocked items out even when checked, and returns them as failed", () => {
    const items = proposalItemsOf(read("orders.csv", "returns-policy.pdf"));
    const blocked = items.filter((item) => item.blocking);
    expect(blocked.length).toBeGreaterThan(0);

    const rule = items.find((item) => item.kind === "rule");
    const { accepted, failed } = selectionResult(
      items,
      ids(items).filter((id) => id !== rule.id),
    );

    expect(ids(failed)).toEqual(ids(blocked));
    expect(ids(accepted)).not.toContain(rule.id);
    expect(accepted.some((item) => item.blocking)).toBe(false);
  });

  it("lets an unreadable file block nothing but itself", () => {
    const sources = read("policy-scan.pdf", "customers.csv");
    const [scan, customers] = sources;
    const items = proposalItemsOf(sources);

    const { accepted, failed } = selectionResult(items, ids(items));

    expect(failed.map((item) => item.cite.sourceId)).toEqual([scan.source_id]);
    expect(accepted.map((item) => item.cite.sourceId)).toContain(
      customers.source_id,
    );
  });
});

describe("reviewDecisions", () => {
  it("accepts the picked rules and the added tables' columns", () => {
    const sources = read("customers.csv", "returns-policy.pdf");
    const [customers, policy] = sources;
    const draft = draftOfSources(sources, "sha256:test");
    const items = proposalItemsOf(sources);
    const rule = items.find((item) => item.kind === "rule");
    const table = items.find((item) => item.kind === "table");

    const decisions = reviewDecisions(draft, [rule, table], {
      [customers.source_id]: "replace",
    });

    expect(decisions.acceptedRuleIds).toEqual([`${policy.source_id}:p1`]);
    expect(decisions.acceptedMappingIndexes).toHaveLength(
      customers.ui.parsed.columns.length,
    );
    expect(decisions.personalDataModes).toEqual({
      [customers.source_id]: "synthetic_replace",
    });
  });
});

describe("readProgress", () => {
  const sources = read("returns-30.pdf", "returns-45.pdf");
  const [first, second] = sources;
  const prepared = {
    ui: {
      sources: sources.map((source) => ({ ...source, status: "reading" })),
      items: proposalItemsOf(sources),
    },
  };
  const live = (...done) =>
    sources.map((source) => ({
      ...source,
      status: done.includes(source) ? source.status : "reading",
    }));

  it("shows a file's items once it's read, and a conflict once both sides are", () => {
    const midRead = readProgress(prepared, live(first));

    expect(midRead.reading.map((source) => source.source_id)).toEqual([
      second.source_id,
    ]);
    expect(
      midRead.items.every((item) => item.cite?.sourceId === first.source_id),
    ).toBe(true);
    expect(ids(midRead.items)).not.toContain("conflict-window");

    expect(ids(readProgress(prepared, live(first, second)).items)).toContain(
      "conflict-window",
    );
  });

  it("reopened mid-read keeps finished files and repeats nothing", () => {
    const before = readProgress(prepared, live(first));
    const reopened = readProgress(prepared, live(first));
    const after = readProgress(prepared, live(first, second));

    expect(ids(reopened.items)).toEqual(ids(before.items));
    expect(new Set(ids(after.items)).size).toBe(after.items.length);
    expect(ids(after.items)).toEqual(expect.arrayContaining(ids(before.items)));
  });
});
