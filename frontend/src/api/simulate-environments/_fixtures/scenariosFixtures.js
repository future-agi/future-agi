import sampleSuite from "./scenarioSamples/03-list-grouped-by-accent.json";
import fieldsSample from "./scenarioSamples/01-list-page-1.json";
import coverageSample from "./scenarioSamples/07-coverage.json";

/**
 * In-memory emulator of the scenarios list, coverage and amend endpoints, used
 * until the live routes are reachable (see scenarios.js `SCENARIOS_SOURCE`).
 * Given the same query params the backend reads, it reproduces the server:
 * object-style filters, free-text search, grouping and pagination over the
 * 20-row suite, the coverage cross-tab, and the amend (edit/drop) route — and
 * synthesises the whole response bodies.
 *
 * The list reads a mutable WORKING copy so an amend (drop / set_field /
 * set_persona) is reflected on the next read; `resetScenarioFixture()` restores
 * the committed suite between tests.
 *
 * The whole file is deleted on the live flip; nothing here ships to production.
 */

// The committed 20-row suite the samples were captured from — the immutable
// seed. `groupings`, `scenario_editing` and the coverage `axes` are static, so
// they are lifted straight off a sample.
export const SAMPLE_ROWS = sampleSuite.results;
const GROUPINGS = sampleSuite.groupings;
const SCENARIO_EDITING = sampleSuite.scenario_editing;
const AXES = coverageSample.axes;

// The mutable working copy the list, coverage and amend routes all read and
// write, seeded from the committed suite. Amends mutate this; a fresh read then
// reflects them. Tests call resetScenarioFixture() to restore the seed.
let WORKING = structuredClone(SAMPLE_ROWS);

export function resetScenarioFixture() {
  WORKING = structuredClone(SAMPLE_ROWS);
}

// The static filter-catalogue skeleton (value / label / type / category). Its
// `choices`/`counts` are recomputed per query over the searched suite below.
const FIELD_DEFS = fieldsSample.fields.map((f) => ({
  value: f.value,
  label: f.label,
  type: f.type,
  category: f.category,
}));

// group_by -> the row field its section tag is read from. `goal` is the server
// default; `""` means no grouping. Persona name is deliberately not a grouping.
const GROUP_KEY = {
  goal: (r) => r.use_case,
  sub_goal: (r) => (r.sub_goals && r.sub_goals[0]) || "",
  accent: (r) => r.persona?.accent,
  age: (r) => r.persona?.age_group,
  attack: (r) => r.coverage?.overlay,
  task: (r) => r.coverage?.task,
};

// Dotted-path read (persona.accent, coverage.overlay) tolerant of missing links.
const getPath = (row, path) =>
  path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), row);

// A field's values on a row: the cell itself, or its members for list fields
// (keywords, sub_goals, persona.languages).
const valuesOf = (row, fieldValue) => {
  const cell = getPath(row, fieldValue);
  if (cell == null) return [];
  return Array.isArray(cell) ? cell : [cell];
};

// Free text matches every word, in any order, against name / instruction /
// use_case / branch. Names are snake_case and shown with spaces, so both forms
// match: `_` is normalised to a space on both sides of the comparison.
const normalize = (s) => String(s ?? "").toLowerCase().replace(/_/g, " ");

function matchesSearch(row, search) {
  const q = normalize(search).trim();
  if (!q) return true;
  const hay = normalize(`${row.name} ${row.instruction} ${row.use_case} ${row.branch}`);
  return q.split(/\s+/).every((word) => hay.includes(word));
}

// Object-style filters: a repeated key is an OR-set on one property, a `_not`
// suffix negates, dotted keys read nested fields, and a list field (keywords,
// sub_goals, languages) matches on membership.
function applyFilters(rows, filters) {
  const entries = Object.entries(filters || {}).filter(([, v]) => {
    const arr = Array.isArray(v) ? v : [v];
    return arr.length > 0;
  });
  if (!entries.length) return rows;
  return rows.filter((row) =>
    entries.every(([rawKey, raw]) => {
      const values = Array.isArray(raw) ? raw : [raw];
      const negate = rawKey.endsWith("_not");
      const key = negate ? rawKey.slice(0, -4) : rawKey;
      const cell = getPath(row, key);
      const matchesAny = Array.isArray(cell)
        ? values.some((v) => cell.includes(v))
        : values.some((v) => String(cell) === String(v));
      return negate ? !matchesAny : matchesAny;
    }),
  );
}

