import { choiceOf, columnsOf } from "./personalData";
import { isSaved } from "./sources.constants";

// A records file's mapping into the world's tables. Edits write onto the
// file's parsed columns under `ui`, so What was read and the relationships
// read the mapping the person settled on.

export const COLUMN_TYPES = ["string", "integer", "number", "date", "boolean"];

// shortcut: sample columns the world's tables already have; read them from the world's schema once the sources API sends it.
export const WORLD_COLUMNS = {
  orders: ["discount_code", "customer_tier", "channel", "status"],
};

const tablesOf = (sources) =>
  sources.filter(
    (source) => !source.ui?.removed && source.ui?.parsed?.kind === "table",
  );

export const mappingVersionOf = (source) => source.ui?.mapping?.v || 1;

// Columns still waiting on a decision: a proposed ID, an unplaced field, numbers stored as text.
export const toReviewOf = (source) =>
  source.ui.parsed.columns.filter(
    (column) =>
      (column.proposal && !column.proposal.approved) ||
      (column.unmatched && !column.mappedTo && !column.leftOut) ||
      (column.textRows?.length && column.type !== "number"),
  ).length;

// The same ID gets the same stand-in in every file, so joins survive.
export const standInId = (value) =>
  `CUST-${String(1000 + (parseInt(String(value).replace(/\D/g, ""), 10) || 0)).padStart(4, "0")}`;

// Other tables a column can point at, by their key.
export const relationshipTargets = (source, sources) =>
  tablesOf(sources)
    .filter(
      (one) =>
        one.source_id !== source.source_id &&
        one.ui.parsed.columns.some((column) => column.key),
    )
    .map(
      (one) =>
        `${one.ui.parsed.table}.${one.ui.parsed.columns.find((column) => column.key).name}`,
    );

// The table a proposed ID points at, and whether the sample ID is there.
export const idCheckOf = (column, sources) => {
  const [table, field] = (column.proposal?.to || "").split(".");
  const target = tablesOf(sources).find((one) => one.ui.parsed.table === table);
  return {
    table,
    field,
    target,
    found:
      Boolean(target) &&
      Object.values(target.ui.parsed.sample || {}).includes(
        column.proposal?.id,
      ),
  };
};

/**
 * Each column that points at another table: whether that table is attached,
 * which rows miss it (every row when it isn't), the person's resolution
 * (`omit`) and whether it still blocks.
 */
export const joinsOf = (source, sources) => {
  const parsed = source.ui.parsed;
  const tables = new Set(tablesOf(sources).map((one) => one.ui.parsed.table));
  return parsed.columns
    .filter((column) => column.ref)
    .map((column) => {
      const table = column.ref.split(".")[0];
      const present = tables.has(table);
      const resolution = source.ui.joins?.[column.name] || null;
      return {
        field: column.name,
        to: column.ref,
        table,
        present,
        missing: present ? parsed.rejected.length : parsed.rows,
        rows: present ? parsed.rejected : [],
        resolution,
        blocking: !resolution && (!present || parsed.rejected.length > 0),
      };
    });
};

// One column's fields changed; the mapping version moves on.
export const editColumn = (source, name, fields) => ({
  ...source,
  ui: {
    ...source.ui,
    mapping: { v: mappingVersionOf(source) + 1 },
    parsed: {
      ...source.ui.parsed,
      columns: source.ui.parsed.columns.map((column) =>
        column.name === name ? { ...column, ...fields } : column,
      ),
    },
  },
});

export const setJoin = (source, field, resolution) => ({
  ...source,
  ui: { ...source.ui, joins: { ...source.ui.joins, [field]: resolution } },
});

/**
 * Approve a proposed ID: the column points at the other table's ID and its
 * mismatched rows are rejected. Replacing IDs marks the column for
 * consistent stand-ins.
 */
// shortcut: writes only this file; mark the other file's ID for the same stand-ins once edits can span files.
export const approveIdMapping = (source, name, { replace }) => {
  const column = source.ui.parsed.columns.find((one) => one.name === name);
  const mismatches = column.proposal.mismatches || [];
  const { rows } = source.ui.parsed;
  const next = editColumn(source, name, {
    ref: column.proposal.to,
    stableId: true,
    proposal: { ...column.proposal, approved: true },
  });
  // The whole column map with this one replaced, as the Personal data tab writes it.
  const columns = columnsOf(source).map((one) =>
    one.name === name ? { ...one, mode: "replace" } : one,
  );
  const choice = source.ui.personal_choice || null;
  return {
    ...next,
    // A file not saved yet gets its status when it's accepted.
    status:
      mismatches.length && isSaved(source) ? "rows_rejected" : source.status,
    status_note: mismatches.length
      ? `${mismatches.length} of ${rows} rows rejected`
      : source.status_note,
    ui: {
      ...next.ui,
      id_mapping: { column: name, to: column.proposal.to, replace },
      // Not chosen yet stays not chosen, so the file still asks.
      ...(replace &&
        (choice
          ? {
              privacy: Object.fromEntries(
                columns.map((one) => [one.name, one.mode]),
              ),
              personal_choice: choiceOf(columns, choice),
            }
          : { privacy: { ...next.ui.privacy, [name]: "replace" } })),
      parsed: {
        ...next.ui.parsed,
        rejected: mismatches,
        accepted: rows - mismatches.length,
      },
    },
  };
};
