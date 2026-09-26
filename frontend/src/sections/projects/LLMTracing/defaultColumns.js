const makeColumn = (id, name, isVisible = true) => ({
  id,
  name,
  isVisible,
  groupBy: null,
});

// Keep these finite presentation fallbacks aligned with
// tracer.utils.helper.get_default_trace_config/get_default_span_config. The
// list APIs still enrich/replace them with eval and annotation columns; these
// defaults make the shared Display state usable before (or without) that
// response instead of leaving AG Grid's private fallback headers as the only
// source of column metadata.
export const TRACE_BUILT_IN_COLUMNS = [
  makeColumn("trace_name", "Trace Name"),
  makeColumn("input", "Input"),
  makeColumn("output", "Output"),
  makeColumn("start_time", "Timestamp"),
  makeColumn("status", "Status"),
  makeColumn("latency", "Latency"),
  makeColumn("total_tokens", "Tokens"),
  makeColumn("cost", "Total Cost"),
  makeColumn("model", "Model"),
  makeColumn("tags", "Tags"),
  makeColumn("user_id", "User Id"),
  makeColumn("trace_id", "Trace Id", false),
  makeColumn("prompt_tokens", "Prompt Tokens", false),
  makeColumn("completion_tokens", "Completion Tokens", false),
  makeColumn("provider", "Provider", false),
  makeColumn("session_id", "Session Id", false),
];

export const SPAN_BUILT_IN_COLUMNS = [
  makeColumn("span_name", "Span Name"),
  makeColumn("status", "Status"),
  makeColumn("input", "Input"),
  makeColumn("output", "Output"),
  makeColumn("latency_ms", "Duration"),
  makeColumn("total_tokens", "Tokens"),
  makeColumn("cost", "Total Cost"),
  makeColumn("model", "Model"),
  makeColumn("start_time", "Timestamp"),
  makeColumn("span_id", "Span Id", false),
  makeColumn("trace_id", "Trace Id", false),
  makeColumn("prompt_tokens", "Prompt Tokens", false),
  makeColumn("completion_tokens", "Completion Tokens", false),
  makeColumn("provider", "Provider", false),
];

const cloneColumns = (columns) => columns.map((column) => ({ ...column }));

const tabSlotKeys = (tab) => {
  const suffix = tab === "spans" ? "spans" : "trace";
  return [`primary-${suffix}`, `compare-${suffix}`];
};

const customColumnsFromSlots = (columnsBySlot, tab) => {
  const seen = new Set();
  return tabSlotKeys(tab).flatMap((slotKey) =>
    (columnsBySlot[slotKey] || []).filter((column) => {
      if (
        column?.groupBy !== "Custom Columns" ||
        !column.id ||
        seen.has(column.id)
      ) {
        return false;
      }
      seen.add(column.id);
      return true;
    }),
  );
};

export const createInitialTracingColumns = () => ({
  "primary-trace": cloneColumns(TRACE_BUILT_IN_COLUMNS),
  "compare-trace": cloneColumns(TRACE_BUILT_IN_COLUMNS),
  "primary-spans": cloneColumns(SPAN_BUILT_IN_COLUMNS),
  "compare-spans": cloneColumns(SPAN_BUILT_IN_COLUMNS),
});

export const addCustomColumnsToTab = (columnsBySlot, tab, newColumns) => {
  const requestedById = new Map();
  (newColumns || []).forEach((column) => {
    if (column?.id && !requestedById.has(column.id)) {
      requestedById.set(column.id, column);
    }
  });
  if (requestedById.size === 0) return columnsBySlot;
  let changed = false;
  const next = { ...columnsBySlot };
  tabSlotKeys(tab).forEach((slotKey) => {
    const slot = columnsBySlot[slotKey] || [];
    const seen = new Set();
    let slotChanged = false;
    const promoted = slot.map((column) => {
      seen.add(column.id);
      const requested = requestedById.get(column.id);
      if (!requested || column.groupBy === "Custom Columns") return column;
      slotChanged = true;
      // Preserve authoritative field/name/renderer metadata; the Custom marker
      // records the user's persisted visibility override for this base id.
      return {
        ...column,
        isVisible: requested.isVisible !== false,
        groupBy: "Custom Columns",
      };
    });
    const additions = [...requestedById.values()]
      .filter((column) => !seen.has(column.id))
      .map((column) => ({ ...column }));
    if (!slotChanged && additions.length === 0) return;
    changed = true;
    next[slotKey] = [...promoted, ...additions];
  });
  return changed ? next : columnsBySlot;
};

