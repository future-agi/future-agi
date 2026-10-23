/*
  The receipt — every check an improvement made, in order, in plain words.
  Written by Future AGI, never by the model proposing the changes.
*/
import { heldSplitText, improverModelsText, judgeFor, markedNotCountedText, REPEATS } from "../../_mock/optimizationRuns";
import { noGainRunsOf } from "../../_mock/optimizer";
import { TIERS } from "../../_mock/improverAttempts";
import { proofDisplay } from "./proofBlock";

const mark = (pass) => (pass === null ? "unverified" : pass ? "passed" : "failed");

export const receiptOf = (record, outcome, { attempts = [], identity = null, envSnapshot = null } = {}) => {
  const r = record?.result;
  if (!r) return [];
  /* A record stamped with the newer engine writes each constraint check as
     its own entry, the calibration, and every improver attempt. */
  const stamped = !!record.frozen?.staticCheck;
  const proposer = record.model === "claude-opus-5" ? "Claude Opus 5" : record.model;
  const lines = [
    ...(record.reread ? [["Agent synced", record.reread.changed ? `Changed outside Future AGI → baseline on ${record.reread.version || "the new version"}` : "no outside change"]] : []),
    ["Inputs frozen", `${r.trainMeasured + r.heldMeasured + (r.mustPassMeasured || 0)} scenarios${identity ? ` on ${identity}` : envSnapshot ? ` on environment ${envSnapshot}` : ""}, the must-pass list, the rules, the model lock and the cost ceiling`],
    ...(r.mustPassMeasured != null ? [["Scenario sets", `${r.trainMeasured} target, seen in full · ${r.heldMeasured} held back, never shown · ${r.mustPassMeasured} must-pass, pass or fail only`]] : []),
    ...(record.reproduce ? [["Failure reproduced", `Reproduced on ${record.reproduce.reproduced} of ${record.reproduce.total} scenarios · ${record.reproduce.inconclusive || 0} inconclusive`]] : []),
    ...(record.proof ? [["Proof kind", record.proof.kind === "prompt-only" ? proofDisplay(record.proof).line : `${record.proof.label} · ${record.proof.tierLabel}${record.proof.kind === "live" ? " — tools reached your real systems" : ""}`]] : []),
    ...(r.split?.kind === "issue" ? [["Held back", r.split.thin ? "none — held-back check is thin" : heldSplitText(r.split)]] : []),
    ["Baseline measured", `${r.base}% on the target scenarios, ${r.heldBase}% held back · run-to-run noise ±${r.noise || 0} pts`],
    ...(stamped && r.calibration ? [["Run-to-run noise · baseline run twice", `Ran ${r.calibration.version || record.baseAgentVersion || "the current version"} twice: ${r.calibration.disagreed} of ${r.calibration.scenarios} scenarios disagreed, so a change on one of those counts only when it shows up on more than one repeat · noise ±${r.noise} pts`]] : []),
    ...(stamped && r.goal ? [["Targets", `${r.goalTargets} ${r.goalTargets === 1 ? "scenario falls" : "scenarios fall"} short of ${r.goal.id === "pass-rate" ? "the pass rate" : r.goal.label}${r.split?.kind === "goal" ? ` · ${r.split.thin ? "none held back — thin" : `${heldSplitText(r.split)} held back`}` : ""}`]] : []),
    ["Runs proposed", `${r.trials.length} ${record.writerModel ? improverModelsText(record.model, record.writerModel) : `by ${proposer}`}${r.stoppedEarly ? ` · stopped ${r.stoppedEarly.complete ? "" : "early "}after ${r.stoppedEarly.after} of up to ${r.stoppedEarly.of} — ${r.stoppedEarly.complete ? "every failing target already fixed" : `no gain in the last ${noGainRunsOf(r.stoppedEarly)}`}` : ""}`],
    ["Runs judged", `by ${judgeFor(record.model)} — a different model family`],
    stamped
      ? ["Static checks", `model, protected parts, areas, settings in range, judges untouched${r.repoTests ? `, ${r.repoTests} on code changes` : ""} — every run · ${(() => { const n = r.trials.filter((t) => t.static && t.static.pass === false).length; return n ? `${n} stopped there` : "all passed"; })()}`]
      : ["Static checks", `model, protected parts, judges untouched${r.repoTests ? `, ${r.repoTests} on code changes` : ""} — every run`],
    /* Only improvements stamped with the check (record.frozen.compatCheck) carry it. */
    ...(r.compat ? [["Fits the environment", `entry point ${r.compat.entry}, build and start command checked against the sealed environment${envSnapshot ? ` ${envSnapshot}` : r.compat.envVersion ? ` ${r.compat.envVersion}` : ""} — every code change · ${(() => { const n = r.trials.filter((t) => t.compat && t.compat.pass === false).length; return n ? `${n} didn't fit, stopped there` : "all fit"; })()}`]] : []),
    ...(r.textSearch ? [["Searched in text", "runs as text conversations · finalists as real calls"]] : []),
    ...(r.winner ? [
      ["Finalists scored once", (r.finalists || []).length ? r.finalists.map((f) => `Run ${f.n} ${f.heldScore}%${f.winner ? " · winner" : ""}`).join(" · ") : `${r.heldBase}% → ${r.heldScore}%`],
      ...(r.confirm ? [["Re-run after the pick", `Run ${r.winner.n} on the held-back set again: ${r.confirm.selectionScore}% at the pick, ${r.confirm.score}% on the re-run · ${r.confirm.held ? "held" : `didn't hold — ${r.confirm.why}`}`]] : []),
      ["Held-back set", `${r.heldBase}% → ${r.heldScore}% on ${r.heldMeasured} scenarios${r.confirm ? " · the re-run" : ""}`],
      ["Must-pass set", `${r.mustPass?.total ?? "—"} scenarios · ${(r.heldBlockers || []).length ? `${r.heldBlockers.length} failed again on a re-run` : "none failed twice"}${(r.flakyToday || []).length ? ` · ${r.flakyToday.length} flaky, not counted` : ""}${markedNotCountedText(r) ? ` · ${markedNotCountedText(r)}` : ""}`],
      ["Repeats", stamped && (r.finalists || []).find((f) => f.winner)?.fullRegression
        ? `each scenario ${r.repeats || REPEATS}× · code change → ${(r.finalists || []).find((f) => f.winner).repeats} repeats per finalist and the full regression set`
        : `each scenario ${r.repeats || REPEATS}×`],
    ] : []),
    /* Each constraint the winner was held to, on its own line. */
    ...(stamped ? (outcome?.checks || []).filter((c) => c.id !== "static").map((c) => [`Constraint · ${c.label}`, `${c.actual} — ${mark(c.pass)}`]) : []),
    /* The customer's coding agent: every attempt and the verdict it was sent. */
    ...attempts.flatMap((a) => [
      [`Improver attempt · ${a.label}`, `${a.title} · commit ${a.commit}${a.fingerprint ? " + uncommitted changes" : ""} · ${a.gate ? "not run" : TIERS[a.where]?.label || a.where}`],
      [`Verdict · attempt ${a.n}`, a.accepted ? "accepted — proven by Future AGI" : `rejected — ${String(a.why || "a check failed").replace(/\.$/, "")}${a.gate ? " · not charged" : ""}`],
    ]),
    ["Verdict", `${outcome?.ending?.label || "—"}`],
  ];
  return lines.map(([step, detail], i) => ({ n: i + 1, step, detail }));
};

/*
  The receipt's seal (RSI PRD: "every check written to a tamper-evident
  receipt"): a fingerprint of every entry, in order. Change one word of any
  entry and the seal no longer matches — Verify recomputes it from the
  record and says so.
*/
const fnv = (text, seed) => {
  let h = seed >>> 0;
  for (let i = 0; i < text.length; i += 1) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
};
export const receiptSealOf = (record, entries = []) => {
  const text = JSON.stringify({ id: record?.id || null, entries: entries.map((e) => [e.n, e.step, e.detail]) });
  return [2166136261, 2654435769, 2246822507, 3266489909].map((seed) => fnv(text, seed).toString(16).padStart(8, "0")).join("");
};
/** "9f3a…c21" — the seal as it's shown. */
export const shortSeal = (seal) => (seal ? `${seal.slice(0, 4)}…${seal.slice(-3)}` : "");
