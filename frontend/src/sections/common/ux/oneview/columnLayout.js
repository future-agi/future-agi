// Column shaping, pure (ONE-VIEW-SPEC §3.2). No React. A screen passes its
// column catalog; the user's choices live in layout prefs `columns`:
//   {visible: {id: bool}, pinned: {id: "left"|null}, order: [ids]|undefined,
//    widths: {id: px}}
// Only differences from the catalog defaults are stored, so an untouched
// layout is `{}` and `isDefaultLayout` is cheap. Unknown ids in stored prefs
// are dropped; new catalog columns appear at their catalog position with
// their `defaultVisible`.
//
// Column definition:
//   {id, label, group?, defaultVisible = true, locked = false (always shown,
//    never hidden or moved, e.g. Name), pinnable = true,
//    defaultPinned?: "left", sortable?, groupable?, description?, width?}

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

/** Column defaults filled in. */
export const columnDefaults = (c) => ({
  ...c,
  label: c.label ?? c.id,
  defaultVisible: c.locked ? true : c.defaultVisible !== false,
  locked: Boolean(c.locked),
  pinnable: c.pinnable !== false,
  defaultPinned: c.defaultPinned === "left" ? "left" : null,
});

/** Stored column prefs made safe (drops unknown ids, wrong types). */
export function normalizeColumnPrefs(prefs, columns) {
  const known = new Set((columns ?? []).map((c) => c.id));
  const p = isObj(prefs) ? prefs : {};
  const pick = (obj, ok) =>
    Object.fromEntries(
      Object.entries(isObj(obj) ? obj : {}).filter(
        ([id, v]) => known.has(id) && ok(v),
      ),
    );
  const out = {};
  const visible = pick(p.visible, (v) => typeof v === "boolean");
  const pinned = pick(p.pinned, (v) => v === "left" || v === null);
  const widths = pick(
    p.widths,
    (v) => typeof v === "number" && Number.isFinite(v) && v > 0,
  );
  if (Object.keys(visible).length) out.visible = visible;
  if (Object.keys(pinned).length) out.pinned = pinned;
  if (Object.keys(widths).length) out.widths = widths;
  if (Array.isArray(p.order)) {
    const order = [...new Set(p.order.filter((id) => known.has(id)))];
    if (order.length) out.order = order;
  }
  return out;
}

/**
 * The display order of the unlocked + locked columns: stored order for known
 * ids, new columns inserted after their catalog predecessor, locked columns
 * at their catalog index, then pinned-left first (stable).
 */
function orderedIds(cols, prefs) {
  const catalog = cols.map((c) => c.id);
  const lockedIdx = new Map(
    cols.map((c, i) => [c.id, i]).filter(([id]) => isLocked(cols, id)),
  );
  const movable = catalog.filter((id) => !lockedIdx.has(id));
  let order = movable;
  if (prefs.order?.length) {
    const stored = prefs.order.filter((id) => movable.includes(id));
    const result = [...stored];
    movable.forEach((id, i) => {
      if (result.includes(id)) return;
      // A column the stored order does not know: after its nearest catalog
      // predecessor that is placed, else first.
      let at = 0;
      for (let j = i - 1; j >= 0; j -= 1) {
        const k = result.indexOf(movable[j]);
        if (k >= 0) {
          at = k + 1;
          break;
        }
      }
      result.splice(at, 0, id);
    });
    order = result;
  }
  const full = [...order];
  [...lockedIdx.entries()]
    .sort((a, b) => a[1] - b[1])
    .forEach(([id, i]) => full.splice(Math.min(i, full.length), 0, id));
  return full;
}

const isLocked = (cols, id) => Boolean(cols.find((c) => c.id === id)?.locked);

/**
 * Resolved columns, in display order:
 * `{...def, hidden, pinned: "left"|null, width: number|null, index}`.
 * `width` is only the user's width (null = the screen's own sizing).
 */
export function resolveColumns(columns, prefs) {
  const cols = (columns ?? []).map(columnDefaults);
  const p = normalizeColumnPrefs(prefs, cols);
  const byId = new Map(cols.map((c) => [c.id, c]));
  const ids = orderedIds(cols, p);
  const resolved = ids.map((id) => {
    const c = byId.get(id);
    const vis = c.locked ? true : p.visible?.[id] ?? c.defaultVisible;
    const pin =
      c.pinnable && p.pinned && id in p.pinned ? p.pinned[id] : c.defaultPinned;
    return {
      ...c,
      hidden: !vis,
      pinned: pin === "left" ? "left" : null,
      width: p.widths?.[id] ?? null,
    };
  });
  const sorted = [
    ...resolved.filter((c) => c.pinned === "left"),
    ...resolved.filter((c) => c.pinned !== "left"),
  ];
  return sorted.map((c, index) => ({ ...c, index }));
}

