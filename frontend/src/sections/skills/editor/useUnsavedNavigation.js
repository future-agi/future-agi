import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { UNSAFE_NavigationContext, useLocation } from "react-router-dom";

import { useBeforeUnload } from "src/hooks/useBeforeUnload";

/** BrowserRouter has no data-router blocker; intercept its navigator and native history. */
export default function useUnsavedNavigation(unsaved) {
  const { navigator } = useContext(UNSAFE_NavigationContext);
  const location = useLocation();
  const dirty = useRef(unsaved);
  dirty.current = unsaved;
  const bypass = useRef(false);
  const index = useRef(window.history.state?.idx);
  const pending = useRef(null);
  const [blocked, setBlocked] = useState(false);
  useBeforeUnload(unsaved);

  useEffect(() => {
    index.current = window.history.state?.idx;
    bypass.current = false;
  }, [location]);

  useEffect(() => {
    const originals = {};
    const wrappers = {};
    const block = (next) => {
      pending.current = next;
      setBlocked(true);
    };
    ["push", "replace", "go"].forEach((method) => {
      originals[method] = navigator[method];
      wrappers[method] = (...args) => {
        const next = () => originals[method].apply(navigator, args);
        if (dirty.current && !bypass.current) block(next);
        else next();
      };
      navigator[method] = wrappers[method];
    });

    let restoring = false;
    let resumePop = null;
    const onPop = (event) => {
      if (restoring) {
        event.stopImmediatePropagation();
        restoring = false;
        block(resumePop);
        return;
      }
      if (!dirty.current || bypass.current) return;
      const target = event.state?.idx;
      // Only intercept entries owned by BrowserRouter; document exits use beforeunload.
      if (typeof target !== "number" || typeof index.current !== "number") return;
      const delta = target - index.current;
      if (!delta) return;
      event.stopImmediatePropagation();
      restoring = true;
      resumePop = () => window.history.go(delta);
      window.history.go(-delta);
    };
    window.addEventListener("popstate", onPop, true);
    return () => {
      Object.keys(originals).forEach((method) => {
        if (navigator[method] === wrappers[method]) navigator[method] = originals[method];
      });
      window.removeEventListener("popstate", onPop, true);
    };
  }, [navigator]);

  return {
    blocked,
    cancel: () => { pending.current = null; setBlocked(false); },
    proceed: () => {
      bypass.current = true;
      setBlocked(false);
      pending.current?.();
      pending.current = null;
    },
    allow: useCallback((next) => { bypass.current = true; next(); }, []),
  };
}
