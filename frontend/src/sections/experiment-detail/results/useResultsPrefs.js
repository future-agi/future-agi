import { useCallback, useState } from "react";
import { DEFAULT_HIDDEN } from "./resultsColumns";

// Per-viewer display preferences of the results grid (PRODUCT-SPEC §3.10.1):
// column visibility per dataset (`results.cols.<datasetId>`), row height and
// inline reasons. localStorage only; every read and write is guarded.

const read = (key, fallback) => {
  try {
    const raw = window.localStorage.getItem(key);
    return raw == null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
};

const write = (key, value) => {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // storage blocked: keep the in-memory value
  }
};

export const colsKey = (datasetId) => `results.cols.${datasetId}`;

export default function useResultsPrefs(datasetId) {
  const [hidden, setHiddenState] = useState(
    () => new Set(read(colsKey(datasetId), DEFAULT_HIDDEN)),
  );
  const [rowHeightKey, setRowHeightKeyState] = useState(() =>
    read("results.density", "Short"),
  );
  const [showReasons, setShowReasonsState] = useState(() =>
    read("results.reasons", false),
  );
  const [viewAllPrompts, setViewAllPromptsState] = useState(false);

  const setHidden = useCallback(
    (next) => {
      setHiddenState((prev) => {
        const value = typeof next === "function" ? next(prev) : next;
        write(colsKey(datasetId), [...value]);
        return new Set(value);
      });
    },
    [datasetId],
  );

  const toggleColumn = useCallback(
    (key) =>
      setHidden((prev) => {
        const next = new Set(prev);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return next;
      }),
    [setHidden],
  );

  const setRowHeightKey = useCallback((key) => {
    write("results.density", key);
    setRowHeightKeyState(key);
  }, []);

  const setShowReasons = useCallback((value) => {
    setShowReasonsState((prev) => {
      const next = typeof value === "function" ? value(prev) : value;
      write("results.reasons", next);
      return next;
    });
  }, []);

  // "Show all columns" = metadata and token columns too.
  const showAllColumns = !hidden.has("meta") && !hidden.has("ops:tokens");
  const setShowAllColumns = useCallback(
    (on) =>
      setHidden((prev) => {
        const next = new Set(prev);
        ["meta", "ops:tokens"].forEach((k) =>
          on ? next.delete(k) : next.add(k),
        );
        return next;
      }),
    [setHidden],
  );

  return {
    hidden,
    setHidden,
    toggleColumn,
    rowHeightKey,
    setRowHeightKey,
    showReasons,
    setShowReasons,
    showAllColumns,
    setShowAllColumns,
    viewAllPrompts,
    setViewAllPrompts: setViewAllPromptsState,
  };
}
