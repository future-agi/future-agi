import { describe, expect, it } from "vitest";
import { fmtTimeRelative } from "src/sections/common/format/fmt";
import {
  groupScores,
  scoresSummary,
  scoreText,
  SCORES_OPEN_MAX,
} from "../annotationsModel";

const s = (label, type, value, who, id) => ({
  id: id ?? `${label}-${who}`,
  label_name: label,
  label_type: type,
  value,
  annotator_name: who,
  updated_at: "2026-10-08T10:00:00Z",
});

describe("annotationsModel (RV#18)", () => {
  it("formats values with their scale", () => {
    expect(scoreText("star", { rating: 2 })).toBe("2/5");
    expect(scoreText("thumbs_up_down", { value: "down" })).toBe("Down");
    expect(scoreText("categorical", { selected: ["a", "b"] })).toBe("a, b");
    expect(scoreText("numeric", { value: 0.8 })).toBe("0.8");
    expect(scoreText("star", null)).toBe("—");
  });

  it("groups by label with agreement", () => {
    const g = groupScores([
      s("Correct?", "thumbs_up_down", { value: "down" }, "Meera"),
      s("Quality", "star", { rating: 2 }, "Meera"),
      s("Correct?", "thumbs_up_down", { value: "down" }, "Ravi"),
      s("Quality", "star", { rating: 4 }, "Ravi"),
      s("Note", "text", { text: "hi" }, "Ravi"),
    ]);
    expect(g.map((x) => x.label)).toEqual(["Correct?", "Quality", "Note"]);
    expect(g[0].agreement).toEqual({
      state: "agree",
      text: "2 of 2 agree · Down",
    });
    expect(g[1].agreement).toEqual({
      state: "split",
      text: "Split · 2/5 vs 4/5",
    });
    expect(g[2].agreement.state).toBe("single");
    expect(g[0].entries.map((e) => e.annotator)).toEqual(["Meera", "Ravi"]);
  });
});

describe("scores Section summary (one view, A1-31)", () => {
  it("says the count, labels and who scored last", () => {
    const scores = [
      s("Correct", "thumbs_up_down", { value: "up" }, "Asha"),
      {
        ...s("Correct", "thumbs_up_down", { value: "down" }, "Ravi"),
        updated_at: "2026-10-16T10:00:00Z",
      },
      s("Tone", "star", { rating: 4 }, "Asha"),
    ];
    expect(
      scoresSummary(scores, {
        now: Date.parse("2026-10-16T12:00:00Z"),
        relative: fmtTimeRelative,
      }),
    ).toBe("3 scores · 2 labels · last by Ravi 2h ago");
    expect(scoresSummary([s("Tone", "star", { rating: 4 }, "Asha")])).toBe(
      "1 score · 1 label · last by Asha",
    );
    expect(scoresSummary([])).toBe("");
    expect(SCORES_OPEN_MAX).toBe(5);
  });
});
