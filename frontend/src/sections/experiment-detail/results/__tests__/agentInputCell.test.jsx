// An agent (graph) run's row stores its input as the native snapshot
// envelope {"values": {"<dataset column id>": "..."}}. The experiment page's
// Input column showed that JSON, keyed by a column id. The cell now shows
// the input text (one value) or each value by its column name.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { InputCell } from "../cells/ExampleCells";
import {
  columnNamesById,
  columnTypesByName,
  displayInputs,
} from "../cells/displayInputs";
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

// An uploaded document is stored under a URL with no file extension; only
// its column's type says it is a document.
const FILE_ID = "2c7d0b9e-7a51-4f0e-9a43-0d5f3c1e6b21";
const DOCUMENT_NAME = "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b";
const DOCUMENT_URL = `http://localhost/documents/ds-1/${DOCUMENT_NAME}`;
// A second document column with the same name, "file".
const SECOND_FILE_ID = "9a4e6c2b-3d1f-4b8a-8e7c-1f2a3b4c5d6e";
const SECOND_DOCUMENT_NAME = "0b9c8d7e-6f5a-4b3c-9d2e-1a0f9e8d7c6b";
const SECOND_DOCUMENT_URL = `http://localhost/documents/ds-1/${SECOND_DOCUMENT_NAME}`;
const SAME_NAME_DOCUMENTS = [
  { id: FILE_ID, name: "file", data_type: "document" },
  { id: SECOND_FILE_ID, name: "file", data_type: "document" },
];

describe("columnTypesByName", () => {
  it("types each media column under the names a results row keys it by", () => {
    expect(
      columnTypesByName([
        { id: QUESTION_ID, name: "question", data_type: "text" },
        { id: FILE_ID, name: "file", data_type: "document" },
        { id: "a", name: "shot", data_type: "image" },
        { id: "b", name: "shot", data_type: "text" },
        { id: "c", data_type: "audio" },
      ]),
    ).toEqual({
      file: "document",
      [`file (${FILE_ID})`]: "document",
      shot: "image",
      "shot (a)": "image",
    });
    expect(columnTypesByName(undefined)).toEqual({});
  });

  it("gives the name to the first column that has it, as the inputs are named", () => {
    const types = columnTypesByName(SAME_NAME_DOCUMENTS);
    expect(types).toEqual({
      file: "document",
      [`file (${FILE_ID})`]: "document",
      [`file (${SECOND_FILE_ID})`]: "document",
    });
    const named = displayInputs(
      {
        values: {
          [FILE_ID]: DOCUMENT_URL,
          [SECOND_FILE_ID]: SECOND_DOCUMENT_URL,
        },
      },
      columnNamesById(SAME_NAME_DOCUMENTS),
    );
    expect(Object.keys(named)).toEqual(["file", `file (${SECOND_FILE_ID})`]);
    Object.keys(named).forEach((name) => expect(types[name]).toBe("document"));
  });

  it("leaves the name untyped when a text column has it first", () => {
    expect(
      columnTypesByName([
        { id: "t", name: "file", data_type: "text" },
        { id: FILE_ID, name: "file", data_type: "document" },
      ]),
    ).toEqual({ [`file (${FILE_ID})`]: "document" });
  });
});

describe("InputCell for a document input", () => {
  const renderDocument = (context) =>
    render(
      <InputCell
        data={{ example_id: "e4", inputs: { file: DOCUMENT_URL } }}
        context={context}
      />,
    );

  it("shows the document's preview control, not its URL as text", () => {
    renderDocument({ inputTypes: { file: "document" } });
    expect(
      screen.getByRole("button", { name: `Open ${DOCUMENT_NAME}` }),
    ).toBeInTheDocument();
    expect(screen.queryByText(DOCUMENT_URL)).toBeNull();
  });

  it("reads the preview field's type when the grid picks that field", () => {
    renderDocument({
      inputTypes: { file: "document" },
      inputPreviewField: "file",
    });
    expect(
      screen.getByRole("button", { name: `Open ${DOCUMENT_NAME}` }),
    ).toBeInTheDocument();
  });

  it("opens the stored document in the media preview", () => {
    renderDocument({ inputTypes: { file: "document" } });
    fireEvent.click(
      screen.getByRole("button", { name: `Open ${DOCUMENT_NAME}` }),
    );
    const objects = document.querySelectorAll("object");
    expect(objects).toHaveLength(1);
    expect(objects[0].getAttribute("data")).toBe(DOCUMENT_URL);
    expect(objects[0].getAttribute("aria-label")).toBe(DOCUMENT_NAME);
    // No extension, so no PDF type is claimed for it.
    expect(objects[0].getAttribute("type")).toBeNull();
  });

  it("keeps an untyped input's URL as text", () => {
    renderDocument({});
    expect(screen.getByText(DOCUMENT_URL)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Open / })).toBeNull();
  });
});

describe("InputCell for two document columns with the same name", () => {
  it.each([
    [
      "named by the results read",
      {
        file: DOCUMENT_URL,
        [`file (${SECOND_FILE_ID})`]: SECOND_DOCUMENT_URL,
      },
    ],
    [
      "stored by column id",
      {
        values: {
          [FILE_ID]: DOCUMENT_URL,
          [SECOND_FILE_ID]: SECOND_DOCUMENT_URL,
        },
      },
    ],
  ])("shows the first document's preview control (%s)", (_, inputs) => {
    render(
      <InputCell
        data={{ example_id: "e5", inputs }}
        context={{
          inputNames: columnNamesById(SAME_NAME_DOCUMENTS),
          inputTypes: columnTypesByName(SAME_NAME_DOCUMENTS),
        }}
      />,
    );
    expect(
      screen.getByRole("button", { name: `Open ${DOCUMENT_NAME}` }),
    ).toBeInTheDocument();
    expect(screen.queryByText(DOCUMENT_URL)).toBeNull();
  });
});
