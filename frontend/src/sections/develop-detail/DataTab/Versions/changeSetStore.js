// Pending change-set per dataset (PRODUCT-SPEC §3.5, area-datasets §5.1).
// Implemented by L0, owned by L3. Hand edits (cells, row metadata, new blank
// rows, row deletes, split membership) stay here until "Save version"
// commits them as one N2 `POST versions/` with `toOperations()`.
//
// Kept out of resetAllStates() on purpose: pending edits survive route
// changes so the PendingChangesBar reappears when the user comes back.
//
// Op shapes are the N2 `operations` items, plus local fields:
//   id      "op_7"    stable id for Revert
//   at      ms        when it was added
//   before  any       the value it replaced (update_cell / set_row_metadata)
import { useMemo } from "react";
import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { enqueueSnackbar } from "notistack";
import { datasetCaps } from "src/sections/develop/realBackendCaps";

/**
 * L1 (the capability contract): the sentence a staged change gets on the real
 * backend, which has no versions to save it into. The grid, the selection
 * bar and the Row view write at once there (classicWrites.js); any other
 * door that still stages a change is refused out loud, never kept as a
 * pending change no one can save.
 */
export const NOT_SAVED_NO_VERSIONS =
  "Not saved: this change needs dataset versions, which this workspace does not have yet.";

export const OP_TYPES = [
  "update_cell",
  "add_row",
  "delete_rows",
  "set_splits",
  "set_row_metadata",
  // R2 (task suites, L5b): `{op, image, …, before}`; one per change-set.
  "set_environment",
  // the interface contract: `{op, row_ids, status, reason?}` (Approve / Needs review /
  // Archive with a reason / Restore); a row keeps its latest pending one.
  "set_status",
  // the interface contract: `{op, row_ids, add[], remove[]}` row tags (metadata.tags).
  "set_tags",
];

export const ROW_STATUS_LABEL = Object.freeze({
  draft: "Draft",
  needs_review: "Needs review",
  approved: "Approved",
  archived: "Archived",
});

/** byCell key used for row-metadata edits. */
export const METADATA_COLUMN = "__row_metadata__";

const EMPTY_SET = Object.freeze({
  baseVersionId: null,
  ops: Object.freeze([]),
});

let seq = 0;
const nextId = () => {
  seq += 1;
  return `op_${seq}`;
};

const same = (a, b) => {
  if (a === b) return true;
  if (a == null || b == null) return a == null && b == null;
  if (typeof a === "object" || typeof b === "object")
    return JSON.stringify(a) === JSON.stringify(b);
  return String(a) === String(b);
};

const uniq = (xs) => [...new Set(xs)];

const isTempRow = (ops, rowId) =>
  ops.some((o) => o.op === "add_row" && o.temp_id === rowId);

