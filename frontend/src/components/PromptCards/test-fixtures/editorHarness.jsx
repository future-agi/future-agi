// Shared jsdom harness for PromptEditor tests (TH-150).
// jsdom has no layout, so Quill's focus()/scrollSelectionIntoView needs Range rects.
import React from "react";
import { render, act } from "@testing-library/react";
import PromptEditor from "../PromptEditor";

export function installRangePolyfill() {
  const rect = () => ({
    top: 0,
    left: 0,
    bottom: 0,
    right: 0,
    width: 0,
    height: 0,
    x: 0,
    y: 0,
    toJSON() {
      return {};
    },
  });
  if (!Range.prototype.getBoundingClientRect) {
    Range.prototype.getBoundingClientRect = rect;
  }
  if (!Range.prototype.getClientRects) {
    Range.prototype.getClientRects = () => ({ length: 0, item: () => null });
  }
}

export function mountEditor(prompt, extraProps = {}) {
  installRangePolyfill();
  const quillRef = React.createRef();
  const onPromptChange = extraProps.onPromptChange || (() => {});
  const utils = render(
    <PromptEditor
      ref={quillRef}
      prompt={prompt}
      onPromptChange={onPromptChange}
      appliedVariableData={{}}
      openVariableEditor={() => {}}
      setSelectedImage={() => {}}
      placeholder="Type here"
      allowedMediaTypes={["image", "audio", "pdf"]}
      {...extraProps}
    />,
  );
  const quill = quillRef.current;
  quill.root.focus();
  // Separate the initial load from the test's user actions: Quill's history
  // merges changes that happen within `delay` (1s) into one undo entry.
  quill.history.cutoff();
  return { quill, quillRef, ...utils };
}

export function clipboardStub(initial = {}) {
  const data = { ...initial };
  return {
    data,
    get types() {
      return Object.keys(data);
    },
    setData(type, value) {
      data[type] = String(value);
    },
    getData(type) {
      return data[type] ?? "";
    },
  };
}

// jsdom has no ClipboardEvent/DataTransfer; a generic Event with a stubbed
// clipboardData is what both Quill's and our listeners read.
export function fireClipboard(el, type, clipboardData) {
  const ev = new Event(type, { bubbles: true, cancelable: true });
  if (clipboardData !== undefined) {
    Object.defineProperty(ev, "clipboardData", { value: clipboardData });
  }
  el.dispatchEvent(ev);
  return ev;
}

export function keydown(el, key, mods = {}) {
  const ev = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...mods,
  });
  el.dispatchEvent(ev);
  return ev;
}

export async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

export function embedNodes(quill) {
  return Array.from(
    quill.root.querySelectorAll(
      "[data-image-data],[data-audio-data],[data-pdf-data]",
    ),
  );
}
