import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { act } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import Quill from "src/utils/quillHtmlExport";
import { getBlocks } from "src/components/PromptCards/common";
import { installRangePolyfill } from "src/components/PromptCards/test-fixtures/editorHarness";
import MessageEditorBlock from "../MessageEditorBlock";

// The block with the real Quill PromptEditor, kept by its parent the way
// MessageEditor keeps it: the parent stores every text the block reports.
const mount = (initial) => {
  const reported = vi.fn();
  const setContent = { current: null };
  function Parent() {
    const [content, set] = React.useState(initial);
    setContent.current = set;
    return (
      <MessageEditorBlock
        content={content}
        onContentChange={(text) => {
          reported(text);
          set(text);
        }}
      />
    );
  }
  render(<Parent />);
  const quill = Quill.find(document.querySelector(".ql-container"));
  return {
    quill,
    reported,
    setFromOutside: (text) => act(() => setContent.current(text)),
  };
};

// The text Quill holds, as the block reads it (with the terminal "\n").
const textOf = (quill) =>
  getBlocks(quill)
    .map((block) => block.text || "")
    .join("");

// Types at the end of the text, before Quill's terminal line break.
const typeAtEnd = (quill, text) =>
  act(() => {
    quill.insertText(quill.getLength() - 1, text, "user");
  });

describe("MessageEditorBlock with the real Quill editor", () => {
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

  it("reports a line break typed at the end after other typing", () => {
    const { quill, reported } = mount("Judge the reply");
    typeAtEnd(quill, "!");
    expect(reported).toHaveBeenCalledTimes(1);
    typeAtEnd(quill, "\n");
    expect(textOf(quill).endsWith("!\n\n")).toBe(true);
    expect(reported).toHaveBeenCalledTimes(2);
    expect(reported).toHaveBeenLastCalledWith(textOf(quill));
  });

  it("reports a line break typed after an outside change that only adds a line break", () => {
    const { quill, reported, setFromOutside } = mount("Judge the reply");
    setFromOutside("Judge the reply\n");
    expect(reported).not.toHaveBeenCalled();
    typeAtEnd(quill, "\n");
    expect(textOf(quill).endsWith("reply\n\n")).toBe(true);
    expect(reported).toHaveBeenCalledTimes(1);
    expect(reported).toHaveBeenLastCalledWith(textOf(quill));
  });

  it("reports a line break typed while that outside change commits", () => {
    // The parent's layout effect types before any passive effect runs: the
    // window between the render that brings the content and its effects.
    const reported = vi.fn();
    const setContent = { current: null };
    const typeOnCommit = { current: false };
    function Parent() {
      const [content, set] = React.useState("Judge the reply");
      setContent.current = set;
      React.useLayoutEffect(() => {
        if (!typeOnCommit.current) return;
        typeOnCommit.current = false;
        const quill = Quill.find(document.querySelector(".ql-container"));
        quill.insertText(quill.getLength() - 1, "\n", "user");
      }, [content]);
      return (
        <MessageEditorBlock
          content={content}
          onContentChange={(text) => {
            reported(text);
            set(text);
          }}
        />
      );
    }
    render(<Parent />);
    typeOnCommit.current = true;
    act(() => setContent.current("Judge the reply\n"));
    const quill = Quill.find(document.querySelector(".ql-container"));
    expect(textOf(quill).endsWith("reply\n\n")).toBe(true);
    expect(reported).toHaveBeenCalledTimes(1);
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
