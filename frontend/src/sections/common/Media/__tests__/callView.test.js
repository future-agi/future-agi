// options-truth L2 G7: a call says how it was placed (Replay of the row's
// recording is not Full voice).
import { describe, expect, it } from "vitest";
import { depthWords } from "../CallView";

describe("depthWords", () => {
  it("names text only, full voice and a replay of the recording", () => {
    expect(depthWords({ depth: "text_only" })).toBe("Text only");
    expect(depthWords({ depth: "full_voice" })).toBe("Full voice");
    expect(
      depthWords({
        depth: "replay",
        replay: { column: "call_audio", caller_lines: 2 },
      }),
    ).toBe("Replay of call_audio (2 recorded caller lines)");
    expect(depthWords({ depth: "replay", replay: { caller_lines: 1 } })).toBe(
      "Replay (1 recorded caller line)",
    );
  });
});