export const removeCustomColumnsFromTab = (
  columnsBySlot,
  tab,
  removeIds,
  canonicalColumns = [],
) => {
  const removeSet = new Set(removeIds || []);
  if (removeSet.size === 0) return columnsBySlot;
  const canonicalById = new Map(
    (canonicalColumns || []).map((column) => [column.id, column]),
  );
  let changed = false;
  const next = { ...columnsBySlot };
  tabSlotKeys(tab).forEach((slotKey) => {
    const slot = columnsBySlot[slotKey] || [];
    let slotChanged = false;
    const filtered = slot.flatMap((column) => {
      if (column?.groupBy !== "Custom Columns" || !removeSet.has(column.id)) {
        return [column];
      }
      slotChanged = true;
      const canonical = canonicalById.get(column.id);
      return canonical ? [{ ...canonical }] : [];
    });
    if (!slotChanged) return;
    changed = true;
    next[slotKey] = filtered;
  });
  return changed ? next : columnsBySlot;
};

export const resetColumnsForTab = (
  columnsBySlot,
  tab,
  canonicalColumns = [],
) => {
  if ((canonicalColumns || []).length === 0) {
    return removeCustomColumnsFromTab(
      columnsBySlot,
      tab,
      customColumnsFromSlots(columnsBySlot, tab).map((column) => column.id),
    );
  }
  const next = { ...columnsBySlot };
  tabSlotKeys(tab).forEach((slotKey) => {
    next[slotKey] = canonicalColumns.map((column) => ({ ...column }));
  });
  return next;
};

// Union both graph slots when serializing. This migrates any legacy
// primary/compare asymmetry without allowing a graph switch to overwrite a
// valid selection with the other slot's empty list.
export const getCustomColumnsByTab = (columnsBySlot) => ({
  trace: customColumnsFromSlots(columnsBySlot, "trace"),
  spans: customColumnsFromSlots(columnsBySlot, "spans"),
});

// A saved-view switch clears the previous view's custom selections before
// applying the next view. A custom id may also be a canonical hidden column,
// so raw filtering would remove that first-class field until another schema
// callback happened. Reconcile against the pristine config as part of clear.
export const clearSavedViewCustomColumns = (
  columnsBySlot,
  canonicalTraceColumns,
  canonicalSpanColumns,
) => {
  const next = {};
  Object.keys(columnsBySlot).forEach((slotKey) => {
    const canonicalColumns = slotKey.includes("spans")
      ? canonicalSpanColumns
      : canonicalTraceColumns;
    const nonCustom = (columnsBySlot[slotKey] || []).filter(
      (column) => column?.groupBy !== "Custom Columns",
    );
    next[slotKey] = mergeAuthoritativeNonCustomColumns(
      nonCustom,
      canonicalColumns,
    );
  });
  return next;
};

// Replace fallback/API metadata while retaining the user's current order and
// visibility. This is intentionally separate from custom columns, which are
// persisted by the parent and appended after this merge.
export const mergeAuthoritativeNonCustomColumns = (current, authoritative) => {
  const currentColumns = (current || []).filter(
    (column) => column?.groupBy !== "Custom Columns",
  );
  const authoritativeColumns = (authoritative || []).filter(
    (column) => column?.groupBy !== "Custom Columns",
  );
  const authoritativeById = new Map(
    authoritativeColumns.map((column) => [column.id, column]),
  );
  const seen = new Set();
  const kept = currentColumns
    .filter((column) => authoritativeById.has(column.id))
    .map((column) => {
      seen.add(column.id);
      return {
        ...authoritativeById.get(column.id),
        isVisible: column.isVisible !== false,
      };
    });
  const added = authoritativeColumns
    .filter((column) => !seen.has(column.id))
    .map((column) => ({ ...column }));
  return [...kept, ...added];
};

