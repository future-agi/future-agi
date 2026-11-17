import { useEffect, useRef, useState } from "react";

import { copyToClipboard } from "src/utils/utils";

// The quickstart Copy button (DS EmptyState and EmptyLayout). It says "Copied"
// only once the code is on the clipboard. When nothing copies (a self-hosted
// instance opened over plain http has no navigator.clipboard, or the browser
// refuses), the code is selected so copying it by hand is one keystroke. The
// wording matches the MCP client setup guide's select-on-fail message.
export const COPY_FAILED =
  "Could not copy. Press Ctrl+C (Cmd+C on Mac) to copy the selected code.";

const RESULT_TEXT = { copied: "Copied", failed: COPY_FAILED };

// For the status node: hidden on screen, still read by screen readers.
export const visuallyHidden = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
};

function selectContents(node) {
  const selection = window.getSelection?.();
  if (!node || !selection) return;
  const range = document.createRange();
  range.selectNodeContents(node);
  selection.removeAllRanges();
  selection.addRange(range);
}

/**
 * `codeRef` goes on the element that shows the code. `status` goes in an
 * always-mounted role="status" node: the button keeps its name, Copy code,
 * and a tooltip is not read out, so that node tells screen-reader users what
 * the copy did.
 */
export function useCopyCode() {
  const codeRef = useRef(null);
  const timer = useRef(null);
  const [result, setResult] = useState(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  // Call straight from the click: the legacy copy fallback only works while
  // the click is being handled.
  const copy = async (text) => {
    const copied = await copyToClipboard(text);
    clearTimeout(timer.current);
    if (copied) {
      setResult("copied");
      timer.current = setTimeout(() => setResult(null), 1500);
    } else {
      selectContents(codeRef.current);
      setResult("failed");
    }
  };

  return {
    codeRef,
    copy,
    copied: result === "copied",
    tooltip: RESULT_TEXT[result] ?? "Copy",
    status: RESULT_TEXT[result] ?? "",
  };
}
