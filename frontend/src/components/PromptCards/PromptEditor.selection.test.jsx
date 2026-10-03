// TH-150 regression tests for PromptEditor with image/audio/PDF attachments.
// PRD r1.2 (company-brain bd2bf50a67307e4f348571e1e64709687f793435), AC ids in test names.
// Written before the fix; the "must fail before fix" subset is marked [RED@dev].
import { describe, it, expect, vi } from "vitest";
import React from "react";
import { getBlocks } from "./common";
import {
  mountEditor,
  clipboardStub,
  fireClipboard,
  keydown,
  flush,
  embedNodes,
} from "./test-fixtures/editorHarness";
import {
  DOC_TEXT_PDF_TEXT,
  DOC_HELLO_IMG_WORLD,
  DOC_IMG_AUDIO,
  DOC_PDF_ONLY,
  PDF_1P,
  PNG_1x1,
  WAV_1S,
  textBlock,
  pdfBlock,
} from "./test-fixtures/media";

const everyClipboardValue = (cb) => Object.values(cb.data).join("\n");

describe("TH-150 select-all scoped to the focused editor (REQ-1)", () => {
  it("AC-1.1 [RED@dev]: Cmd/Ctrl+A selects the whole focused message incl. the attachment, not sibling editors", () => {
    const a = mountEditor(DOC_TEXT_PDF_TEXT);
    const b = mountEditor([textBlock("other")]);
    a.quill.root.focus();
    a.quill.setSelection(1, 0, "silent");
    const ev = keydown(a.quill.root, "a", { metaKey: true });
    expect(ev.defaultPrevented).toBe(true);
    expect(a.quill.getSelection()).toEqual({ index: 0, length: 12 }); // length 13 minus terminal \n
    expect(b.quill.getSelection()).toBeNull();
    // Ctrl variant (non-macOS)
    a.quill.setSelection(3, 0, "silent");
    keydown(a.quill.root, "a", { ctrlKey: true });
    expect(a.quill.getSelection()).toEqual({ index: 0, length: 12 });
  });

  it("AC-1.2 [RED@dev]: attachment-only message selects exactly the embed block", () => {
    const { quill } = mountEditor(DOC_PDF_ONLY);
    expect(quill.getLength()).toBe(2);
    quill.setSelection(1, 0, "silent");
    keydown(quill.root, "a", { metaKey: true });
    expect(quill.getSelection()).toEqual({ index: 0, length: 1 });
  });

  it("AC-2.2: the attachment card is contenteditable=false and marked as a media embed", () => {
    const { quill } = mountEditor(DOC_TEXT_PDF_TEXT);
    const [node] = embedNodes(quill);
    expect(node.getAttribute("contenteditable")).toBe("false");
    expect(node.classList.contains("prompt-media-embed")).toBe(true); // [RED@dev]
  });
});

describe("TH-150 copy projection (REQ-2, REQ-3, REQ-10, REQ-15)", () => {
  it("AC-2.1 [RED@dev]: copying text+PDF+text writes text/plain only, with no card labels, URL, size or HTML", () => {
    const { quill } = mountEditor(DOC_TEXT_PDF_TEXT);
    quill.setSelection(0, quill.getLength() - 1, "silent");
    const cb = clipboardStub();
    const ev = fireClipboard(quill.root, "copy", cb);
    expect(ev.defaultPrevented).toBe(true);
    expect(cb.data["text/plain"]).toBe("hello\nworld");
    expect(cb.data["text/html"]).toBeUndefined();
    const all = everyClipboardValue(cb);
    expect(all).not.toContain(PDF_1P.url);
    expect(all).not.toContain(PDF_1P.name);
    expect(all).not.toContain("KB");
    expect(all).not.toContain("delete");
    // the source document is untouched by copy
    expect(getBlocks(quill)).toEqual([
      { type: "text", text: "hello\n" },
      pdfBlock,
      { type: "text", text: "world\n" },
    ]);
  });

  it("AC-3.1 + AC-10.1 [RED@dev]: whitespace and full {{a}}/{{b}} tokens survive copy next to an audio embed with one closing brace each", () => {
    const { quill } = mountEditor([
      textBlock("  lead  {{a}}\n{{b}} tail"),
      { ...DOC_IMG_AUDIO[2] },
    ]);
    quill.setSelection(0, quill.getLength() - 1, "silent");
    const cb = clipboardStub();
    fireClipboard(quill.root, "copy", cb);
    expect(cb.data["text/plain"]).toBe("  lead  {{a}}\n{{b}} tail");
    expect(everyClipboardValue(cb)).not.toContain(WAV_1S.name);
  });

  it("AC-10.3: a partial selection inside a variable copies only the selected logical characters", () => {
    const { quill } = mountEditor([textBlock("Hi {{name}} there")]);
    // "Hi " 0-2, "{{name}" 3-9, embed 10, " there" 11-16 -> select "name}" + embed = indices 5..10
    quill.setSelection(5, 6, "silent");
    const cb = clipboardStub();
    fireClipboard(quill.root, "copy", cb);
    expect(cb.data["text/plain"]).toBe("name}}");
    quill.setSelection(5, 4, "silent"); // "name" only, no brace repair
    const cb2 = clipboardStub();
    fireClipboard(quill.root, "copy", cb2);
    expect(cb2.data["text/plain"]).toBe("name");
  });

  it("empty selection [RED@dev]: copy touches nothing on the clipboard (Quill's default wrote empty text/plain + text/html)", () => {
    const { quill } = mountEditor(DOC_TEXT_PDF_TEXT);
    quill.setSelection(2, 0, "silent");
    const cb = clipboardStub();
    fireClipboard(quill.root, "copy", cb);
    expect(Object.keys(cb.data)).toEqual([]);
  });
});