/** Merge `op` into `ops` (pure). Returns the new ops array. */
export function applyOp(ops, input) {
  if (!input || !OP_TYPES.includes(input.op)) return ops;
  const op = { ...input, id: input.id ?? nextId(), at: input.at ?? Date.now() };

  switch (op.op) {
    case "update_cell": {
      // Edits to a pending new row change that row's cells instead.
      if (isTempRow(ops, op.row_id)) {
        return ops.map((o) =>
          o.op === "add_row" && o.temp_id === op.row_id
            ? { ...o, cells: { ...o.cells, [op.column_id]: op.value } }
            : o,
        );
      }
      const prev = ops.find(
        (o) =>
          o.op === "update_cell" &&
          o.row_id === op.row_id &&
          o.column_id === op.column_id,
      );
      const before = prev ? prev.before : op.before;
      const rest = ops.filter((o) => o !== prev);
      // Typing the original value back is a revert (or a no-op).
      if ((prev || op.before !== undefined) && same(op.value, before))
        return rest;
      return [...rest, { ...op, id: prev?.id ?? op.id, before }];
    }
    case "set_row_metadata": {
      if (isTempRow(ops, op.row_id)) {
        return ops.map((o) =>
          o.op === "add_row" && o.temp_id === op.row_id
            ? { ...o, metadata: op.metadata }
            : o,
        );
      }
      const prev = ops.find(
        (o) => o.op === "set_row_metadata" && o.row_id === op.row_id,
      );
      const before = prev ? prev.before : op.before;
      const rest = ops.filter((o) => o !== prev);
      if ((prev || op.before !== undefined) && same(op.metadata, before))
        return rest;
      return [...rest, { ...op, id: prev?.id ?? op.id, before }];
    }
    case "add_row":
      return [
        ...ops,
        {
          ...op,
          temp_id:
            op.temp_id ??
            `new-${ops.filter((o) => o.op === "add_row").length + 1}`,
          cells: op.cells ?? {},
          split_ids: op.split_ids ?? [],
          metadata: op.metadata ?? {},
        },
      ];
    case "set_status": {
      const ids = uniq(op.row_ids ?? []);
      // Pending new rows take the status on their add_row.
      const newIds = ids.filter((id) => isTempRow(ops, id));
      const savedIds = ids.filter((id) => !isTempRow(ops, id));
      let next = ops.map((o) =>
        o.op === "add_row" && newIds.includes(o.temp_id)
          ? { ...o, status: op.status }
          : o,
      );
      next = next
        .map((o) =>
          o.op === "set_status"
            ? {
                ...o,
                row_ids: (o.row_ids ?? []).filter(
                  (id) => !savedIds.includes(id),
                ),
              }
            : o,
        )
        .filter((o) => o.op !== "set_status" || o.row_ids.length);
      return savedIds.length ? [...next, { ...op, row_ids: savedIds }] : next;
    }
    case "delete_rows": {
      if (op.selection) {
        // A whole-selection delete replaces any earlier one.
        return [
          ...ops.filter((o) => !(o.op === "delete_rows" && o.selection)),
          op,
        ];
      }
      const ids = uniq(op.row_ids ?? []);
      const temp = new Set(ids.filter((id) => isTempRow(ops, id)));
      const real = ids.filter((id) => !temp.has(id));
      const realSet = new Set(real);
      // Deleting a pending new row just drops it; edits to deleted rows go.
      let next = ops.filter(
        (o) =>
          !(o.op === "add_row" && temp.has(o.temp_id)) &&
          !(
            ["update_cell", "set_row_metadata"].includes(o.op) &&
            realSet.has(o.row_id)
          ),
      );
      next = next
        .map((o) =>
          o.op === "set_splits"
            ? {
                ...o,
                row_ids: o.row_ids.filter(
                  (id) => !realSet.has(id) && !temp.has(id),
                ),
              }
            : o,
        )
        .filter((o) => !(o.op === "set_splits" && !o.row_ids.length));
      if (!real.length) return next;
      const existing = next.find((o) => o.op === "delete_rows" && o.row_ids);
      if (existing) {
        return next.map((o) =>
          o === existing ? { ...o, row_ids: uniq([...o.row_ids, ...real]) } : o,
        );
      }
      return [...next, { ...op, row_ids: real }];
    }
    case "set_splits": {
      // Net effect per row: one set_splits op per row, add/remove cancel out.
      const add = uniq(op.add ?? []);
      const remove = uniq(op.remove ?? []).filter((s) => !add.includes(s));
      let next = ops;
      (op.row_ids ?? []).forEach((rowId) => {
        if (isTempRow(next, rowId)) {
          next = next.map((o) =>
            o.op === "add_row" && o.temp_id === rowId
              ? {
                  ...o,
                  split_ids: uniq([...o.split_ids, ...add]).filter(
                    (s) => !remove.includes(s),
                  ),
                }
              : o,
          );
          return;
        }
        const prev = next.find(
          (o) =>
            o.op === "set_splits" &&
            o.row_ids.length === 1 &&
            o.row_ids[0] === rowId,
        );
        const pAdd = new Set(prev?.add ?? []);
        const pRemove = new Set(prev?.remove ?? []);
        add.forEach((s) => (pRemove.has(s) ? pRemove.delete(s) : pAdd.add(s)));
        remove.forEach((s) => (pAdd.has(s) ? pAdd.delete(s) : pRemove.add(s)));
        const rest = next.filter((o) => o !== prev);
        next =
          pAdd.size || pRemove.size
            ? [
                ...rest,
                {
                  op: "set_splits",
                  id: prev?.id ?? nextId(),
                  at: op.at,
                  row_ids: [rowId],
                  add: [...pAdd],
                  remove: [...pRemove],
                },
              ]
            : rest;
      });
      return next;
    }
    case "set_tags": {
      // the interface contract: tags added / removed on rows (pending new rows take them in
      // their own metadata).
      const ids = uniq(op.row_ids ?? []);
      const add = op.add ?? [];
      const remove = op.remove ?? [];
      if (!ids.length || (!add.length && !remove.length)) return ops;
      const newIds = ids.filter((id) => isTempRow(ops, id));
      const savedIds = ids.filter((id) => !isTempRow(ops, id));
      const next = ops.map((o) => {
        if (o.op !== "add_row" || !newIds.includes(o.temp_id)) return o;
        const tags = o.metadata?.tags ?? [];
        return {
          ...o,
          metadata: {
            ...(o.metadata ?? {}),
            tags: [
              ...new Set([...tags.filter((t) => !remove.includes(t)), ...add]),
            ],
          },
        };
      });
      return savedIds.length
        ? [...next, { ...op, row_ids: savedIds, add, remove }]
        : next;
    }
    case "set_environment": {
      // One environment change per change-set: the first `before` (the
      // whole environment) is kept; a patch equal to it drops the op.
      const prior = ops.find((o) => o.op === "set_environment");
      const rest = ops.filter((o) => o.op !== "set_environment");
      const before = prior?.before ?? op.before ?? null;
      const { op: _o, id: _i, at: _a, before: _b, ...patch } = op;
      const unchanged =
        before && Object.keys(patch).every((k) => same(before[k], patch[k]));
      if (unchanged) return rest;
      return [...rest, { ...op, before }];
    }
    default:
      return ops;
  }
}

