// The dataset list's filter and Group by fields (W9-SCOPE §5 A2, A3): one
// registry for the list toolbar, the chips and the mock's query, so both
// sides read an item's value the same way. Pure.
//
// An item is a dataset list row as the W9 query returns it: get-datasets'
// row plus `metadata`, `folder`, `agent_ids`, `agents`, `starred`,
// `last_run` and `delta_vs_baseline`.

/** The fixed fields, in menu order. */
export const DATASET_FIELDS = Object.freeze([
  { id: "name", label: "Name", word: "name", type: "text" },
  {
    id: "kind",
    label: "Kind",
    word: "kind",
    type: "categorical",
    groupable: true,
  },
  {
    id: "source",
    label: "Source",
    word: "source",
    type: "categorical",
    groupable: true,
  },
  {
    id: "owner",
    label: "Owner",
    word: "owner",
    type: "categorical",
    groupable: true,
  },
  {
    id: "tag",
    label: "Tag",
    word: "tag",
    type: "categorical",
    multi: true,
    groupable: true,
  },
  {
    id: "folder",
    label: "Folder",
    word: "folder",
    type: "folder",
    groupable: true,
  },
  {
    id: "agent",
    label: "Agent",
    word: "agent",
    type: "categorical",
    multi: true,
    groupable: true,
  },
  { id: "starred", label: "Starred", word: "starred", type: "boolean" },
  { id: "rows", label: "Rows", word: "rows", type: "number" },
  {
    id: "experiments",
    label: "Experiments",
    word: "experiments",
    type: "number",
  },
  { id: "updated", label: "Updated", word: "updated", type: "datetime" },
  { id: "created", label: "Created", word: "created", type: "datetime" },
  {
    id: "last_pass",
    label: "Last pass",
    word: "last pass",
    type: "percent",
    quality: true,
  },
  {
    id: "delta_vs_baseline",
    label: "Δ vs baseline",
    word: "Δ vs baseline",
    type: "points",
    quality: true,
    significant: true,
  },
  { id: "baseline", label: "Baseline", word: "baseline", type: "number" },
  { id: "best", label: "Best", word: "best", type: "number" },
]);

export const METADATA_PREFIX = "metadata.";
export const isMetadataField = (id) =>
  typeof id === "string" && id.startsWith(METADATA_PREFIX);
export const metadataKeyOf = (id) =>
  isMetadataField(id) ? id.slice(METADATA_PREFIX.length) : null;

/** The fields Group by offers (metadata keys are added by the caller). */
export const GROUPABLE_FIELDS = Object.freeze(
  DATASET_FIELDS.filter((f) => f.groupable).map((f) => f.id),
);

/** The type a metadata key filters as, from the values datasets hold. */
export function metadataFieldType(values) {
  const seen = (values ?? []).filter((v) => v !== null && v !== undefined);
  if (seen.length && seen.every((v) => typeof v === "number")) return "number";
  if (seen.length && seen.every((v) => typeof v === "boolean"))
    return "boolean";
  return "categorical";
}

/**
 * Each metadata key the items hold, typed by every value it holds there:
 * `{tier: "number", pii: "boolean", region: "categorical"}`. The list's
 * field types come from every listed dataset, so a search never changes
 * what a key filters as. Pure.
 * @param {{metadata?: object}[]} items
 */
export function metadataTypesOf(items) {
  const values = new Map();
  (items ?? []).forEach((item) =>
    Object.entries(item?.metadata ?? {}).forEach(([key, v]) => {
      if (!values.has(key)) values.set(key, []);
      values.get(key).push(v);
    }),
  );
  return Object.fromEntries(
    [...values.keys()]
      .sort()
      .map((key) => [key, metadataFieldType(values.get(key))]),
  );
}

/**
 * One field per metadata key: `metadata.domain`. A key's type is
 * `metadataTypes[key]` when given (the whole list's, see metadataTypesOf),
 * else from its facet values; keys come from both.
 * @param {{[key: string]: {value: any, count: number}[]}} metadataFacets
 * @param {{[key: string]: string}} [metadataTypes]
 */
