import { describe, it, expect, vi, afterEach } from "vitest";
import { selectContractedList } from "../contract-validation";

const wrap = (result) => ({ data: { status: true, result } });

describe("selectContractedList", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the result array when every item carries the required fields", () => {
    const raw = wrap([{ label_id: "l1", score_source: "human" }]);

    const out = selectContractedList(raw, {
      requiredItemKeys: ["label_id", "score_source"],
      label: "scores/for-source",
    });

    expect(out).toEqual([{ label_id: "l1", score_source: "human" }]);
  });

  it("throws (in dev/test) naming the dropped field when a required key is missing", () => {
    // Simulates a serializer rename: score_source -> score_origin.
    const raw = wrap([{ label_id: "l1", score_origin: "human" }]);

    expect(() =>
      selectContractedList(raw, {
        requiredItemKeys: ["label_id", "score_source"],
        label: "scores/for-source",
      }),
    ).toThrow(/score_source/);
  });

  it("unwraps the axios envelope and tolerates results/top-level shapes", () => {
    expect(
      selectContractedList(
        { data: { results: [{ id: "1" }] } },
        { requiredItemKeys: ["id"], label: "x" },
      ),
    ).toEqual([{ id: "1" }]);
  });

  it("returns the fallback when there is no list payload", () => {
    expect(
      selectContractedList(
        { data: {} },
        { requiredItemKeys: ["id"], label: "x", fallback: [] },
      ),
    ).toEqual([]);
  });
});