/** Remove an op by id, or the edit of one cell (`revertOp(ops, rowId, colId)`). */
export function revertOp(ops, opIdOrRowId, colId) {
  if (colId === undefined) {
    const hit = ops.find((o) => o.id === opIdOrRowId);
    if (hit) return ops.filter((o) => o !== hit);
    // A row id alone reverts every pending change on that row.
    return ops
      .filter(
        (o) =>
          !(
            ["update_cell", "set_row_metadata"].includes(o.op) &&
            o.row_id === opIdOrRowId
          ) && !(o.op === "add_row" && o.temp_id === opIdOrRowId),
      )
      .map((o) =>
        o.op === "set_splits" ||
        o.op === "set_status" ||
        o.op === "set_tags" ||
        (o.op === "delete_rows" && o.row_ids)
          ? { ...o, row_ids: o.row_ids.filter((id) => id !== opIdOrRowId) }
          : o,
      )
      .filter(
        (o) =>
          !(
            (o.op === "set_splits" ||
              o.op === "set_status" ||
              o.op === "set_tags" ||
              (o.op === "delete_rows" && o.row_ids)) &&
            !o.row_ids.length
          ),
      );
  }
  if (colId === METADATA_COLUMN)
    return ops.filter(
      (o) => !(o.op === "set_row_metadata" && o.row_id === opIdOrRowId),
    );
  return ops.filter(
    (o) =>
      !(
        o.op === "update_cell" &&
        o.row_id === opIdOrRowId &&
        o.column_id === colId
      ),
  );
}

/**
 * Counts for the PendingChangesBar:
 * `2 added · 5 edited · 1 deleted · 3 split changes`.
 * `deleted` is null for a whole-selection delete (count unknown client-side).
 */
export function countOps(ops) {
  const added = ops.filter((o) => o.op === "add_row").length;
  const edited = ops.filter(
    (o) => o.op === "update_cell" || o.op === "set_row_metadata",
  ).length;
  const sel = ops.find((o) => o.op === "delete_rows" && o.selection);
  const deleted = sel
    ? null
    : ops
        .filter((o) => o.op === "delete_rows")
        .reduce((n, o) => n + (o.row_ids?.length ?? 0), 0);
  const splitRows = new Set(
    ops.filter((o) => o.op === "set_splits").flatMap((o) => o.row_ids),
  );
  const env = ops.find((o) => o.op === "set_environment");
  const statusRows = new Set(
    ops.filter((o) => o.op === "set_status").flatMap((o) => o.row_ids ?? []),
  );
  const tagRows = new Set(
    ops.filter((o) => o.op === "set_tags").flatMap((o) => o.row_ids ?? []),
  );
  return {
    added,
    edited,
    status_changes: statusRows.size,
    tag_changes: tagRows.size,
    deleted,
    split_changes: splitRows.size,
    // R2: `{from, to}` image of the pending environment change, else null.
    environment: env
      ? { from: env.before?.image ?? null, to: env.image ?? null }
      : null,
    total: ops.length,
  };
}

