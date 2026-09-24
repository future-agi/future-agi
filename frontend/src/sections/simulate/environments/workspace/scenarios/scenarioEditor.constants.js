// Options, derivations and copy for the single-scenario editor.
//
// The editor exposes only the fields the server allows to change (its
// `scenario_editing` block gates the rest read-only). A save sends an `amend`
// to the harness scenarios endpoint; a behavioural or persona change re-proves
// the scenario, and a refusal comes back with its reason.

export const EDITOR_COPY = {
  title: "Edit scenario",
  subtitle: "Editable fields are saved to the suite; others are read-only because they're proved, not described.",
  cancel: "Cancel",
  save: "Save scenario",
  scenarioSection: "Scenario",
  personaSection: "Persona",
  callerSection: "Caller",
  constraintsSection: "Call constraints",
};

// Which surfaces have a human on the other end (a caller to shape) and which of
// those are voice, where call constraints like max turns and background noise
// apply. A browser or coding agent has neither.
export const CONVERSATIONAL_SURFACES = ["voice", "chat", "messaging", "email", "multi"];
export const VOICE_ONLY_SURFACES = ["voice", "multi"];

export const TONE_OPTIONS = [
  "neutral", "polite", "urgent", "impatient", "sceptical",
  "angry", "confused", "apologetic",
];
export const STYLE_OPTIONS = ["concise", "verbose", "formal", "casual", "chatty", "terse"];
export const ACCENT_OPTIONS = ["US", "UK", "IN", "BR", "AE", "JP", "other"];
export const LANGUAGE_OPTIONS = ["English", "Spanish", "Portuguese", "Hindi", "Japanese", "Arabic"];
// The server lists noise on/off as "present" and "quiet line"; a scenario stores them as true/false.
export const noiseKey = (value) => {
  if (typeof value === "string") return value;
  if (value === true) return "present";
  return value === false ? "quiet line" : "";
};
export const noiseValue = (key) => {
  if (key === "present") return true;
  return key === "quiet line" ? false : key;
};

// A Query-tab filter token as a query-param key; the server reads the suffix as the operator.
const OPERATOR_SUFFIX = {
  is_not: "_not",
  not_equals: "_not",
  contains: "_contains",
  not_contains: "_not_contains",
};
export const filterParamKey = (field, operator) => `${field}${OPERATOR_SUFFIX[operator] || ""}`;

// Read an accent out of a "US female" / "IN male" voice string.
const parseAccent = (voice) => {
  const s = (voice || "").toLowerCase();
  return ACCENT_OPTIONS.find((a) => s.startsWith(a.toLowerCase())) || "US";
};

// Turn the persona's traits + voice into the caller-meta fields, so the editor
// opens with defaults derived from the row rather than empty controls. If the
// row ever carries these explicitly, those win at the call site.
export const deriveCaller = (persona) => {
  const traits = (persona?.traits || []).map((t) => t.toLowerCase());
  const tone = TONE_OPTIONS.find((o) => traits.some((t) => t.includes(o))) || "neutral";
  const style = STYLE_OPTIONS.find((o) => traits.some((t) => t.includes(o)))
    || (traits.includes("terse") ? "terse" : "casual");
  return { tone, style, accent: parseAccent(persona?.voice), language: "English" };
};

// Persona trait "background noise" → high, else none.
export const deriveNoise = (persona) => {
  const traits = (persona?.traits || []).map((t) => t.toLowerCase());
  return traits.some((t) => t.includes("noise")) ? "high" : "none";
};

// Sub-goals arrive as strings or {label|text|title} objects; the textarea edits
// them as one-per-line, so flatten to labels on open and re-split on change.
export const subTaskLabel = (s) =>
  typeof s === "string" ? s : (s?.label || s?.text || s?.title || "");

export const subTasksToText = (subTasks) =>
  (subTasks || []).map(subTaskLabel).filter(Boolean).join("\n");

export const textToSubTasks = (text) =>
  (text || "").split("\n").map((t) => t.trim()).filter(Boolean);
