// DocLens model (R4 lane M, pure): JSON outputs compared field by field
// against the expected fields (each lane ✓ / ✗) or, when the row carries no
// per-field expectations, against the baseline lane. Every number in a
// sentence is counted here from the values (§C10: no digit literals in the
// templates).

/** A JSON object from a value (object or JSON text), else null. */
export function jsonObjectOf(value) {
  let v = value;
  if (typeof v === "string") {
    const t = v.trim();
    if (!t.startsWith("{")) return null;
    try {
      v = JSON.parse(t);
    } catch {
      return null;
    }
  }
  return v && typeof v === "object" && !Array.isArray(v) ? v : null;
}

/**
 * Expected fields for a lane: the lane's own `expected` (object / JSON),
 * else the lens-level `expected`, else the Field match eval's
 * `eval_details.fields` (`{field: {expected}}`) on the lane's result.
 */
export function expectedFieldsOf(lane, expected, outputKeys) {
  const fits = (o) =>
    o && (!outputKeys || Object.keys(o).some((k) => outputKeys.has(k)));
  const own = [lane?.expected, expected]
    .map((e) => jsonObjectOf(unwrapExpected(e)))
    .find(fits);
  if (own) return { fields: own, source: "row" };
  const scores = Object.values(lane?.result?.scores ?? {});
  const details = scores.find((s) => s?.eval_details?.fields)?.eval_details
    ?.fields;
  if (details && typeof details === "object")
    return {
      fields: Object.fromEntries(
        Object.entries(details).map(([k, x]) => [k, x?.expected]),
      ),
      source: "eval",
    };
  return null;
}

/** The results row's `{value: {<primary>: v}, primary}` wrapper → `v`. */
export function unwrapExpected(e) {
  if (
    e &&
    typeof e === "object" &&
    !Array.isArray(e) &&
    typeof e.primary === "string" &&
    e.value &&
    typeof e.value === "object"
  )
    return e.value[e.primary];
  return e;
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/**
 * Rows of the field table.
 * @param {{lanes: object[], expected?: *}} p
 * @returns {{rows: {key: string, expected: *, hasExpected: boolean,
 *   cells: {runId: string, value: *, present: boolean, match: boolean|null,
 *   numDelta: number|null, vs: *}[]}[], against: "expected"|"baseline"|null,
 *   source: "row"|"eval"|null, parsed: boolean[]}}
 */
export function fieldRowsOf({ lanes = [], expected } = {}) {
  const objs = lanes.map((l) => jsonObjectOf(l?.value));
  // Expected fields count only when they name at least one output field
  // (a row's free-text expected answer is not a field list).
  const outputKeys = new Set(objs.flatMap((o) => Object.keys(o ?? {})));
  const exp =
    lanes.map((l) => expectedFieldsOf(l, expected, outputKeys)).find(Boolean) ??
    null;
  const baseIdx = Math.max(
    0,
    lanes.findIndex((l) => l?.isBaseline),
  );
  const against = exp ? "expected" : objs[baseIdx] ? "baseline" : null;
  const keys = [];
  const add = (o) =>
    Object.keys(o ?? {}).forEach((k) => {
      if (!keys.includes(k)) keys.push(k);
    });
  add(exp?.fields);
  objs.forEach(add);
  const rows = keys.map((key) => {
    const hasExpected = Boolean(exp && key in exp.fields);
    const ref = hasExpected
      ? exp.fields[key]
      : against === "baseline"
        ? objs[baseIdx]?.[key]
        : undefined;
    return {
      key,
      expected: hasExpected ? exp.fields[key] : undefined,
      hasExpected,
      cells: lanes.map((l, i) => {
        const o = objs[i];
        const present = Boolean(o && key in o);
        const value = present ? o[key] : undefined;
        const compare =
          hasExpected || (against === "baseline" && i !== baseIdx);
        return {
          runId: l?.runId,
          value,
          present,
          match: compare ? present && same(value, ref) : null,
          numDelta:
            compare &&
            typeof value === "number" &&
            typeof ref === "number" &&
            value !== ref
              ? value - ref
              : null,
          vs: compare ? ref : undefined,
        };
      }),
    };
  });
  return {
    rows,
    against,
    source: exp?.source ?? null,
    parsed: objs.map(Boolean),
  };
}

/** `gemini-2.5-flash` from a lane label `#1B gemini-2.5-flash`. */
export const laneName = (lane) => {
  const parts = String(lane?.label ?? "").split(" ");
  return parts[0]?.startsWith("#") && parts.length > 1
    ? parts.slice(1).join(" ")
    : lane?.label ?? "";
};

const show = (v) => (v === undefined ? "missing" : JSON.stringify(v));

/**
 * Per lane: fields right of fields expected, and the misses. Only when the
 * table compares against expected fields.
 */
export function laneScores(table) {
  if (table.against !== "expected") return [];
  const counted = table.rows.filter((r) => r.hasExpected);
  return (table.rows[0]?.cells ?? []).map((_, i) => {
    const misses = counted
      .filter((r) => r.cells[i].match === false)
      .map((r) => ({
        key: r.key,
        got: r.cells[i].value,
        expected: r.expected,
      }));
    return {
      right: counted.length - misses.length,
      total: counted.length,
      misses,
    };
  });
}

/**
 * The summary line: the lane with the most misses (the candidate when
 * tied), e.g. `gemini-2.5-flash got 3 of 4 fields · warranty_months 24
 * (expected 12)`; every lane right → `Every run got all 4 fields`.
 */
export function docSummary(table, lanes) {
  const scores = laneScores(table);
  if (!scores.length) return null;
  let worst = -1;
  scores.forEach((s, i) => {
    if (!s.misses.length) return;
    if (worst < 0 || s.misses.length >= scores[worst].misses.length) worst = i;
  });
  if (worst < 0)
    return `Every run got all ${scores[0].total} ${scores[0].total === 1 ? "field" : "fields"}`;
  const s = scores[worst];
  const miss = s.misses
    .map((m) => `${m.key} ${show(m.got)} (expected ${show(m.expected)})`)
    .join(" · ");
  return `${laneName(lanes[worst])} got ${s.right} of ${s.total} ${s.total === 1 ? "field" : "fields"} · ${miss}`;
}
