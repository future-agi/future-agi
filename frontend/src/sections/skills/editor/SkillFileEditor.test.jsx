import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SkillFileEditor from "./SkillFileEditor";

const editor = vi.hoisted(() => ({ setPosition: vi.fn(), revealLineInCenter: vi.fn(), focus: vi.fn(), copy: vi.fn(), getDomNode: vi.fn(), onDidLayoutChange: vi.fn() }));
vi.mock("@monaco-editor/react", async () => {
  const { default: PropTypes } = await import("prop-types");
  function MockEditor({ value, onChange, language, options, height }) {
    return <textarea aria-label="Code editor" data-language={language} data-height={height} value={value} readOnly={options.readOnly} onChange={(event) => onChange(event.target.value)} />;
  }
  MockEditor.propTypes = {
    value: PropTypes.string,
    onChange: PropTypes.func.isRequired,
    language: PropTypes.string,
    height: PropTypes.string,
    options: PropTypes.shape({ readOnly: PropTypes.bool }).isRequired,
  };
  return {
    useMonaco: () => ({
      Uri: { parse: (path) => ({ toString: () => path }) },
      editor: {
        getEditors: () => [{ ...editor, getModel: () => ({ uri: { toString: () => "scripts/check.py" } }) }],
        onDidCreateEditor: () => ({ dispose: vi.fn() }),
      },
    }),
    Editor: MockEditor,
  };
});
vi.mock("src/utils/utils", () => ({ copyToClipboard: editor.copy }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const file = { path: "scripts/check.py", content: "print('synthetic')" };
beforeEach(() => { vi.clearAllMocks(); editor.getDomNode.mockReturnValue(null); editor.onDidLayoutChange.mockReturnValue({ dispose: vi.fn() }); });
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

  it("fills the flex pane instead of reserving a fixed viewport height", () => {
    render(<SkillFileEditor file={file} onChange={vi.fn()} />);
    const editorElement = screen.getByLabelText("Code editor");
    expect(editorElement).toHaveAttribute("data-height", "100%");
    expect(editorElement.parentElement).toHaveStyle({ flex: "1", minHeight: "0" });
    expect(editorElement.parentElement.parentElement).toHaveStyle({ flex: "1", minHeight: "0" });
  });

  it("focuses the requested file and check line", () => {
    render(<SkillFileEditor file={file} onChange={vi.fn()} target={{ line: 3 }} />);
    expect(editor.setPosition).toHaveBeenCalledWith({ lineNumber: 3, column: 1 });
    expect(editor.revealLineInCenter).toHaveBeenCalledWith(3);
    expect(editor.focus).toHaveBeenCalled();
  });

  it("waits for the retained hidden Source editor to become visible before focusing a check", () => {
    let visible = false;
    let layout;
    editor.getDomNode.mockReturnValue({ getClientRects: () => visible ? [{}] : [] });
    const dispose = vi.fn();
    editor.onDidLayoutChange.mockImplementation((callback) => { layout = callback; return { dispose }; });
    const view = render(<SkillFileEditor file={file} onChange={vi.fn()} target={{ line: 3 }} />);
    expect(editor.focus).not.toHaveBeenCalled();
    visible = true;
    layout();
    expect(editor.setPosition).toHaveBeenCalledWith({ lineNumber: 3, column: 1 });
    expect(editor.focus).toHaveBeenCalledTimes(1);
    layout();
    expect(editor.focus).toHaveBeenCalledTimes(1);
    view.unmount();
    expect(dispose).toHaveBeenCalled();
  });

  it("keeps the form-backed copy action in sync after reloading another draft", () => {
    const { rerender } = render(<SkillFileEditor file={file} onChange={vi.fn()} />);
    rerender(<SkillFileEditor file={{ ...file, content: "Reloaded server content" }} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(editor.copy).toHaveBeenCalledWith("Reloaded server content");
  });
});
