import { describe, expect, it } from "vitest";
import { normalizeRecordings } from "src/utils/utils";

describe("normalizeRecordings", () => {
  it("keeps the backend's stereo channel layout", () => {
    const layout = { left: "customer", right: "assistant" };
    expect(
      normalizeRecordings({ stereo: "s.wav", stereo_channels: layout })
        .stereoChannels,
    ).toEqual(layout);
  });

  it("keeps the layout on the nested recording shape too", () => {
    const layout = { left: "assistant", right: "customer" };
    expect(
      normalizeRecordings({
        stereo_url: "s.wav",
        mono: {},
        stereo_channels: layout,
      }).stereoChannels,
    ).toEqual(layout);
  });

  it("has no layout when the backend sends none", () => {
    expect(normalizeRecordings({ stereo: "s.wav" }).stereoChannels).toBeNull();
    expect(normalizeRecordings(null).stereoChannels).toBeNull();
  });
});
