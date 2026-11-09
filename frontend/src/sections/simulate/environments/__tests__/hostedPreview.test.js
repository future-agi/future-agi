// options-truth L5 finalize: the Hosted platform card previews only the
// platforms its panel offers (Bland and ElevenLabs left the panel in G5).
import { describe, expect, it } from "vitest";
import { OPTIONS } from "../environmentOptions";
import { HOSTED_PLATFORMS_BY_TYPE } from "../hostedPlatforms";

describe("Hosted platform card preview", () => {
  it("names only platforms the panel lists", () => {
    const hosted = OPTIONS.find((o) => o.id === "hosted");
    const names = Object.values(HOSTED_PLATFORMS_BY_TYPE)
      .flat()
      .map((p) => p.name);
    expect(hosted.preview.length).toBeGreaterThan(0);
    hosted.preview.forEach((chip) =>
      expect(names.some((n) => n.split(" ")[0] === chip)).toBe(true),
    );
  });
});
