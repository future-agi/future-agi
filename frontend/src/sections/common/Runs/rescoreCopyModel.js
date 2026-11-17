// W1-2 (MUST-BUILD W1-2): a Re-score makes a new run (#2A·2) that carries
// every output and holds the new scores; the run it started from keeps its
// scores. The one place the re-score confirms, cost lines and toasts are
// worded: the leaderboard, the results eval column menu, the analysis
// "Re-score #2A on vN", the experiment's Evaluations drawer, Score with… and
// the mock's server message all read it. Pure: responses in, sentences out.
// The judge's price comes from the one estimator (estimateInputs
// judgeCallUsd), passed in as `priceOf` so this model stays pure.
import { formatRunLabel, parseRunLabel } from "./runLabel";
import {
  joinAnd,
  keepScores,
  plural,
  rescoreResultOf,
  usdText,
} from "./rescoreWords";
import { rescoreMessage } from "./rescoreMessage";

export { rescoreMessage, rescoreResultOf, usdText };

/** W1-7: what each error policy does to an errored try, in one clause. */
export const POLICY_WORDS = Object.freeze({
  zero: "Errored tries count as 0 in every score",
  exclude: "Errored tries get no score, so they leave n and every mean",
  fail: "Errored tries count as 0 and the run fails",
});

/**
 * The labels the new runs get, one per run, numbered per config: two runs of
 * #2A (attempts 1 and 2) re-scored together make #2A·3 and #2A·4.
 */
export function nextLabelsOf(runs = []) {
  const top = new Map();
  const parsed = runs.map((r) => parseRunLabel(r?.label));
  runs.forEach((r, i) => {
    const p = parsed[i];
    if (!p) return;
    const key = `${p.ordinal}|${p.letter}`;
    const n = Math.max(
      Number(r?.attempts_count) || 1,
      Number(r?.attempt) || p.attempt || 1,
      p.attempt || 1,
    );
    top.set(key, Math.max(top.get(key) ?? 0, n));
  });
  return parsed
    .map((p) => {
      if (!p) return "";
      const key = `${p.ordinal}|${p.letter}`;
      const n = top.get(key) + 1;
      top.set(key, n);
      return formatRunLabel({ ...p, attempt: n });
    })
    .filter(Boolean);
}

/**
 * The confirm's body before a re-score of `evalNames` on `runs` (R1 runs
 * with `label`, `attempt`, `attempts_count`), on `rowCount` rows when it is
 * Score with… on a selection.
 */
export function rescoreConfirmBody({
  runs = [],
  evalNames = [],
  rowCount,
  errorPolicy,
  adding = false,
  nothing = false,
  datasetVersion = null,
  runOnlyEvals = [],
}) {
  const body = rescoreConfirmCore({
    runs,
    evalNames,
    rowCount,
    errorPolicy,
    adding,
    nothing,
    datasetVersion,
  });
  // L7c g5 (rs:leaderboard-row-menu): the evals the run itself scores
  // (Task completion, Tool-call accuracy) are not judged again; the
  // confirm names them so it never implies every eval is re-scored.
  const left = (runOnlyEvals ?? []).filter(Boolean);
  if (!left.length || errorPolicy) return body;
  return `${body} ${joinAnd(left)} ${left.length === 1 ? "is" : "are"} scored by the run itself and ${left.length === 1 ? "keeps its" : "keep their"} scores.`;
}

function rescoreConfirmCore({
  runs,
  evalNames,
  rowCount,
  errorPolicy,
  adding,
  nothing,
  datasetVersion,
}) {
  const labels = runs.map((r) => r?.label).filter(Boolean);
  // HO R6: a re-score on a newer version reads that version's rows.
  if (Number.isInteger(datasetVersion) && !errorPolicy) {
    const nextsV = runs.some((r) => r?.next_label !== undefined)
      ? runs.map((r) => r?.next_label).filter(Boolean)
      : nextLabelsOf(runs);
    if (nothing)
      return `Outputs stay as they are. v${datasetVersion} changed no row ${joinAnd(labels)} ran, so no new run is made.`;
    const what = evalNames.length ? joinAnd(evalNames) : "Every eval";
    return `Outputs stay as they are. ${what} ${evalNames.length === 1 ? "scores" : "score"} the rows v${datasetVersion} changed against v${datasetVersion}${nextsV.length ? ` in ${nextsV.length === 1 ? "a new run" : "new runs"} (${joinAnd(nextsV)})` : " in a new run"}; ${keepScores(labels)} When no score moves, no run is made.`;
  }
  // The server's plan names each new run's label (`next_label`); before it
  // answers, the labels are worked out from the runs.
  const nexts = runs.some((r) => r?.next_label !== undefined)
    ? runs.map((r) => r?.next_label).filter(Boolean)
    : nextLabelsOf(runs);
  if (errorPolicy) {
    const into = nexts.length ? ` (${joinAnd(nexts)})` : "";
    return `Outputs stay as they are. ${POLICY_WORDS[errorPolicy] ?? ""}, in ${nexts.length > 1 ? "new runs" : "a new run"}${into}; ${keepScores(labels)} No judge is called.`;
  }
  const what = evalNames.length ? joinAnd(evalNames) : "Every eval";
  const scope = rowCount
    ? `${plural(rowCount, "row")} of ${joinAnd(labels)}`
    : `the outputs of ${joinAnd(labels)}`;
  const into = nexts.length
    ? ` in ${nexts.length === 1 ? "a new run" : "new runs"} (${joinAnd(nexts)})`
    : " in a new run";
  // The plan found nothing to score: no new run is promised.
  if (nothing)
    return `Outputs stay as they are. ${what} already ${evalNames.length === 1 ? "reads" : "read"} this definition on ${joinAnd(labels)}, so no new run is made.`;
  // Add evaluation: the eval joins the experiment and scores the stored
  // outputs for the first time.
  if (adding)
    return `Outputs stay as they are. ${what} ${evalNames.length === 1 ? "joins" : "join"} the experiment and ${evalNames.length === 1 ? "scores" : "score"} ${scope}${into}; ${keepScores(labels)}`;
  return `Outputs stay as they are. ${what} ${evalNames.length === 1 ? "scores" : "score"} ${scope} again${into}; ${keepScores(labels)} When no score moves, no run is made.`;
}