/** `{rowId: {colId: {before, after, opId}}}`; metadata under METADATA_COLUMN. */
export function cellIndex(ops) {
  const out = {};
  ops.forEach((o) => {
    if (o.op === "update_cell") {
      out[o.row_id] = out[o.row_id] ?? {};
      out[o.row_id][o.column_id] = {
        before: o.before,
        after: o.value,
        opId: o.id,
      };
    } else if (o.op === "set_row_metadata") {
      out[o.row_id] = out[o.row_id] ?? {};
      out[o.row_id][METADATA_COLUMN] = {
        before: o.before,
        after: o.metadata,
        opId: o.id,
      };
    }
  });
  return out;
}

/** The N2 `operations` body: local fields (`id`, `at`, `before`) stripped. */
export function toOperations(ops) {
  return ops.map((o) => {
    const { id: _id, at: _at, before: _before, ...rest } = o;
    return rest;
  });
}

/** Ids of pending new rows and rows pending delete. */
export function rowStates(ops) {
  return {
    newRowIds: ops.filter((o) => o.op === "add_row").map((o) => o.temp_id),
    deletedRowIds: uniq(
      ops
        .filter((o) => o.op === "delete_rows" && o.row_ids)
        .flatMap((o) => o.row_ids),
    ),
    deleteSelection:
      ops.find((o) => o.op === "delete_rows" && o.selection)?.selection ?? null,
    // the interface contract: `{rowId: {status, reason}}` of rows with a pending status.
    pendingStatus: Object.fromEntries(
      ops
        .filter((o) => o.op === "set_status")
        .flatMap((o) =>
          (o.row_ids ?? []).map((id) => [
            id,
            { status: o.status, reason: o.reason ?? null },
          ]),
        ),
    ),
  };
}

export const useChangeSetStore = create((set, get) => ({
  /** datasetId -> { baseVersionId, ops[] } */
  sets: {},
  addOp: (datasetId, op) => {
    if (!datasetCaps().pendingChanges) {
      enqueueSnackbar(NOT_SAVED_NO_VERSIONS, {
        variant: "error",
        preventDuplicate: true,
      });
      return false;
    }
    set((s) => {
      const cur = s.sets[datasetId] ?? EMPTY_SET;
      return {
        sets: { ...s.sets, [datasetId]: { ...cur, ops: applyOp(cur.ops, op) } },
      };
    });
    return true;
  },
  revert: (datasetId, opIdOrRowId, colId) =>
    set((s) => {
      const cur = s.sets[datasetId] ?? EMPTY_SET;
      return {
        sets: {
          ...s.sets,
          [datasetId]: { ...cur, ops: revertOp(cur.ops, opIdOrRowId, colId) },
        },
      };
    }),
  discard: (datasetId) =>
    set((s) => {
      const { [datasetId]: _gone, ...rest } = s.sets;
      return { sets: rest };
    }),
  setBaseVersion: (datasetId, baseVersionId) =>
    set((s) => {
      const cur = s.sets[datasetId] ?? EMPTY_SET;
      return { sets: { ...s.sets, [datasetId]: { ...cur, baseVersionId } } };
    }),
  getOps: (datasetId) => get().sets[datasetId]?.ops ?? EMPTY_SET.ops,
}));

const hasPendingOps = (datasetId) =>
  (useChangeSetStore.getState().sets[datasetId]?.ops?.length ?? 0) > 0;

/**
 * the interface contract (DI-03 / DV-01): user columns created while editing (paste's
 * `Create column`, Add column). A column is written at once and is not part
 * of a version, so the pending bar and the Save dialog disclose it and
 * Discard offers to delete it. `cols` = datasetId -> [{id, name, from}]
 * (`from`: "paste" | "add_column").
 *
 * the interface contract review: a column is recorded only while the dataset has pending
 * changes (paste records its columns after its rows are pending), and the
 * record is dropped when the change-set empties (saved, discarded, undone),
 * so Discard never offers to delete a column added outside these changes.
 */
