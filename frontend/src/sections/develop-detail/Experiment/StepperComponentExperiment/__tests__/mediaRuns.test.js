import { describe, expect, it } from "vitest";
import { mediaRunsOf } from "../mediaRuns";

describe("mediaRunsOf (options-truth L3 G7 W5)", () => {
  it("TTS: one run per model and picked voice, named like the created runs", () => {
    const runs = mediaRunsOf("tts", [
      {
        model: [
          {
            value: "tts-1-hd",
            voices: [{ voice: "alloy" }, { voice: "nova" }],
          },
          { value: "gpt-4o-mini-tts", voices: ["coral"] },
          { value: "sonic-2", voices: [] },
        ],
      },
    ]);
    expect(runs.map((r) => `${r.letter} ${r.name}`)).toEqual([
      "A TTS × tts-1-hd · alloy",
      "B TTS × tts-1-hd · nova",
      "C TTS × gpt-4o-mini-tts · coral",
    ]);
  });
  it("STT and Image: one run per model; LLM forms have none here", () => {
    const cfg = [
      { model: [{ value: "whisper-1" }, { value: "gpt-4o-transcribe" }, {}] },
    ];
    expect(mediaRunsOf("stt", cfg).map((r) => r.name)).toEqual([
      "STT × whisper-1",
      "STT × gpt-4o-transcribe",
    ]);
    expect(
      mediaRunsOf("image", [{ model: [{ value: "gpt-image-1" }] }])[0],
    ).toMatchObject({
      letter: "A",
      name: "IMAGE × gpt-image-1",
      voice: null,
    });
    expect(mediaRunsOf("llm", cfg)).toEqual([]);
    expect(mediaRunsOf(undefined, cfg)).toEqual([]);
  });
});