// Merge the pristine config with persisted/custom selections without ever
// producing duplicate ids. If an attribute is promoted to a first-class API
// column (voice `user_interruption_count` is one example), retain the custom
// marker as a persisted visibility override but use the authoritative field,
// label, and renderer metadata. CallLogsGrid then controls its existing base
// definition instead of creating a second AG Grid column with the same colId.
export const mergeColumnsWithAuthoritativeConfig = (
  current,
  authoritative,
  pendingCustomColumns = [],
  { preserveCurrentOrder = true } = {},
) => {
  const currentColumns = current || [];
  const authoritativeById = new Map(
    (authoritative || []).map((column) => [column.id, column]),
  );
  const orderColumns = currentColumns.map((column) => {
    const authoritativeColumn = authoritativeById.get(column.id);
    return column?.groupBy === "Custom Columns" && authoritativeColumn
      ? { ...column, groupBy: authoritativeColumn.groupBy }
      : column;
  });
  const base = preserveCurrentOrder
    ? mergeAuthoritativeNonCustomColumns(orderColumns, authoritative)
    : (authoritative || [])
        .filter((column) => column?.groupBy !== "Custom Columns")
        .map((column) => ({ ...column }));
  const candidates = [
    ...currentColumns.filter((column) => column?.groupBy === "Custom Columns"),
    ...(pendingCustomColumns || []),
  ];
  const seenCustom = new Set();
  const baseIndexById = new Map(
    base.map((column, index) => [column.id, index]),
  );
  const custom = [];
  candidates.forEach((column) => {
    if (!column?.id || seenCustom.has(column.id)) return;
    seenCustom.add(column.id);
    const baseIndex = baseIndexById.get(column.id);
    if (baseIndex !== undefined) {
      base[baseIndex] = {
        ...base[baseIndex],
        isVisible: column.isVisible !== false,
        groupBy: "Custom Columns",
      };
      return;
    }
    custom.push({ ...column });
  });
  return [...base, ...custom];
};

// Config callbacks provide the pristine API/default state before a saved view
// is overlaid. Keep both visibility and order together so leaving a saved view
// can faithfully restore trace, span, and voice grids (including hidden ids).
export const getCanonicalColumnSnapshot = (columns) => {
  const canonicalColumns = (columns || [])
    .filter((column) => column?.groupBy !== "Custom Columns" && column?.id)
    .map((column) => ({
      ...column,
      isVisible: column.isVisible !== false,
    }));
  return {
    columns: canonicalColumns,
    order: canonicalColumns.map((column) => column.id),
  };
};

export const clearSavedColumnHydrationRefs = ({
  pendingColumnStateRef,
  pendingSavedColsRef,
  appliedIdSetKeyRef,
  userToggledColsRef,
}) => {
  if (pendingColumnStateRef) pendingColumnStateRef.current = null;
  if (pendingSavedColsRef) pendingSavedColsRef.current = null;
  if (appliedIdSetKeyRef) appliedIdSetKeyRef.current = null;
  if (userToggledColsRef) userToggledColsRef.current = new Set();
};

export const restoreCanonicalColumnVisibility = (columns, canonical) => {
  const visibilityById = new Map(
    (canonical || []).map((column) => [column.id, column.isVisible !== false]),
  );
  return (columns || []).map((column) => {
    const isVisible = visibilityById.has(column.id)
      ? visibilityById.get(column.id)
      : true;
    return column.isVisible === isVisible ? column : { ...column, isVisible };
  });
};
