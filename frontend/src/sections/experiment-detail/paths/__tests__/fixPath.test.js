import { describe, expect, it } from "vitest";
import {
  causeForPath,
  fixReasonOf,
  fixTargetOf,
  ownerNodeOf,
} from "../fixPath";

const path = {
  id: "lookup_order ×2 → search_kb → reply",
  steps: [
    { name: "lookup_order", repeat: 2 },
    { name: "search_kb", repeat: 1 },
    { name: "reply", repeat: 1 },
  ],
  attempts: 24,
  failed: 18,
  example_rows: ["r13", "r18"],
};
const nodes = [{ id: "classify_intent" }, { id: "respond" }];

describe("fixTargetOf", () => {
  it("single run: only failing paths", () => {
    expect(fixTargetOf(path, { compare: false, prun: "B" })).toEqual({
      runId: "B",
      path,
    });
    expect(
      fixTargetOf({ ...path, failed: 0 }, { compare: false, prun: "B" }),
    ).toBeNull();
  });
  it("compare: the side that fails (this run first)", () => {
    const row = { a: { ...path, failed: 0 }, b: path };
    expect(fixTargetOf(row, { compare: true, prun: "D", pcmp: "B" })).toEqual({
      runId: "B",
      path,
    });
    expect(
      fixTargetOf({ a: path, b: path }, { compare: true, prun: "D", pcmp: "B" })
        .runId,
    ).toBe("D");
    expect(
      fixTargetOf(
        { a: null, b: { ...path, failed: 0 } },
        { compare: true, prun: "D", pcmp: "B" },
      ),
    ).toBeNull();
  });
});

describe("ownerNodeOf", () => {
  it("the finding's node when it is an LLM node", () => {
    expect(ownerNodeOf(path, nodes, { node: "respond" })).toBe("respond");
  });
  it("a tool step belongs to the LLM node before it, else the first node", () => {
    expect(ownerNodeOf(path, nodes, null)).toBe("classify_intent");
    const p = { steps: [{ name: "respond" }, { name: "issue_refund" }] };
    expect(ownerNodeOf(p, nodes, { node: "lookup_order" })).toBe("respond");
    expect(ownerNodeOf(path, [], null)).toBeNull();
  });
});

describe("causeForPath / fixReasonOf", () => {
  const cause = {
    id: "RC_PREFIX",
    run_id: "B",
    title: 'Order id loses its "A-" prefix',
    rows: [{ row_id: "r18" }],
    first_divergence: {
      step_name: "classify_intent",
      detail: "classify_intent output changed",
    },
  };
  it("matches by run and a shared row", () => {
    expect(causeForPath([cause], "B", path)).toBe(cause);
    expect(causeForPath([cause], "D", path)).toBeNull();
    expect(
      causeForPath([cause], "B", { ...path, example_rows: ["x"] }),
    ).toBeNull();
  });
  it("reason: path · fails k of n tries · first wrong step (finding title over a generic detail)", () => {
    expect(fixReasonOf(path, cause)).toBe(
      'Fixing: lookup_order ×2 → search_kb → reply · fails 18 of 24 tries · first wrong step: classify_intent (Order id loses its "A-" prefix)',
    );
    expect(
      fixReasonOf(path, {
        ...cause,
        first_divergence: {
          step_name: "lookup_order",
          detail: 'order id without "A-"',
        },
      }),
    ).toBe(
      'Fixing: lookup_order ×2 → search_kb → reply · fails 18 of 24 tries · first wrong step: lookup_order (order id without "A-")',
    );
    expect(fixReasonOf(path, null)).toBe(
      "Fixing: lookup_order ×2 → search_kb → reply · fails 18 of 24 tries",
    );
  });
});
