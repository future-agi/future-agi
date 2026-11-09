// R4 lane X (MUST 4): the Matrix's voice layer — call cells, latency /
// interruption / cost heat, persona groups, regression badges and the voice
// verdict — all from one voice-summary read and voiceSummaryModel.
import { levelSentence } from "./pageShip";
import { useMemo } from "react";
import { useVoiceSummary } from "src/api/develop/r4";
import {
  callTable,
  isTextOnly,
  latencyHeat,
  personaGroups,
  regressionsOf,
  voiceRunStats,
  voiceRuns,
  voiceVerdict,
} from "src/sections/common/Analysis/voiceSummaryModel";

/** `heat=` values (C4) and their labels; `latency` / `interruptions` only on voice data. */
export const HEAT_OPTIONS = Object.freeze([
  { value: "result", label: "Result" },
  { value: "latency", label: "Latency p90", voice: true },
  {
    value: "interruptions",
    label: "Kept talking over the caller",
    voice: true,
  },
  { value: "cost", label: "Cost" },
]);

export const heatOptionsFor = (voice) =>
  HEAT_OPTIONS.filter((o) => !o.voice || voice);

/** The heat a URL value asks for, when this data has it (else `result`). */
export function heatOf(param, voice) {
  return heatOptionsFor(voice).some((o) => o.value === param)
    ? param
    : "result";
}

/** Heat strength 0–1 of a cell for a mode (null = no colour, `—`). */
export function cellHeat(mode, { call, cost, rowMaxCost } = {}) {
  if (mode === "latency")
    return call && !call.textOnly ? latencyHeat(call.p90) : null;
  if (mode === "interruptions")
    return call && !call.textOnly ? Math.min(1, (call.kept ?? 0) / 3) : null;
  if (mode === "cost")
    return Number.isFinite(cost) && rowMaxCost > 0 ? cost / rowMaxCost : null;
  return null;
}

/**
 * @returns {null|{summary, statsById, calls, personaOf, personaLabel,
 *   textOnly: Set<string>, regressions, groups, verdicts, thresholds}}
 */
export default function useVoiceMatrix({
  datasetId,
  matrix,
  runIds,
  baselineId,
}) {
  const isScenarios = matrix?.dataset_kind === "scenarios";
  const { data } = useVoiceSummary(datasetId, {
    runIds,
    baselineId,
    enabled: isScenarios && runIds.length > 0,
  });
  const key = runIds.join(",");
  return useMemo(() => {
    if (!isScenarios || !data?.voice) return null;
    const runs = voiceRuns(data).filter((r) => runIds.includes(r.run_id));
    if (!runs.length) return null;
    const statsById = Object.fromEntries(
      runs.map((r) => [r.run_id, voiceRunStats(r, data)]),
    );
    const { calls, personaOf } = callTable(data, { runIds });
    const personaLabel = (k) =>
      data.personas?.find((p) => p.key === k)?.short ?? k ?? null;
    const ids = runIds.filter((id) => statsById[id]);
    const base = baselineId ? statsById[baselineId] : null;
    const regressions = {};
    if (base)
      ids
        .filter((id) => id !== baselineId)
        .forEach((id) => {
          regressions[id] = regressionsOf(base, statsById[id]);
        });
    const groups = Object.fromEntries(
      personaGroups(data, { runIds: ids, baselineId }).map((g) => [g.label, g]),
    );
    const verdicts = base
      ? ids
          .filter((id) => id !== baselineId)
          .map((id) => voiceVerdict(data, { baselineId, candidateId: id }))
          .filter(Boolean)
      : [];
    return {
      summary: data,
      statsById,
      calls,
      personaOf,
      personaLabel,
      textOnly: new Set(runs.filter(isTextOnly).map((r) => r.run_id)),
      regressions,
      groups,
      verdicts,
      thresholds: data.thresholds ?? {},
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, key, baselineId, isScenarios]);
}

/** A voice verdict as a VerdictLine verdict (candidate name bold). */
export function asVerdictLine(v) {
  if (!v) return null;
  const name = v.cand?.name ?? "";
  // IT-R4 G6 P1 (S1): a not-significant voice verdict reads the same
  // `Level:` sentence as the Experiments tab.
  if (
    v.verb === "is level with" &&
    !v.fasterButWorse &&
    v.level &&
    Number.isFinite(v.level.candRate) &&
    Number.isFinite(v.level.baseRate)
  ) {
    const who = (s) =>
      s?.name && s?.label ? `${s.name} (${s.label})` : s?.label ?? s?.name;
    const head = levelSentence({
      cand: who(v.cand),
      base: who(v.base),
      a: v.level.candRate,
      b: v.level.baseRate,
      n: v.level.n,
      noun: "call",
      range: v.level.range,
    });
    const sentence = `${head}${v.level.tail ?? ""}`;
    return {
      tone: v.tone,
      verb: v.verb,
      sentence,
      parts: [{ t: "text", text: sentence }],
      deltaPts: v.deltaPts,
      worse: v.worse,
      better: v.better,
      chips: [],
      confidence: null,
    };
  }
  const i = v.sentence.indexOf(name);
  const parts =
    name && i >= 0
      ? [
          ...(i > 0 ? [{ t: "text", text: v.sentence.slice(0, i) }] : []),
          { t: "run", text: name },
          { t: "text", text: v.sentence.slice(i + name.length) },
        ]
      : [{ t: "text", text: v.sentence }];
  return {
    tone: v.tone,
    verb: v.verb,
    sentence: v.sentence,
    parts,
    deltaPts: v.deltaPts,
    worse: v.worse,
    better: v.better,
    chips: [],
    confidence: null,
  };
}
