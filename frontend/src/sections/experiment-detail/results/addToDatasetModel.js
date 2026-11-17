// AddResultsToDatasetDialog model (PRODUCT-SPEC §3.10.8, C11). Pure.

/**
 * The run the rows come from: the run of a movement pill if one is active,
 * else the focused / first candidate, else the only run.
 */
export function sourceRunFor({ pills, runs, baselineId, focusedRunId }) {
  const movement = (pills ?? []).find(
    (p) => p.type === "movement" && p.run && !p.run.startsWith("any_"),
  );
  if (movement && runs.some((r) => r.run_id === movement.run))
    return movement.run;
  if (focusedRunId && runs.some((r) => r.run_id === focusedRunId))
    return focusedRunId;
  return (
    runs.find((r) => r.run_id !== baselineId)?.run_id ?? runs[0]?.run_id ?? null
  );
}

/** "regressions" / "improvements" / "changes" / "results" from the pills. */
export function sourceKind(pills, runId) {
  const m = (pills ?? []).find(
    (p) =>
      p.type === "movement" && (p.run === runId || p.run?.startsWith?.("any_")),
  );
  if (m?.dir === "regressed") return "regressions";
  if (m?.dir === "improved") return "improvements";
  if (m?.dir === "changed") return "changes";
  if ((pills ?? []).some((p) => p.type === "fails")) return "failures";
  if ((pills ?? []).some((p) => p.type === "error")) return "errors";
  return "results";
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** iterate r1 G3 (P7): splits a result set usually gets tagged with. */
export const SUGGESTED_SPLITS = Object.freeze(["hard", "regression"]);

/**
 * The version note. This dataset: `Tagged 7 rows as hard from #4A
 * regressions` (`… from #2G review` for picked results), `Updated the
 * expected value of 3 rows from #2G`, or "" until something is picked (the
 * note never claims a change that is not there); other datasets: `Added 7
 * rows from #4A regressions`.
 */
export function defaultDescription({
  count,
  runLabel,
  kind,
  splitNames,
  target,
  updateExpected = false,
}) {
  const what = kind === "results" ? "review" : kind;
  const from = runLabel ? `from ${runLabel} ${what}` : `from ${what}`;
  if (target === "same" && splitNames?.length)
    return `Tagged ${plural(count, "row")} as ${splitNames.join(", ")} ${from}`;
  if (target === "same")
    return updateExpected
      ? `Updated the expected value of ${plural(count, "row")}${runLabel ? ` from ${runLabel}` : ""}`
      : "";
  return `Added ${plural(count, "row")} ${from}`;
}

/**
 * iterate r2 G3 (P6): how many of the picked rows each split already holds
 * (`{[splitId]: n}`), from the rows' `split_ids` (a fact, not a number to
 * compute elsewhere).
 */
export function splitMembership(rows, splits) {
  const out = {};
  (splits ?? []).forEach((s) => {
    out[s.id] = (rows ?? []).filter((r) =>
      (r?.split_ids ?? []).includes(s.id),
    ).length;
  });
  return out;
}

/** `3 of 3 already in hard` (or the bare name when none is in it). */
export const splitChipLabel = (name, inSplit, count) =>
  inSplit > 0 ? `${inSplit} of ${count} already in ${name}` : name;

/**
 * P6: the save would change nothing (every picked split already holds every
 * row, nothing new, no expected update) → the reason the button is off:
 * `All 3 rows are already tagged hard — pick another split`, else null.
 */
export function noOpReason({
  count,
  splitIds,
  splitNames,
  newSplits,
  updateExpected,
  membership,
}) {
  if (!count || !splitIds?.length || newSplits?.length || updateExpected)
    return null;
  const all = splitIds.every((id) => (membership?.[id] ?? 0) >= count);
  if (!all) return null;
  const rowsText = count === 1 ? "The row is" : `All ${count} rows are`;
  return `${rowsText} already tagged ${splitNames.join(", ")} — pick another split`;
}

/**
 * P7: `Tag 3 rows with a split` (this dataset) / `Add to dataset`.
 * integration (DI-06 e): with the third argument the title follows the action —
 * this dataset: splits `Tag 2 rows with a split`, expected only `Use #4A's
 * output as expected (2 rows)`, both `Tag 2 rows and update expected`;
 * another or a new dataset `Add 2 rows to a dataset`.
 */
export const dialogTitleFor = (target, count, action) => {
  if (!action)
    return target === "same"
      ? `Tag ${plural(count, "row")} with a split`
      : "Add to dataset";
  if (target !== "same") return `Add ${plural(count, "row")} to a dataset`;
  const { splits = false, updateExpected = false, runLabel } = action;
  if (updateExpected && splits)
    return `Tag ${plural(count, "row")} and update expected`;
  if (updateExpected)
    return `Use ${runLabel ? `${runLabel}'s` : "the run's"} output as expected (${plural(count, "row")})`;
  return `Tag ${plural(count, "row")} with a split`;
};

/**
 * integration (DI-06 b): the Columns step's start — each source user column to
 * the target column of the same name, else null (Skip).
 * `{[sourceColumnId]: targetColumnId | null}`.
 */
export function defaultColumnMap(sourceCols, targetCols) {
  const byName = new Map((targetCols ?? []).map((c) => [c.name, c.id]));
  return Object.fromEntries(
    (sourceCols ?? []).map((c) => [c.id, byName.get(c.name) ?? null]),
  );
}

/** `{mapped, total}` of a column map over the source columns. */
export function columnMapCount(sourceCols, map) {
  const total = (sourceCols ?? []).length;
  const mapped = (sourceCols ?? []).filter((c) => map?.[c.id]).length;
  return { mapped, total };
}

/** `2 of 3 columns map`. */
export const columnMapText = ({ mapped, total }) =>
  `${mapped} of ${total} ${total === 1 ? "column" : "columns"} map`;

/**
 * The disabled reason when nothing maps (the same sentence the server's 400
 * answers): `None of <source>'s columns map to <target>. Pick a column for
 * at least one.`
 */
export const noColumnsMapReason = (sourceName, targetName) =>
  `None of ${sourceName ?? "the source"}'s columns map to ${targetName ?? "the target"}. Pick a column for at least one.`;

/**
 * integration review (DI-06 d): true when the action reads the run's output —
 * this dataset with `Use …'s output as the expected value`, or another / a
 * new dataset with `output` or `output_review`. The try picker shows only
 * then (a tag-only or keep-expected save reads no output).
 */
export const readsRunOutput = ({ target, updateExpected, expectedFrom }) =>
  target === "same"
    ? Boolean(updateExpected)
    : expectedFrom === "output" || expectedFrom === "output_review";

/**
 * integration review (DI-06 d): one R3 result's output for a try (1-based) — the
 * text the save writes, so the preview shows the same try. Try 1 is
 * `output`; later tries read `trial_outputs[trial - 1]` (null when the row
 * has no such try).
 */
export function trialOutputOf(result, trial = 1) {
  const t = Number(trial) || 1;
  if (t <= 1) return result?.trial_outputs?.[0] ?? result?.output ?? null;
  return result?.trial_outputs?.[t - 1] ?? null;
}

/**
 * integration review 2 (DI-06 d): a row's expected as it stands now — the latest
 * version's value when it changed after the baseline (`expected.latest`),
 * else the baseline's; the primary expected column when there are several.
 */
export function latestExpectedOf(row) {
  const e = row?.expected;
  if (e == null || typeof e !== "object" || Array.isArray(e)) return e ?? null;
  const pick = (v) =>
    e.primary && v && typeof v === "object" ? v[e.primary] ?? null : v ?? null;
  return e.latest != null ? pick(e.latest) : pick(e.value);
}

const asText = (v) =>
  v == null ? null : typeof v === "string" ? v : JSON.stringify(v);
// The grid's copy of a long output is cut and ends with `…`.
const CUT_MARK = "…";

/**
 * integration review 2 (DI-06 d): what `Use …'s output as the expected value`
 * changes, read before the click from the rows on the page. Per picked row:
 *  - `change`: the try's output differs from the expected as it stands;
 *  - `same`: they are already equal (the save leaves the row as is);
 *  - `none`: the run has no output for that try (left as is);
 *  - `unsure`: the row is not on the page, or the grid's output is cut
 *    (`…`) where the expected starts with the same text.
 * @returns {{change: object[], same: object[], none: object[],
 *   unsure: object[], upTo: number}}  rows (or `{example_id}` when not on
 *   the page); `upTo` = change + unsure
 */
export function expectedPlanOf({ rows, exampleIds, runId, trial = 1 }) {
  const byId = new Map((rows ?? []).map((r) => [r.example_id, r]));
  const plan = { change: [], same: [], none: [], unsure: [] };
  (exampleIds ?? []).forEach((id) => {
    const row = byId.get(id);
    if (!row) {
      plan.unsure.push({ example_id: id });
      return;
    }
    const out = asText(trialOutputOf(row.results?.[runId], trial));
    if (out == null) {
      plan.none.push(row);
      return;
    }
    const now = asText(latestExpectedOf(row));
    if (out === now) plan.same.push(row);
    else if (
      out.endsWith(CUT_MARK) &&
      typeof now === "string" &&
      now.startsWith(out.slice(0, -CUT_MARK.length))
    )
      plan.unsure.push(row);
    else plan.change.push(row);
  });
  return { ...plan, upTo: plan.change.length + plan.unsure.length };
}

/**
 * The button's verb for the plan: `Replace 1 expected value` /
 * `Replace up to 3 expected values` (some rows cannot be compared here).
 */
export const replaceExpectedText = ({ change, unsure, upTo }) =>
  unsure.length
    ? `Replace up to ${plural(upTo, "expected value")}`
    : `Replace ${plural(change.length, "expected value")}`;

/**
 * The disabled reason when no picked row would change (`plan.upTo` is 0):
 * `Nothing to replace: the expected values already equal #4A's try 2
 * output.`, or, when no picked row has that try, `Nothing to replace: #4A
 * has no try 2 output on these rows.`
 */
export function nothingToReplaceText(plan, runLabel, trial = 1) {
  const who = runLabel ? `${runLabel}'s` : "the run's";
  if (!plan?.same?.length)
    return `Nothing to replace: ${runLabel ?? "the run"} has no try ${trial} output on ${(plan?.none?.length ?? 0) === 1 ? "this row" : "these rows"}.`;
  return `Nothing to replace: the expected values already equal ${who} try ${trial} output.`;
}

/** `Nothing changed: the expected values already equal #4A's try 1 output.` */
export const nothingChangedText = (runLabel, trial = 1) =>
  `Nothing changed: the expected values already equal ${runLabel ? `${runLabel}'s` : "the run's"} try ${trial} output.`;

/**
 * Toast after R6. This dataset with splits: `Saved v7 · 6 rows tagged hard
 * (1 was already in it)`; otherwise `2 rows added to Misses · v1`.
 */
export function successText(
  result,
  { splitNames, target, datasetName, runLabel, updateExpected = false },
) {
  const v = result?.version?.number ? ` · v${result.version.number}` : "";
  // integration (DI-06 d): an expected-only save whose values already equal the
  // output saves no version and says so.
  if (
    target === "same" &&
    updateExpected &&
    !splitNames?.length &&
    !result?.rows_updated &&
    !result?.version?.number
  )
    return nothingChangedText(runLabel, result?.trial ?? 1);
  if (target === "same" && splitNames?.length) {
    const moved = result.split_changes ?? 0;
    const already = result.already_in_split ?? 0;
    const head = result?.version?.number
      ? `Saved v${result.version.number} · `
      : "";
    const expected = result.rows_updated
      ? ` · ${plural(result.rows_updated, "expected value")} updated`
      : "";
    if (!moved && !result.rows_updated)
      return result?.version?.number
        ? `Saved v${result.version.number} · no split changes`
        : `No changes: every row was already tagged ${splitNames.join(", ")}`;
    return `${head}${plural(moved, "row")} tagged ${splitNames.join(", ")}${already ? ` (${already} ${already === 1 ? "was" : "were"} already in it)` : ""}${expected}`;
  }
  if (target === "same") {
    const parts = [];
    if (splitNames?.length) {
      const moved = result.split_changes ?? 0;
      const already = result.already_in_split ?? 0;
      parts.push(
        `${plural(moved, "row")} added to split ${splitNames.join(", ")}${already ? ` (${already} ${already === 1 ? "was" : "were"} already in it)` : ""}`,
      );
    }
    if (result.rows_updated)
      parts.push(`${plural(result.rows_updated, "expected value")} updated`);
    // P6: `Save a version without tagging` saves the note as a version.
    if (!parts.length)
      return result?.version?.number
        ? `Saved v${result.version.number} · no rows tagged`
        : "No changes: every row was already there";
    return `${parts.join(" · ")}${v}`;
  }
  return `${plural(result.rows_added ?? 0, "row")} added to ${datasetName ?? "the dataset"}${v}`;
}

/** The R6 body. */
export function r6Body({
  datasetId,
  runId,
  exampleIds,
  target,
  splitIds,
  newSplits,
  updateExpected,
  expectedFrom,
  description,
  newDatasetName,
  saveVersion = false,
  trial = 1,
  columnMap = null,
  needsReview = false,
}) {
  const same = target === "same";
  return {
    // integration (DI-06): the try whose output is read; the Columns step's map
    // (another dataset); `reply as expected · needs review` marks the rows.
    trial,
    ...(target === "other" && columnMap ? { column_map: columnMap } : {}),
    ...(!same && needsReview
      ? { needs_review: true, expected_source: "production_output" }
      : {}),
    // P6: a version with the note and no split change.
    ...(same && saveVersion ? { save_version: true } : {}),
    source_dataset_id: datasetId,
    run_id: runId,
    example_ids: exampleIds,
    mode: same ? "same_dataset" : "copy",
    split_ids: same ? splitIds : [],
    new_splits: same ? newSplits.map((name) => ({ name })) : [],
    expected_from: same ? (updateExpected ? "output" : "keep") : expectedFrom,
    version_description: description,
    ...(target === "new" ? { new_dataset_name: newDatasetName } : {}),
  };
}

/**
 * L7c g6 (bulk:tag-rows): mapped columns that land in a column of another
 * name or role (`expected_reply → question`), so the copy is never silent
 * about it. `[{from, to}]`.
 */
export function crossMappedColumns(sourceCols, targetCols, map) {
  const byId = new Map((targetCols ?? []).map((t) => [t.id, t]));
  return (sourceCols ?? [])
    .map((c) => ({ c, t: map?.[c.id] ? byId.get(map[c.id]) : null }))
    .filter(
      ({ c, t }) =>
        t && (t.name !== c.name || (c.role && t.role && c.role !== t.role)),
    )
    .map(({ c, t }) => ({ from: c.name, to: t.name, role: t.role ?? null }));
}

/** `2 columns go to a column with another name: a → b, c → d.` */
export const crossMapText = (pairs) =>
  `${pairs.length} ${pairs.length === 1 ? "column goes" : "columns go"} into a column with another name or role: ${pairs
    .map((p) => `${p.from} → ${p.to}${p.role ? ` (${p.role})` : ""}`)
    .join(", ")}. Check this is where the values belong before you add.`;

// ---------------------------------------------------------------------------
// Copy result rows into another or a new dataset: POST
// datasets/{dataset id | new}/rows/from-results/ with mode `copy`. The server
// copies the picked try's saved values only (inputs, output, chosen scores);
// it never tags splits, replaces values in place or saves a version.

/** One request copies 1 to 1,000 rows and up to 100 columns. */
export const MAX_COPY_ROWS = 1000;
export const MAX_COPY_COLUMNS = 100;
/** Longest dataset name and note the server takes. */
export const MAX_COPY_TEXT = 2000;

/** The Expected value choice (`keep`, `none`, `output`, `output_review`) → `expected_from`. */
export function copyExpectedFrom(choice) {
  if (choice === "none") return "empty";
  if (choice === "output" || choice === "output_review") return "output";
  return "keep";
}

export const isExternalRun = (run) => run?.task_kind === "external";

/**
 * A run on this dataset's rows copies the inputs saved with the run
 * (`inputs.values`, keyed by this dataset's column id), not the dataset's
 * columns as they are now: a column added after the run has no saved value.
 * `{saved, unsaved}` split this dataset's columns by whether any picked row
 * saved a value for them. Without saved inputs on the page (the rows are not
 * loaded) every column is kept and the server says which rows lack one.
 */
export function savedInputColumns(sourceCols, rows) {
  const cols = sourceCols ?? [];
  const keys = new Set();
  (rows ?? []).forEach((r) => {
    const values = r?.inputs?.values;
    if (values && typeof values === "object" && !Array.isArray(values))
      Object.keys(values).forEach((k) => keys.add(k));
  });
  if (!keys.size) return { saved: cols, unsaved: [] };
  return {
    saved: cols.filter((c) => keys.has(c.id)),
    unsaved: cols.filter((c) => !keys.has(c.id)),
  };
}

/**
 * The result columns a copy can carry, in order, as `{key, label, name,
 * kind}`: `key` is the server's source selector and `name` the column name
 * in a new dataset.
 *  - inputs: each column saved with a run on this dataset's rows (see
 *    `savedInputColumns`), else the whole saved input (`inputs`);
 *  - `expected`: the row's expected when it is not one of those columns, or
 *    the run's output when that is copied as the expected value;
 *  - `output`: the picked try's output;
 *  - `score:<metric id>`: each score of the run (copied only when picked).
 */
export function copySources({ run, sourceCols, metrics, expectedChoice }) {
  const cols = isExternalRun(run) ? [] : sourceCols ?? [];
  const lines = cols.length
    ? cols.map((c) => ({
        key: c.id,
        label: c.name,
        name: c.name,
        kind: "input",
      }))
    : [{ key: "inputs", label: "Inputs", name: "input", kind: "input" }];
  const from = copyExpectedFrom(expectedChoice);
  if (from === "output")
    lines.push({
      key: "expected",
      label: `Expected (${run?.label ? `${run.label}'s` : "the run's"} output)`,
      name: "expected",
      kind: "expected",
    });
  else if (from === "keep" && !cols.length)
    lines.push({
      key: "expected",
      label: "Expected",
      name: "expected",
      kind: "expected",
    });
  lines.push({
    key: "output",
    label: run?.label ? `Output (${run.label})` : "Output",
    name: "output",
    kind: "output",
  });
  (metrics ?? []).forEach((m) => {
    if (!m?.key) return;
    if (Array.isArray(m.runs) && run?.run_id && !m.runs.includes(run.run_id))
      return;
    const name = m.name || "Score";
    lines.push({
      key: `score:${m.key}`,
      label: `${name} (score)`,
      name,
      kind: "score",
    });
  });
  return lines;
}

const DEFAULT_TARGET_NAMES = {
  inputs: ["input", "inputs"],
  expected: ["expected"],
  output: ["output"],
};

/**
 * Another dataset's column map, `{[key]: targetColumnId | null}`: a pick
 * still valid on these target columns is kept as picked (two picks of one
 * column are said by `sharedTargetName`, never undone); any other line goes
 * to the target column of its name (`input`, `expected`, `output` for the
 * result's own values) when no line has it yet; scores start skipped. The
 * expected line claims first, so the run's output used as the expected
 * value lands in the target's `expected` column.
 */
export function defaultCopyMap(lines, targetCols, prev = {}) {
  const ids = new Set((targetCols ?? []).map((t) => t.id));
  const byName = new Map();
  (targetCols ?? []).forEach((t) => {
    if (!byName.has(t.name)) byName.set(t.name, t.id);
  });
  const map = {};
  const used = new Set();
  (lines ?? []).forEach((l) => {
    if (!Object.prototype.hasOwnProperty.call(prev ?? {}, l.key)) return;
    const v = prev[l.key];
    if (v === null) map[l.key] = null;
    else if (ids.has(v)) {
      map[l.key] = v;
      used.add(v);
    }
  });
  const order = [
    ...(lines ?? []).filter((l) => l.kind === "expected"),
    ...(lines ?? []).filter((l) => l.kind !== "expected"),
  ];
  order.forEach((l) => {
    if (l.key in map) return;
    if (l.kind === "score") {
      map[l.key] = null;
      return;
    }
    const hit = (DEFAULT_TARGET_NAMES[l.key] ?? [l.name])
      .map((n) => byName.get(n))
      .find((id) => id && !used.has(id));
    map[l.key] = hit ?? null;
    if (hit) used.add(hit);
  });
  return map;
}

/** A new dataset's copied lines, `{[key]: boolean}`: every line but the scores. */
export function defaultIncluded(lines, prev = {}) {
  return Object.fromEntries(
    (lines ?? []).map((l) => [
      l.key,
      Object.prototype.hasOwnProperty.call(prev ?? {}, l.key)
        ? Boolean(prev[l.key])
        : l.kind !== "score",
    ]),
  );
}

/**
 * The new dataset's column names, `{[key]: name}` for the copied lines
 * (`forced` lines always), each name used once (`output (2)`).
 */
export function newColumnNames(lines, included, forced = []) {
  const taken = new Set();
  const out = {};
  (lines ?? []).forEach((l) => {
    if (!included?.[l.key] && !forced.includes(l.key)) return;
    const base = String(l.name ?? "").trim() || "column";
    let name = base;
    for (let n = 2; taken.has(name); n += 1) name = `${base} (${n})`;
    taken.add(name);
    out[l.key] = name;
  });
  return out;
}

/** The target column two lines both go to (its name), else null. */
export function sharedTargetName(map, targetCols) {
  const seen = new Set();
  const dup = Object.values(map ?? {}).find((v) => {
    if (!v) return false;
    if (seen.has(v)) return true;
    seen.add(v);
    return false;
  });
  if (!dup) return null;
  return (targetCols ?? []).find((t) => t.id === dup)?.name ?? "one column";
}

/** `{[key]: name | column id}` without the skipped lines. */
const mappedOnly = (map) =>
  Object.fromEntries(
    Object.entries(map ?? {}).filter(([, v]) => v != null && v !== ""),
  );

const scoreIdsOf = (map) =>
  Object.keys(map)
    .filter((k) => k.startsWith("score:"))
    .map((k) => k.slice("score:".length));

/** The copy request body (the target id goes in the URL, never the body). */
export function copyBody({
  datasetId,
  runId,
  exampleIds,
  target,
  trial = 1,
  expectedChoice = "keep",
  columnMap,
  newDatasetName,
  note = "",
}) {
  const map = mappedOnly(columnMap);
  return {
    source_dataset_id: datasetId,
    run_id: runId,
    example_ids: exampleIds,
    trial,
    mode: "copy",
    column_map: map,
    score_columns: scoreIdsOf(map),
    expected_from: copyExpectedFrom(expectedChoice),
    ...(expectedChoice === "output_review"
      ? { needs_review: true, expected_source: "production_output" }
      : {}),
    version_description: note,
    ...(target === "new" ? { new_dataset_name: newDatasetName } : {}),
  };
}

const FAILURE_TEXT = {
  example_unavailable: "This row or try is not in the run.",
  result_unavailable: "This try has no finished result to copy.",
  output_unavailable: "This result has no saved output.",
  score_unavailable: "A chosen score was not given for this try.",
  input_unavailable: "A mapped input is missing from the saved row.",
  invalid_result: "The saved score does not match this run.",
  invalid_value: "A value does not fit the destination column.",
};

/**
 * A failed row that a second send of the same copy can add: its try had no
 * finished result yet, or the server gave a reason this page does not know.
 * Every other reason comes back the same on the same run, try and columns.
 */
export const isRetryableFailure = (failure) =>
  failure?.code === "result_unavailable" ||
  !Object.prototype.hasOwnProperty.call(FAILURE_TEXT, failure?.code);

/**
 * After some rows were added: the same copy for the failed rows that can
 * still be added, sent to the dataset the rows went to with the columns the
 * server resolved there (never a second new dataset).
 */
export function retryBody(body, result) {
  const rest = { ...body };
  delete rest.new_dataset_name;
  const map = mappedOnly(result?.columnMap);
  return {
    ...rest,
    example_ids: (result?.failures ?? [])
      .filter(isRetryableFailure)
      .map((f) => f.example_id),
    column_map: map,
    score_columns: scoreIdsOf(map),
  };
}

const INTERNAL_TEXT = /Traceback|Stack\s*trace|SELECT\s|DB::|ClickHouse/i;
// The client's own words when the answer had no body; never a reason.
const CLIENT_TEXT =
  /^(Request failed with status code \d+|Network Error|timeout of \d+ms exceeded|Something went wrong)$/i;

/** A server sentence fit to show as it is (short, one line), else null. */
export function safeServerText(value) {
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!text || text.length > 240 || /[\r\n]/.test(text)) return null;
  return INTERNAL_TEXT.test(text) || CLIENT_TEXT.test(text) ? null : text;
}