describe("TH-150 deletion semantics (REQ-7, REQ-13)", () => {
  it("AC-7.1: select-all + Backspace removes text and attachment in one keypress; empty document is the pinned P1 shape", () => {
    const onPromptChange = vi.fn();
    const { quill } = mountEditor(DOC_TEXT_PDF_TEXT, { onPromptChange });
    keydown(quill.root, "a", { metaKey: true });
    keydown(quill.root, "Backspace");
    expect(quill.getContents().ops).toEqual([{ insert: "\n" }]);
    expect(embedNodes(quill)).toHaveLength(0);
    expect(quill.root.classList.contains("ql-blank")).toBe(true);
    expect(onPromptChange).toHaveBeenLastCalledWith([{ type: "text", text: "\n" }]);
    expect(quill.getSelection()).toEqual({ index: 0, length: 0 });
  });

  it("AC-7.2: forward Delete over the full selection gives the same result", () => {
    const { quill } = mountEditor(DOC_TEXT_PDF_TEXT);
    keydown(quill.root, "a", { metaKey: true });
    keydown(quill.root, "Delete");
    expect(quill.getContents().ops).toEqual([{ insert: "\n" }]);
  });

  it("AC-7.3: caret immediately after the PDF, Backspace removes the PDF only", () => {
    const { quill } = mountEditor(DOC_TEXT_PDF_TEXT);
    quill.setSelection(7, 0, "silent"); // start of "world"
    keydown(quill.root, "Backspace");
    expect(quill.getContents().ops).toEqual([{ insert: "hello\nworld\n" }]);
    expect(quill.getSelection()).toEqual({ index: 6, length: 0 });
  });

  it("AC-7.4 [RED@dev]: caret exactly at the PDF index, forward Delete removes the PDF only", () => {
    const { quill } = mountEditor(DOC_TEXT_PDF_TEXT);
    quill.setSelection(6, 0, "silent"); // the embed's own index
    keydown(quill.root, "Delete");
    expect(quill.getContents().ops).toEqual([{ insert: "hello\nworld\n" }]);
    expect(quill.getSelection()).toEqual({ index: 6, length: 0 });
  });

  it("AC-7.5: partial range crossing an image removes exactly llo + image + wo", () => {
    const { quill } = mountEditor(DOC_HELLO_IMG_WORLD);
    // hello\n 0-5, img 6, world\n 7-12 ; select from "l"(2) through "o"(8) inclusive => (2, 7)
    quill.setSelection(2, 7, "silent");
    keydown(quill.root, "Backspace");
    expect(quill.getContents().ops).toEqual([{ insert: "herld\n" }]);
    expect(quill.getSelection()).toEqual({ index: 2, length: 0 });
  });

  it("AC-7.6: adjacent image/audio: two Backspaces from just after the audio remove audio then image", () => {
    const { quill } = mountEditor(DOC_IMG_AUDIO);
    // a\n 0-1, img 2, audio 3, b\n 4-5
    quill.setSelection(4, 0, "silent");
    keydown(quill.root, "Backspace");
    expect(embedNodes(quill)).toHaveLength(1);
    expect(quill.root.querySelector("[data-image-data]")).not.toBeNull();
    keydown(quill.root, "Backspace");
    expect(embedNodes(quill)).toHaveLength(0);
    expect(quill.getContents().ops).toEqual([{ insert: "a\nb\n" }]);
  });

  it("AC-5.1 / AC-8.3 [RED@dev]: the card delete control removes only that reference, makes no network call, and undo restores a working card", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(""));
    const a = mountEditor(DOC_HELLO_IMG_WORLD);
    const b = mountEditor(DOC_HELLO_IMG_WORLD);
    const deleteBtn = a.quill.root.querySelector('[data-image-data] img[alt="delete"]')
      ?.closest("button");
    expect(deleteBtn).toBeTruthy();
    a.quill.history.cutoff();
    deleteBtn.click();
    await flush();
    expect(embedNodes(a.quill)).toHaveLength(0);
    expect(embedNodes(b.quill)).toHaveLength(1);
    expect(fetchSpy).not.toHaveBeenCalled();
    a.quill.history.undo();
    await flush();
    const [restored] = embedNodes(a.quill);
    expect(restored).toBeTruthy();
    expect(JSON.parse(restored.getAttribute("data-image-data")).url).toBe(PNG_1x1.url);
    expect(getBlocks(a.quill)).toEqual([
      { type: "text", text: "hello\n" },
      DOC_HELLO_IMG_WORLD[1],
      { type: "text", text: "world\n" },
    ]);
    fetchSpy.mockRestore();
  });
});

