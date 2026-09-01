import { describe, it, expect, beforeEach } from "vitest";
import { buildAddEvalsDraft } from "../buildAddEvalsDraft";

// The draft is the only thing that survives the hop to the create page, so
// whatever preset the toolbar was showing has to travel with the range.
// Dropping it forces the create page to guess, and a day-granular guess
// rewrites the window the user was actually looking at.
const draftValues = (url) => {
  const draftId = new URLSearchParams(url.split("?")[1]).get("draft");
  return JSON.parse(localStorage.getItem(`task-draft-${draftId}`)).values;
};

describe("buildAddEvalsDraft time window", () => {
  beforeEach(() => localStorage.clear());

  it("carries the toolbar's preset into the draft", () => {
    const url = buildAddEvalsDraft({
      observeId: "proj",
      rowType: "spans",
      dateFilter: {
        dateFilter: ["2026-02-21 00:00:00", "2026-08-21 00:00:00"],
        dateOption: "6M",
      },
    });
    expect(draftValues(url).datePreset).toBe("6M");
  });

  it("keeps a zoomed window as Custom", () => {
    const url = buildAddEvalsDraft({
      observeId: "proj",
      rowType: "spans",
      dateFilter: {
        dateFilter: ["2026-08-21 08:00:00", "2026-08-21 15:00:00"],
        dateOption: "Custom",
      },
    });
    const values = draftValues(url);
    expect(values.datePreset).toBe("Custom");
    expect([values.startDate, values.endDate]).toEqual([
      "2026-08-21 08:00:00",
      "2026-08-21 15:00:00",
    ]);
  });

  it("treats a range with no preset as Custom rather than guessing", () => {
    const url = buildAddEvalsDraft({
      observeId: "proj",
      rowType: "spans",
      dateFilter: {
        dateFilter: ["2026-08-21 08:00:00", "2026-08-21 15:00:00"],
      },
    });
    expect(draftValues(url).datePreset).toBe("Custom");
  });

  // With no incoming window the helper generates a twelve-month range, so the
  // preset has to agree with the range it just built.
  it("labels its own generated fallback range 12M", () => {
    const url = buildAddEvalsDraft({ observeId: "proj", rowType: "spans" });
    expect(draftValues(url).datePreset).toBe("12M");
  });
});