const DATASET_PATH = /^\/dashboard\/develop\/[A-Za-z0-9-]+\?tab=data$/;

/** Where the copied rows are: the answer's own dataset link, else built from its id. */
export function datasetLinkOf(result) {
  if (
    typeof result?.dataset_url === "string" &&
    DATASET_PATH.test(result.dataset_url)
  )
    return result.dataset_url;
  return result?.dataset_id
    ? `/dashboard/develop/${encodeURIComponent(result.dataset_id)}?tab=data`
    : null;
}

/** The answer's `result`, read without trusting any count to be there. */
export function copyResultOf(result) {
  if (!result || typeof result !== "object" || Array.isArray(result))
    return null;
  if (!Number.isInteger(result.rows_added)) return null;
  const failures = Array.isArray(result.failures)
    ? result.failures.filter((f) => f && f.example_id)
    : [];
  const failed = Number.isInteger(result.rows_failed)
    ? Math.max(result.rows_failed, failures.length)
    : failures.length;
  return {
    datasetId: result.dataset_id ?? null,
    datasetName: result.dataset_name ?? null,
    link: datasetLinkOf(result),
    created: result.created === true,
    requested: Number.isInteger(result.rows_requested)
      ? result.rows_requested
      : result.rows_added + failed,
    added: result.rows_added,
    failed,
    failures,
    columnMap:
      result.column_map && typeof result.column_map === "object"
        ? result.column_map
        : {},
  };
}

