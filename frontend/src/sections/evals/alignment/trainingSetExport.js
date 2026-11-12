// W7-2 B3: Train a smaller judge = a judge training set out of the
// reviewers' labels, and the held-out check of a tuned judge before use
// (W7-SCOPE §7 B3, owner decision 2: no trainer). Pure. The files are
// planned and written by W9 C's training-set model (planTrainingSet,
// leakage / overlap lines, trainingFiles → writers.toOpenAiFineTune); the
// split itself is seeded and stratified on the server (agreement route).
// Nothing here invents a share, a seed or a price.
import {
  countsLine,
  leakAction,
  leakLine,
  leftOutLine,
  noInputLine,
  overlapLines,
  planTrainingSet,
  trainingFiles,
} from "src/sections/develop-detail/DataTab/Splits/trainingSetModel";
import { costClause, judgeCallsCost } from "./judgeCost";

export const SHARES_REQUIRED =
  "Choose train, validation and test shares that add up to 100, and a seed";
export const TRAIN_DONE =
  "Next: fine-tune with your provider, add the tuned model in Settings › Custom models, then check it here on the test rows.";
export const SPLIT_KEYS = ["train", "validation", "test"];
// The columns of one training row: the judge's rendered inputs and the
// reviewer's verdict (+ note).
export const TRAINING_ROLES = Object.freeze({
  user: "judge_input",
  assistant: "reviewer_verdict",
});

const isShare = (v) =>
  v !== "" && v != null && Number.isFinite(Number(v)) && Number(v) >= 0;

/**
 * The three shares and the seed, all typed, each 0-100 and summing to 100
 * with a non-empty train and test part; else the reason Prepare is off.
 */
export function sharesReason({ train, validation, test, seed } = {}) {
  if (![train, validation, test].every(isShare)) return SHARES_REQUIRED;
  const [t, v, s] = [train, validation, test].map(Number);
  if (t <= 0 || s <= 0 || t + v + s !== 100) return SHARES_REQUIRED;
  if (seed == null || String(seed).trim() === "") return SHARES_REQUIRED;
  return null;
}

/** Query params of a split (`train_share=70&…&seed=3`), or {} without one. */
export const splitParams = (shares) =>
  shares && !sharesReason(shares)
    ? {
        train_share: Number(shares.train),
        validation_share: Number(shares.validation),
        test_share: Number(shares.test),
        seed: String(shares.seed).trim(),
      }
    : {};

/** The table rows planTrainingSet reads (`{row_id, order, col: {cell_value}}`). */
const tableRowOf = (r) => ({
  row_id: r.row_id,
  order: r.row_no != null ? r.row_no - 1 : null,
  [TRAINING_ROLES.user]: { cell_value: r.user },
  [TRAINING_ROLES.assistant]: { cell_value: r.assistant },
});

/**
 * W9 C's plan of the three files from the server's training rows;
 * `leaveOutLeaked` ({validation?, test?}) drops the rows whose input a
 * train row has from that file.
 */
export function trainingPlanOf(training, leaveOutLeaked = {}) {
  const rows = training?.rows ?? [];
  return planTrainingSet({
    parts: SPLIT_KEYS.map((key) => ({
      key,
      splitName: key,
      rows: rows.filter((r) => r.split === key).map(tableRowOf),
    })),
    roles: TRAINING_ROLES,
    leaveOutLeaked,
  });
}

/** `[{key, label}]`: the Leave-out actions for parts with leaked rows. */
export const leakActionsOf = (plan) =>
  (plan?.parts ?? [])
    .filter((p) => p.key !== "train" && p.leaks.length && !p.leakedOut)
    .map((p) => ({ key: p.key, label: leakAction(plan, p.key) }));

/** Row ids the test file leaves out (leaked), which the check skips too. */
export const leftOutTestRows = (plan) => {
  const test = (plan?.parts ?? []).find((p) => p.key === "test");
  return test?.leakedOut ? test.leaks.map((l) => l.row.row_id) : [];
};

/** Test rows whose input a train row has, still in the test file (0 once left out). */
export const testLeaksLeft = (plan) => {
  const test = (plan?.parts ?? []).find((p) => p.key === "test");
  return test && !test.leakedOut ? test.leaks.length : 0;
};

/** The dialog's lines: counts, left out, leakage and overlap (W9 C's). */
export function trainingLines(plan) {
  return [
    countsLine(plan),
    leftOutLine(plan),
    noInputLine(plan),
    leakLine(plan, "validation"),
    leakLine(plan, "test"),
    ...overlapLines(plan),
  ].filter(Boolean);
}

/** The JSONL files, named `<eval>-<dataset>-v<N>-<split>.jsonl`. */
export function trainingFilesOf(training, plan) {
  return trainingFiles({
    plan,
    roles: TRAINING_ROLES,
    system: training?.instructions ?? "",
    datasetName: [training?.eval_name, training?.dataset_name]
      .filter(Boolean)
      .join(" "),
    versionNumber: training?.version_number ?? null,
  });
}

/**
 * The tuned-judge check's confirm: what the check scores, priced by the one
 * estimator (a Custom model at its own prices). The check scores the test
 * rows with the tuned model and again with the current judge (`current`,
 * null for the default judge), so it names both and counts 2n calls:
 * `Score 18 test rows with <model> and again with <current> · 36 judge
 * calls · about $0.30`. Without `current` only `model` scores (n calls).
 */
export function checkConfirm(n, model, opts) {
  const both = opts !== undefined;
  const models = both ? [model, opts?.current ?? null] : [model];
  const cost = judgeCallsCost(n, models, opts?.customPrices ?? null);
  const again = both
    ? ` and again with ${opts?.current ?? "the current judge"}`
    : "";
  return `Score ${n} test rows with ${model}${again} · ${cost.calls} judge calls · ${costClause(cost)}`;
}

/**
 * The version's record of the held-out check (config_snapshot.checked_on):
 * the tuned judge on the test rows against the reviewers, and the current
 * judge on the same rows.
 */
export function checkedOnOf(report, shares) {
  const cmp = report?.compare;
  if (!cmp) return null;
  return {
    split: "test",
    seed: String(shares?.seed ?? ""),
    shares: {
      train: Number(shares?.train),
      validation: Number(shares?.validation),
      test: Number(shares?.test),
    },
    model: cmp.judge_model,
    n: cmp.n,
    accuracy: cmp.accuracy,
    kappa: cmp.kappa,
    vs: {
      model: cmp.current?.judge_model ?? null,
      accuracy: cmp.current?.accuracy ?? null,
      kappa: cmp.current?.kappa ?? null,
    },
  };
}
