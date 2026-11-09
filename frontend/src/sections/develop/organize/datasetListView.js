// The dataset list's filter and Group by state as the toolbar shows it
// (W9-SCOPE §5 A2, A3, A5). Pure: fields from the query's facets, value
// labels, the left-out sentence, the Group by options, the footer count,
// and the saved-view state round trip (the page is never saved).
import {
  ANSWERED_DATASET_FIELDS,
  METADATA_PREFIX,
  metadataFields,
  valueLabel,
} from "./datasetFields";
import { META_COLUMN_PREFIX } from "../datasetList";
import {
  parseFilters,
  serializeFilters,
} from "src/sections/common/ListFilter/listFilterModel";

/**
 * The fields the toolbar offers: the answered ones + one per metadata key.
 * `metadataTypes` (the query's `metadata_types`, typed over every listed
 * dataset) keeps a key and its type while a search narrows the facets.
 */
export const listFields = (facets, metadataTypes) => [
  ...ANSWERED_DATASET_FIELDS,
  ...metadataFields(facets?.metadata, metadataTypes),
];

/** Agent ids → names from the agent facet (labels the server sent). */
export const agentNamesOf = (facets) =>
  Object.fromEntries(
    (facets?.agent ?? []).map((f) => [String(f.value), f.label ?? f.value]),
  );

/**
 * A value's words in chips and menus (agents by name, kinds in words), as
 * ListFilter calls it: `labelOf(field, value)`; null leaves the raw value.
 */
export const labelOfFor = (facets) => {
  const agentNames = agentNamesOf(facets);
  return (field, value) =>
    valueLabel(field?.id ?? field, value, { agentNames });
};

const plural = (n) => `${n} dataset${n === 1 ? "" : "s"}`;

/**
 * Under the chips: `2 datasets have no finished run and are left out by
 * “Last pass”.` per field the server counted.
 */
export function leftOutNotes(leftOut, fields) {
  return Object.entries(leftOut ?? {})
    .filter(([, n]) => n > 0)
    .map(([id, n]) => {
      const field = fields.find((f) => f.id === id);
      const label = field?.label ?? id;
      const verb = n === 1 ? "has" : "have";
      const is = n === 1 ? "is" : "are";
      return field?.quality
        ? `${plural(n)} ${verb} no finished run and ${is} left out by “${label}”.`
        : `${plural(n)} ${verb} no value for “${label}” and ${is} left out.`;
    });
}

/** Group by options: `None` + the groupable fields (metadata keys too). */
export const groupOptions = (fields) => [
  { id: "", label: "None" },
  ...fields
    .filter((f) => f.groupable)
    .map((f) => ({ id: f.id, label: f.label })),
];

export const GROUP_HINT = "A dataset with several tags shows under each.";

/** The footer: `2 of 9 datasets match` while filtered, else `9 datasets`. */
export function matchLine(shown, all, filtered) {
  if (!filtered) return plural(all);
  return `${shown} of ${plural(all)} match`;
}

/** The view state a saved list view holds (never the page). */
export const savedListState = (view) => ({
  q: view?.q ?? "",
  sort: view?.sort ?? "",
  size: view?.size,
  f: view?.f ?? "",
  group: view?.group ?? "",
});

/** A saved view's state applied to the list: the page goes back to 0. */
export const applySavedListState = (state) => ({
  q: state?.q ?? "",
  sort: state?.sort ?? "",
  ...(state?.size ? { size: state.size } : {}),
  f: state?.f ?? "",
  group: state?.group ?? "",
  page: 0,
});

/** The URL's `f` as `{filters, error}` (a bad link never breaks the page). */
export const filtersFromUrl = (f) => parseFilters(f ?? "");
/** Filters as the URL's `f` (compact JSON; "" when none). */
export const urlFromFilters = (filters) => serializeFilters(filters);

// Group by ⇄ list column (M4 mount): a column header's "Group by <column>"
// and the toolbar's Group by are one setting (URL `group`). The list's
// groupable columns are Folder, Agents and one per metadata key.
const COLUMN_OF_GROUP = Object.freeze({ folder: "folder", agent: "agents" });

/** The list column a Group by field shows in (`metadata.tier` → `meta:tier`). */
export function groupColumnOf(groupId) {
  if (!groupId) return null;
  if (groupId.startsWith(METADATA_PREFIX))
    return `${META_COLUMN_PREFIX}${groupId.slice(METADATA_PREFIX.length)}`;
  return COLUMN_OF_GROUP[groupId] ?? null;
}

/** The Group by field of a list column (`meta:tier` → `metadata.tier`). */
export function groupOfColumn(columnId) {
  if (!columnId) return "";
  if (columnId.startsWith(META_COLUMN_PREFIX))
    return `${METADATA_PREFIX}${columnId.slice(META_COLUMN_PREFIX.length)}`;
  const hit = Object.entries(COLUMN_OF_GROUP).find(([, c]) => c === columnId);
  return hit ? hit[0] : "";
}
