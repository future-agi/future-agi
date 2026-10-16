import { unitWord } from "src/sections/projects/observePopulation";
import { formatCount, formatDayRange } from "./insightsFormat";

// Plain-language facts about a completed run, shared by the report header,
// the method note and the Ask composer.

/** First and last day the run's analysed items cover, from `totals.daily`. */
export function runDayRange(run) {
  const days = Object.keys(run?.result?.totals?.daily || {})
    .filter((key) => /^\d{4}-\d{2}-\d{2}$/.test(key))
    .sort();
  if (days.length) return [days[0], days[days.length - 1]];
  const window = run?.scope?.window;
  if (Array.isArray(window) && window.length === 2) return window;
  return null;
}

/**
 * The meta line under the report's question:
 * "108 sessions sampled of 108 · 76 analysed (32 had no failure) · 39 users · Oct 8–15"
 */
export function reportMetaParts(run, lens) {
  if (!run) return [];
  const result = run.result || {};
  const unit = run.unit;
  const sample = result.sample_size ?? run.sample_size ?? 0;
  const population = result.population_size ?? run.population_size ?? 0;
  const analysed = result.totals?.count ?? run.analysed_count ?? 0;
  const excluded = Number(result.excluded) || 0;
  const parts = [
    `${formatCount(sample)} ${unitWord(unit, sample)} sampled of ${formatCount(population)}`,
  ];
  const why =
    lens === "failures" ? "had no failure" : "not relevant to the question";
  parts.push(
    `${formatCount(analysed)} analysed${excluded > 0 ? ` (${formatCount(excluded)} ${why})` : ""}`,
  );
  const users = result.totals?.users;
  if (unit !== "users" && Number.isFinite(users))
    parts.push(`${formatCount(users)} ${users === 1 ? "user" : "users"}`);
  const range = runDayRange(run);
  if (range) {
    const [from, to] = range.map((day) =>
      /^\d{4}-\d{2}-\d{2}$/.test(day) ? `${day}T12:00:00Z` : day,
    );
    parts.push(formatDayRange(from, to));
  }
  return parts;
}

/** Three starting questions for Ask, fitted to the report's unit and lens. */
export function suggestedQuestions({ unit, lens, hasPrevious } = {}) {
  const noun =
    unit === "traces"
      ? "requests"
      : unit === "users"
        ? "users"
        : "conversations";
  const first =
    unit === "users"
      ? "Which kinds of users fare worst?"
      : "Which users are hit hardest?";
  const second = hasPrevious
    ? "What changed since the last run?"
    : lens === "failures"
      ? "Which failure should we fix first?"
      : `What do the unresolved ${noun} share?`;
  const third =
    lens === "failures" || lens === "experience"
      ? `Show ${noun} where the agent recovered`
      : `Show ${noun} the agent got wrong`;
  return [first, second, third];
}

// The Ask thread of a run lives for this browser session.
const ASK_PREFIX = "insights-ask:";

export function readAskThread(runId) {
  if (!runId) return [];
  try {
    const raw = globalThis.sessionStorage?.getItem(ASK_PREFIX + runId);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed)
      ? parsed.filter((turn) => turn && typeof turn.question === "string")
      : [];
  } catch {
    return [];
  }
}

export function writeAskThread(runId, thread) {
  if (!runId) return;
  try {
    globalThis.sessionStorage?.setItem(
      ASK_PREFIX + runId,
      JSON.stringify(thread.filter((turn) => turn.status !== "busy")),
    );
  } catch {
    // Storage full or blocked: the thread lasts until the page reloads.
  }
}

/**
 * An Ask response as {answer: [{text, unit_ids, category_ids, evidence}],
 * followUps, dropped} where dropped counts statements that did not check out.
 */
export function normalizeAnswer(result) {
  const paragraphs = Array.isArray(result?.answer)
    ? result.answer
    : typeof result?.answer === "string"
      ? [{ text: result.answer }]
      : [];
  return {
    answer: paragraphs
      .filter((p) => p && String(p.text || "").trim())
      .map((p) => ({
        text: String(p.text).trim(),
        unit_ids: Array.isArray(p.unit_ids) ? p.unit_ids.filter(Boolean) : [],
        category_ids: Array.isArray(p.category_ids)
          ? p.category_ids.filter(Boolean)
          : [],
        evidence: Array.isArray(p.evidence)
          ? p.evidence.filter((e) => e && e.quote)
          : [],
      })),
    followUps: (Array.isArray(result?.follow_ups) ? result.follow_ups : [])
      .filter((q) => typeof q === "string" && q.trim())
      .slice(0, 3),
    dropped: Number(result?.dropped) || 0,
  };
}

// The Ask endpoint takes a question of up to 500 characters and history
// messages of up to 4,000; only the latest turns are worth sending.
export const ASK_QUESTION_MAX = 500;
const HISTORY_CONTENT_MAX = 4000;
const HISTORY_TURNS = 6;

/** The conversation so far, as the Ask endpoint takes it. */
export const askHistory = (thread) =>
  thread
    .filter((turn) => turn.status === "done")
    .slice(-HISTORY_TURNS)
    .flatMap((turn) => [
      { role: "user", content: turn.question.slice(0, ASK_QUESTION_MAX) },
      {
        role: "assistant",
        content: (turn.answer || [])
          .map((p) => p.text)
          .join("\n\n")
          .slice(0, HISTORY_CONTENT_MAX),
      },
    ]);
