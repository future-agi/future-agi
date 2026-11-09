// options-truth L7b G3 (typed:model-params, FE): a TTS / STT / image rerun
// offers only the model (and the voice for TTS).
import { describe, expect, it } from "vitest";
import {
  mediaChangesOf,
  mediaModelsOf,
  mediaTypeOfConfig,
} from "../mediaChangesModel";

describe("L7b G3 media fields in Rerun with changes", () => {
  it("knows a media run by its config", () => {
    expect(mediaTypeOfConfig({ media: { type: "stt" } })).toBe("stt");
    expect(mediaTypeOfConfig({ kind: "prompt" })).toBeNull();
  });
  it("lists the models of the run's type, its own first", () => {
    const stt = mediaModelsOf("stt", "whisper-1");
    expect(stt[0]).toBe("whisper-1");
    expect(stt).toContain("gpt-4o-transcribe");
    expect(stt).toContain("nova-3");
    expect(stt.some((m) => /tts|image|dall-e/.test(m))).toBe(false);
    expect(mediaModelsOf("tts")).toContain("tts-1-hd");
    expect(mediaModelsOf("image")).toContain("dall-e-3");
  });
  it("posts the model and the TTS voice, nothing else", () => {
    const tts = { media: { type: "tts", model: "tts-1-hd", voice: "alloy" } };
    expect(mediaChangesOf(tts, { model: "tts-1-hd", voice: "alloy" })).toEqual(
      [],
    );
    expect(
      mediaChangesOf(tts, { model: "gpt-4o-mini-tts", voice: "nova" }),
    ).toEqual([
      { axis: "model", field: "id", value: "gpt-4o-mini-tts" },
      { axis: "voice", field: "voice", value: "nova" },
    ]);
    expect(
      mediaChangesOf(
        { media: { type: "stt", model: "whisper-1" } },
        { voice: "nova" },
      ),
    ).toEqual([]);
  });
});
