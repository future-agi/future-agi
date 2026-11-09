import { useEffect, useLayoutEffect, useState } from "react";
import { useParams } from "react-router-dom";

// Grid column view mode (R2-SPEC P6 §3.2): `View as ▸ Auto · JSON · YAML ·
// Text` from the column menu, per dataset + column in localStorage
// (`fa.colView.<datasetId>.<columnId>`). The menu writes and broadcasts;
// every cell of the column re-renders.

export const COLUMN_VIEW_MODES = ["auto", "json", "yaml", "text"];
export const COLUMN_VIEW_LABEL = {
  auto: "Auto",
  json: "JSON",
  yaml: "YAML",
  text: "Text",
};
const EVENT = "fa:column-view";
const memory = new Map();
const key = (datasetId, columnId) =>
  `fa.colView.${datasetId ?? "_"}.${columnId}`;

export function readColumnViewMode(datasetId, columnId) {
  if (!columnId) return "auto";
  try {
    const v = window.localStorage.getItem(key(datasetId, columnId));
    return COLUMN_VIEW_MODES.includes(v) ? v : "auto";
  } catch {
    return "auto";
  }
}

export function setColumnViewMode(datasetId, columnId, mode) {
  try {
    if (!mode || mode === "auto")
      window.localStorage.removeItem(key(datasetId, columnId));
    else window.localStorage.setItem(key(datasetId, columnId), mode);
  } catch {
    // storage unavailable: the mode lasts for this page
    memory.set(key(datasetId, columnId), mode);
  }
  window.dispatchEvent(
    new CustomEvent(EVENT, { detail: { datasetId, columnId, mode } }),
  );
}

/**
 * The AG Grid column id of the cell a renderer sits in (`col-id` on
 * `.ag-cell`), read after mount from the renderer's root ref.
 */
export function useCellColumnId(ref) {
  const [colId, setColId] = useState(null);
  useLayoutEffect(() => {
    const id = ref.current?.closest?.(".ag-cell")?.getAttribute("col-id");
    if (id && id !== colId) setColId(id);
  }, [ref, colId]);
  return colId;
}

/** `[mode, setMode]` of a grid column's view. */
export default function useColumnViewMode(columnId) {
  const { dataset } = useParams();
  const [mode, setMode] = useState(() =>
    columnId ? readColumnViewMode(dataset, columnId) : "auto",
  );
  useEffect(() => {
    if (!columnId) return undefined;
    const k = key(dataset, columnId);
    setMode(memory.get(k) ?? readColumnViewMode(dataset, columnId));
    const sync = (e) => {
      if (e?.detail?.columnId && e.detail.columnId !== columnId) return;
      setMode(memory.get(k) ?? readColumnViewMode(dataset, columnId));
    };
    window.addEventListener(EVENT, sync);
    return () => window.removeEventListener(EVENT, sync);
  }, [dataset, columnId]);
  return [mode, (m) => setColumnViewMode(dataset, columnId, m)];
}
