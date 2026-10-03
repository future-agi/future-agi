// TH-150 / AC-14.1 — characterization of text-only PromptEditor behaviour.
//
// Written against unmodified dev (ce6af27f) BEFORE any product change. Every
// expectation below is the value the editor produced at that commit; the fix
// must keep this file green. Pinned choices (engineering design §3):
//   P1 empty document after full delete  -> ops [{insert:"\n"}], getBlocks []
//   P3 history: one user edit after placeEditBolt still needs ONE undo
//   P4 external HTML paste: placeEditBolt strips `bold` on the next user change,
//      so no inline formatting survives today; whitespace and text do.
import { describe, it, expect } from "vitest";
import { getBlocks } from "./common";
import {
  mountEditor,
  clipboardStub,
  fireClipboard,
  keydown,
  flush,
} from "./test-fixtures/editorHarness";
import { textBlock } from "./test-fixtures/media";

describe("PromptEditor baseline (text-only, AC-14.1)", () => {
  it("loads text blocks as one line per block and keeps the terminal newline", () => {
    const { quill } = mountEditor([textBlock("hello world")]);
    expect(quill.getContents().ops).toEqual([{ insert: "hello world\n" }]);
    expect(quill.getLength()).toBe(12);
    expect(getBlocks(quill)).toEqual([{ type: "text", text: "hello world\n" }]);
  });

  it("P1: full-range user deletion leaves exactly one empty line and no blocks", () => {
    const { quill } = mountEditor([textBlock("hello")]);
    quill.setSelection(0, quill.getLength() - 1, "silent");
    keydown(quill.root, "Backspace");
    expect(quill.getContents().ops).toEqual([{ insert: "\n" }]);
    expect(getBlocks(quill)).toEqual([{ type: "text", text: "\n" }]);
    expect(quill.getSelection()).toEqual({ index: 0, length: 0 });
  });

  it("copy of plain text puts the exact selected text on text/plain", () => {
    const { quill } = mountEditor([textBlock("  two  spaces\nnext")]);
    quill.setSelection(0, quill.getLength() - 1, "silent");
    const cb = clipboardStub();
    fireClipboard(quill.root, "copy", cb);
    expect(cb.data["text/plain"]).toBe("  two  spaces\nnext");
  });

  it("Cmd/Ctrl+A has no Quill binding: the logical selection does not change", () => {
    const { quill } = mountEditor([textBlock("hello")]);
    quill.setSelection(2, 0, "silent");
    const ev = keydown(quill.root, "a", { metaKey: true });
    expect(ev.defaultPrevented).toBe(false);
    expect(quill.getSelection()).toEqual({ index: 2, length: 0 });
  });

  it("variables render as text + EditVariable embed; getBlocks restores the brace", () => {
    const { quill } = mountEditor([textBlock("Hi {{name}} there")]);
    const ops = quill.getContents().ops;
    expect(
      ops.map((o) => (typeof o.insert === "string" ? o.insert : "EMBED")),
    ).toEqual(["Hi ", "{{name}", "EMBED", " there\n"]);
    expect(getBlocks(quill)).toEqual([
      { type: "text", text: "Hi {{name}} there\n" },
    ]);
  });

  it("P3: one user edit after variable highlighting is reverted by one undo", () => {
    const { quill } = mountEditor([textBlock("Hi {{name}} there")]);
    quill.history.clear();
    quill.history.cutoff();
    // document: "Hi " (0-2) "{{name}" (3-9) [EditVariable] (10) " there\n" (11-17)
    quill.deleteText(11, 6, "user");
    expect(getBlocks(quill)).toEqual([{ type: "text", text: "Hi {{name}}\n" }]);
    quill.history.undo();
    expect(getBlocks(quill)).toEqual([
      { type: "text", text: "Hi {{name}} there\n" },
    ]);
  });

  it("P4: external HTML paste keeps text and whitespace; bold is stripped by placeEditBolt", async () => {
    const { quill } = mountEditor([textBlock("")]);
    quill.setSelection(0, 0, "silent");
    const cb = clipboardStub({
      "text/html": "<b>x</b>&nbsp; y {{v}}",
      "text/plain": "x  y {{v}}",
    });
    fireClipboard(quill.root, "paste", cb);
    await flush();
    // getText() drops embeds in Quill 2 (the EditVariable "}" is an embed), so
    // compare through getBlocks, which restores the brace. The &nbsp; from the
    // HTML survives as U+00A0 (Quill's HTML conversion), the plain space as-is.
    expect(getBlocks(quill)).toEqual([
      { type: "text", text: "x\u00a0 y {{v}}\n" },
    ]);
    const bold = quill.getContents().ops.filter((o) => o.attributes?.bold);
    // only the {{v}} variable highlight is bold; the pasted <b>x</b> is not
    expect(bold.map((o) => o.insert)).toEqual(["{{v}"]);
  });

  it("external plain-text paste preserves leading and double spaces", async () => {
    const { quill } = mountEditor([textBlock("")]);
    quill.setSelection(0, 0, "silent");
    const cb = clipboardStub({ "text/plain": "  a  b" });
    fireClipboard(quill.root, "paste", cb);
    await flush();
    expect(quill.getText()).toBe("  a  b\n");
  });
});
