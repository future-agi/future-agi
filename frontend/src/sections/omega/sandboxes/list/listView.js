/**
 * Search, state filters and column sorting for the Sandboxes list and the
 * Files list. Pure functions over the rows the API returned; nothing here
 * asks the API again.
 */

/**
 * A sort heading whose arrow sits just past its words, in the cell's padding,
 * so the heading never wraps to make room for the arrow.
 */
export const SORT_LABEL_SX = {
  position: "relative",
  whiteSpace: "nowrap",
  "& .MuiTableSortLabel-icon": {
    position: "absolute",
    left: "100%",
    ml: 0.25,
  },
};

/** The states a list filter offers, in the order the chips show them. */
export const STATE_FILTERS = ["active", "recovery_required", "hibernated"];

const STATE_RANK = { active: 0, recovery_required: 1, hibernated: 2 };

const text = (value) => (typeof value === "string" ? value : "");

/** True when the name, id or image holds the search words (any case). */
export function matchesSearch(sandbox, query) {
  const needle = text(query).trim().toLowerCase();
  if (!needle) return true;
  return [sandbox.name, sandbox.id, sandbox.image].some((value) =>
    text(value).toLowerCase().includes(needle),
  );
}

export function filterSandboxes(sandboxes, { query = "", states = [] } = {}) {
  return sandboxes.filter(
    (sandbox) =>
      (states.length === 0 || states.includes(sandbox.state)) &&
      matchesSearch(sandbox, query),
  );
}

const byName = (a, b) =>
  text(a).localeCompare(text(b), undefined, {
    numeric: true,
    sensitivity: "base",
  });

const instant = (iso) => {
  const t = typeof iso === "string" ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : null;
};

const PAST = { desc: "newest first", asc: "oldest first" };

/**
 * Each sortable column: its heading, how two rows compare (ascending), the
 * direction a first click sorts in (newest first for past times, soonest
 * first for hibernation, A to Z for words), and each direction in words for
 * the Sort by menu the cards offer.
 */
export const SANDBOX_SORTS = {
  name: {
    label: "Name",
    first: "asc",
    words: { asc: "A to Z", desc: "Z to A" },
    compare: (a, b) => byName(a.name, b.name),
  },
  state: {
    label: "State",
    first: "asc",
    words: { asc: "Running first", desc: "Hibernated first" },
    compare: (a, b) => (STATE_RANK[a.state] ?? 9) - (STATE_RANK[b.state] ?? 9),
  },
  lastActivityAt: {
    label: "Last activity",
    first: "desc",
    words: PAST,
    time: (row) => row.lastActivityAt,
  },
  createdAt: {
    label: "Created",
    first: "desc",
    words: PAST,
    time: (row) => row.createdAt,
  },
  hibernatesAt: {
    label: "Hibernates",
    first: "asc",
    words: { asc: "soonest first", desc: "latest first" },
    time: (row) => (row.state === "active" ? row.hibernatesAt : null),
  },
};

const flip = (direction) => (direction === "asc" ? "desc" : "asc");

/**
 * The Sort by menu's choices: each column in its first direction, then
 * reversed ("Last activity, newest first", "Last activity, oldest first").
 */
export const SANDBOX_SORT_CHOICES = Object.entries(SANDBOX_SORTS).flatMap(
  ([key, spec]) =>
    [spec.first, flip(spec.first)].map((direction) => ({
      value: `${key}:${direction}`,
      label: `${spec.label}, ${spec.words[direction]}`,
    })),
);

/** A sort as the menu's value; "" is the API's own order. */
export function sortValue(sort) {
  return sort ? `${sort.key}:${sort.direction}` : "";
}

/** The sort a menu value names, or null for the API's own order. */
export function sortFromValue(value) {
  const [key, direction] = String(value ?? "").split(":");
  if (!SANDBOX_SORTS[key] || (direction !== "asc" && direction !== "desc")) {
    return null;
  }
  return { key, direction };
}

/**
 * Rows in the order a sort names, `{ key, direction }`; the API's own order
 * when there is none. A row with no time for a time column always goes last.
 * Ties keep the API's order.
 */
function sortRows(rows, sort, table) {
  const spec = sort && table[sort.key];
  if (!spec) return rows;
  const sign = sort.direction === "desc" ? -1 : 1;
  const indexed = rows.map((row, index) => ({ row, index }));
  indexed.sort((left, right) => {
    let order;
    if (spec.time) {
      const a = instant(spec.time(left.row));
      const b = instant(spec.time(right.row));
      if (a === null || b === null) {
        order = a === null && b === null ? 0 : a === null ? 1 : -1;
        return order || left.index - right.index;
      }
      order = sign * (a - b);
    } else {
      order = sign * spec.compare(left.row, right.row);
    }
    return order || left.index - right.index;
  });
  return indexed.map(({ row }) => row);
}

export function sortSandboxes(sandboxes, sort) {
  return sortRows(sandboxes, sort, SANDBOX_SORTS);
}

/** The next sort after a click on a column: its first direction, then flip. */
export function nextSort(current, key, table = SANDBOX_SORTS) {
  if (current?.key === key) {
    return { key, direction: flip(current.direction) };
  }
  return { key, direction: table[key]?.first || "asc" };
}

/** Files: folders stay above files whichever way a column sorts. */
export const FILE_SORTS = {
  name: { first: "asc", compare: (a, b) => byName(a.name, b.name) },
  size: {
    first: "desc",
    compare: (a, b) => (Number(a.size) || 0) - (Number(b.size) || 0),
  },
};

export function sortFiles(entries, sort) {
  const sorted = sortRows(entries, sort, FILE_SORTS);
  if (!sort || !FILE_SORTS[sort.key]) return sorted;
  return [
    ...sorted.filter((entry) => entry.directory),
    ...sorted.filter((entry) => !entry.directory),
  ];
}
