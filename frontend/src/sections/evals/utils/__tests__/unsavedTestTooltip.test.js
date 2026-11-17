import { describe, expect, it } from "vitest";
import { unsavedTestTooltip } from "../unsavedDefinition";

// The tooltip on "Testing unsaved changes" says only what is true for every
// evaluation type: Test runs the unsaved edits and saves nothing, and Save
// Version keeps them. The composite's words stay as they are.

const ONE_SENTENCE =
  "Test runs your unsaved edits without saving them. Save Version keeps your edits for experiments and runs.";

describe("tooltip on Testing unsaved changes", () => {
  it("says only that Test runs the unsaved edits for an LLM judge", () => {
    expect(
      unsavedTestTooltip({
        isComposite: false,
        evalType: "llm",
        isJevModel: false,
      }),
    ).toBe(ONE_SENTENCE);
  });

  it("says only that Test runs the unsaved edits for an agent judge", () => {
    expect(
      unsavedTestTooltip({
        isComposite: false,
        evalType: "agent",
        isJevModel: false,
      }),
    ).toBe(ONE_SENTENCE);
  });

  it("says nothing about the default version for code, which Test runs as edited", () => {
    expect(
      unsavedTestTooltip({
        isComposite: false,
        evalType: "code",
        isJevModel: false,
      }),
    ).toBe(ONE_SENTENCE);
  });

  it("says nothing about the default version for a Jev judge, which Test runs as edited", () => {
    expect(
      unsavedTestTooltip({
        isComposite: false,
        evalType: "llm",
        isJevModel: true,
      }),
    ).toBe(ONE_SENTENCE);
  });

  it("never says the pass threshold or system message come from the default version", () => {
    for (const evalType of ["llm", "agent", "code", undefined]) {
      for (const isJevModel of [false, true]) {
        const tooltip = unsavedTestTooltip({
          isComposite: false,
          evalType,
          isJevModel,
        });
        expect(tooltip).toBe(ONE_SENTENCE);
        expect(tooltip).not.toMatch(/default version/);
      }
    }
  });

  it("keeps the composite's words", () => {
    expect(
      unsavedTestTooltip({
        isComposite: true,
        evalType: "llm",
        isJevModel: false,
      }),
    ).toBe(
      "Test runs what is on screen without saving it. Save Changes keeps it for experiments and runs.",
    );
  });
});