/** Drop overrides that equal the catalog default; drop a no-op order. */
export function compactColumnPrefs(prefs, columns) {
  const cols = (columns ?? []).map(columnDefaults);
  const p = normalizeColumnPrefs(prefs, cols);
  const byId = new Map(cols.map((c) => [c.id, c]));
  const out = {};
  const visible = Object.fromEntries(
    Object.entries(p.visible ?? {}).filter(
      ([id, v]) => !byId.get(id).locked && v !== byId.get(id).defaultVisible,
    ),
  );
  const pinned = Object.fromEntries(
    Object.entries(p.pinned ?? {}).filter(
      ([id, v]) =>
        byId.get(id).pinnable && (v ?? null) !== byId.get(id).defaultPinned,
    ),
  );
  if (Object.keys(visible).length) out.visible = visible;
  if (Object.keys(pinned).length) out.pinned = pinned;
  if (p.widths) out.widths = p.widths;
  if (p.order) {
    // Canonical: the stored order IS the display order (pinned first), and
    // it is dropped when the display equals the catalog's.
    const movable = new Set(cols.filter((c) => !c.locked).map((c) => c.id));
    const { order: _o, ...rest } = p;
    const def = resolveColumns(cols, rest).map((c) => c.id);
    const mine = resolveColumns(cols, p).map((c) => c.id);
    if (mine.join("|") !== def.join("|"))
      out.order = mine.filter((id) => movable.has(id));
  }
  return out;
}

/** True when the prefs change nothing. */
export const isDefaultLayout = (columns, prefs) =>
  Object.keys(compactColumnPrefs(prefs, columns)).length === 0;

const withField = (prefs, field, id, value) => ({
  ...(isObj(prefs) ? prefs : {}),
  [field]: { ...(prefs?.[field] ?? {}), [id]: value },
});

/** Hide a column (locked columns stay). Returns new prefs. */
export function hideColumn(prefs, columns, id) {
  const c = (columns ?? []).find((x) => x.id === id);
  if (!c || c.locked) return prefs ?? {};
  return compactColumnPrefs(withField(prefs, "visible", id, false), columns);
}

/** Show a column. */
export function showColumn(prefs, columns, id) {
  const c = (columns ?? []).find((x) => x.id === id);
  if (!c) return prefs ?? {};
  return compactColumnPrefs(withField(prefs, "visible", id, true), columns);
}

/** Show every column. */
export function showAllColumns(prefs, columns) {
  const visible = Object.fromEntries((columns ?? []).map((c) => [c.id, true]));
  return compactColumnPrefs({ ...(prefs ?? {}), visible }, columns);
}

/** Toggle a column's visibility. */
export function toggleColumn(prefs, columns, id) {
  const r = resolveColumns(columns, prefs).find((c) => c.id === id);
  if (!r) return prefs ?? {};
  return r.hidden
    ? showColumn(prefs, columns, id)
    : hideColumn(prefs, columns, id);
}

/** Order prefs from a full list of ids (only movable ones are stored). */
function withOrder(prefs, columns, ids) {
  const cols = (columns ?? []).map(columnDefaults);
  const movable = new Set(cols.filter((c) => !c.locked).map((c) => c.id));
  return compactColumnPrefs(
    { ...(prefs ?? {}), order: ids.filter((id) => movable.has(id)) },
    columns,
  );
}

/**
 * Move a column to `toIndex` of the resolved order (hidden columns count).
 * Locked columns never move.
 */
export function moveColumn(prefs, columns, id, toIndex) {
  const resolved = resolveColumns(columns, prefs);
  const from = resolved.findIndex((c) => c.id === id);
  if (from < 0 || resolved[from].locked) return prefs ?? {};
  const ids = resolved.map((c) => c.id);
  ids.splice(from, 1);
  const to = Math.max(0, Math.min(Number(toIndex) || 0, ids.length));
  ids.splice(to, 0, id);
  return withOrder(prefs, columns, ids);
}

/**
 * Can `id` move by `delta` (±1) among VISIBLE columns of its pin group?
 * @returns {{ok: boolean, reason?: string, targetId?: string}}
 */
export function canMoveColumnBy(columns, prefs, id, delta) {
  const resolved = resolveColumns(columns, prefs);
  const me = resolved.find((c) => c.id === id);
  if (!me) return { ok: false, reason: "Unknown column" };
  if (me.locked) return { ok: false, reason: `${me.label} keeps its place` };
  const peers = resolved.filter((c) => !c.hidden && c.pinned === me.pinned);
  const i = peers.findIndex((c) => c.id === id);
  const target = peers[i + (delta < 0 ? -1 : 1)];
  if (!target || target.locked)
    return { ok: false, reason: delta < 0 ? "Already first" : "Already last" };
  return { ok: true, targetId: target.id };
}

/** Move `id` one visible place left (-1) or right (+1). */
export function moveColumnBy(prefs, columns, id, delta) {
  const can = canMoveColumnBy(columns, prefs, id, delta);
  if (!can.ok) return prefs ?? {};
  const resolved = resolveColumns(columns, prefs);
  const ids = resolved.map((c) => c.id).filter((x) => x !== id);
  const t = ids.indexOf(can.targetId);
  ids.splice(delta < 0 ? t : t + 1, 0, id);
  return withOrder(prefs, columns, ids);
}

