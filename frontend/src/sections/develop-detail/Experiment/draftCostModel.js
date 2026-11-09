// IT-R5 G1 P4: one cost line in every source footer (pure):
// `24 tasks × 1 try · ~$6.30 · ~15–23 min at 2 at a time`, with the
// assumption behind it (model price, tokens per try, judge calls). The
// numbers are runEstimateLines' (the Review step's), for the one draft run.
import {
  MODEL_PRICES,
  TOKENS_PER_TRY,
} from "src/sections/common/RunSources/declaredUses";
import { JUDGE_COST_PER_CALL } from "src/sections/common/Analysis/runStats";
import { fmtCost } from "src/sections/common/format/fmt";
import { costLine, draftModelOf, runEstimateLines } from "./costEstimateModel";
import { newR4Card } from "./experimentFormValues";

const n = (x) => Number(x).toLocaleString("en-US");
const money = (x) => fmtCost(x).replace(/\.00$/, "");

/**
 * options-truth L2 R3: the same function prices the drawer footer, the
 * harness recipe summary and the Add card, with the run's model (S5), its
 * cost cap (expected never above it, `stops at $0.50`), unpriced models
 * (`~$— · no price for my-custom-llm · judge calls ~$0.036`), sandbox vCPU
 * and the caller's scope (`rows` = the rows in scope, e.g. 1 task).
 * @param {{draft: object, rows: number, tries: number, history?: object[],
 *   evals?: object[], rowNoun?: string}} p
 * @returns {{text: string, needsModel: boolean, assumption: string}|null}
 */
export function draftCostLine({
  draft,
  rows,
  tries,
  history = [],
  evals = [],
  rowNoun = "row",
}) {
  const r = Math.max(0, Math.round(Number(rows) || 0));
  // A voice run repeats each call its own number of times.
  const t = Math.max(
    1,
    Math.round(
      Number(draft?.kind === "voice" ? draft?.slot?.voice?.tries : 0) ||
        Number(tries) ||
        1,
    ),
  );
  if (!draft?.kind || !r) return null;
  const card = newR4Card(draft);
  const base = costLine({
    promptConfig: [card],
    rows: r,
    trials: t,
    evals,
    history,
  });
  const story = runEstimateLines({
    cards: [card],
    rows: r,
    trials: t,
    estimate: base?.estimate,
    history,
  });
  if (!story) return null;
  const line = story.lines?.[0] ?? {};
  const model = draft.kind === "voice" ? null : draftModelOf(draft);
  const needsModel = draft.kind === "code" && !model;
  const unpriced = draft.kind === "code" && model && line.priced === false;
  const conc = line.conc ?? null;
  const voice = draft.kind === "voice";
  const unit = voice ? "call" : rowNoun;
  const range = line.minutes ?? story.minutes;
  const mins = range
    ? `~${range[0] === range[1] ? range[0] : `${range[0]}–${range[1]}`} min${conc ? ` at ${conc} at a time` : ""}`
    : null;
  const judgeCalls = Number(base?.estimate?.judge_calls) || 0;
  const judgeUsd = Number(story.judgeUsd) || 0;
  const cost = needsModel
    ? "~$ — add your model to estimate"
    : unpriced
      ? [
          "~$—",
          `no price for ${model}`,
          judgeUsd > 0 ? `judge calls ~${money(judgeUsd)}` : null,
        ]
          .filter(Boolean)
          .join(" · ")
      : `~${money(story.expected)}`;
  // R3(c): the run stops at its cap; say so when the cap binds.
  // A harness run whose own run cap binds stops there (`$5 per run`), else
  // at its per-try budget.
  // options-truth L3 G3: the cap is for the run; the judges sit on top
  // (`~$1.01 · stops at $1 for the run · + $0.010 judges`).
  const judgesOnTop = judgeUsd > 0 ? `+ ${money(judgeUsd)} judges` : null;
  const stops =
    line.clamped && line.cap
      ? draft.kind === "harness"
        ? line.runCap
          ? [`stops at ${money(line.runCap)} per run`, judgesOnTop]
          : [
              // Every try hits its cap: the amount is the worst case.
              `stops at ${money(line.cap)} per try (worst case: every try hits it)`,
              judgesOnTop,
            ]
        : [`stops at ${money(line.cap)} for the run`, judgesOnTop]
      : [];
  const text = [
    `${n(r)} ${r === 1 ? unit : `${unit}s`} × ${n(t)} ${t === 1 ? "try" : "tries"}`,
    cost,
    ...stops,
    mins,
  ]
    .filter(Boolean)
    .join(" · ");
  const price = model ? MODEL_PRICES[model] : null;
  // R3(b): "the last finished run" only when such a run exists.
  const past = (history ?? []).length > 0 && draft.kind === "harness";
  const assumption = [
    voice
      ? "Calls priced at the voice pipeline's per-minute rates."
      : model && price
        ? `${model} at list price ($${price[0]} in / $${price[1]} out per 1M tokens)${past ? ", or the last finished run with the same setup" : ""}.`
        : model
          ? `${model} has no list price here, so its calls are not in the estimate${judgeUsd > 0 ? "; the judges are" : ""}.`
          : "Your model: not priced until you add it.",
    voice || (model && !price)
      ? null
      : `~${n(TOKENS_PER_TRY.input)} input + ${n(TOKENS_PER_TRY.output)} output tokens per try.`,
    line.cap && draft.kind !== "voice"
      ? `Stops at ${money(line.cap)}${draft.kind === "harness" ? " per try" : " for the run"}.`
      : null,
    judgeCalls
      ? `${n(judgeCalls)} judge calls ≈ ${money(judgeUsd)} (${JUDGE_COST_PER_CALL === judgeUsd / judgeCalls ? `$${JUDGE_COST_PER_CALL} each` : "at each eval's judge model"}).`
      : "Judges: none picked yet (Evaluations step).",
  ]
    .filter(Boolean)
    .join(" ");
  return { text, needsModel, assumption };
}
