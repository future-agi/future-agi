// The Hosted platform card previews only the platforms its panel offers.
// Bland.ai and ElevenLabs are listed in the panel but disabled, so the card
// does not name them.
import { describe, expect, it } from "vitest";
import { OPTIONS } from "../environmentOptions";
import { HOSTED_PLATFORMS_BY_TYPE } from "../hostedPlatforms";

describe("Hosted platform card preview", () => {
  it("names only platforms the panel offers", () => {
    const hosted = OPTIONS.find((o) => o.id === "hosted");
    const names = Object.values(HOSTED_PLATFORMS_BY_TYPE)
      .flat()
      .filter((p) => !p.comingSoon)
      .map((p) => p.name);
    expect(hosted.preview.length).toBeGreaterThan(0);
    hosted.preview.forEach((chip) =>
      expect(names.some((n) => n.split(" ")[0] === chip)).toBe(true),
    );
  });
});
