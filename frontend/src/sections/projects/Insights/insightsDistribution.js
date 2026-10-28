// Category colours and the stacked distributions built from them.
//
// Colour follows the category, never its rank: ids are given slots in the
// order they first appear across the report's runs (oldest run first), so a
// category keeps its colour on every run of the pager and in the trend. The
// eight slots are a CVD-validated categorical palette (adjacent ΔE ≥ 8 in both
// modes); anything past eight, and the "didn't fit" bucket, folds into a grey
// "Other" rather than a generated ninth hue.

export const CATEGORY_PALETTE = {
  light: [
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#6250d6",
    "#e34948",
  ],
  dark: [
    "#3987e5",
    "#d95926",
    "#199e70",
    "#c98500",
    "#d55181",
    "#008300",
    "#9085e9",
    "#e66767",
  ],
};
export const OTHER_COLOR = { light: "#a3a29c", dark: "#6b6a65" };
export const OTHER_ID = "__other__";

export const categoryRowId = (id) => `insights-category-${id}`;

export const isMutedCategory = (category) =>
  category?.status === "unmatched" ||
  /^other$/i.test(String(category?.name || "").trim());

const byCountDesc = (a, b) => (b.count || 0) - (a.count || 0);

// Top-level items of a run in the shape the charts use.
export const runTops = (run) =>
  (run?.distribution || []).map((d) => ({
    id: d.category_id,
    name: d.name,
    count: d.count || 0,
    share: d.share || 0,
    status: d.status,
  }));

export const resultTops = (categories = []) =>
  categories
    .filter((c) => c.level === 1)
    .map((c) => ({
      id: c.id,
      name: c.name,
      count: c.stats?.count || 0,
      share: c.stats?.share || 0,
      status: c.status,
    }));

/**
 * Slot order for category ids. The report's saved categories (`taxonomy`
 * tops, the same on the reports home and the report page) come first, then
 * categories first seen in `runs` (oldest run first), then `extra` (the
 * current run's tops, which may not be in the run list yet).
 */
export function buildColorOrder({ taxonomy, runs = [], extra = [] } = {}) {
  const order = [];
  const seen = new Set();
  const add = (item) => {
    if (!item?.id || seen.has(item.id) || isMutedCategory(item)) return;
    seen.add(item.id);
    order.push(item.id);
  };
  (taxonomy?.tops || []).forEach(add);
  [...runs]
    .sort((a, b) => new Date(a.created_at) - new Date(b.created_at))
    .forEach((run) => [...runTops(run)].sort(byCountDesc).forEach(add));
  [...extra].sort(byCountDesc).forEach(add);
  return order;
}

export function colorFor(id, order, mode = "light") {
  const palette = CATEGORY_PALETTE[mode] || CATEGORY_PALETTE.light;
  const slot = order.indexOf(id);
  if (slot < 0 || slot >= palette.length) return null;
  return palette[slot];
}

/**
 * Stacked-bar segments, largest first, with the grey "Other" last. `max` caps
 * the coloured segments (the rest fold into Other) — a card legend shows four
 * or five, the report shows every coloured slot.
 */
export function buildSegments(
  items = [],
  { order = [], mode = "light", max } = {},
) {
  const total = items.reduce((sum, item) => sum + (item.count || 0), 0);
  const colored = [];
  const other = { id: OTHER_ID, name: "Other", count: 0, members: [] };
  [...items].sort(byCountDesc).forEach((item) => {
    const color = isMutedCategory(item) ? null : colorFor(item.id, order, mode);
    if (color && (!max || colored.length < max - 1 || items.length <= max)) {
      colored.push({ ...item, color });
    } else {
      other.count += item.count || 0;
      other.members.push(item);
    }
  });
  const segments = [...colored];
  if (other.count > 0) {
    // A lone folded category keeps its own name.
    const single = other.members.length === 1 ? other.members[0] : null;
    segments.push({
      ...other,
      id: single?.id || OTHER_ID,
      name: single?.name || "Other",
      color: OTHER_COLOR[mode] || OTHER_COLOR.light,
      muted: true,
    });
  }
  return segments.map((segment) => ({
    ...segment,
    share: total ? segment.count / total : 0,
  }));
}

/**
 * One stacked row per completed run, oldest first, for the "Across runs"
 * chart. Category ids are stable across runs that share a frozen taxonomy.
 */
export function buildTrend(
  runs = [],
  { order = [], mode = "light", max } = {},
) {
  return [...runs]
    .filter((run) => run.status === "completed" && run.distribution?.length)
    .sort((a, b) => new Date(a.created_at) - new Date(b.created_at))
    .map((run) => ({
      runId: run.id,
      createdAt: run.created_at,
      count:
        run.analysed_count ||
        runTops(run).reduce((sum, item) => sum + item.count, 0),
      segments: buildSegments(runTops(run), { order, mode, max }),
    }));
}

const sameName = (a, b) => {
  const name = (category) =>
    String(category?.name || "")
      .trim()
      .toLowerCase();
  return Boolean(name(a)) && name(a) === name(b);
};

const isEmpty = (value) =>
  value == null || value === "" || (Array.isArray(value) && value.length === 0);

/**
 * A top whose only subcategory has the same name (a report with three or
 * fewer themes makes each theme its own top) is one category, not
 * "Refunds › Refunds". Returns the categories with each such sub folded into
 * its top (the top keeps its id and units, and takes the sub's description,
 * notes or insight where it has none) and `echoes`, sub id → top id, so
 * anything that names the sub resolves to the top.
 */
