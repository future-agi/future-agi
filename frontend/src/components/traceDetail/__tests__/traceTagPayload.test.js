import { describe, expect, it } from "vitest";
import { serializeTraceTags } from "../traceTagPayload";
import { normalizeTags } from "../tagUtils";

describe("serializeTraceTags", () => {
  it("converts rich trace tags to the string payload expected by the API", () => {
    expect(
      serializeTraceTags([
        { name: "customer-escalation", color: "#EF4444" },
        { name: "needs-review", color: "#3B82F6" },
      ]),
    ).toEqual(["customer-escalation", "needs-review"]);
  });

  it("keeps existing string tags and supports clearing all tags", () => {
    expect(serializeTraceTags(["existing-tag"])).toEqual(["existing-tag"]);
    expect(serializeTraceTags([])).toEqual([]);
  });

  // TraceTagsUpdate items are non-blank strings; one blank legacy tag must
  // not make the API reject the whole list.
  it("drops blank names", () => {
    expect(
      serializeTraceTags([
        "",
        "   ",
        { name: "", color: "#8B5CF6" },
        { name: "prod", color: "#3B82F6" },
      ]),
    ).toEqual(["prod"]);
  });

  // The call sites pass normalizeTags() output, so pin the pair together:
  // stored trace tags are strings, and a blank one is dropped on the next save.
  it("drops blank stored tags after normalizeTags", () => {
    expect(serializeTraceTags(normalizeTags(["", "prod", "  "]))).toEqual([
      "prod",
    ]);
  });
});