// Ordering: number | name | use_case | status, a `-` prefix reverses. Used as
// the base order and, under grouping, as the within-group tiebreak.
const COMPARATORS = {
  number: (a, b) => (a.number ?? 0) - (b.number ?? 0),
  name: (a, b) => String(a.name).localeCompare(String(b.name)),
  use_case: (a, b) => String(a.use_case).localeCompare(String(b.use_case)),
  status: (a, b) => String(a.status).localeCompare(String(b.status)),
};

function sortRows(rows, ordering) {
  const desc = String(ordering).startsWith("-");
  const key = desc ? String(ordering).slice(1) : String(ordering);
  const cmp = COMPARATORS[key] || COMPARATORS.number;
  const sorted = [...rows].sort(cmp);
  return desc ? sorted.reverse() : sorted;
}

// Base ordering over the whole filtered suite (by the ordering key), stamping
// each row's `group` tag. The suite is NOT reordered by group here: grouping is
// applied to the page slice (see below), which is why a group can span pages and
// the same group can appear on more than one page.
function orderRows(rows, groupBy, ordering) {
  const key = groupBy ? GROUP_KEY[groupBy] || GROUP_KEY.goal : null;
  return sortRows(rows, ordering).map((r) => ({
    ...r,
    group: key ? key(r) ?? "" : "",
  }));
}

// The server orders THE PAGE by group: after slicing, the page rows are
// stable-sorted by group name so each section is a run of consecutive rows,
// while the within-group order stays the base ordering. Ungrouped leaves the
// slice as-is.
function groupPage(pageRows, groupBy) {
  if (!groupBy) return pageRows;
  return [...pageRows].sort((a, b) => String(a.group).localeCompare(String(b.group)));
}

// The sections on this page, in row order: count = how many landed here, total
// = how many the whole filtered suite holds (a group can span pages).
function pageSections(pageRows, totalsMap) {
  const counts = new Map();
  const order = [];
  for (const r of pageRows) {
    if (!counts.has(r.group)) order.push(r.group);
    counts.set(r.group, (counts.get(r.group) || 0) + 1);
  }
  return order.map((name) => ({
    name,
    count: counts.get(name),
    total: totalsMap.get(name) ?? counts.get(name),
  }));
}

// The filter catalogue, recomputed over the SEARCHED-not-filtered suite so that
// picking one value never removes the others (an OR can still be built). Enum
// choices are count-desc then name; a property the suite does not use is
// dropped; string fields stay with empty choices.
function buildFields(searchedRows) {
  const out = [];
  for (const def of FIELD_DEFS) {
    if (def.type !== "enum") {
      out.push({ ...def, choices: [], counts: {} });
      continue;
    }
    const counts = {};
    for (const row of searchedRows) {
      for (const v of valuesOf(row, def.value)) {
        counts[v] = (counts[v] || 0) + 1;
      }
    }
    const choices = Object.keys(counts).sort(
      (a, b) => counts[b] - counts[a] || a.localeCompare(b),
    );
    if (!choices.length) continue;
    out.push({ ...def, choices, counts });
  }
  return out;
}

/**
 * Emulate GET /simulate/api/harness-jobs/{id}/scenarios/.
 *
 * @param params  { page (1-idx), limit, search, group_by, ordering, ...filters }
 *                group_by omitted -> server default `goal`; `""` -> no grouping.
 * @param rows    the suite to serve (defaults to the mutable WORKING copy, so an
 *                amend is reflected on the next read; tests pass a larger
 *                re-numbered set to exercise pagination, which bypasses WORKING).
 */
