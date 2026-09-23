import { useCallback, useEffect, useMemo, useState } from "react";
import { defaultVisibleIds, allEligibleIds, SECTIONS } from "./panelRegistry";

const DEFAULT_SECTION_ORDER = SECTIONS.map((s) => s.id);

/**
 * Layout state for the single-run analytics tab.
 *
 * Model: an ordered list of visible panel ids + a bag of custom
 * widget definitions. Persisted per env surface so a voice run and a
 * chat run remember different arrangements.
 *
 * Named views: multiple layouts saved under user-picked labels, shown
 * as tabs. The "active view" is the one currently rendered; every edit
 * saves straight into it.
 *
 * Storage: localStorage. No backend so the shipped surface can grow
 * without dragging schema migrations. When the team wants team-shared
 * views this whole hook grows a fetch/mutate layer and the render
 * side doesn't change.
 */

const STORAGE_KEY = (surface) => `simulate-v2:run-analytics-layout:${surface || "generic"}`;
const CUSTOM_STORAGE_KEY = (surface) => `simulate-v2:run-analytics-custom:${surface || "generic"}`;

/**
 * Shape of storage:
 *   {
 *     activeView: "default" | string,
 *     views: {
 *       [viewName]: { order: string[], custom: Array<CustomWidget> },
 *     },
 *   }
 * A missing entry falls back to the surface-appropriate default order.
 */
function readStorage(surface) {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY(surface));
    if (!raw) return null;
    return JSON.parse(raw);
  } catch { return null; }
}

function writeStorage(surface, state) {
  try {
    window.localStorage.setItem(STORAGE_KEY(surface), JSON.stringify(state));
  } catch { /* quota etc — best effort */ }
}

const DEFAULT_VIEW_NAME = "Default";

function makeDefault(surface) {
  return {
    order: defaultVisibleIds(surface),
    custom: [],
    /* Per-panel span overrides — { [panelId]: number }. A missing
       entry falls back to the registry's defaultSpan. Users widen
       a panel from the kebab menu; the override survives reloads. */
    spans: {},
    /* Per-panel section overrides — { [panelId]: sectionId }. When a
       user drags a widget out of its registry section, this remembers
       the new home so it doesn't snap back on the next render. */
    sectionOverrides: {},
    /* Per-panel config overrides. `defaultConfig` is what shipped
       (or, for custom widgets, what the user saved at creation);
       `overrides[id]` is a partial patch merged on top at render
       time. `hasOverrides(id)` powers the "modified" dot on the
       widget header; `resetWidget(id)` empties the entry. */
    overrides: {},
    /* Ordered list of section ids. Defaults to the registry's order.
       Users can drag section headers to reorder whole sections
       (Trends → above Breakdowns, etc.) without touching individual
       widgets. */
    sectionOrder: [...DEFAULT_SECTION_ORDER],
    /* Only a user drag pins the section order; otherwise it tracks the
       registry default so re-ordered defaults reach saved layouts. */
    sectionOrderCustom: false,
    knownIds: allEligibleIds(surface),
  };
}

