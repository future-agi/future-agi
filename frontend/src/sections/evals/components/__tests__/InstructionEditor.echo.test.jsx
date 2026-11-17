import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { act } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import Quill from "src/utils/quillHtmlExport";
import { getBlocks } from "src/components/PromptCards/common";
import { installRangePolyfill } from "src/components/PromptCards/test-fixtures/editorHarness";
import InstructionEditor from "../InstructionEditor";

vi.mock("../ModelSelector", () => ({ default: () => null }));

// The agent instruction editor with the real Quill PromptEditor, kept by its
// parent the way the eval form keeps it: the parent stores every text the
// editor reports.
const mount = (initial) => {
  const reported = vi.fn();
  const setValue = { current: null };
  function Parent() {
    const [value, set] = React.useState(initial);
    setValue.current = set;
    return (
      <InstructionEditor
        value={value}
        onChange={(text) => {
          reported(text);
          set(text);
        }}
        model="synthetic-model"
        onModelChange={vi.fn()}
      />
    );
  }
  render(<Parent />);
  const quill = Quill.find(document.querySelector(".ql-container"));
  return {
    quill,
    reported,
    setFromOutside: (text) => act(() => setValue.current(text)),
  };
};

// The text Quill holds, as the editor reads it (with the terminal "\n").
const textOf = (quill) =>
  getBlocks(quill)
    .map((block) => block.text || "")
    .join("");

// Types at the end of the text, before Quill's terminal line break.
const typeAtEnd = (quill, text) =>
  act(() => {
    quill.insertText(quill.getLength() - 1, text, "user");
  });

describe("InstructionEditor with the real Quill editor", () => {
  beforeEach(() => {
    installRangePolyfill();
  });

  it("does not report a prompt set from outside back as a change", () => {
    const { quill, reported, setFromOutside } = mount("Judge the reply");
    setFromOutside("Judge the other reply");
    expect(textOf(quill)).toBe("Judge the other reply\n");
    expect(reported).not.toHaveBeenCalled();
    typeAtEnd(quill, "!");
    expect(reported).toHaveBeenCalledTimes(1);
    expect(reported).toHaveBeenLastCalledWith(textOf(quill));
  });

  it("reports a line break typed while a newline-only outside change commits", () => {
    const reported = vi.fn();
    const setValue = { current: null };
    const typeOnCommit = { current: false };
    function Parent() {
      const [value, set] = React.useState("Judge the reply");
      setValue.current = set;
      React.useLayoutEffect(() => {
        if (!typeOnCommit.current) return;
        typeOnCommit.current = false;
        const quill = Quill.find(document.querySelector(".ql-container"));
        quill.insertText(quill.getLength() - 1, "\n", "user");
      }, [value]);
      return (
        <InstructionEditor
          value={value}
          onChange={(text) => {
            reported(text);
            set(text);
          }}
          model="synthetic-model"
          onModelChange={vi.fn()}
        />
      );
    }
    render(<Parent />);
    typeOnCommit.current = true;
    act(() => setValue.current("Judge the reply\n"));
    const quill = Quill.find(document.querySelector(".ql-container"));
    expect(textOf(quill)).toBe("Judge the reply\n\n");
    expect(reported).toHaveBeenCalledTimes(1);
    expect(reported).toHaveBeenLastCalledWith(textOf(quill));
  });

  it("reports a line break typed at the end after other typing", () => {
    const { quill, reported } = mount("Judge the reply");
    typeAtEnd(quill, "!");
    expect(reported).toHaveBeenCalledTimes(1);
    typeAtEnd(quill, "\n");
    expect(textOf(quill).endsWith("!\n\n")).toBe(true);
    expect(reported).toHaveBeenCalledTimes(2);
    expect(reported).toHaveBeenLastCalledWith(textOf(quill));
  });

  it("reports a line break typed at the end of saved text that ends with one", () => {
    const { quill, reported } = mount("Judge the reply\n");
    typeAtEnd(quill, "\n");
    expect(textOf(quill).endsWith("reply\n\n")).toBe(true);
    expect(reported).toHaveBeenCalledTimes(1);
    expect(reported).toHaveBeenLastCalledWith(textOf(quill));
  });
});
