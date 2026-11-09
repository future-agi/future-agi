import { useMemo } from "react";
import { sniffValue } from "./sniff";
import useColumnViewMode from "./useColumnViewMode";

const TYPED_KINDS = new Set([
  "object",
  "array",
  "chat",
  "embedding",
  "images",
  "ref",
  "yaml_text",
]);
const HUGE_TEXT = 20000;

/** True when a textual grid cell should render its typed summary. */
export function wantsTypedCell(value, dataType, mode = "auto") {
  if (mode && mode !== "auto") return true;
  if (typeof value === "string" && value.length > HUGE_TEXT) return true;
  const s = sniffValue(value, { declaredType: dataType });
  return TYPED_KINDS.has(s.kind) || Boolean(s.meta?.mismatch);
}

/**
 * For the versioned grid's compact density (CellChrome): whether a text /
 * json cell shows the typed renderer (summary, `View as` mode) instead of
 * the plain one-line text. Re-renders when the column's `View as` changes.
 */
export default function useTypedCell({ value, dataType, columnId, enabled }) {
  const [mode] = useColumnViewMode(enabled ? columnId : null);
  return useMemo(
    () => Boolean(enabled) && wantsTypedCell(value, dataType, mode),
    [enabled, value, dataType, mode],
  );
}