/**
 * The confirm's cost line from the mock's plan (run-evaluations dry_run):
 * `744 judge calls at gpt-4o · about $0.45`; a bar that moved re-reads the
 * stored scores (`no judge calls`); rule-based checks are free.
 * @param {object} plan  {by_model: [{model, calls}], rule_based, kept, cells}
 * @param {(model: string|null) => number} priceOf  the one estimator's
 *   price of one judge call
 * @returns {{text: string, usd: number}}
 */
export function rescoreCostLine(plan, priceOf) {
  if (!plan) return { text: "", usd: 0 };
  const parts = [];
  let usd = 0;
  (plan.by_model ?? []).forEach(({ model, calls }) => {
    const cost = calls * (Number(priceOf?.(model)) || 0);
    usd += cost;
    parts.push(
      `${plural(calls, "judge call")} at ${model ?? "the default judge"} · about ${usdText(cost)}`,
    );
  });
  if (plan.kept)
    parts.push(
      `${plural(plan.kept, "score")} re-read at the new bar · no judge call`,
    );
  if (plan.no_call)
    parts.push(
      `${plural(plan.no_call, "skipped or errored try", "skipped or errored tries")} · no judge call`,
    );
  if (plan.rule_based)
    parts.push(`${plural(plan.rule_based, "check")} · free (rule-based)`);
  if (plan.policy_tries)
    parts.push(
      `${plural(plan.policy_tries, "errored try", "errored tries")} rebuilt · no judge call`,
    );
  if (!parts.length)
    return {
      text: plan.cells
        ? "No judge calls"
        : "Nothing to score again on these runs",
      usd: 0,
    };
  return { text: parts.join(" · "), usd };
}

/**
 * The toast's buttons: `Open #2A·2` (the first new run) and `See the 7
 * rows` (compare the run it started from with the new run, filtered to the
 * rows that flipped). Each names what it opens; none when nothing changed.
 * @returns {{open: {label, runId, experimentId}|null,
 *   rows: {label, baselineId, runId, rowIds}|null}}
 */
export function rescoreActions(result, { experimentId = null } = {}) {
  const r = rescoreResultOf(result);
  const first = r && !r.unchanged ? r.runs?.[0] : null;
  if (!first) return { open: null, rows: null };
  const rowIds = first.flipped_rows ?? [];
  // Several new runs: the compare view opens the first pair, so the button
  // names whose rows they are (the sentence counts every run's).
  const whose = (r.runs ?? []).length > 1 ? ` in ${first.label}` : "";
  return {
    open: {
      label: `Open ${first.label}`,
      runId: first.run_id,
      experimentId,
    },
    rows: rowIds.length
      ? {
          label: `${`See the ${plural(rowIds.length, "row")}`.replace(
            /^See the 1 row$/,
            "See the row",
          )}${whose}`,
          baselineId: first.from_run_id,
          runId: first.run_id,
          rowIds,
          // The rows pill adds its own count: `Changed by Groundedness re-score (199)`.
          pillLabel: (r.eval_names ?? []).length
            ? `Changed by ${joinAnd(r.eval_names)} re-score`
            : "Changed by re-score",
        }
      : null,
  };
}

