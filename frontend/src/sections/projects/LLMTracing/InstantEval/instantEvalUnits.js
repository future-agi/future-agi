import { UNITS } from "src/sections/projects/observePopulation";
import { splitWindow } from "src/sections/projects/observeScope";
import {
  ANSWER_TYPES,
  MAX_QUESTIONS,
  cleanCategories,
  readsFor,
} from "./instantEvalPayloads";

// What Instant eval knows about each view it asks about (traces, spans,
// sessions or users): its words, what Jev reads of each row, and the
// question drafts and times the page shows.

export const unitMeta = (unit) => UNITS[unit] || UNITS.traces;

export const defaultReads = (unit) => readsFor(unit)[0];

// A read choice only applies to the view that offers it.
export const normalizeReads = (unit, reads) =>
  readsFor(unit).includes(reads) ? reads : defaultReads(unit);

// The grid's scope filters carry the date window as a created_at "between"
// item; eval tasks take it as a sibling date_range instead.
export function splitScopeFilters(scopeFilters = []) {
  const { window, filters } = splitWindow(scopeFilters);
  return { dateRange: window || undefined, filters };
}

export const formatCount = (count, lowerBound) =>
  `${Number(count || 0).toLocaleString()}${lowerBound ? "+" : ""}`;

export const emptyDraft = () => ({
  question: "",
  answerType: ANSWER_TYPES.YES_NO,
  categories: [],
  definitions: {},
});

// Questions handed over from Insights (or a saved question) as editor drafts.
export function draftsFromQuestions(questions = []) {
  const drafts = (Array.isArray(questions) ? questions : [])
    .filter((entry) => entry && typeof entry.question === "string")
    .slice(0, MAX_QUESTIONS)
    .map((entry) => {
      const isCategory =
        entry.answerType === ANSWER_TYPES.CATEGORY ||
        entry.answer_type === ANSWER_TYPES.CATEGORY;
      const categories = isCategory
        ? cleanCategories(entry.categories || entry.labels || [])
        : [];
      return {
        question: entry.question,
        answerType: isCategory ? ANSWER_TYPES.CATEGORY : ANSWER_TYPES.YES_NO,
        categories,
        definitions:
          entry.definitions && typeof entry.definitions === "object"
            ? { ...entry.definitions }
            : {},
      };
    });
  return drafts.length ? drafts : [emptyDraft()];
}

// "8 s", "1 min 5 s".
export const formatSeconds = (seconds) => {
  const whole = Math.max(0, Math.round(seconds || 0));
  return whole >= 60
    ? `${Math.floor(whole / 60)} min ${whole % 60} s`
    : `${whole} s`;
};

export const formatWhen = (value) =>
  value
    ? new Date(value).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "";

// "just now", "4 min ago", "3 h ago", "Oct 12".
export function formatAgo(value, now = Date.now()) {
  if (!value) return "";
  const seconds = (now - new Date(value).getTime()) / 1000;
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  if (seconds < 7 * 86400) {
    const days = Math.round(seconds / 86400);
    return days === 1 ? "yesterday" : `${days} days ago`;
  }
  return new Date(value).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}
