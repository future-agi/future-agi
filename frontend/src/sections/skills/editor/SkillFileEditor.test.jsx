import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SkillFileEditor from "./SkillFileEditor";

const editor = vi.hoisted(() => ({ setPosition: vi.fn(), revealLineInCenter: vi.fn(), focus: vi.fn(), copy: vi.fn() }));
vi.mock("@monaco-editor/react", () => ({
  useMonaco: () => ({
    Uri: { parse: (path) => ({ toString: () => path }) },
    editor: {
      getEditors: () => [{ ...editor, getModel: () => ({ uri: { toString: () => "scripts/check.py" } }) }],
      onDidCreateEditor: () => ({ dispose: vi.fn() }),
    },
  }),
  Editor: ({ value, onChange, language, options }) => <textarea aria-label="Code editor" data-language={language} value={value} readOnly={options.readOnly} onChange={(event) => onChange(event.target.value)} />,
}));
vi.mock("src/utils/utils", () => ({ copyToClipboard: editor.copy }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const file = { path: "scripts/check.py", content: "print('synthetic')" };
beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("skill file editor", () => {
  it("uses the shared FormCodeEditor with extension-specific language and read-only support", () => {
    const onChange = vi.fn();
    const { rerender } = render(<SkillFileEditor file={file} onChange={onChange} />);
    const textarea = screen.getByLabelText("Code editor");
    expect(textarea).toHaveAttribute("data-language", "python");
    fireEvent.change(textarea, { target: { value: "Updated content" } });
    expect(onChange).toHaveBeenCalledWith("Updated content");
    rerender(<SkillFileEditor file={file} onChange={onChange} readOnly />);
    expect(textarea).toHaveAttribute("readonly");
  });

  it("focuses the requested file and check line", () => {
    render(<SkillFileEditor file={file} onChange={vi.fn()} target={{ line: 3 }} />);
    expect(editor.setPosition).toHaveBeenCalledWith({ lineNumber: 3, column: 1 });
    expect(editor.revealLineInCenter).toHaveBeenCalledWith(3);
    expect(editor.focus).toHaveBeenCalled();
  });

  it("keeps the form-backed copy action in sync after reloading another draft", () => {
    const { rerender } = render(<SkillFileEditor file={file} onChange={vi.fn()} />);
    rerender(<SkillFileEditor file={{ ...file, content: "Reloaded server content" }} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(editor.copy).toHaveBeenCalledWith("Reloaded server content");
  });
});
