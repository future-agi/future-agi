import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { UNSAFE_NavigationContext } from "react-router-dom";

import { useBeforeUnload } from "src/hooks/useBeforeUnload";

/** BrowserRouter has no data-router blocker; guard its navigator and native Back. */
export default function useEditorNavigationGuard(dirty) {
  const { navigator } = useContext(UNSAFE_NavigationContext);
  const [blocked, setBlocked] = useState(false);
  const pending = useRef(null);
  const bypass = useRef(false);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  useBeforeUnload(dirty);

  useEffect(() => {
    const originals = { push: navigator.push, replace: navigator.replace, go: navigator.go };
    const index = window.history.state?.idx;
    let restoring = false;
    const wrappers = {};
    Object.keys(originals).forEach((method) => {
      wrappers[method] = (...args) => {
        if (dirtyRef.current && !bypass.current) {
          pending.current = () => originals[method].apply(navigator, args);
          setBlocked(true);
          return;
        }
        originals[method].apply(navigator, args);
      };
      navigator[method] = wrappers[method];
    });
    const onPop = (event) => {
      if (restoring) {
        event.stopImmediatePropagation();
        restoring = false;
        return;
      }
      if (!dirtyRef.current || bypass.current || !Number.isInteger(index)) return;
      const nextIndex = event.state?.idx;
      if (!Number.isInteger(nextIndex) || nextIndex === index) return;
      event.stopImmediatePropagation();
      restoring = true;
      window.history.go(index - nextIndex);
      pending.current = () => window.history.go(nextIndex - index);
      setBlocked(true);
    };
    window.addEventListener("popstate", onPop, true);
    return () => {
      Object.keys(originals).forEach((method) => {
        if (navigator[method] === wrappers[method]) navigator[method] = originals[method];
      });
      window.removeEventListener("popstate", onPop, true);
    };
  }, [navigator]);

  const allow = useCallback((action) => {
    bypass.current = true;
    action();
  }, []);
  return {
    blocked,
    allow,
    stay: () => { pending.current = null; setBlocked(false); },
    leave: () => {
      const action = pending.current;
      pending.current = null;
      setBlocked(false);
      if (action) allow(action);
    },
  };
}
