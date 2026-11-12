// The results filter of one Explore group (W5-2 B1). Pure. A group opens
// the results filtered by the group's own pill (a metadata or split pill)
// when the results summary knows that key / split; the caller keeps it only
// when it selects exactly the group's rows, else the group's rows pill.
import { NO_SPLIT, rowsPill } from "src/sections/common/Results/filterPills";
import { expressionPill } from "src/sections/common/filters/expressionPill";
import { groupLabel, MISSING } from "./workbenchModel";

/** The literal of a group name: `true`, `3`, `"enterprise"`; null for none. */
function literalOf(group) {
  if (group == null || group === MISSING) return null;
  if (group === "true" || group === "false") return group;
  if (/^-?\d+(\.\d+)?$/.test(group)) return group;
  return JSON.stringify(group);
}

/**
 * @param {string} rowsKey  the rows axis (`meta:difficulty`, `meta:split`…)
 * @param {string} group    the group's value as Explore prints it
 * @param {{metadata_keys?: string[], metadata_values?: Object<string, any[]>,
 *   splits?: Array<{id: string, name: string}>}} summary  R3 summary
 * @returns {object|null}  a `meta` / `split` pill, or null
 */
export function ownGroupPill(rowsKey, group, summary) {
  // An expression group's own filter is the expression equal to the
  // group's value (`(len(customer_message) > 120) = true`); the caller
  // keeps it only when the results answer exactly the group's rows.
  if (rowsKey?.startsWith("expr:")) {
    const lit = literalOf(group);
    return lit ? expressionPill(`(${rowsKey.slice(5)}) = ${lit}`) : null;
  }
  if (!rowsKey?.startsWith("meta:")) return null;
  const key = rowsKey.slice(5);
  if (key === "split") {
    if (group === "(no split)") return { type: "split", values: [NO_SPLIT] };
    const split = (summary?.splits ?? []).find((s) => s.name === group);
    return split ? { type: "split", values: [split.id] } : null;
  }
  if (!(summary?.metadata_keys ?? []).includes(key)) return null;
  const value = (summary?.metadata_values?.[key] ?? []).find(
    (v) => String(v) === group,
  );
  return value === undefined ? null : { type: "meta", key, op: "eq", value };
}

/** The group's rows pill: `Difficulty: hard` with exactly its row ids. */
export const groupRowsPill = (rowsKey, group, rowIds) =>
  rowsPill(rowIds, { label: `${groupLabel(rowsKey)}: ${group}` });

/** True when two id lists hold the same ids. */
export function sameIds(a, b) {
  const x = new Set(a ?? []);
  const y = new Set(b ?? []);
  return x.size === y.size && [...x].every((id) => y.has(id));
}
