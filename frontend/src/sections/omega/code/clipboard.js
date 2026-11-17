// Copy for the code panels, on every origin a stack is served from. The async
// Clipboard API exists only in a secure context (https, localhost); a
// self-hosted stack on plain http at a LAN address has none, so Copy falls
// back to a hidden textarea and execCommand("copy"), as utils.js
// copyToClipboard does. Two differences make this its own helper: the
// textarea goes inside the panel, because a drawer's focus trap pulls focus
// back from one appended to <body> and the copy then takes nothing; and it
// carries the no-recording class, because it may hold the revealed key and
// PostHog's replay records input values. Resolves true only when the text
// reached the clipboard as far as the browser reports.

import { NO_RECORDING_CLASS } from "./noRecording";

function fallbackCopy(text, container) {
  const doc = container?.ownerDocument ?? globalThis.document;
  if (!doc || typeof doc.execCommand !== "function") return false;
  const host = container ?? doc.body;
  const previous = doc.activeElement;
  const area = doc.createElement("textarea");
  area.value = text;
  area.readOnly = true;
  area.className = NO_RECORDING_CLASS;
  area.setAttribute("aria-hidden", "true");
  area.tabIndex = -1;
  Object.assign(area.style, {
    position: "fixed",
    top: "0",
    left: "0",
    width: "1px",
    height: "1px",
    opacity: "0",
    pointerEvents: "none",
  });
  host.appendChild(area);
  let copied = false;
  try {
    area.focus({ preventScroll: true });
    area.select();
    area.setSelectionRange(0, text.length);
    copied = doc.execCommand("copy") === true;
  } catch {
    copied = false;
  } finally {
    area.remove();
    if (previous && typeof previous.focus === "function")
      previous.focus({ preventScroll: true });
  }
  return copied;
}

/** Copy `text`; `container` is the element the fallback textarea goes in. */
export async function copyText(text, container) {
  const clipboard = globalThis.navigator?.clipboard;
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(text);
      return true;
    } catch {
      // Refused (no permission, document not focused): try the fallback.
    }
  }
  return fallbackCopy(text, container);
}