export function queryScenarioFixture(params = {}, rows = WORKING) {
  const {
    page = 1,
    limit = 25,
    search = "",
    ordering = "number",
    group_by: groupByParam,
    ...filters
  } = params;
  const groupBy = groupByParam === undefined ? "goal" : groupByParam;

  const searched = rows.filter((r) => matchesSearch(r, search));
  const fields = buildFields(searched);

  const filtered = applyFilters(searched, filters);
  const ordered = orderRows(filtered, groupBy, ordering);

  const count = ordered.length;
  const totalPages = Math.max(1, Math.ceil(count / limit));
  const current = Math.min(Math.max(1, Number(page) || 1), totalPages);
  const start = (current - 1) * limit;
  const results = groupPage(ordered.slice(start, start + limit), groupBy);

  // Whole-suite totals per group, so a page section can report its full size.
  const totalsMap = new Map();
  for (const r of ordered) totalsMap.set(r.group, (totalsMap.get(r.group) || 0) + 1);

  return {
    count,
    next: current < totalPages ? `?page=${current + 1}&limit=${limit}` : null,
    previous: current > 1 ? `?page=${current - 1}&limit=${limit}` : null,
    current_page: current,
    total_pages: totalPages,
    results,
    fields,
    groups: pageSections(results, totalsMap),
    group_by: groupBy,
    groupings: GROUPINGS,
    scenario_editing: SCENARIO_EDITING,
  };
}

// ── Amend (edit + drop) ─────────────────────────────────────────────────────

const EDITABLE_FIELDS = SCENARIO_EDITING.editable_fields;
const PERSONA_FIELDS = SCENARIO_EDITING.persona_fields;
const REWORK_FIELDS = SCENARIO_EDITING.rework_fields;

// A set_field/set_persona op names a server field; map it to the WORKING row's
// own key. Coverage/persona keys are already snake_case, so most pass through.
const ROW_FIELD = { tests: "tests", max_turns: "max_turns", background_noise: "background_noise" };

// Resolve the amend `scenario`/`scenarios` selector against WORKING. `scenario`
// is a comma-list of names, keys, numbers, ranges or ordinals; `scenarios` is an
// array of names/keys. Returns the matched rows (deduped) and the tokens that
// matched nothing (each becomes a refused receipt).
function resolveTargets(rows, change) {
  const tokens = [];
  if (change.scenario != null) {
    String(change.scenario).split(",").map((t) => t.trim()).filter(Boolean).forEach((t) => tokens.push(t));
  }
  if (Array.isArray(change.scenarios)) {
    change.scenarios.map((t) => String(t).trim()).filter(Boolean).forEach((t) => tokens.push(t));
  }
  const matched = new Map();
  const unmatched = [];
  for (const tok of tokens) {
    let found = [];
    if (/^\d+-\d+$/.test(tok)) {
      const [lo, hi] = tok.split("-").map(Number);
      found = rows.filter((r) => r.number >= lo && r.number <= hi);
    } else if (/^\d+$/.test(tok)) {
      found = rows.filter((r) => r.number === Number(tok));
    } else {
      found = rows.filter(
        (r) => r.name === tok || r.scenario_key === tok || normalize(r.name) === normalize(tok),
      );
    }
    if (found.length) found.forEach((r) => matched.set(r.id, r));
    else unmatched.push(tok);
  }
  return { matched: [...matched.values()], unmatched };
}

// One receipt per scenario, keeping the strongest outcome if it is named more
// than once (a refusal outweighs a rework outweighs an applied write).
const OUTCOME_RANK = { applied: 0, queued: 1, reworked: 2, refused: 3 };
function recordReceipt(map, scenario, outcome, why) {
  const prev = map.get(scenario);
  if (!prev || OUTCOME_RANK[outcome] > OUTCOME_RANK[prev.outcome]) {
    map.set(scenario, { scenario, outcome, ...(why ? { why } : {}) });
  }
}

/**
 * Emulate POST /simulate/api/harness-jobs/{id}/scenarios/amend/.
 *
 * Applies `drop`, `set_field` and `set_persona` changes against WORKING and
 * returns `{ receipts: [{ scenario, outcome, why }] }`. Editability mirrors the
 * `scenario_editing` block: `tests` writes immediately (applied); behavioural
 * (`max_turns`, `background_noise`) and persona fields force a re-proof
 * (reworked when `rework:true`, else refused); everything else is refused with a
 * `why`. A drop never renumbers the survivors (§3).
 */