/**
 * A 2xx answer: `completed` (every row added), `partial` (some rows added,
 * the rest listed), `failed` (none added) or `unknown` (an answer that does
 * not say what was added — never read as success).
 */
export function copyOutcomeOf(res) {
  const result = copyResultOf(res?.data?.result);
  if (!result) return { kind: "unknown" };
  if (result.added === 0) return { kind: "failed", result };
  if (result.failed > 0) return { kind: "partial", result };
  return { kind: "completed", result };
}

const REFUSAL_TEXT = {
  400: "The rows could not be added. Check your choices and try again.",
  401: "Sign in again to add rows.",
  403: "You do not have permission to add rows to this dataset.",
  404: "This dataset or run is no longer available.",
  409: "This run's saved rows are not available to copy.",
  429: "The dataset or row limit has been reached. No rows were added.",
  503: "The rows could not be saved. No rows were added.",
};

/**
 * A failed request: `failed` (422, nothing added, every row listed),
 * `refused` (nothing added; the server's reason, a 403 from its `detail`)
 * or `unknown` (no answer, or a server failure without the endpoint's own
 * refusal: the rows may have been added).
 *
 * The app's client (src/utils/axios) rejects with the answer's body spread
 * out plus its HTTP status as `statusCode` (`{...body, statusCode,
 * transportCode}`, no `response`); a raw Axios error keeps both under
 * `response`. Both are read. A body's own `status` is its `true`/`false`
 * flag, never the HTTP status.
 */
