// M10 (W7 integration H-A1b): the eval detail header's version chip. A typed
// eval is versioned by its own editor, so its chip names the eval's current
// version even while the page holds the default version it loaded (V1), as
// the remote walk saw after Save changes made v2; every other eval keeps
// naming the version picked on the page.
import { describe, expect, it } from "vitest";
import { versionChipOf } from "../versionChip";

const v1 = { id: "ver-1", version_number: 1, is_default: true };

describe("versionChipOf", () => {
  it("a remote scorer saved as v2 reads V2, not the V1 the page loaded", () => {
    expect(
      versionChipOf({
        viewingVersion: v1,
        evalData: { current_version: "V2" },
        evalType: "remote",
      }),
    ).toBe("V2");
  });

  it("a decision tree after Save version reads its current version", () => {
    expect(
      versionChipOf({
        viewingVersion: v1,
        evalData: { current_version: "V3" },
        evalType: "dag",
      }),
    ).toBe("V3");
  });

  it("an LLM eval names the version picked on the page", () => {
    expect(
      versionChipOf({
        viewingVersion: { version_number: 2 },
        evalData: { current_version: "V3" },
        evalType: "llm",
      }),
    ).toBe("V2");
  });

  it("with no version picked it names the current one, else V1", () => {
    expect(
      versionChipOf({ evalData: { current_version: "V4" }, evalType: "llm" }),
    ).toBe("V4");
    expect(versionChipOf({ evalData: {}, evalType: "code" })).toBe("V1");
  });
});