function hydrate(surface) {
  const stored = readStorage(surface);
  if (!stored || typeof stored !== "object") {
    return {
      activeView: DEFAULT_VIEW_NAME,
      views: { [DEFAULT_VIEW_NAME]: makeDefault(surface) },
    };
  }
  /* Reconcile against the current registry — a panel that was hidden
     and then removed from the codebase shouldn't hang around as a
     ghost id, and a new panel added since the user last edited
     should appear at the bottom of the visible list so they don't
     miss it. */
  const eligible = new Set(allEligibleIds(surface));
  const views = { ...stored.views };
  if (!views[DEFAULT_VIEW_NAME]) views[DEFAULT_VIEW_NAME] = makeDefault(surface);
  Object.keys(views).forEach((name) => {
    const v = views[name] || {};
    const custom = Array.isArray(v.custom) ? v.custom.filter(Boolean) : [];
    const customIds = new Set(custom.map((w) => w.id));
    const currentOrder = Array.isArray(v.order) ? v.order : [];
    const cleaned = currentOrder.filter((id) => eligible.has(id) || customIds.has(id));
    const seen = new Set(cleaned);
    /* Append built-in panels shipped since this view was saved. A panel
       the view already knew about and isn't in `order` was hidden on
       purpose, so it stays hidden. */
    const known = Array.isArray(v.knownIds) ? new Set(v.knownIds) : null;
    defaultVisibleIds(surface).forEach((id) => {
      if (!seen.has(id) && !known?.has(id)) cleaned.push(id);
    });
    /* Section order is stored per-view too. Reconcile against
       DEFAULT_SECTION_ORDER — drop unknown sections (renamed /
       removed in code) and append any new registry sections at
       the bottom so users notice them without losing their layout. */
    const sectionOrderCustom = !!v.sectionOrderCustom && Array.isArray(v.sectionOrder);
    const rawSectionOrder = sectionOrderCustom ? v.sectionOrder : [...DEFAULT_SECTION_ORDER];
    const knownSections = new Set(DEFAULT_SECTION_ORDER);
    const seenSections = new Set();
    const cleanedSectionOrder = [];
    rawSectionOrder.forEach((sid) => {
      if (knownSections.has(sid) && !seenSections.has(sid)) {
        cleanedSectionOrder.push(sid); seenSections.add(sid);
      }
    });
    DEFAULT_SECTION_ORDER.forEach((sid) => { if (!seenSections.has(sid)) cleanedSectionOrder.push(sid); });
    views[name] = {
      order: cleaned, custom,
      spans: v.spans && typeof v.spans === "object" ? v.spans : {},
      sectionOverrides: v.sectionOverrides && typeof v.sectionOverrides === "object" ? v.sectionOverrides : {},
      overrides: v.overrides && typeof v.overrides === "object" ? v.overrides : {},
      sectionOrder: cleanedSectionOrder,
      sectionOrderCustom,
      knownIds: allEligibleIds(surface),
    };
  });
  const activeView = views[stored.activeView] ? stored.activeView : DEFAULT_VIEW_NAME;
  return { activeView, views };
}