export function copyErrorOf(error) {
  if (!error || typeof error !== "object") return { kind: "unknown" };
  const raw =
    error.response && typeof error.response === "object"
      ? error.response
      : null;
  const status = Number(raw ? raw.status : error.statusCode);
  if (!Number.isInteger(status) || status < 400) return { kind: "unknown" };
  const data = raw
    ? raw.data && typeof raw.data === "object"
      ? raw.data
      : {}
    : error;
  const text =
    safeServerText(data.detail) ??
    safeServerText(data.message) ??
    safeServerText(data.error) ??
    safeServerText(data.result);
  if (status === 422) {
    const result = copyResultOf(data.result);
    if (result) return { kind: "failed", status, message: text, result };
  }
  if (status >= 500 && !(status === 503 && data.status === false))
    return { kind: "unknown", status };
  return {
    kind: "refused",
    status,
    message: text ?? REFUSAL_TEXT[status] ?? "The rows could not be added.",
  };
}

/** `Row 18` (a numbered row) or the row's own label; null when not on the page. */
export function rowLabelOf(row) {
  const label = row?.row_label;
  if (label == null || label === "")
    return Number.isInteger(row?.row_order) ? `Row ${row.row_order + 1}` : null;
  return /^\d+$/.test(String(label)) ? `Row ${label}` : String(label);
}

