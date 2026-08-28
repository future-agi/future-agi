import { describe, expect, it } from "vitest";

import { OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS } from "src/config/runtime_limits";
import { boundObserveListRow } from "../observeListPayload";

describe("boundObserveListRow", () => {
  it("preserves ordinary values and bounds large prompts", () => {
    const tags = ["one", "two"];
    const row = boundObserveListRow({
      trace_id: "trace-1",
      tags,
      input: "x".repeat(OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS + 50),
    });

    expect(row.trace_id).toBe("trace-1");
    expect(row.tags).toBe(tags);
    expect(row.input).toHaveLength(OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS);
    expect(row.input).toMatch(/…$/);
  });

  it("serializes only structured values that exceed the preview budget", () => {
    const small = { customer: "a" };
    const row = boundObserveListRow({
      small,
      huge: { payload: "x".repeat(OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS + 50) },
    });

    expect(row.small).toBe(small);
    expect(typeof row.huge).toBe("string");
    expect(row.huge).toHaveLength(OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS);
    expect(row.huge).toMatch(/…$/);
  });
});
