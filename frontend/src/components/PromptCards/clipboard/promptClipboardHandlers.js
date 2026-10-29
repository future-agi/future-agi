// Attached in the capture phase on quill.root so they run before Quill's own
// clipboard listeners (which bail on e.defaultPrevented). Any application can
// read the OS clipboard, so it only gets text/plain plus an opaque handle; the
// attachment references stay in tab memory and are re-validated on paste.
import Quill from "quill";
import {
  buildClipboardItems,
  buildTextProjection,
  itemsContainMedia,
} from "./textProjection";
import * as store from "./internalClipboardStore";
import { INTERNAL_MIME, OMISSION_REASONS, RECORD_VERSION } from "./constants";
import { BLOT_BY_MEDIA_KIND } from "../Blots/mediaValue";

const Delta = Quill.import("delta");

export function createPromptClipboardHandlers({
  quill,
  getProvenance,
  getAllowedMediaTypes,
  notify,
  makeId,
}) {
  // Fail closed: without an explicit list nothing may be inserted by paste.
  const allowed = () => getAllowedMediaTypes?.() || [];
  const report = (reason) => {
    try {
      notify?.(reason);
    } catch (e) {
      // notifications must never break editing
    }
  };

  function capture(range) {
    const delta = quill.getContents(range.index, range.length);
    return {
      text: buildTextProjection(delta),
      items: buildClipboardItems(delta),
    };
  }

  // Writes text/plain (always, even "") and, only when attachments are part of
  // the selection, the opaque handle. Returns the record or null; never throws.
  function writeClipboard(e, range) {
    const data = e.clipboardData;
    if (!data || typeof data.setData !== "function") return null;
    const { text, items } = capture(range);
    try {
      data.setData("text/plain", text);
      if (itemsContainMedia(items)) {
        const handle = makeId();
        const record = {
          version: RECORD_VERSION,
          handle,
          createdAt: Date.now(),
          text,
          provenance: getProvenance(),
          items,
        };
        data.setData(INTERNAL_MIME, handle);
        store.put(record);
        return record;
      }
      store.clear();
      return { version: RECORD_VERSION, handle: null, text, items };
    } catch (err) {
      return null;
    }
  }

  function onCopy(e) {
    const range = quill.getSelection();
    e.preventDefault(); // never let Quill write text/html with card markup
    if (!range || range.length === 0) return; // nothing selected: clipboard untouched
    if (!writeClipboard(e, range)) report(OMISSION_REASONS.UNSUPPORTED);
  }

  function onCut(e) {
    const range = quill.getSelection();
    e.preventDefault();
    if (!range || range.length === 0) return;
    if (!quill.isEnabled()) {
      // read-only: behave like copy, never mutate
      if (!writeClipboard(e, range)) report(OMISSION_REASONS.UNSUPPORTED);
      return;
    }
    const lengthBefore = quill.getLength();
    const record = writeClipboard(e, range);
    if (!record) {
      report(OMISSION_REASONS.UNSUPPORTED);
      return; // source unchanged
    }
    // Defensive: capture is synchronous, but refuse to delete anything other
    // than exactly what was captured.
    const live = quill.getSelection();
    if (
      !live ||
      live.index !== range.index ||
      live.length !== range.length ||
      quill.getLength() !== lengthBefore
    ) {
      return;
    }
    quill.deleteText(range.index, range.length, "user");
    quill.setSelection(range.index, 0, "silent");
  }

  function readData(e, type) {
    try {
      return e.clipboardData?.getData?.(type) ?? "";
    } catch (err) {
      return "";
    }
  }

  function onPaste(e) {
    if (!quill.isEnabled()) {
      e.preventDefault();
      return;
    }
    e.preventDefault();
    const range = quill.getSelection(true) || { index: 0, length: 0 };
    const handle = readData(e, INTERNAL_MIME);
    const text = readData(e, "text/plain");
    const html = readData(e, "text/html");

    // Nothing this editor can insert (e.g. files only): leave the document
    // and the current selection exactly as they are.
    if (!handle && !text && !html) return;

    if (!handle) {
      pasteExternal(range, text, html);
      return;
    }

    const record = store.get(handle);
    const verdict = record
      ? store.validateRecord(record, {
          clipboardText: text,
          live: getProvenance(),
        })
      : { ok: false, reason: OMISSION_REASONS.OTHER_CONTEXT };
    if (!verdict.ok) {
      // A handle is only ever written when attachments were copied, so the
      // text-only fallback is a real omission worth reporting.
      pasteExternal(range, text, "");
      report(verdict.reason);
      return;
    }

    const kinds = allowed();
    const kept = [];
    let omitted = 0;
    record.items.forEach((item) => {
      if (
        item.kind === "text" ||
        item.kind === "variableClose" ||
        kinds.includes(item.kind)
      ) {
        kept.push(item);
      } else {
        omitted += 1;
      }
    });

    let delta = new Delta().retain(range.index).delete(range.length);
    kept.forEach((item) => {
      if (item.kind === "text") delta = delta.insert(item.text);
      else if (item.kind === "variableClose") delta = delta.insert("}");
      else {
        const value = {
          url: item.url,
          name: item.name,
          size: item.size,
          id: makeId(),
        };
        if (item.kind === "audio") value.mimeType = item.mimeType;
        delta = delta.insert({ [BLOT_BY_MEDIA_KIND[item.kind]]: value });
      }
    });
    applyPaste(range, delta);
    if (omitted > 0) report(OMISSION_REASONS.NOT_ALLOWED);
  }

  // External clipboard content: Quill's HTML conversion for text and inline
  // formats, but no embed of any kind may be created from outside the editor.
  function pasteExternal(range, text, html) {
    let converted;
    try {
      converted = quill.clipboard.convert({ html: html || undefined, text });
    } catch (err) {
      converted = new Delta().insert(text || "");
    }
    const textOnly = new Delta();
    (converted.ops || []).forEach((op) => {
      if (typeof op.insert === "string")
        textOnly.insert(op.insert, op.attributes);
    });
    const delta = new Delta()
      .retain(range.index)
      .delete(range.length)
      .concat(textOnly);
    applyPaste(range, delta);
  }

  function applyPaste(range, delta) {
    const lengthBefore = quill.getLength();
    quill.updateContents(delta, "user");
    // Block embeds split the line they land in, so derive the inserted length
    // from the document instead of from the op list.
    const inserted = quill.getLength() - (lengthBefore - range.length);
    quill.setSelection(range.index + Math.max(0, inserted), 0, "silent");
    if (typeof quill.scrollSelectionIntoView === "function") {
      try {
        quill.scrollSelectionIntoView();
      } catch (err) {
        // layout-less environments (tests) have no bounds
      }
    }
  }

  const listeners = { copy: onCopy, cut: onCut, paste: onPaste };

  function attach() {
    Object.keys(listeners).forEach((type) =>
      quill.root.addEventListener(type, listeners[type], { capture: true }),
    );
  }

  function detach() {
    Object.keys(listeners).forEach((type) =>
      quill.root.removeEventListener(type, listeners[type], { capture: true }),
    );
  }

  return { onCopy, onCut, onPaste, attach, detach };
}
