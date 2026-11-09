import { useCallback, useEffect, useState } from "react";

// Working minimal version (L0; owned by L1). Formatted | Raw, remembered
// per browser and shared by every viewer on the page (Row view, result
// drawer, review): `R` in one place flips them all.
export const VALUE_VIEW_MODE_KEY = "fa.rowView.mode";
const EVENT = "fa:value-view-mode";
const MODES = ["formatted", "raw"];

const read = () => {
  try {
    const v = window.localStorage.getItem(VALUE_VIEW_MODE_KEY);
    return MODES.includes(v) ? v : "formatted";
  } catch {
    return "formatted";
  }
};

/**
 * @returns {[("formatted"|"raw"), (mode: "formatted"|"raw"|((m) => string)) => void]}
 */
export default function useValueViewMode() {
  const [mode, setModeState] = useState(read);

  useEffect(() => {
    const sync = () => setModeState(read());
    window.addEventListener(EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const setMode = useCallback((next) => {
    const value = typeof next === "function" ? next(read()) : next;
    if (!MODES.includes(value)) return;
    try {
      window.localStorage.setItem(VALUE_VIEW_MODE_KEY, value);
    } catch {
      // storage unavailable: still switch for this page
    }
    setModeState(value);
    window.dispatchEvent(new Event(EVENT));
  }, []);

  return [mode, setMode];
}
