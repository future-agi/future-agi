import { describe, expect, it } from "vitest";
import { clipOf, measuredReadouts } from "../AudioLens";

describe("AudioLens readouts from the clip (options-truth L3 G7 W5)", () => {
  const clip = {
    kind: "audio",
    transcript: "Your refund is on its way.",
    measured: {
      ttfb_ms: 732,
      roundtrip_wer: 0.023,
      roundtrip_transcript: "Your refund is on its way",
      naturalness: 4.3,
    },
  };
  it("reads a clip served as a JSON string or an object", () => {
    expect(clipOf(JSON.stringify(clip))).toEqual(clip);
    expect(clipOf(clip)).toBe(clip);
    expect(clipOf("/x/a.wav")).toBeNull();
    expect(clipOf("{not json")).toBeNull();
  });
  it("shows what was measured on the clip", () => {
    const r = measuredReadouts(JSON.stringify(clip));
    expect(r["eval:ttfb"].display).toBe("732 ms");
    expect(r["eval:roundtrip_wer"].display).toBe("2.3%");
    expect(r["eval:naturalness"].display).toBe("4.3");
    expect(measuredReadouts("/x/a.wav")).toEqual({});
  });
});
