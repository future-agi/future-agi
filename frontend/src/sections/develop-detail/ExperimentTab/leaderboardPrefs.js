// The leaderboard's per-viewer view (ONE-VIEW-SPEC A2-01, A2-07): which
// chart cards show, deltas on/off, the Charts section and the columns, all
// in G's layout prefs (`fa.layout.<user>.leaderboard.<datasetId>`), so the
// View menu, saved views and `Reset view` cover them.
//
// The old per-dataset key `fa.expList.<datasetId>` (columns / charts /
// deltas) is read ONCE, moved into the layout and removed.
import { useCallback, useEffect, useMemo } from "react";
import {
  getLayout,
  setLayout,
  useLayoutKey,
  useLayoutPrefs,
} from "src/sections/common/ux/oneview";
import { migrateLegacyColumns } from "./leaderboardColumns";

export const LB_SURFACE = "leaderboard";
export const CHARTS_SECTION = "lb.charts";

export const CHART_CARDS = [
  { id: "scores", label: "Scores" },
  { id: "latency", label: "Latency p50 / p99" },
  { id: "cost", label: "Total cost" },
  { id: "errors", label: "Error rate" },
];
const ALL_CARDS = CHART_CARDS.map((c) => c.id);

// ------------------------------------------------------------ legacy key

export const defaultPrefs = () => ({
  columns: {},
  charts: { open: true, cards: [...ALL_CARDS] },
  showDeltas: true,
});

export const prefsStorageKey = (datasetId) => `fa.expList.${datasetId}`;

/** Defaults overlaid with whatever valid keys `raw` carries. */
export function mergePrefs(base, raw) {
  const out = { ...base, charts: { ...base.charts } };
  if (!raw || typeof raw !== "object") return out;
  if (raw.columns && typeof raw.columns === "object")
    out.columns = Object.fromEntries(
      Object.entries(raw.columns).filter(([, v]) => typeof v === "boolean"),
    );
  if (raw.charts && typeof raw.charts === "object") {
    if (typeof raw.charts.open === "boolean") out.charts.open = raw.charts.open;
    if (Array.isArray(raw.charts.cards))
      out.charts.cards = raw.charts.cards.filter((id) =>
        CHART_CARDS.some((c) => c.id === id),
      );
  }
  if (typeof raw.showDeltas === "boolean") out.showDeltas = raw.showDeltas;
  return out;
}

const safeStorage = () => {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
};

/** The old key's prefs (defaults filled in); broken storage → defaults. */
export function readPrefs(datasetId, storage = safeStorage()) {
  try {
    const text = storage?.getItem(prefsStorageKey(datasetId));
    return mergePrefs(defaultPrefs(), text ? JSON.parse(text) : null);
  } catch {
    return defaultPrefs();
  }
}

/** Kept for the round-trip test; nothing writes the old key any more. */
export function writePrefs(datasetId, prefs, storage = safeStorage()) {
  try {
    storage?.setItem(prefsStorageKey(datasetId), JSON.stringify(prefs));
  } catch {
    // Private windows and blocked storage: the view still works.
  }
}

/**
 * Pure: what the old prefs mean in the layout. Only choices that differ
 * from the one-view defaults move; what the layout already says wins.
 * @param {object|null} legacy readPrefs() of a key that exists
 * @param {object[]} catalog columnCatalog()
 * @param {object} layout current layout prefs
 * @returns {object|null} a layout patch, or null when nothing moves
 */
export function legacyToLayout(legacy, catalog, layout = {}) {
  if (!legacy) return null;
  const patch = {};
  const columns = migrateLegacyColumns(
    legacy.columns,
    catalog,
    layout.columns ?? {},
  );
  if (columns) patch.columns = columns;
  const sections = layout.sections ?? {};
  if (legacy.charts?.open === false && !(CHARTS_SECTION in sections))
    patch.sections = { [CHARTS_SECTION]: false };
  const extras = {};
  const have = layout.extras ?? {};
  const cards = legacy.charts?.cards;
  if (
    Array.isArray(cards) &&
    cards.join("|") !== ALL_CARDS.join("|") &&
    !("chartCards" in have)
  )
    extras.chartCards = cards;
  if (legacy.showDeltas === false && !("showDeltas" in have))
    extras.showDeltas = false;
  if (Object.keys(extras).length) patch.extras = extras;
  return Object.keys(patch).length ? patch : null;
}

