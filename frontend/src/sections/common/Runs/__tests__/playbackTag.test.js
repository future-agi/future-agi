// L7c g1 (the capability contract c): no Simulated / Plays back tag beside a playing run.
import { describe, expect, it } from "vitest";
import PlaybackTag, { playbackText } from "../PlaybackTag";

describe("PlaybackTag (hidden)", () => {
  it("renders nothing and has no text, whatever the run's playback speed", () => {
    expect(playbackText({ playback_speed: 20 })).toBeNull();
    expect(playbackText({})).toBeNull();
    expect(PlaybackTag({ run: { playback_speed: 20 } })).toBeNull();
  });
});