export function useRunLayout({ surface }) {
  const [state, setState] = useState(() => hydrate(surface));

  /* Rehydrate if the surface changes (e.g., user opens a chat run
     after a voice one in the same session). */
  useEffect(() => { setState(hydrate(surface)); }, [surface]);

  const persist = useCallback((next) => {
    writeStorage(surface, next);
    return next;
  }, [surface]);

  const currentView = state.views[state.activeView] || makeDefault(surface);
  const order = currentView.order;
  const custom = currentView.custom;
  const spans = currentView.spans || {};
  const sectionOverrides = currentView.sectionOverrides || {};
  const overrides = currentView.overrides || {};
  const sectionOrder = currentView.sectionOrder || [...DEFAULT_SECTION_ORDER];

  const visibleIds = order;
  const hiddenIds = useMemo(() => {
    const visible = new Set(order);
    const customIds = new Set(custom.map((w) => w.id));
    /* Hidden = every built-in that's not currently in the order.
       Custom widgets that aren't in the order are also hidden. */
    return [
      ...allEligibleIds(surface).filter((id) => !visible.has(id)),
      ...custom.map((w) => w.id).filter((id) => !visible.has(id)),
    ];
  }, [order, custom, surface]);

  /* ── mutations ───────────────────────────────────────────────── */
  const patchActiveView = (mutator) => {
    setState((prev) => {
      const view = prev.views[prev.activeView] || makeDefault(surface);
      const nextView = mutator(view);
      const next = { ...prev, views: { ...prev.views, [prev.activeView]: nextView } };
      persist(next);
      return next;
    });
  };

  const move = (id, toIndex) => patchActiveView((v) => {
    const withoutId = v.order.filter((x) => x !== id);
    const clamped = Math.max(0, Math.min(toIndex, withoutId.length));
    withoutId.splice(clamped, 0, id);
    return { ...v, order: withoutId };
  });

  const reorder = (nextOrder) => patchActiveView((v) => ({ ...v, order: nextOrder }));

  const hide = (id) => patchActiveView((v) => ({ ...v, order: v.order.filter((x) => x !== id) }));

  /* Restore a built-in to its default slot: just before the next
     default-order panel that's still visible. Custom widgets append. */
  const show = (id) => patchActiveView((v) => {
    if (v.order.includes(id)) return v;
    const defaults = defaultVisibleIds(surface);
    const at = defaults.indexOf(id);
    const nextVisible = at === -1 ? null : defaults.slice(at + 1).find((d) => v.order.includes(d));
    const order = [...v.order];
    order.splice(nextVisible ? order.indexOf(nextVisible) : order.length, 0, id);
    return { ...v, order };
  });

  const reset = () => setState((prev) => {
    const next = {
      ...prev,
      views: { ...prev.views, [prev.activeView]: makeDefault(surface) },
    };
    persist(next);
    return next;
  });

  const addCustomWidget = (widget) => patchActiveView((v) => ({
    ...v,
    order: [...v.order, widget.id],
    custom: [...v.custom, widget],
  }));

  const updateCustomWidget = (id, patch) => patchActiveView((v) => ({
    ...v,
    custom: v.custom.map((w) => (w.id === id ? { ...w, ...patch } : w)),
  }));

  const removeCustomWidget = (id) => patchActiveView((v) => ({
    ...v,
    order: v.order.filter((x) => x !== id),
    custom: v.custom.filter((w) => w.id !== id),
    spans: omitKey(v.spans, id),
  }));

  const duplicateCustomWidget = (id) => patchActiveView((v) => {
    const src = v.custom.find((w) => w.id === id);
    if (!src) return v;
    const copy = { ...src, id: `custom-${Math.random().toString(36).slice(2, 10)}`, title: `${src.title} (copy)` };
    const idx = v.order.indexOf(id);
    const nextOrder = [...v.order];
    if (idx >= 0) nextOrder.splice(idx + 1, 0, copy.id);
    else nextOrder.push(copy.id);
    return { ...v, order: nextOrder, custom: [...v.custom, copy] };
  });

  const setSpan = (id, span) => patchActiveView((v) => ({
    ...v,
    spans: { ...v.spans, [id]: span },
  }));

  const resetSpan = (id) => patchActiveView((v) => ({
    ...v,
    spans: omitKey(v.spans, id),
  }));

  const setSectionOverride = (id, sectionId) => patchActiveView((v) => ({
    ...v,
    sectionOverrides: { ...v.sectionOverrides, [id]: sectionId },
  }));

  const resetSectionOverride = (id) => patchActiveView((v) => ({
    ...v,
    sectionOverrides: omitKey(v.sectionOverrides, id),
  }));

  /* Atomic reorder + section-move — used on drag drop so a single
     patch flows through storage. Two separate setState calls
     technically compose, but keeping it in one mutation makes the
     intent grep-able and avoids any transient render where the
     widget has its new position but old section. */
  const reorderAndSection = (nextOrder, id, sectionId) => patchActiveView((v) => ({
    ...v,
    order: nextOrder,
    sectionOverrides: sectionId
      ? { ...v.sectionOverrides, [id]: sectionId }
      : v.sectionOverrides,
  }));

  const reorderSections = (nextSectionOrder) => patchActiveView((v) => ({
    ...v,
    sectionOrder: nextSectionOrder,
    sectionOrderCustom: true,
  }));

  const resetSectionOrder = () => patchActiveView((v) => ({
    ...v,
    sectionOrder: [...DEFAULT_SECTION_ORDER],
    sectionOrderCustom: false,
  }));

  /* Widget-level config overrides. patchOverride merges into any
     existing override map for that id (last write wins per key).
     Every editable field a user changes on a shipped widget lands
     here; the renderer applies them on top of defaultConfig at
     render time. Reset empties the entry. */
  const patchOverride = (id, patch) => patchActiveView((v) => ({
    ...v,
    overrides: {
      ...v.overrides,
      [id]: { ...(v.overrides?.[id] || {}), ...patch },
    },
  }));

  /* Unified reset — clears every user modification for this widget:
     the config overrides map, the resize override, and the section
     override. A user hitting "Reset to default" on a widget expects
     it to return to exactly how it shipped, regardless of which
     control they used to change it. */
  const resetOverride = (id) => patchActiveView((v) => ({
    ...v,
    overrides: omitKey(v.overrides, id),
    spans: omitKey(v.spans, id),
    sectionOverrides: omitKey(v.sectionOverrides, id),
  }));

  const hasOverrides = (id) => {
    const cfg = overrides[id];
    if (cfg && Object.keys(cfg).length > 0) return true;
    if (spans[id] != null) return true;
    if (sectionOverrides[id] != null) return true;
    return false;
  };

  const getOverride = (id) => overrides[id];

  const moveTo = (id, index) => patchActiveView((v) => {
    const filtered = v.order.filter((x) => x !== id);
    const clamped = Math.max(0, Math.min(index, filtered.length));
    filtered.splice(clamped, 0, id);
    return { ...v, order: filtered };
  });

  /* ── named views ─────────────────────────────────────────────── */
  const viewNames = useMemo(() => Object.keys(state.views), [state.views]);

  const switchView = (name) => setState((prev) => {
    if (!prev.views[name]) return prev;
    const next = { ...prev, activeView: name };
    persist(next);
    return next;
  });

  /* Save the arrangement on screen as a new view and switch to it.
     A name that's already taken gets a " (2)", " (3)"… suffix. */
  const addViewFrom = (prev, sourceName, name) => {
    const base = (name || "").trim();
    if (!base) return prev;
    let finalName = base;
    for (let i = 2; prev.views[finalName]; i += 1) finalName = `${base} (${i})`;
    const source = prev.views[sourceName] || makeDefault(surface);
    const next = { activeView: finalName, views: { ...prev.views, [finalName]: cloneView(source) } };
    persist(next);
    return next;
  };

  const saveAsView = (name) => setState((prev) => addViewFrom(prev, prev.activeView, name));

  const duplicateView = (name) => setState((prev) => (
    prev.views[name] ? addViewFrom(prev, name, `${name} (copy)`) : prev
  ));

  /* Tab drag reorder. Default stays pinned first. */
  const reorderViews = (names) => setState((prev) => {
    const ordered = [DEFAULT_VIEW_NAME, ...names.filter((n) => n !== DEFAULT_VIEW_NAME)];
    const views = {};
    ordered.forEach((n) => { if (prev.views[n]) views[n] = prev.views[n]; });
    Object.keys(prev.views).forEach((n) => { if (!views[n]) views[n] = prev.views[n]; });
    const next = { ...prev, views };
    persist(next);
    return next;
  });

  const renameView = (from, to) => setState((prev) => {
    const trimmed = (to || "").trim();
    if (!trimmed || !prev.views[from] || prev.views[trimmed]) return prev;
    if (from === DEFAULT_VIEW_NAME) return prev; // Default is reserved.
    const nextViews = Object.fromEntries(
      Object.entries(prev.views).map(([k, v]) => [k === from ? trimmed : k, v]),
    );
    const activeView = prev.activeView === from ? trimmed : prev.activeView;
    const next = { activeView, views: nextViews };
    persist(next);
    return next;
  });

  const deleteView = (name) => setState((prev) => {
    if (name === DEFAULT_VIEW_NAME || !prev.views[name]) return prev;
    const { [name]: _removed, ...rest } = prev.views;
    const activeView = prev.activeView === name ? DEFAULT_VIEW_NAME : prev.activeView;
    const next = { activeView, views: rest };
    persist(next);
    return next;
  });

  return {
    surface,
    activeView: state.activeView,
    viewNames,

    visibleIds,
    hiddenIds,
    order,
    spans,
    sectionOverrides,
    sectionOrder,
    overrides,
    customWidgets: custom,

    move, moveTo, reorder, reorderAndSection, hide, show, reset,
    setSpan, resetSpan,
    setSectionOverride, resetSectionOverride,
    reorderSections, resetSectionOrder,
    patchOverride, resetOverride, hasOverrides, getOverride,
    addCustomWidget, updateCustomWidget, removeCustomWidget, duplicateCustomWidget,

    switchView, saveAsView, duplicateView, reorderViews, renameView, deleteView,

    DEFAULT_VIEW_NAME,
  };
}

function cloneView(v) {
  return JSON.parse(JSON.stringify(v));
}

function omitKey(obj, key) {
  if (!obj || !(key in obj)) return obj || {};
  const next = { ...obj };
  delete next[key];
  return next;
}

/* Utility exported for the URL sync layer — `?view=name` in the
   query string switches to that named view on mount if it exists. */
export function readViewFromUrl() {
  try {
    const params = new URLSearchParams(window.location.search);
    return params.get("view") || null;
  } catch { return null; }
}

export function writeViewToUrl(name) {
  try {
    const params = new URLSearchParams(window.location.search);
    if (name && name !== DEFAULT_VIEW_NAME) params.set("view", name);
    else params.delete("view");
    const qs = params.toString();
    const url = qs ? `${window.location.pathname}?${qs}${window.location.hash}` : `${window.location.pathname}${window.location.hash}`;
    window.history.replaceState(null, "", url);
  } catch { /* no-op */ }
}