export function mergeEchoedSubcategories(categories = []) {
  const byId = new Map(categories.map((c) => [c.id, c]));
  const echoes = new Map();
  const merged = new Map();
  categories
    .filter((c) => c.level === 1)
    .forEach((top) => {
      const childIds =
        top.children ||
        categories.filter((c) => c.parent_id === top.id).map((c) => c.id);
      const children = childIds.map((id) => byId.get(id)).filter(Boolean);
      if (children.length !== 1 || !sameName(top, children[0])) return;
      const sub = children[0];
      const one = { ...top, children: [], has_children: false };
      ["description", "insight", "notable"].forEach((key) => {
        if (isEmpty(one[key]) && !isEmpty(sub[key])) one[key] = sub[key];
      });
      merged.set(top.id, one);
      echoes.set(sub.id, top.id);
    });
  if (!echoes.size) return { categories, echoes };
  return {
    categories: categories
      .filter((c) => !echoes.has(c.id))
      .map((c) => merged.get(c.id) || c),
    echoes,
  };
}

/** The categories for some ids, each once, skipping ids not in the run. */
export const categoriesFor = (ids = [], categoriesById) => {
  const seen = new Set();
  return ids
    .map((id) => categoriesById.get(id))
    .filter((category) => {
      if (!category || seen.has(category.id)) return false;
      seen.add(category.id);
      return true;
    });
};

/** Rows of the categories table: tops with their children nested. */
export function buildCategoryTree(categories = []) {
  const byId = new Map(categories.map((c) => [c.id, c]));
  const tops = categories.filter((c) => c.level === 1);
  return tops.map((top) => {
    const childIds =
      top.children ||
      categories.filter((c) => c.parent_id === top.id).map((c) => c.id);
    const children = childIds.map((id) => byId.get(id)).filter(Boolean);
    // A top whose only child is itself (same name, or a child with no name
    // of its own) reads as one level. A lone sub always has its top's count
    // (subs split their top's items), so the count can't tell: an "Emerging"
    // top holding one real theme keeps it.
    const single =
      children.length === 1 &&
      (sameName(top, children[0]) || !String(children[0].name || "").trim());
    return {
      ...top,
      childRows: single ? [] : children,
      expandable: Boolean(top.has_children) && !single && children.length > 0,
    };
  });
}

const SORTERS = {
  count: (c) => c.stats?.count ?? 0,
  users: (c) => c.stats?.users ?? 0,
  failure: (c) => c.stats?.failure_rate ?? -1,
  unresolved: (c) => c.stats?.unresolved_rate ?? -1,
  sentiment: (c) => c.stats?.sentiment ?? -1,
  notable: (c) => c.notable?.length ?? 0,
  share: (c) => c.stats?.count ?? 0,
  sessions: (c) => c.stats?.sessions ?? 0,
  latency: (c) => c.stats?.latency_ms ?? -1,
  cost: (c) => c.stats?.cost ?? -1,
  errors: (c) => c.stats?.error_rate ?? -1,
};

/** Sort tops and, within each, their children; muted rows always sink. */
export function sortCategoryTree(tree, key = "count", direction = "desc") {
  const value = SORTERS[key] || SORTERS.count;
  const sign = direction === "asc" ? 1 : -1;
  const compare = (a, b) => {
    const muted = Number(isMutedCategory(a)) - Number(isMutedCategory(b));
    if (muted) return muted;
    return (
      sign * (value(a) - value(b)) ||
      (b.stats?.count ?? 0) - (a.stats?.count ?? 0)
    );
  };
  return [...tree]
    .sort(compare)
    .map((row) => ({ ...row, childRows: [...row.childRows].sort(compare) }));
}

/**
 * Series for the trend chart: the share of each top-level category in every
 * completed run, oldest first. A category keeps its slot colour on every
 * run; one past the palette, or a muted one, folds into a grey "Other".
 *   {series: [{id, name, color}],
 *    rows: [{runId, time, n, shares, counts, fresh}]}
 * `shares` and `counts` are keyed by series id; shares of a run sum to 1.
 * `fresh`: the run found its categories afresh, so its shares don't compare
 * with the runs before it.
 */
export function buildAreaSeries(
  runs = [],
  { order = [], mode = "light" } = {},
) {
  const completed = [...runs]
    .filter((run) => run.status === "completed" && run.distribution?.length)
    .sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
  const names = new Map();
  const used = new Set();
  let otherUsed = false;
  const rows = completed.map((run) => {
    const tops = runTops(run);
    const total = tops.reduce((sum, item) => sum + item.count, 0);
    const shares = {};
    const counts = {};
    tops.forEach((item) => {
      const colored =
        !isMutedCategory(item) && colorFor(item.id, order, mode) !== null;
      const key = colored ? item.id : OTHER_ID;
      if (colored) {
        names.set(item.id, item.name);
        used.add(item.id);
      } else otherUsed = true;
      counts[key] = (counts[key] || 0) + item.count;
    });
    Object.keys(counts).forEach((key) => {
      shares[key] = total ? counts[key] / total : 0;
    });
    return {
      runId: run.id,
      time: new Date(run.finished_at || run.created_at).getTime(),
      n: run.analysed_count || total,
      shares,
      counts,
      fresh: run.taxonomy_mode === "discover",
    };
  });
  const series = order
    .filter((id) => used.has(id))
    .map((id) => ({
      id,
      name: names.get(id),
      color: colorFor(id, order, mode),
    }));
  if (otherUsed)
    series.push({
      id: OTHER_ID,
      name: "Other",
      color: OTHER_COLOR[mode] || OTHER_COLOR.light,
      muted: true,
    });
  return { series, rows };
}