/** Freeze (`"left"`) or unfreeze (`null`) a column. */
export function pinColumn(prefs, columns, id, side) {
  const c = (columns ?? []).map(columnDefaults).find((x) => x.id === id);
  if (!c || !c.pinnable) return prefs ?? {};
  return compactColumnPrefs(
    withField(prefs, "pinned", id, side === "left" ? "left" : null),
    columns,
  );
}

/**
 * IT-R5 G3 (S3): `Freeze up to here` — every shown, freezable column from
 * the first through `id` (in view order) is frozen. Pure.
 */
export function pinThroughColumn(prefs, columns, id) {
  const order = resolveColumns(columns, prefs).filter((c) => !c.hidden);
  const at = order.findIndex((c) => c.id === id);
  if (at < 0) return prefs ?? {};
  return order
    .slice(0, at + 1)
    .filter((c) => c.pinnable && c.pinned !== "left")
    .reduce((acc, c) => pinColumn(acc, columns, c.id, "left"), prefs ?? {});
}

/** Set a user width (px); null clears it. */
export function setColumnWidth(prefs, columns, id, px) {
  const widths = { ...(prefs?.widths ?? {}) };
  if (px == null) delete widths[id];
  else widths[id] = Math.round(px);
  return compactColumnPrefs({ ...(prefs ?? {}), widths }, columns);
}

/** Back to the catalog defaults. */
export const resetColumns = () => ({});

// ------------------------------------------------------------- adapters

/**
 * AG Grid column state for `api.applyColumnState({state, applyOrder: true})`.
 * `width` only when the user set one (flex columns keep flexing).
 */
export function toAgColumnState(resolved) {
  return (resolved ?? []).map((c) => {
    const s = { colId: c.id, hide: Boolean(c.hidden), pinned: c.pinned };
    if (c.width) s.width = c.width;
    return s;
  });
}

/**
 * Column prefs from AG Grid column state (`api.getColumnState()`); columns
 * AG knows but the catalog does not (selection, auto group) are ignored.
 * @param {{includeWidths?: boolean}} [opts]  widths only after a resize
 */
export function fromAgColumnState(state, columns, opts = {}) {
  const cols = (columns ?? []).map(columnDefaults);
  const known = new Set(cols.map((c) => c.id));
  const rows = (state ?? []).filter((s) => known.has(s.colId));
  const prefs = {
    order: rows.map((s) => s.colId),
    visible: Object.fromEntries(rows.map((s) => [s.colId, !s.hide])),
    pinned: Object.fromEntries(
      rows.map((s) => [s.colId, s.pinned === "left" ? "left" : null]),
    ),
  };
  if (opts.includeWidths)
    prefs.widths = Object.fromEntries(
      rows.filter((s) => s.width > 0).map((s) => [s.colId, s.width]),
    );
  return compactColumnPrefs(prefs, columns);
}

/**
 * TanStack Table state: `{columnVisibility, columnOrder, columnPinning,
 * columnSizing}`.
 */
export function toTanstackState(resolved) {
  const list = resolved ?? [];
  return {
    columnVisibility: Object.fromEntries(list.map((c) => [c.id, !c.hidden])),
    columnOrder: list.map((c) => c.id),
    columnPinning: {
      left: list.filter((c) => c.pinned === "left").map((c) => c.id),
      right: [],
    },
    columnSizing: Object.fromEntries(
      list.filter((c) => c.width).map((c) => [c.id, c.width]),
    ),
  };
}

/** Column prefs from TanStack state (round-trip of `toTanstackState`). */
export function fromTanstackState(state, columns) {
  const s = state ?? {};
  return compactColumnPrefs(
    {
      order: s.columnOrder,
      visible: s.columnVisibility,
      pinned: Object.fromEntries(
        (columns ?? []).map((c) => [
          c.id,
          (s.columnPinning?.left ?? []).includes(c.id) ? "left" : null,
        ]),
      ),
      widths: s.columnSizing,
    },
    columns,
  );
}

/**
 * Rows in the order the panel opened with (IT-R1 G5 P11): freezing or
 * hiding a column never moves rows under the cursor; new columns go last;
 * `resync` (after a drag or Alt+↑/↓) takes the layout's order. Grouped
 * lists keep each group together, in the caller's group order, so a
 * heading never repeats. Pure.
 */
export function stableColumnRows(columns, order, groupOrder = []) {
  const byId = new Map((columns ?? []).map((c) => [c.id, c]));
  const seen = new Set(order ?? []);
  const rows = [
    ...(order ?? []).map((id) => byId.get(id)).filter(Boolean),
    ...(columns ?? []).filter((c) => !seen.has(c.id)),
  ];
  if (!rows.some((c) => c.group)) return rows;
  const groups = [...groupOrder];
  rows.forEach((c) => {
    if (!groups.includes(c.group ?? null)) groups.push(c.group ?? null);
  });
  return groups.flatMap((g) => rows.filter((c) => (c.group ?? null) === g));
}
