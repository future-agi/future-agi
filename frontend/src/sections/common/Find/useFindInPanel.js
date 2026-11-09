// W5-8 Find (W5-SCOPE §7 C4): Cmd/Ctrl+F inside one panel opens its find
// bar; Esc closes it. Matching, highlighting and scrolling are the trace
// drawer's existing engine (useSearchHighlight: TreeWalker + CSS highlights),
// imported, never copied.
import { useCallback, useEffect, useRef, useState } from "react";
import useSearchHighlight from "src/components/traceDetail/useSearchHighlight";
import { ownsFindShortcut } from "./findModel";

/**
 * @param {{containerRef: {current: HTMLElement|null}, enabled?: boolean}} args
 */
export default function useFindInPanel({ containerRef, enabled = true }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const { matchCount, activeIndex, next, prev } = useSearchHighlight(
    containerRef,
    open ? query : "",
  );

  // Clicking text inside the panel focuses nothing; remember where the last
  // click landed so Cmd/Ctrl+F after it still belongs to the panel.
  const pointerInside = useRef(false);
  useEffect(() => {
    if (!enabled) return undefined;
    const onDown = (e) => {
      const c = containerRef.current;
      pointerInside.current = Boolean(c && c.contains(e.target));
    };
    const onKey = (e) => {
      if (
        !ownsFindShortcut(
          e,
          containerRef.current,
          document.activeElement,
          pointerInside.current,
        )
      )
        return;
      e.preventDefault();
      e.stopPropagation();
      // A selection inside the panel seeds the query, like a browser find.
      const sel = window.getSelection?.()?.toString?.().trim();
      if (sel && sel.length <= 200 && !sel.includes("\n")) setQuery(sel);
      setOpen(true);
    };
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [containerRef, enabled]);

  const close = useCallback(() => {
    setOpen(false);
  }, []);

  return {
    open,
    setOpen,
    close,
    query,
    setQuery,
    matchCount,
    activeIndex,
    next,
    prev,
  };
}
