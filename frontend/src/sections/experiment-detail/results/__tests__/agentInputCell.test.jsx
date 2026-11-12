// An agent (graph) run's row stores its input as the native snapshot
// envelope {"values": {"<dataset column id>": "..."}}. The experiment page's
// Input column showed that JSON, keyed by a column id. The cell now shows
// the input text (one value) or each value by its column name.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { InputCell } from "../cells/ExampleCells";
import { columnNamesById, displayInputs } from "../cells/displayInputs";
import { extraFieldNames, primaryText } from "../cells/cellCommon";
import { inputsPreviewChoice } from "src/sections/common/Preview/distinguishingField";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));

const QUESTION_ID = "884f2c0e-51a0-43b9-9104-5b6d463fc839";
const CONTEXT_ID = "5ef7eb3f-ef12-4336-ab53-b44225022fcd";
const NAMES = columnNamesById([
  { id: QUESTION_ID, name: "question", data_type: "text" },
  { id: CONTEXT_ID, name: "context", data_type: "text" },
  { id: "no-name" },
]);

describe("displayInputs", () => {
  it("unwraps one value to its column name and its text", () => {
    const inputs = displayInputs(
      { values: { [QUESTION_ID]: "What is 2 + 2?" } },
      NAMES,
    );
    expect(inputs).toEqual({ question: "What is 2 + 2?" });
    expect(primaryText(inputs)).toBe("What is 2 + 2?");
  });

  it("names every value by its column, never by its id", () => {
    const inputs = displayInputs(
      { values: { [QUESTION_ID]: "Why?", [CONTEXT_ID]: "Because." } },
      NAMES,
    );
    expect(inputs).toEqual({ question: "Why?", context: "Because." });
    expect(extraFieldNames(inputs)).toEqual(["context"]);
  });

  it("keeps an unknown id as its key and leaves other inputs as they are", () => {
    expect(displayInputs({ values: { abc: "x" } }, NAMES)).toEqual({
      abc: "x",
    });
    expect(displayInputs({ values: { [QUESTION_ID]: "x" } })).toEqual({
      [QUESTION_ID]: "x",
    });
    const prompt = { question: "Hi", values: "a list" };
    expect(displayInputs(prompt, NAMES)).toBe(prompt);
    expect(displayInputs({ question: "Hi" }, NAMES)).toEqual({
      question: "Hi",
    });
    expect(displayInputs("plain", NAMES)).toBe("plain");
    expect(displayInputs(null, NAMES)).toBe(null);
    expect(displayInputs({ values: ["a"] }, NAMES)).toEqual({
      values: ["a"],
    });
  });

  it("lets the preview pick a named column, not the envelope", () => {
    const rows = [
      { inputs: { values: { [QUESTION_ID]: "A?", [CONTEXT_ID]: "same" } } },
      { inputs: { values: { [QUESTION_ID]: "B?", [CONTEXT_ID]: "same" } } },
    ];
    expect(inputsPreviewChoice(rows).field).toBe("values");
    expect(
      inputsPreviewChoice(
        rows.map((r) => ({ inputs: displayInputs(r.inputs, NAMES) })),
      ).field,
    ).toBe("question");
  });
});

describe("InputCell for an agent run row", () => {
  it("shows the input text, not the JSON keyed by a column id", () => {
    render(
      <InputCell
        data={{
          example_id: "e1",
          inputs: { values: { [QUESTION_ID]: "What is 2 + 2?" } },
        }}
        context={{ inputNames: NAMES, previewField: "values" }}
      />,
    );
    expect(screen.getByText("What is 2 + 2?")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(QUESTION_ID);
    expect(document.body.textContent).not.toContain("{");
  });

  it("shows the other values by column name", () => {
    render(
      <InputCell
        data={{
          example_id: "e2",
          inputs: { values: { [QUESTION_ID]: "Why?", [CONTEXT_ID]: "Ctx" } },
        }}
        context={{
          inputNames: NAMES,
          previewField: "values",
          inputPreviewField: "question",
        }}
      />,
    );
    expect(screen.getByText("Why?")).toBeInTheDocument();
    expect(screen.getByText("+1 field")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(CONTEXT_ID);
  });

  it("leaves a prompt run row's named input as it was", () => {
    render(
      <InputCell
        data={{ example_id: "e3", inputs: { question: "Name a colour" } }}
        context={{ inputNames: NAMES, previewField: "question" }}
      />,
    );
    expect(screen.getByText("Name a colour")).toBeInTheDocument();
  });
});