export const useCreatedColumnsStore = create((set, get) => ({
  cols: {},
  add: (datasetId, column) => {
    if (!hasPendingOps(datasetId)) return;
    set((s) => ({
      cols: {
        ...s.cols,
        [datasetId]: [
          ...(s.cols[datasetId] ?? []).filter((c) => c.id !== column.id),
          {
            id: column.id,
            name: column.name,
            from: column.from ?? "add_column",
          },
        ],
      },
    }));
  },
  remove: (datasetId, id) =>
    set((s) => ({
      cols: {
        ...s.cols,
        [datasetId]: (s.cols[datasetId] ?? []).filter((c) => c.id !== id),
      },
    })),
  clear: (datasetId) =>
    set((s) => {
      const { [datasetId]: _gone, ...rest } = s.cols;
      return { cols: rest };
    }),
  of: (datasetId) => get().cols[datasetId] ?? [],
}));

// The change-set emptied (saved, discarded or undone): its columns are no
// longer "added with these changes".
useChangeSetStore.subscribe((state) => {
  const { cols, clear } = useCreatedColumnsStore.getState();
  Object.keys(cols).forEach((datasetId) => {
    if (!(state.sets[datasetId]?.ops?.length > 0)) clear(datasetId);
  });
});

/**
 * Discard's `Also delete …` starts ticked only when every column came from
 * paste (they hold the pasted rows' cells); a column added with Add column
 * while changes were pending is kept unless the user ticks it.
 */
export const dropCreatedColumnsByDefault = (cols = []) =>
  cols.length > 0 && cols.every((c) => c.from === "paste");

/** `column “urgency”` · `columns “a” and “b”` · `3 columns`. */
export function createdColumnsText(cols = []) {
  const names = cols.map((c) => `“${c.name}”`);
  if (!names.length) return "";
  if (names.length === 1) return `column ${names[0]}`;
  if (names.length <= 3)
    return `columns ${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
  return `${names.length} columns`;
}

/** True when any dataset has pending ops (for a beforeunload warning). */
export const anyPendingChanges = () =>
  Object.values(useChangeSetStore.getState().sets).some((s) => s.ops.length);

/**
 * Reload and tab close warn while edits are pending (the app uses a
 * BrowserRouter, so in-app navigation keeps them instead: this store is not
 * in resetAllStates()).
 */
export function warnBeforeUnload(event) {
  if (!anyPendingChanges()) return undefined;
  event.preventDefault();
  // Chrome needs returnValue set; the browser shows its own text.
  event.returnValue = "";
  return "";
}

if (typeof window !== "undefined" && !window.__dsxChangeSetUnloadGuard) {
  window.__dsxChangeSetUnloadGuard = true;
  window.addEventListener("beforeunload", warnBeforeUnload);
}

/**
 * The change-set of one dataset.
 * @returns {{ops: object[], counts: object, byCell: object, hasPending: boolean,
 *   baseVersionId: string|null, newRowIds: string[], deletedRowIds: string[],
 *   deleteSelection: object|null, addOp: (op: object) => void,
 *   revert: (opIdOrRowId: string, colId?: string) => void,
 *   discard: () => void, setBaseVersion: (id: string) => void,
 *   toOperations: () => object[]}}
 */
export function useChangeSet(datasetId) {
  const { cur, addOp, revert, discard, setBaseVersion } = useChangeSetStore(
    useShallow((s) => ({
      cur: s.sets[datasetId] ?? EMPTY_SET,
      addOp: s.addOp,
      revert: s.revert,
      discard: s.discard,
      setBaseVersion: s.setBaseVersion,
    })),
  );
  return useMemo(() => {
    const ops = cur.ops;
    return {
      ops,
      baseVersionId: cur.baseVersionId,
      counts: countOps(ops),
      byCell: cellIndex(ops),
      hasPending: ops.length > 0,
      ...rowStates(ops),
      addOp: (op) => addOp(datasetId, op),
      revert: (opIdOrRowId, colId) => revert(datasetId, opIdOrRowId, colId),
      discard: () => discard(datasetId),
      setBaseVersion: (id) => setBaseVersion(datasetId, id),
      toOperations: () => toOperations(ops),
    };
  }, [cur, datasetId, addOp, revert, discard, setBaseVersion]);
}