export function metadataFields(metadataFacets, metadataTypes) {
  const keys = new Set([
    ...Object.keys(metadataFacets ?? {}),
    ...Object.keys(metadataTypes ?? {}),
  ]);
  return [...keys].sort().map((key) => ({
    id: `${METADATA_PREFIX}${key}`,
    label: key,
    word: `${METADATA_PREFIX}${key}`,
    type:
      metadataTypes?.[key] ??
      metadataFieldType((metadataFacets?.[key] ?? []).map((f) => f.value)),
    metadata: true,
    groupable: true,
  }));
}

/** Every field: the fixed ones plus one per metadata key (see metadataFields). */
export const allDatasetFields = (metadataFacets, metadataTypes) => [
  ...DATASET_FIELDS,
  ...metadataFields(metadataFacets, metadataTypes),
];

/**
 * The fields the list answers today, for the toolbar's field menu and the
 * unknown-field sentence. Last pass and Δ vs baseline join once the run
 * pair helper they share with Best exists (W9-SCOPE §5 A2); until then the
 * query refuses them and no menu offers them.
 */
export const ANSWERED_DATASET_FIELDS = Object.freeze(
  DATASET_FIELDS.filter((f) => !f.quality),
);

/** The 400 sentence for an unknown field. */
export const unknownFieldSentence = (id) =>
  `Unknown field “${id}”. Fields: ${ANSWERED_DATASET_FIELDS.map((f) => f.word).join(", ")}, metadata.<key>.`;

const ownerOf = (item) =>
  item?.created_by_name ?? item?.owner_name ?? item?.owner ?? null;
const listOf = (v) => (Array.isArray(v) ? v : v == null ? [] : [v]);

/**
 * An item's value(s) for a field: a list for multi-valued fields, else one
 * value; null when it has none (quality fields without a finished run).
 */
export function fieldValue(item, fieldId) {
  if (isMetadataField(fieldId)) {
    const v = item?.metadata?.[metadataKeyOf(fieldId)];
    return v === undefined ? null : v;
  }
  switch (fieldId) {
    case "name":
      return item?.name ?? null;
    case "kind":
      return item?.kind ?? null;
    case "source":
      return item?.source ?? null;
    case "owner":
      return ownerOf(item);
    case "tag":
      return listOf(item?.tags);
    case "folder":
      return item?.folder ?? "";
    case "agent":
      return listOf(item?.agent_ids);
    case "starred":
      return Boolean(item?.starred);
    case "rows":
      return item?.number_of_datapoints ?? null;
    case "experiments":
      return item?.number_of_experiments ?? null;
    case "updated":
      return item?.updated_at ?? null;
    case "created":
      return item?.created_at ?? null;
    case "last_pass":
      return item?.last_run?.value ?? null;
    case "delta_vs_baseline":
      return item?.delta_vs_baseline?.pts ?? null;
    case "baseline":
      return item?.baseline?.value ?? null;
    case "best":
      return item?.best_run?.value ?? null;
    default:
      return undefined;
  }
}

/** Kind words as the list shows them. */
const KIND_LABELS = Object.freeze({
  rows: "Rows",
  task_suite: "Task suite",
  scenarios: "Voice scenarios",
  conversations: "Sessions",
});

/** A categorical value's label (agents by name, kinds in words). */
export function valueLabel(fieldId, value, { agentNames } = {}) {
  if (value === null || value === undefined || value === "") return null;
  if (fieldId === "agent") return agentNames?.[value] ?? String(value);
  if (fieldId === "kind") return KIND_LABELS[value] ?? String(value);
  if (fieldId === "source")
    return String(value).charAt(0).toUpperCase() + String(value).slice(1);
  return String(value);
}

/** The group a dataset with no value goes to: `No tag`, `No folder`. */
export function emptyGroupLabel(field) {
  return `No ${String(field?.word ?? field?.label ?? "value").replace(
    /^metadata\./,
    "",
  )}`;
}
