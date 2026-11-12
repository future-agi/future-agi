// R3 (SVP-4.7, RC#1, RC#5): the plain-language pieces of the results grid.
// Pure: no React, no I/O.
//   expectedSentence   the checks of a row as one sentence
//   expectedChecklist  the checks as ✓ / ✕ lines against one result
//   primaryPassMetric  the pass/fail eval a run cell's dot reads
//   trialOutcome       `2/3 trials` + dot tone of one result
//   whyChips           why a row is in Regressed / Improved / Changed
import { fmtCount } from "src/sections/common/format/fmt";

const MAX_CLAUSES = 3;

const isPassValue = (v) =>
  v === 1 || v === true || v === "pass" || v === "Pass";

/** The checks list of an `expected` value (task suites), or null. */
export function checksOf(expected) {
  const v = expected?.value ?? expected;
  const list = Array.isArray(v?.checks)
    ? v.checks
    : Array.isArray(v)
      ? v
      : null;
  if (!list?.length) return null;
  const items = list.filter(
    (c) => c && typeof c === "object" && (c.description || c.name),
  );
  return items.length ? items : null;
}

const clauseOf = (c) => String(c.description || c.name).trim();

/** `a`, `a and b`, `a, b and c`, `a, b, c and 2 more`. */
export function joinClauses(clauses, max = MAX_CLAUSES) {
  const list = (clauses ?? []).filter(Boolean);
  if (!list.length) return "";
  if (list.length <= max) {
    if (list.length === 1) return list[0];
    return `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
  }
  return `${list.slice(0, max).join(", ")} and ${fmtCount(list.length - max, "more check", "more checks")}`;
}

/**
 * One sentence for a row's expected outcome: the check descriptions for a
 * task suite (`lookup_order returned A-8830, exactly one refund on A-8830
 * and 2 more checks`), else the primary expected text. `prefix: true` adds
 * `Expected: `.
 */
export function expectedSentence(expected, { prefix = false, max } = {}) {
  const checks = checksOf(expected);
  let body = "";
  if (checks) body = joinClauses(checks.map(clauseOf), max);
  else {
    const name = expected?.primary;
    const value = name ? expected?.value?.[name] : expected?.value;
    body =
      typeof value === "string"
        ? value
        : value == null
          ? ""
          : typeof value === "object"
            ? Object.values(value).find(
                (x) => typeof x === "string" && x.trim(),
              ) ?? JSON.stringify(value)
            : String(value);
  }
  if (!body) return "";
  return prefix ? `Expected: ${body}` : body;
}

/**
 * The checks as a checklist against one result: `[{key, text, passed}]`,
 * `passed` true / false / null (not reached or unknown). Uses per-check
 * results when the API sends them (`trials_detail[t].checks[]` or
 * `scores["check:<key>"]`); else reads the pass/fail eval of that trial:
 * a pass ticks every check; a fail names its first failed check in the
 * reason (`Order found failed; later evals were not reached.`), so the
 * checks before it passed and the ones after were not reached.
 */
export function expectedChecklist(
  expected,
  result,
  { trial = 1, metricKey } = {},
) {
  const checks = checksOf(expected);
  if (!checks) return [];
  const t = Math.max(1, trial) - 1;
  const fromTrial = Object.fromEntries(
    (result?.trials_detail?.[t]?.checks ?? []).map((c) => [c.key, c.passed]),
  );
  const perCheck = (c) => {
    if (c.key in fromTrial)
      return fromTrial[c.key] == null ? null : Boolean(fromTrial[c.key]);
    const sc = result?.scores?.[`check:${c.key}`];
    if (sc?.trials?.length)
      return sc.trials[t] == null ? null : isPassValue(sc.trials[t]);
    return undefined;
  };
  const direct = checks.map(perCheck);
  if (direct.every((v) => v !== undefined))
    return checks.map((c, i) => ({
      key: c.key ?? clauseOf(c),
      text: clauseOf(c),
      passed: direct[i],
    }));
  const s = metricKey ? result?.scores?.[metricKey] : null;
  const v = s?.trials?.[t] ?? (t === 0 ? s?.value : null);
  const trialPassed = v == null ? null : isPassValue(v);
  const reason = String(s?.reason ?? "");
  const failedIdx = checks.findIndex(
    (c) => c.name && reason.startsWith(`${c.name} failed`),
  );
  return checks.map((c, i) => {
    let passed = direct[i] !== undefined ? direct[i] : null;
    if (direct[i] === undefined) {
      if (trialPassed === true) passed = true;
      else if (trialPassed === false && failedIdx >= 0)
        passed = i < failedIdx ? true : i === failedIdx ? false : null;
    }
    return { key: c.key ?? clauseOf(c), text: clauseOf(c), passed };
  });
}

/** The first pass/fail eval (Task success), else the first eval, or null. */
export function primaryPassMetric(metrics) {
  const list = metrics ?? [];
  return (
    list.find((m) => m.kind === "eval" && m.output_type === "pass_fail") ??
    list.find((m) => m.output_type === "pass_fail") ??
    null
  );
}

const isScored = (v) =>
  v === 0 ||
  v === 1 ||
  v === true ||
  v === false ||
  v === "pass" ||
  v === "fail" ||
  v === "Pass" ||
  v === "Fail";

/**
 * Trial outcome of a result on the primary pass/fail eval:
 * `{pass, n, tone: "pass"|"fail"|"flaky"|"none", label}` where `label` is
 * `Pass` / `Fail` for one trial and `2/3 trials` for k.
 */
export function trialOutcome(result, metricKey) {
  const s = metricKey ? result?.scores?.[metricKey] : null;
  const values = (
    s?.trials?.length ? s.trials : s?.value != null ? [s.value] : []
  ).filter(isScored);
  const n = values.length;
  if (!n) return { pass: 0, n: 0, tone: "none", label: "Not scored", dots: [] };
  const pass = values.filter(isPassValue).length;
  const tone = pass === n ? "pass" : pass === 0 ? "fail" : "flaky";
  const label = n === 1 ? (pass ? "Pass" : "Fail") : `${pass}/${n} trials`;
  return { pass, n, tone, label, dots: values.map(isPassValue) };
}

const scoreText = (s) => {
  if (!s) return "—";
  if (s.display) return String(s.display);
  if (typeof s.value === "number")
    return String(Math.round(s.value * 100) / 100);
  return "—";
};

/**
 * Why a row is in the Show set: up to `max` chips, worst first,
 * `#2A Task success 3/3 → 0/3` (red when it got worse).
 * @returns {{runId, metricKey, text, tone: "bad"|"good"}[]}
 */
export function whyChips(
  row,
  {
    runIds = [],
    baselineId,
    defsByKey = {},
    runsById = {},
    show = "changed",
    max = 2,
  } = {},
) {
  const base = row?.results?.[baselineId];
  const out = [];
  runIds
    .filter((id) => id !== baselineId)
    .forEach((runId) => {
      const r = row?.results?.[runId];
      Object.entries(r?.movement ?? {}).forEach(([key, move]) => {
        if (move !== "regressed" && move !== "improved") return;
        if (show === "regressed" && move !== "regressed") return;
        if (show === "improved" && move !== "improved") return;
        const def = defsByKey[key];
        if (def && def.kind && def.kind !== "eval") return;
        const delta = r?.delta?.[key];
        out.push({
          runId,
          metricKey: key,
          tone: move === "regressed" ? "bad" : "good",
          weight:
            Math.abs(typeof delta === "number" ? delta : 1) +
            (move === "regressed" ? 10 : 0),
          text: `${runsById[runId]?.label ?? ""} ${def?.name ?? key} ${scoreText(base?.scores?.[key])} → ${scoreText(r?.scores?.[key])}`.trim(),
        });
      });
    });
  return out
    .sort((a, b) => b.weight - a.weight)
    .slice(0, max)
    .map(({ weight, ...c }) => c);
}