/**
 * Move `fa.expList.<datasetId>` into the layout once the catalog is known,
 * then delete it (A2-07: migrate once, then stop writing it).
 * @param {string} datasetId
 * @param {object[]} catalog
 * @param {boolean} ready the catalog has its evals (R1 loaded)
 */
export function useLegacyPrefsMigration(datasetId, catalog, ready) {
  const key = useLayoutKey(LB_SURFACE, datasetId);
  useEffect(() => {
    if (!ready || !datasetId) return;
    const storage = safeStorage();
    let raw = null;
    try {
      raw = storage?.getItem(prefsStorageKey(datasetId)) ?? null;
    } catch {
      return;
    }
    if (raw == null) return;
    const cur = getLayout(key);
    const patch = legacyToLayout(readPrefs(datasetId, storage), catalog, cur);
    if (patch)
      setLayout(key, {
        ...cur,
        columns: patch.columns ?? cur.columns,
        sections: { ...cur.sections, ...(patch.sections ?? {}) },
        extras: { ...cur.extras, ...(patch.extras ?? {}) },
      });
    try {
      storage.removeItem(prefsStorageKey(datasetId));
    } catch {
      // Blocked storage: nothing was stored either.
    }
  }, [ready, datasetId, key, catalog]);
}

// ------------------------------------------------------------ the view

/**
 * The leaderboard's view prefs from the layout.
 * @returns {{chartCards: string[], setChartCards(ids|fn), showDeltas:
 *   boolean, setShowDeltas(bool), chartsOpen: boolean, setChartsOpen(bool),
 *   chartsStored: boolean|undefined}}
 */
export function useLeaderboardPrefs(datasetId) {
  const [prefs, patch] = useLayoutPrefs(LB_SURFACE, datasetId);
  const extras = prefs.extras ?? {};
  const chartCards = useMemo(() => {
    const raw = extras.chartCards;
    return Array.isArray(raw)
      ? ALL_CARDS.filter((id) => raw.includes(id))
      : ALL_CARDS;
  }, [extras.chartCards]);
  const setChartCards = useCallback(
    (next) =>
      patch((cur) => {
        const before = Array.isArray(cur.extras?.chartCards)
          ? cur.extras.chartCards
          : ALL_CARDS;
        const value = typeof next === "function" ? next(before) : next;
        const ordered = ALL_CARDS.filter((id) => value.includes(id));
        const { chartCards: _c, ...rest } = cur.extras ?? {};
        return {
          ...cur,
          // All four = the default: nothing to store.
          extras:
            ordered.length === ALL_CARDS.length
              ? rest
              : { ...rest, chartCards: ordered },
        };
      }),
    [patch],
  );
  const showDeltas = extras.showDeltas !== false;
  const setShowDeltas = useCallback(
    (on) => patch({ extras: { showDeltas: on ? undefined : false } }),
    [patch],
  );
  const stored = prefs.sections?.[CHARTS_SECTION];
  // IT-R1 G2 P3: charts start collapsed to their one-line summary on every
  // screen size (they took half of a 900 px screen); opening or closing
  // them is remembered.
  const chartsOpen = typeof stored === "boolean" ? stored : false;
  const setChartsOpen = useCallback(
    (open) => patch({ sections: { [CHARTS_SECTION]: Boolean(open) } }),
    [patch],
  );
  return {
    chartCards,
    setChartCards,
    showDeltas,
    setShowDeltas,
    chartsOpen,
    setChartsOpen,
    chartsStored: stored,
  };
}
