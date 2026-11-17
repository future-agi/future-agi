import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CodeEvalEditor, { PYTHON_CODE_TEMPLATE } from "../CodeEvalEditor";

// Monaco does not run in jsdom.
vi.mock("../CodeEditor", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { post: vi.fn() },
}));

const LANGUAGE_HELP =
  "The run's language is detected from the code; this choice decides it only when the code could be either.";

describe("CodeEvalEditor language select", () => {
  it.each([
    ["an editable eval", false],
    ["a read-only eval", true],
  ])("says under the select how the language is chosen (%s)", (_, disabled) => {
    render(
      <CodeEvalEditor
        code={PYTHON_CODE_TEMPLATE}
        setCode={vi.fn()}
        codeLanguage="python"
        setCodeLanguage={vi.fn()}
        disabled={disabled}
      />,
    );

    const select = screen.getByRole("combobox");
    const help = screen.getByText(LANGUAGE_HELP);

    expect(
      select.compareDocumentPosition(help) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
