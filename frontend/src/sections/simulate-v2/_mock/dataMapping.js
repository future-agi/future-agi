/*
  Mapping a records file into the world's tables (data.md "Field mapping" and
  the ID-mapping example): a type and a stable-ID marker per column, the
  relationships between tables, fields the builder couldn't place, a column
  stored as the wrong type, and a column that holds the same ID another file
  calls something else (orders.buyer_id is the world's customers.customer_id).

  Edits write onto the source's parsed columns, so the preview, the joins and
  the proposal all read the mapping the person settled on. Nothing parses a
  real file.
*/
import { envChangePatch } from "./versions";

export const COLUMN_TYPES = ["string", "integer", "number", "date", "boolean"];

/* Columns the world's table already has, for fields the builder couldn't place. */
export const WORLD_COLUMNS = {
  orders: ["discount_code", "customer_tier", "channel", "status"],
};

/* What a value becomes when it's replaced. An ID gets the same stand-in
   wherever it appears, so a relationship across files survives. */
export const isIdValue = (v) => /^[A-Z]{1,4}-?\d+$/.test(String(v));
export const standInId = (v) => {
  const n = parseInt(String(v).replace(/\D/g, ""), 10) || 0;
  return `CUST-${String(1000 + n).padStart(4, "0")}`;
};
export const standInOf = (value, mode, i = 0) => {
  if (mode === "omit") return "—";
  if (mode === "mask") return String(value).replace(/[^@.\s-]/g, "•");
  if (mode === "replace")
    return /@/.test(String(value))
      ? `customer${i + 1}@sandbox.test`
      : /^[A-Z][a-z]+ [A-Z]/.test(String(value))
        ? `Customer ${i + 1}`
        : isIdValue(value)
          ? standInId(value)
          : `X-${100 + i}`;
  return String(value);
};

/* Bump the mapping version on a source, noting when. */
const mappingOf = (source) => ({
  v: (source.mapping?.v || 1) + 1,
  at: new Date().toISOString(),
});

/** The columns that still need a decision: a proposed mapping, unmatched fields, a wrong type. */
export const mappingReviewOf = (source) => {
  const cols = source?.parsed?.columns || [];
  return {
    proposals: cols.filter((c) => c.proposal && !c.proposal.approved),
    unmatched: cols.filter((c) => c.unmatched && !c.mappedTo && !c.leftOut),
    wrongType: cols.filter((c) => c.textRows?.length && c.type !== "number"),
  };
};
export const mappingToReview = (source) => {
  const r = mappingReviewOf(source);
  return r.proposals.length + r.unmatched.length + r.wrongType.length;
};

/** The table a proposed mapping points at, and whether the ID exists there. */
export const idCheckOf = (col, sources) => {
  const to = col.proposal?.to || "";
  const [table, field] = to.split(".");
  const target = sources.find(
    (s) =>
      !s.archived && s.parsed?.kind === "table" && s.parsed.table === table,
  );
  const id = col.proposal?.id;
  return {
    table,
    field,
    id,
    target,
    found: !!target && Object.values(target.parsed.sample || {}).includes(id),
  };
};

/** Tables a column can point at: every table in the data, by its key. */
export const relationshipTargets = (source, sources) =>
  sources
    .filter(
      (s) =>
        !s.archived &&
        s.id !== source.id &&
        s.parsed?.kind === "table" &&
        s.parsed.columns.some((c) => c.key),
    )
    .map(
      (s) => `${s.parsed.table}.${s.parsed.columns.find((c) => c.key).name}`,
    );

/* The sources array with one source's parsed columns changed. */
const withColumn = (sources, id, name, fields) =>
  sources.map((s) =>
    s.id === id
      ? {
          ...s,
          mapping: mappingOf(s),
          parsed: {
            ...s.parsed,
            columns: s.parsed.columns.map((c) =>
              c.name === name ? { ...c, ...fields } : c,
            ),
          },
        }
      : s,
  );

const changeNote = (envState, source, detail) =>
  source?.inEnv && detail
    ? {
        ...envChangePatch(envState, "seed", detail),
        changeSource: `file:${source.name}`,
      }
    : {};

/** Change one column: its type, stable-ID marker, relationship or resolution. */
export const columnEditPatch = (envState, source, name, fields, detail) => ({
  sources: withColumn(envState.sources || [], source.id, name, fields),
  ...changeNote(envState, source, detail),
});

/** A column stored as text that holds numbers: read it as a number. */
export const readAsNumberPatch = (envState, source, name) =>
  columnEditPatch(
    envState,
    source,
    name,
    {
      type: "number",
      textRows: [],
      converted:
        source.parsed.columns.find((c) => c.name === name)?.textRows?.length ||
        0,
    },
    `${source.name}.${name} is read as a number`,
  );

