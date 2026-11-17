import { describe, it, expect } from "vitest";
import { moreSettingsChanges } from "../moreSettings";

describe("moreSettingsChanges (A2-15 collapsed header)", () => {
  it("is empty on defaults, so the section starts collapsed", () => {
    expect(
      moreSettingsChanges({ experimentType: "llm", startFrom: null }),
    ).toEqual([]);
    expect(moreSettingsChanges()).toEqual([]);
  });

  it("names a non-text type and a Start from", () => {
    expect(
      moreSettingsChanges({
        experimentType: "tts",
        startFrom: { experimentId: "e1", label: "#2" },
      }),
    ).toEqual(["TTS", "starting from #2"]);
    expect(moreSettingsChanges({ startFrom: { experimentId: "e1" } })).toEqual([
      "starting from an earlier experiment",
    ]);
  });
});
