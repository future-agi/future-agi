// IT-R2 G5 P5: what a nested value in the row drawer can become, from where
// it is read: `Add as column · Filter by this · Use as variable`. The row
// drawer provides the actions; ValueViewer → ValueTree render them on every
// nested field (no context = no buttons, e.g. outside a dataset page).
import { createContext, useContext } from "react";

/**
 * @typedef {object} ValueActions
 * @property {(path: string) => void} [addColumn]   `metadata.customer.tier`
 * @property {(path: string, value: any) => void} [filterBy]
 * @property {(path: string) => void} [useAsVariable]
 */
export const ValueActionsContext = createContext(null);
export const useValueActions = () => useContext(ValueActionsContext);

/** `metadata.customer.tier` -> `{column: "metadata", rel: "customer.tier"}`. */
export function splitColumnPath(path) {
  const s = String(path ?? "");
  const m = /^([^.[\]]+)(.*)$/.exec(s);
  if (!m) return { column: s, rel: "" };
  return { column: m[1], rel: m[2].replace(/^\./, "") };
}

/** A filter-store entry `<column>::<rel> equals value` (DevelopFilterBox). */
export function pathFilterEntry(columnId, rel, value, id) {
  const scalar =
    value == null || typeof value !== "object" ? value : JSON.stringify(value);
  return {
    ...(id ? { id } : {}),
    columnId: rel ? `${columnId}::${rel}` : columnId,
    filterConfig: {
      filterType: "text",
      filterOp: "equals",
      filterValue: String(scalar ?? ""),
    },
    _meta: { parentProperty: "" },
  };
}

/** `{{metadata.customer.tier}}`. */
export const variableOf = (path) => `{{${path}}}`;
