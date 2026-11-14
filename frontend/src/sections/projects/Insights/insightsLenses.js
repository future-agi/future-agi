import { UNITS } from "src/sections/projects/observePopulation";
import { humanizeValue } from "./insightsFormat";

// A lens is what a report looks for. It decides what the per-unit summary
// extracts, which units count (the failures lens leaves out units with no
// failure) and how categories are named.
export const LENSES = [
  {
    key: "usage",
    title: "Usage",
    question: "What are people asking for?",
    description: "Topics and intents: what people come to get done.",
    icon: "mdi:message-text-outline",
  },
  {
    key: "failures",
    title: "Failures",
    question: "Where is the agent failing?",
    description: "Mistakes, broken flows and requests the agent couldn't meet.",
    icon: "mdi:alert-octagon-outline",
    excludes: "units with no failure",
  },
  {
    key: "experience",
    title: "Experience",
    question: "How do conversations end?",
    description: "Outcomes, friction and how people feel by the end.",
    icon: "mdi:emoticon-neutral-outline",
  },
  {
    key: "personas",
    title: "Personas",
    question: "Who are our users?",
    description: "Kinds of users, what they want and what sets them apart.",
    icon: "mdi:account-group-outline",
    unit: "users",
  },
  {
    key: "custom",
    title: "Your question",
    question: "",
    description: "Ask anything about your traffic in your own words.",
    icon: "mdi:help-circle-outline",
  },
];

export const lensFor = (key) =>
  LENSES.find((lens) => lens.key === key) || LENSES[0];

export const TEMPLATES = LENSES.map((lens) => ({
  lens: lens.key,
  title: lens.key === "custom" ? "Ask your own question" : lens.question,
  description: lens.description,
  icon: lens.icon,
  unit: lens.unit,
}));

export const UNIT_ORDER = ["traces", "sessions", "users"];

export const UNIT_HELP = {
  traces: "Traces: one request and its response each",
  sessions: "Sessions: whole conversations",
  users: "Users: everything a user did in the window",
};

export const unitTitle = (unit) => (UNITS[unit] || UNITS.traces).title;

// Pipeline stages, in order, as the run reports them in `stage`.
export const STAGES = [
  { key: "queued", label: "Queued", detail: "Waiting for a worker" },
  { key: "reading", label: "Reading", detail: "Loading each item's messages" },
  { key: "summarising", label: "Summarising", detail: "Summarising each item" },
  { key: "grouping", label: "Grouping", detail: "Finding the categories" },
  { key: "measuring", label: "Measuring", detail: "Computing the statistics" },
  { key: "writing", label: "Writing", detail: "Writing the summary" },
  { key: "done", label: "Done", detail: "" },
];

export const stageIndex = (stage) =>
  Math.max(
    0,
    STAGES.findIndex((s) => s.key === stage),
  );

export const isRunActive = (status) =>
  status === "pending" || status === "running";

// Outcome values the extractor writes, in reading order.
export const OUTCOMES = [
  "resolved",
  "partially_resolved",
  "escalated",
  "unresolved",
  "abandoned",
  "unclear",
];

const SENTIMENT_WORD = {
  1: "very negative",
  2: "negative",
  3: "neutral",
  4: "positive",
  5: "very positive",
};

/**
 * Facet chips of an analysed unit: [{key, label, tone}] where tone is
 * "bad" | "good" | "neutral".
 */
export function facetChips(facets = {}) {
  const chips = [];
  if (facets.outcome) {
    const tone = ["resolved"].includes(facets.outcome)
      ? "good"
      : ["unresolved", "abandoned"].includes(facets.outcome)
        ? "bad"
        : "neutral";
    chips.push({ key: "outcome", label: humanizeValue(facets.outcome), tone });
  }
  if (Number.isFinite(facets.sentiment)) {
    chips.push({
      key: "sentiment",
      label:
        `Sentiment ${facets.sentiment}/5 · ${SENTIMENT_WORD[facets.sentiment] || ""}`.trim(),
      tone:
        facets.sentiment <= 2
          ? "bad"
          : facets.sentiment >= 4
            ? "good"
            : "neutral",
    });
  }
  // "none", or a failure the unit no longer counts (nothing backed it), is
  // not a failure to show.
  if (
    facets.failure_mode &&
    facets.failure_present !== false &&
    String(facets.failure_mode).trim().toLowerCase() !== "none"
  ) {
    chips.push({
      key: "failure_mode",
      label: facets.failure_mode,
      tone: "bad",
    });
  }
  if (Number.isFinite(facets.broke_at_turn)) {
    chips.push({
      key: "broke_at_turn",
      label: `Broke at turn ${facets.broke_at_turn}`,
      tone: "bad",
    });
  }
  if (facets.language) {
    chips.push({ key: "language", label: facets.language, tone: "neutral" });
  }
  return chips;
}

/** Attribute pills: [{key, label, value, tone}] from facets.attributes. */
export function attributePills(attributes = {}) {
  return Object.entries(attributes || {}).map(([key, value]) => {
    if (typeof value === "boolean")
      return {
        key,
        label: humanizeValue(key),
        value: value ? "true" : "false",
        tone: value ? "on" : "off",
      };
    return {
      key,
      label: humanizeValue(key),
      value: value == null || value === "" ? "–" : String(value),
      tone: "neutral",
    };
  });
}

export const ATTRIBUTE_KEY = /^[a-z][a-z0-9_]{0,59}$/;
export const MAX_ATTRIBUTES = 8;

export function attributeError(attribute, all = []) {
  const key = attribute?.key?.trim() || "";
  if (!key) return "";
  if (!ATTRIBUTE_KEY.test(key))
    return "Lowercase letters, digits and underscores; start with a letter";
  if (all.filter((a) => a.key?.trim() === key).length > 1)
    return "Each attribute needs its own key";
  return "";
}
