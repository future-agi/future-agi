import { useContext, useEffect, useRef } from "react";
import { UNSAFE_NavigationContext } from "react-router-dom";

/** BrowserRouter has no data-router blocker. Install only for a dirty metric
 * editor, and restore its navigator/listeners on save, discard or unmount.
 * Capture POP before BrowserRouter sees it, return to the current history entry,
 * then replay the original delta only after an explicit discard decision. */
export default function useMetricNavigationGuard(dirty, onBlock) {
  const { navigator } = useContext(UNSAFE_NavigationContext);
  const block = useRef(onBlock);
  block.current = onBlock;
  const bypass = useRef(false);
  const allowNavigation = (go) => {
    bypass.current = true;
    try {
      go();
    } finally {
      bypass.current = false;
    }
  };
  useEffect(() => {
    if (!dirty) return undefined;
    const original = {};
    for (const method of ["push", "replace", "go"]) {
      original[method] = navigator[method];
      navigator[method] = (...args) => {
        if (bypass.current) return original[method].apply(navigator, args);
        block.current(() => allowNavigation(() => original[method].apply(navigator, args)));
        return undefined;
      };
    }
    const currentIndex = window.history.state?.idx;
    let returning = false;
    let replay = false;
    let pendingDelta = 0;
    const pop = (event) => {
      if (replay) {
        replay = false;
        return;
      }
      if (returning) {
        event.stopImmediatePropagation();
        returning = false;
        block.current(() => {
          replay = true;
          window.history.go(pendingDelta);
        });
        return;
      }
      const nextIndex = window.history.state?.idx;
      if (!Number.isInteger(currentIndex) || !Number.isInteger(nextIndex)) return;
      const delta = nextIndex - currentIndex;
      if (!delta) return;
      event.stopImmediatePropagation();
      pendingDelta = delta;
      returning = true;
      window.history.go(-delta);
    };
    window.addEventListener("popstate", pop, true);
    return () => {
      for (const method of Object.keys(original)) navigator[method] = original[method];
      window.removeEventListener("popstate", pop, true);
    };
  }, [dirty, navigator]);
  return allowNavigation;
}
