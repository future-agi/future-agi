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