/**
 * The rows that could not be copied, one group per reason, in the server's
 * order: `[{text, count, labels}]` (`labels` = the rows on the page).
 */
export function failureGroups(failures, rows) {
  const labels = new Map(
    (rows ?? []).map((r) => [r.example_id, rowLabelOf(r)]),
  );
  const groups = new Map();
  (failures ?? []).forEach((f) => {
    const text =
      FAILURE_TEXT[f.code] ??
      safeServerText(f.message) ??
      "This row could not be copied.";
    if (!groups.has(text)) groups.set(text, { text, count: 0, labels: [] });
    const group = groups.get(text);
    group.count += 1;
    const label = labels.get(f.example_id);
    if (label) group.labels.push(label);
  });
  return [...groups.values()];
}

const nameOf = (result, fallback) =>
  result?.datasetName ?? fallback ?? "the dataset";

/** `2 rows added to Golden`. */
export const copyDoneText = (result, fallbackName) =>
  `${plural(result.added, "row")} added to ${nameOf(result, fallbackName)}`;

/** `Added 3 of 5 rows to Golden. 2 could not be copied.` */
export const copyPartialText = (result, fallbackName) =>
  `Added ${result.added} of ${plural(result.requested, "row")} to ${nameOf(result, fallbackName)}. ${result.failed} could not be copied.`;

/** `No rows were added and no dataset was created. 2 rows could not be copied.` */
export const copyFailedText = (result, { newDataset = false } = {}) =>
  `No rows were added${newDataset ? " and no dataset was created" : ""}. ${plural(result.failed, "row")} could not be copied.`;

/** The answer never came back: the rows may be there already. */
export const copyUnknownText = (targetName) =>
  `We could not confirm whether the rows were added. Check ${targetName ?? "your datasets"} before you send them again, so they are not added twice.`;

/**
 * Sending again after an answer that never came back, the new dataset's name
 * is refused as taken: the first request may have made that dataset.
 */
export const nameMayExistText = (name) =>
  `A dataset named "${name}" may already have been made by the request we could not confirm. Check your datasets before you choose another name.`;
