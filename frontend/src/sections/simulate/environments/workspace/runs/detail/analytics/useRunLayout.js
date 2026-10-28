import { useCallback, useMemo, useState } from "react";
import { SECTIONS, eligibleIds } from "./panelRegistry";

// The run analytics layout, per environment: named views, each an ordered list
// of visible widget ids plus widths, moved sections and widget copies. Kept in
// this browser only (no backend for layouts); every storage call is guarded so
// a blocked or full localStorage leaves the default layout working.
export const DEFAULT_VIEW_NAME = "Default";
const DEFAULT_SECTION_ORDER = SECTIONS.map((section) => section.id);

export const layoutStorageKey = (envId) =>
  `simulate-env:run-analytics-layout:${envId || "generic"}`;

function readStorage(envId) {
  try {
    const raw = window.localStorage.getItem(layoutStorageKey(envId));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeStorage(envId, state) {
  try {
    window.localStorage.setItem(layoutStorageKey(envId), JSON.stringify(state));
  } catch {
    // Storage blocked or full: the layout still works for this visit.
  }
}

function makeDefault(surface) {
  return {
    order: eligibleIds(surface),
    custom: [],
    spans: {},
    sectionOverrides: {},
    sectionOrder: [...DEFAULT_SECTION_ORDER],
    knownIds: eligibleIds(surface),
  };
}

const objectOr = (value) =>
  value && typeof value === "object" && !Array.isArray(value) ? value : {};

// Drops ids no longer shipped, appends widgets shipped since the view was
// saved (a known id missing from `order` was hidden on purpose and stays
// hidden), and keeps the section order to known sections.
function reconcile(view, surface) {
  const eligible = eligibleIds(surface);
  const custom = Array.isArray(view?.custom)
    ? view.custom.filter((widget) => widget?.id && widget?.of)
    : [];
  const customIds = new Set(custom.map((widget) => widget.id));
  const order = (Array.isArray(view?.order) ? view.order : []).filter(
    (id) => eligible.includes(id) || customIds.has(id),
  );
  const known = new Set(Array.isArray(view?.knownIds) ? view.knownIds : []);
  eligible.forEach((id) => {
    if (!order.includes(id) && !known.has(id)) order.push(id);
  });
  const sectionOrder = (
    Array.isArray(view?.sectionOrder) ? view.sectionOrder : []
  ).filter(
    (id, index, list) =>
      DEFAULT_SECTION_ORDER.includes(id) && list.indexOf(id) === index,
  );
  DEFAULT_SECTION_ORDER.forEach((id) => {
    if (!sectionOrder.includes(id)) sectionOrder.push(id);
  });
  return {
    order,
    custom,
    spans: objectOr(view?.spans),
    sectionOverrides: objectOr(view?.sectionOverrides),
    sectionOrder,
    knownIds: eligible,
  };
}

function hydrate(envId, surface, initialView) {
  const stored = readStorage(envId);
  const views = {};
  Object.entries(objectOr(stored?.views)).forEach(([name, view]) => {
    views[name] = reconcile(view, surface);
  });
  if (!views[DEFAULT_VIEW_NAME]) {
    views[DEFAULT_VIEW_NAME] = makeDefault(surface);
  }
  const ordered = {
    [DEFAULT_VIEW_NAME]: views[DEFAULT_VIEW_NAME],
    ...views,
  };
  const activeView = [initialView, stored?.activeView].find(
    (name) => name && ordered[name],
  );
  return { activeView: activeView || DEFAULT_VIEW_NAME, views: ordered };
}

const omitKey = (object, key) => {
  const next = { ...object };
  delete next[key];
  return next;
};

const newId = (prefix) =>
  `${prefix}-${Math.random().toString(36).slice(2, 10)}`;

export function useRunLayout({ envId, surface, initialView }) {
  const [state, setState] = useState(() =>
    hydrate(envId, surface, initialView),
  );

  const commit = useCallback(
    (update) =>
      setState((previous) => {
        const next = update(previous);
        if (next !== previous) writeStorage(envId, next);
        return next;
      }),
    [envId],
  );

  const patchView = (mutate) =>
    commit((previous) => {
      const view = previous.views[previous.activeView] || makeDefault(surface);
      return {
        ...previous,
        views: { ...previous.views, [previous.activeView]: mutate(view) },
      };
    });

  const view = state.views[state.activeView] || makeDefault(surface);
  const { order, custom, spans, sectionOverrides, sectionOrder } = view;

  const hiddenIds = useMemo(
    () => [
      ...eligibleIds(surface).filter((id) => !order.includes(id)),
      ...custom.map((widget) => widget.id).filter((id) => !order.includes(id)),
    ],
    [order, custom, surface],
  );

  const moveTo = (id, index) =>
    patchView((current) => {
      const next = current.order.filter((item) => item !== id);
      next.splice(Math.max(0, Math.min(index, next.length)), 0, id);
      return { ...current, order: next };
    });

  const hide = (id) =>
    patchView((current) => ({
      ...current,
      order: current.order.filter((item) => item !== id),
    }));

  // Back to its default slot: just before the next default widget still shown.
  const show = (id) =>
    patchView((current) => {
      if (current.order.includes(id)) return current;
      const defaults = eligibleIds(surface);
      const at = defaults.indexOf(id);
      const before =
        at === -1
          ? null
          : defaults.slice(at + 1).find((item) => current.order.includes(item));
      const next = [...current.order];
      next.splice(before ? next.indexOf(before) : next.length, 0, id);
      return { ...current, order: next };
    });

  const reset = () =>
    commit((previous) => ({
      ...previous,
      views: { ...previous.views, [previous.activeView]: makeDefault(surface) },
    }));

  // A copy of a widget, drawn from the same real data, right after the source
  // in the Custom widgets section.
  const duplicate = (id, title) =>
    patchView((current) => {
      const source = current.custom.find((widget) => widget.id === id);
      const copy = {
        id: newId("copy"),
        of: source?.of || id,
        title: `${source?.title || title} (copy)`,
      };
      const next = [...current.order];
      const at = next.indexOf(id);
      next.splice(at === -1 ? next.length : at + 1, 0, copy.id);
      return { ...current, order: next, custom: [...current.custom, copy] };
    });

  const removeCustom = (id) =>
    patchView((current) => ({
      ...current,
      order: current.order.filter((item) => item !== id),
      custom: current.custom.filter((widget) => widget.id !== id),
      spans: omitKey(current.spans, id),
      sectionOverrides: omitKey(current.sectionOverrides, id),
    }));

  const setSpan = (id, span) =>
    patchView((current) => ({
      ...current,
      spans: { ...current.spans, [id]: span },
    }));

  // One drop: the new order and, when it landed in another section, that home.
  const reorderAndSection = (nextOrder, id, sectionId) =>
    patchView((current) => ({
      ...current,
      order: nextOrder,
      sectionOverrides: sectionId
        ? { ...current.sectionOverrides, [id]: sectionId }
        : current.sectionOverrides,
    }));

  const reorderSections = (nextSectionOrder) =>
    patchView((current) => ({ ...current, sectionOrder: nextSectionOrder }));

  const hasOverrides = (id) =>
    spans[id] != null || sectionOverrides[id] != null;

  const resetOverride = (id) =>
    patchView((current) => ({
      ...current,
      spans: omitKey(current.spans, id),
      sectionOverrides: omitKey(current.sectionOverrides, id),
    }));

  const viewNames = useMemo(() => Object.keys(state.views), [state.views]);

  const switchView = (name) =>
    commit((previous) =>
      previous.views[name] && previous.activeView !== name
        ? { ...previous, activeView: name }
        : previous,
    );

  // A taken name gets " (2)", " (3)" and so on.
  const addViewFrom = (previous, sourceName, name) => {
    const base = (name || "").trim();
    if (!base) return previous;
    let finalName = base;
    for (let n = 2; previous.views[finalName]; n += 1) {
      finalName = `${base} (${n})`;
    }
    const source = previous.views[sourceName] || makeDefault(surface);
    return {
      activeView: finalName,
      views: {
        ...previous.views,
        [finalName]: JSON.parse(JSON.stringify(source)),
      },
    };
  };

  const saveAsView = (name) =>
    commit((previous) => addViewFrom(previous, previous.activeView, name));

  const duplicateView = (name) =>
    commit((previous) =>
      previous.views[name]
        ? addViewFrom(previous, name, `${name} (copy)`)
        : previous,
    );

  const renameView = (from, to) =>
    commit((previous) => {
      const name = (to || "").trim();
      if (
        !name ||
        from === DEFAULT_VIEW_NAME ||
        !previous.views[from] ||
        previous.views[name]
      )
        return previous;
      const views = Object.fromEntries(
        Object.entries(previous.views).map(([key, value]) => [
          key === from ? name : key,
          value,
        ]),
      );
      return {
        activeView: previous.activeView === from ? name : previous.activeView,
        views,
      };
    });

  const deleteView = (name) =>
    commit((previous) => {
      if (name === DEFAULT_VIEW_NAME || !previous.views[name]) return previous;
      return {
        activeView:
          previous.activeView === name
            ? DEFAULT_VIEW_NAME
            : previous.activeView,
        views: omitKey(previous.views, name),
      };
    });

  // Default stays first.
  const reorderViews = (names) =>
    commit((previous) => {
      const views = { [DEFAULT_VIEW_NAME]: previous.views[DEFAULT_VIEW_NAME] };
      names.forEach((name) => {
        if (previous.views[name]) views[name] = previous.views[name];
      });
      Object.keys(previous.views).forEach((name) => {
        if (!views[name]) views[name] = previous.views[name];
      });
      return { ...previous, views };
    });

  return {
    activeView: state.activeView,
    viewNames,
    visibleIds: order,
    hiddenIds,
    spans,
    sectionOverrides,
    sectionOrder,
    customWidgets: custom,
    moveTo,
    hide,
    show,
    reset,
    duplicate,
    removeCustom,
    setSpan,
    reorderAndSection,
    reorderSections,
    hasOverrides,
    resetOverride,
    switchView,
    saveAsView,
    duplicateView,
    renameView,
    deleteView,
    reorderViews,
  };
}
