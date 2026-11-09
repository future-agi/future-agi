// W6 C3: a judge that cannot read its mapped media is named before the run,
// with the exact copy; code evals, readable media and unknown judges pass.
import { describe, expect, it } from "vitest";
import { judgeMediaRefusal, judgeMediaWarnings } from "../judgeMedia";

const columns = [
  { id: "c-audio", name: "call_audio", data_type: "audio" },
  { id: "c-shot", name: "screenshot", data_type: "image" },
  { id: "c-text", name: "message", data_type: "text" },
];
const inputs = {
  "gpt-4o-mini": ["text", "image"],
  "claude-sonnet-4-5": ["text", "image", "document"],
  "gemini-2.5-pro": ["text", "image", "audio", "video", "document"],
};
const correctness = (model, mapping) => ({
  evalTemplateName: "Correctness",
  model,
  config: { mapping },
});

describe("judgeMediaWarnings", () => {
  it("names the judge, the column and a judge that reads it", () => {
    const w = judgeMediaWarnings(
      [correctness("gpt-4o-mini", { input: "c-audio", output: "output" })],
      columns,
      inputs,
    );
    expect(w).toHaveLength(1);
    expect(w[0].text).toBe(
      "Correctness's judge gpt-4o-mini cannot read audio: call_audio is audio. Pick a judge that reads audio (gemini-2.5-pro) or map a transcript",
    );
    expect(judgeMediaRefusal(w)).toBe(w[0].text);
  });
  it("passes a judge that reads the media", () => {
    expect(
      judgeMediaWarnings(
        [correctness("gemini-2.5-pro", { input: "c-audio" })],
        columns,
        inputs,
      ),
    ).toEqual([]);
    expect(
      judgeMediaWarnings(
        [correctness("gpt-4o-mini", { input: "c-shot" })],
        columns,
        inputs,
      ),
    ).toEqual([]);
  });
  it("uses the default judge for an eval without its own model", () => {
    const w = judgeMediaWarnings(
      [correctness(undefined, { input: "c-audio" })],
      columns,
      inputs,
      {
        defaultJudge: "claude-sonnet-4-5",
      },
    );
    expect(w[0].judge).toBe("claude-sonnet-4-5");
  });
  it("skips code evals, text columns, paths and judges with unknown inputs", () => {
    expect(
      judgeMediaWarnings(
        [
          {
            ...correctness("gpt-4o-mini", { input: "c-audio" }),
            templateType: "code",
          },
          correctness("gpt-4o-mini", { input: "c-text" }),
          correctness("gpt-4o-mini", { input: "c-audio::$.transcript" }),
          correctness("my-model", { input: "c-audio" }),
        ],
        columns,
        inputs,
      ),
    ).toEqual([]);
    expect(judgeMediaRefusal([])).toBeNull();
  });
  it("says to map a transcript when no model reads the media", () => {
    const w = judgeMediaWarnings(
      [correctness("gpt-4o-mini", { input: "c-audio" })],
      columns,
      { "gpt-4o-mini": ["text", "image"] },
    );
    expect(w[0].text).toMatch(/is audio\. Map a transcript$/);
  });
});