export function amendScenarioFixture(body = {}) {
  const { rework = false, changes = [] } = body;
  const receipts = new Map();
  const dropIds = new Set();

  for (const change of changes) {
    const { matched, unmatched } = resolveTargets(WORKING, change);
    unmatched.forEach((tok) => recordReceipt(receipts, tok, "refused", `no scenario matches "${tok}"`));

    if (change.op === "drop") {
      matched.forEach((row) => {
        dropIds.add(row.id);
        recordReceipt(receipts, row.name, "applied");
      });
      continue;
    }

    if (change.op === "set_field") {
      const { field, value } = change;
      const editable = EDITABLE_FIELDS.includes(field);
      const needsRework = REWORK_FIELDS.includes(field);
      matched.forEach((row) => {
        if (!editable) {
          recordReceipt(receipts, row.name, "refused", `${field} is not editable: it is proved, not described`);
          return;
        }
        if (needsRework && !rework) {
          recordReceipt(receipts, row.name, "refused", `${field} needs a re-proof; resend with rework: true`);
          return;
        }
        row[ROW_FIELD[field] || field] = value;
        recordReceipt(receipts, row.name, needsRework ? "reworked" : "applied");
      });
      continue;
    }

    if (change.op === "set_persona") {
      const persona = change.persona || {};
      const keys = Object.keys(persona);
      const blocked = keys.filter((k) => !PERSONA_FIELDS.includes(k));
      matched.forEach((row) => {
        if (blocked.length) {
          recordReceipt(receipts, row.name, "refused", `persona ${blocked[0]} is not editable: it is proved, not described`);
          return;
        }
        if (!rework) {
          recordReceipt(receipts, row.name, "refused", "persona changes need a re-proof; resend with rework: true");
          return;
        }
        row.persona = { ...(row.persona || {}), ...persona };
        recordReceipt(receipts, row.name, "reworked");
      });
      continue;
    }
  }

  if (dropIds.size) WORKING = WORKING.filter((r) => !dropIds.has(r.id));

  return { receipts: [...receipts.values()] };
}

// ── Coverage cross-tab ──────────────────────────────────────────────────────

// Distinct level names of a coverage axis over the given rows, alphabetical
// (the server's row/column order).
const levelsOf = (rows, axis) =>
  [...new Set(rows.map((r) => r.coverage?.[axis]).filter((v) => v != null))].sort();

// Count-desc then name, matching the server's per_axis and fields ordering.
function countMap(rows, axis) {
  const counts = {};
  for (const r of rows) {
    const v = r.coverage?.[axis];
    if (v != null) counts[v] = (counts[v] || 0) + 1;
  }
  return Object.keys(counts)
    .sort((a, b) => counts[b] - counts[a] || a.localeCompare(b))
    .reduce((acc, k) => { acc[k] = counts[k]; return acc; }, {});
}

/**
 * Emulate GET /simulate/api/harness-jobs/{id}/scenarios/coverage/.
 *
 * Takes the same `search` + object-style filters as the list, plus `row_axis` /
 * `col_axis`. Computes the cross-tab (zeros included) and per_axis breakdown
 * from the WORKING rows' `coverage{}` objects, so an amend that drops or edits a
 * scenario reshapes the grid on the next read.
 */
export function coverageScenarioFixture(params = {}) {
  const {
    search = "",
    row_axis: rowAxisParam,
    col_axis: colAxisParam,
    ...filters
  } = params;
  const rowAxis = rowAxisParam || "task";
  const colAxis = colAxisParam || "overlay";

  const searched = WORKING.filter((r) => matchesSearch(r, search));
  const filtered = applyFilters(searched, filters);

  const rows = levelsOf(filtered, rowAxis);
  const columns = levelsOf(filtered, colAxis);

  const cellCounts = {};
  for (const r of filtered) {
    const rv = r.coverage?.[rowAxis];
    const cv = r.coverage?.[colAxis];
    if (rv == null || cv == null) continue;
    cellCounts[`${rv}|${cv}`] = (cellCounts[`${rv}|${cv}`] || 0) + 1;
  }
  const cells = rows.flatMap((row) =>
    columns.map((column) => ({ row, column, count: cellCounts[`${row}|${column}`] || 0 })),
  );

  const per_axis = AXES.map((axis) => {
    const counts = countMap(filtered, axis);
    return {
      axis,
      levels: Object.keys(counts).length,
      scenarios: filtered.length,
      counts,
    };
  });

  return {
    per_axis,
    row_axis: rowAxis,
    col_axis: colAxis,
    rows,
    columns,
    cells,
    axes: AXES,
  };
}