describe("TH-150 undo/redo restores attachments (REQ-8)", () => {
  it("AC-8.1 [RED@dev]: undo after select-all + Backspace restores text, PDF metadata and a usable delete control", async () => {
    const { quill } = mountEditor(DOC_TEXT_PDF_TEXT);
    keydown(quill.root, "a", { metaKey: true });
    keydown(quill.root, "Backspace");
    expect(embedNodes(quill)).toHaveLength(0);
    quill.history.undo();
    await flush();
    expect(getBlocks(quill)).toEqual([
      { type: "text", text: "hello\n" },
      pdfBlock,
      { type: "text", text: "world\n" },
    ]);
    const [node] = embedNodes(quill);
    expect(node.textContent).toContain(PDF_1P.name);
    const btn = node.querySelector("button");
    expect(btn).toBeTruthy();
    btn.click();
    await flush();
    expect(embedNodes(quill)).toHaveLength(0); // the restored control works
  });
});

describe("TH-150 read-only editors (REQ-11)", () => {
  it("AC-11.1 / AC-11.2 [RED@dev]: disabled editor allows select/copy but hides delete controls and ignores cut/paste/Backspace", async () => {
    const onPromptChange = vi.fn();
    const { quill } = mountEditor(DOC_HELLO_IMG_WORLD, { disabled: true, onPromptChange });
    expect(quill.isEnabled()).toBe(false);
    keydown(quill.root, "a", { metaKey: true });
    expect(quill.getSelection()).toEqual({ index: 0, length: 12 });
    const cb = clipboardStub();
    fireClipboard(quill.root, "copy", cb);
    expect(cb.data["text/plain"]).toBe("hello\nworld");
    const before = quill.getContents().ops;
    fireClipboard(quill.root, "cut", clipboardStub());
    fireClipboard(quill.root, "paste", clipboardStub({ "text/plain": "x" }));
    await flush();
    keydown(quill.root, "Backspace");
    expect(quill.getContents().ops).toEqual(before);
    expect(quill.root.querySelector('[data-image-data] img[alt="delete"]')).toBeNull();
    expect(onPromptChange).not.toHaveBeenCalled();
  });
});

describe("TH-150 external paste never creates media (REQ-6, REQ-15)", () => {
  it("AC-15.2 [RED@dev]: hostile HTML carrying data-pdf-data cannot create an attachment; plain text still pastes", async () => {
    const { quill } = mountEditor([textBlock("x")]);
    quill.setSelection(1, 0, "silent");
    const cb = clipboardStub({
      "text/html": `<div data-pdf-data='{"url":"https://evil.invalid/x.pdf","pdf_name":"evil","pdf_size":1}'></div><p>plain</p>`,
      "text/plain": "plain",
    });
    fireClipboard(quill.root, "paste", cb);
    await flush();
    expect(embedNodes(quill)).toHaveLength(0);
    expect(quill.getContents().ops.every((o) => typeof o.insert === "string")).toBe(true);
    expect(getBlocks(quill)[0].text).toContain("plain");
  });

  it("AC-6.1: external text with spaces and a variable pastes with spacing intact and variable styling", async () => {
    const { quill } = mountEditor([textBlock("")]);
    quill.setSelection(0, 0, "silent");
    fireClipboard(quill.root, "paste", clipboardStub({ "text/plain": "x  y {{v}}" }));
    await flush();
    expect(getBlocks(quill)).toEqual([{ type: "text", text: "x  y {{v}}\n" }]);
    expect(quill.getContents().ops.some((o) => o.attributes?.bold && o.insert === "{{v}")).toBe(true);
    expect(embedNodes(quill)).toHaveLength(0);
  });
});