/**
 * Approve a proposed ID mapping: the column becomes a reference to the other
 * file's ID, the rows whose ID isn't there are listed, and — if the IDs are
 * replaced — both files get the same stand-in for the same ID.
 */
export const approveIdMappingPatch = (
  envState,
  source,
  name,
  { replace = true } = {},
) => {
  const col = source.parsed.columns.find((c) => c.name === name);
  const [table, field] = col.proposal.to.split(".");
  const mismatches = col.proposal.mismatches || [];
  const now = new Date().toISOString();
  const sources = (envState.sources || []).map((s) => {
    if (s.id === source.id) {
      const rows = s.parsed.rows;
      return {
        ...s,
        mapping: mappingOf(s),
        idMapping: {
          column: name,
          to: col.proposal.to,
          approvedAt: now,
          replace,
        },
        status: mismatches.length ? "needs-attention" : s.status,
        statusNote: mismatches.length
          ? `${mismatches.length} of ${rows} rows rejected`
          : s.statusNote,
        privacy: replace
          ? { ...(s.privacy || {}), [name]: "replace" }
          : s.privacy,
        parsed: {
          ...s.parsed,
          rejected: mismatches,
          accepted: rows - mismatches.length,
          columns: s.parsed.columns.map((c) =>
            c.name === name
              ? {
                  ...c,
                  ref: col.proposal.to,
                  stableId: true,
                  proposal: { ...c.proposal, approved: true },
                }
              : c,
          ),
        },
      };
    }
    if (
      replace &&
      !s.archived &&
      s.parsed?.kind === "table" &&
      s.parsed.table === table
    )
      return {
        ...s,
        mapping: mappingOf(s),
        privacy: { ...(s.privacy || {}), [field]: "replace" },
        parsed: {
          ...s.parsed,
          columns: s.parsed.columns.map((c) =>
            c.name === field ? { ...c, stableId: true } : c,
          ),
        },
      };
    return s;
  });
  return {
    sources,
    ...changeNote(
      envState,
      source,
      `${source.name}: ${name} mapped to ${col.proposal.to}${replace ? ", IDs replaced in both files" : ""}`,
    ),
  };
};

/** Say it isn't the same ID: the proposal is dropped and the column stays a plain field. */
export const dismissIdMappingPatch = (envState, source, name) =>
  columnEditPatch(
    envState,
    source,
    name,
    { proposal: null },
    `${source.name}: ${name} kept as its own field`,
  );

/* ── personal data ──────────────────────────────────────────────────────── */

export const PERSONAL_CHOICES = [
  {
    id: "keep",
    label: "Keep",
    note: "Real values stay as they are in the world.",
  },
  {
    id: "replace",
    label: "Replace with synthetic values",
    note: "Stand-ins that keep the same person the same across files.",
  },
  {
    id: "none",
    label: "No personal data present",
    note: "Nothing in this file identifies a person.",
  },
];
export const personalChoiceLabel = (id) =>
  PERSONAL_CHOICES.find((c) => c.id === id)?.label || "Not chosen yet";

/** Does the source hold personal data, which columns, and what did the person choose? */
export const personalDataOf = (source) => {
  const columns = (source?.parsed?.columns || [])
    .filter((c) => c.sensitive)
    .map((c) => c.name);
  const has =
    columns.length > 0 ||
    (source?.parsed?.kind === "text" && source?.role === "example");
  return {
    has,
    columns,
    suggested: "replace",
    choice: source?.personalChoice || null,
  };
};

/** Choosing sets how each personal column is handled; the person can still tune a column. */
export const personalChoicePatch = (envState, source, choice) => {
  const cols = personalDataOf(source).columns;
  const mode = choice === "replace" ? "replace" : "retain";
  const privacy = {
    ...(source.privacy || {}),
    ...Object.fromEntries(cols.map((c) => [c, mode])),
  };
  return {
    sources: (envState.sources || []).map((s) =>
      s.id === source.id ? { ...s, personalChoice: choice, privacy } : s,
    ),
    ...changeNote(
      envState,
      source,
      `${source.name}: personal data — ${personalChoiceLabel(choice).toLowerCase()}`,
    ),
  };
};

/** Who sees a source's data once it's in the world. */
export const WHO_SEES_PERSONAL =
  "The agent and simulated people see the result.";

/**
 * Records added to fill a gap: the new files are tagged with the table they
 * fill, and a table that was already there (its rows named records it lacked)
 * counts those rows as supplied. `join` is { to, field, present }.
 */
export const supplyRecordsPatch = (envState, sourceId, join, added) => {
  const table = String(join.to).split(".")[0];
  return {
    sources: [
      ...(envState.sources || []).map((x) =>
        x.id === sourceId && join.present
          ? { ...x, joins: { ...(x.joins || {}), [join.field]: "supplied" } }
          : x,
      ),
      ...added.map((r) => ({ ...r, fills: [table] })),
    ],
  };
};