/** Several experiments' responses read as one (the results page posts one per experiment). */
export function mergeRescoreResults(responses = []) {
  const list = responses.map(rescoreResultOf).filter(Boolean);
  if (!list.length) return null;
  if (list.length === 1) return list[0];
  const changed = list.filter((x) => !x.unchanged);
  // Per eval, the versions every response read.
  const versions = new Map();
  list.forEach((x) =>
    (x.eval_versions ?? []).forEach((e) => {
      const cur = versions.get(e.key) ?? { ...e, versions: [] };
      cur.versions = [
        ...new Set([...cur.versions, ...(e.versions ?? [])]),
      ].sort();
      versions.set(e.key, cur);
    }),
  );
  const evalVersions = versions.size ? [...versions.values()] : undefined;
  if (!changed.length)
    return {
      ...list[0],
      eval_versions: evalVersions,
      unchanged_labels: list.flatMap((x) => x.unchanged_labels ?? []),
      skipped: list.flatMap((x) => x.skipped ?? []),
      same_definition: list.every((x) => x.same_definition),
    };
  const sum = (k) => changed.reduce((s, x) => s + (x[k] ?? 0), 0);
  return {
    unchanged: false,
    eval_version: changed[0].eval_version,
    eval_versions: evalVersions,
    eval_names: [...new Set(changed.flatMap((x) => x.eval_names ?? []))],
    runs: changed.flatMap((x) => x.runs ?? []),
    labels: changed.flatMap((x) => x.labels ?? []),
    child_run_ids: changed.flatMap((x) => x.child_run_ids ?? []),
    flipped: Object.assign({}, ...changed.map((x) => x.flipped ?? {})),
    fail_to_pass: sum("fail_to_pass"),
    pass_to_fail: sum("pass_to_fail"),
    rescored_cells: sum("rescored_cells"),
    unchanged_labels: list.flatMap((x) => x.unchanged_labels ?? []),
    skipped: list.flatMap((x) => x.skipped ?? []),
  };
}

/** The confirm's title: `Re-score #2A with Tone?` · `Score 3 rows of #2A with Tone?`. */
export function rescoreTitle({
  runs = [],
  evalNames = [],
  rowIds,
  errorPolicy,
}) {
  const labels = runs.map((r) => r?.label).filter(Boolean);
  const who = labels.length > 2 ? `${labels.length} runs` : joinAnd(labels);
  if (errorPolicy === "exclude")
    return `Leave the errored tries of ${who} out of scores?`;
  if (errorPolicy === "zero") return `Count the errored tries of ${who} as 0?`;
  if (errorPolicy === "fail") return `Fail ${who} on errored tries?`;
  const what = evalNames.length ? ` with ${joinAnd(evalNames)}` : "";
  const whom = who ? ` ${who}` : "";
  return rowIds?.length
    ? `Score ${plural(rowIds.length, "row")} of${whom}${what}?`
    : `Re-score${whom}${what}?`;
}

/** Several experiments' plans (one per rerun-cells dry run) read as one. */
export function mergeRescorePlans(plans = []) {
  const list = plans.filter(Boolean);
  if (!list.length) return null;
  const byModel = new Map();
  list.forEach((p) =>
    (p.by_model ?? []).forEach(({ model, calls }) =>
      byModel.set(model, (byModel.get(model) ?? 0) + calls),
    ),
  );
  const sum = (k) => list.reduce((s, p) => s + (p[k] ?? 0), 0);
  return {
    by_model: [...byModel.entries()].map(([model, calls]) => ({
      model,
      calls,
    })),
    judge_calls: sum("judge_calls"),
    rule_based: sum("rule_based"),
    kept: sum("kept"),
    no_call: sum("no_call"),
    cells: sum("cells"),
    policy_tries: sum("policy_tries"),
    runs: list.flatMap((p) => p.runs ?? []),
    skipped: list.flatMap((p) => p.skipped ?? []),
    // HO R6: the dataset version every plan scores on, when they agree.
    dataset_version: list.every(
      (p) => p.dataset_version === list[0].dataset_version,
    )
      ? list[0].dataset_version ?? null
      : null,
  };
}

/**
 * HO R6: a re-score asked to score on vN goes ahead only when the server's
 * plan says it scores on vN; otherwise the confirm refuses with the reason
 * (nothing is sent), so the dialog never promises a version it would not
 * use. Any other request keeps its plan.
 */
export function versionPlanGuard(plan, datasetVersion) {
  if (!plan || plan.error || !Number.isInteger(datasetVersion)) return plan;
  if (plan.dataset_version === datasetVersion) return plan;
  return {
    error: true,
    reason: `The server did not plan this re-score on v${datasetVersion}, so nothing was sent.`,
  };
}

/**
 * Score with…'s eval list for one run: the run's own evals first (`Tone ·
 * this run's`), then the dataset's evals it does not carry yet (`Tone ·
 * new on this run`). Each choice names the id rerun-cells takes: the
 * run's experiment eval (R3 `metrics[].uem_ids`) or the dataset eval.
 * @returns {{key, id, name, label}[]}
 */
export function scoreWithChoices(run, { metrics = [], datasetEvals = [] }) {
  if (!run?.run_id) return [];
  const own = metrics
    .filter((m) => m?.uem_ids?.[run.run_id])
    .map((m) => ({
      key: m.key,
      id: m.uem_ids[run.run_id],
      name: m.name ?? m.key,
      label: `${m.name ?? m.key} · on ${run.label ?? "this run"}`,
    }));
  const have = new Set(own.map((c) => c.key));
  const added = datasetEvals
    .filter((e) => e?.id && e.key && !have.has(e.key))
    .map((e) => ({
      key: e.key,
      id: e.id,
      name: e.name ?? e.key,
      label: `${e.name ?? e.key} · new on ${run.label ?? "this run"}`,
    }));
  return [...own, ...added];
}
