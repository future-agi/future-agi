// Options, derivations and copy for the single-scenario editor.
//
// The editor exposes only the fields the server allows to change (its
// `scenario_editing` block gates the rest read-only). A save sends an `amend`
// to the harness scenarios endpoint; a behavioural or persona change re-proves
// the scenario, and a refusal comes back with its reason.

export const EDITOR_COPY = {
  title: "Edit scenario",
  subtitle:
    "Editable fields are saved to the suite; others are read-only because they're proved, not described.",
  cancel: "Cancel",
  save: "Save scenario",
  scenarioSection: "Scenario",
  personaSection: "Persona",
  callerSection: "Caller",
  constraintsSection: "Call constraints",
  personaHint:
    "Who's on the other end of the run. Shapes tone, pacing and any traits the caller carries into the conversation.",
  subGoalsSection: "Sub-goals",
  builderSection: "Edit through the builder",
  builderHint:
    "Locked to the verification proof. Ask the builder to change them.",
  builderNote:
    "Scenarios are verified before they land, so these change through the builder, which re-checks them.",
  sendToBuilder: "Send to builder",
  useLibrary: "Use a library persona",
  saveToLibrary: "Save to library",
  libraryNote:
    "Pick a library persona to fill the fields below, or save this caller to reuse it in other environments.",
  mustPass: "Must-pass",
  mustPassHint:
    "An improvement that breaks it is rejected, however much else it lifts.",
};

// The persona fields the editor can write: the draft key, the server key the
// `scenario_editing.persona_fields` gate and the `set_persona` op use, and the
// label. Languages are a list; the rest are single values.
export const PERSONA_FIELDS = [
  { key: "accent", server: "accent", label: "Accent" },
  {
    key: "communicationStyle",
    server: "communication_style",
    label: "Communication style",
  },
  { key: "personality", server: "personality", label: "Personality" },
  { key: "occupation", server: "occupation", label: "Occupation" },
  { key: "location", server: "location", label: "Location" },
  { key: "languages", server: "languages", label: "Languages", list: true },
];

// The persona library's agent type for an environment's surface: "voice" or
// "text", or null so the create drawer asks.
export const personaTypeFor = (surface) => {
  if (surface === "voice") return "voice";
  return ["chat", "text", "messaging", "email"].includes(surface)
    ? "text"
    : null;
};

// Sub-goals per scenario, and the quiet warning outside that range.
export const SUBGOAL_MIN = 2;
export const SUBGOAL_MAX = 7;
export const subGoalCountNote = (n) => {
  if (n < SUBGOAL_MIN) {
    return `Add ${SUBGOAL_MIN - n} more. A scenario needs at least ${SUBGOAL_MIN} sub-goals to be graded.`;
  }
  if (n > SUBGOAL_MAX) {
    return `${n - SUBGOAL_MAX} over. Keep it to ${SUBGOAL_MAX} and split the rest into another scenario.`;
  }
  return null;
};

// What an edit does to past results, said before Save.
export const EDIT_EFFECT = {
  minor: {
    title: "Past runs are re-graded",
    note: "A grader fix on what a pass looks like. Past runs are re-graded from stored traces.",
  },
  major: {
    title: "Past results need a re-run",
    note: "This edit re-proves the scenario. Past results don't compare until it is re-run.",
  },
  retest: {
    title: "Past results need a re-run",
    note: "It tests something else now. Past results don't compare until it is re-run.",
  },
};

// The scenario as a text block for the builder composer, from the editor's
// current values.
export const scaffoldTextFor = (draft = {}, subGoals = []) => {
  const p = draft.persona || {};
  const personaBits = [
    p.name,
    p.ageGroup && `age ${p.ageGroup}`,
    p.gender,
    p.accent && `accent ${p.accent}`,
    p.communicationStyle && `style ${p.communicationStyle}`,
    p.personality && `personality ${p.personality}`,
    p.occupation && `occupation ${p.occupation}`,
    p.location && `in ${p.location}`,
    p.languages && `speaks ${p.languages}`,
  ].filter(Boolean);
  const lines = [
    `Scenario: ${draft.name || "untitled"}`,
    draft.useCase && `Use case: ${draft.useCase}`,
    draft.branch && `Branch: ${draft.branch}`,
    draft.instruction && `What the caller wants: ${draft.instruction}`,
    draft.tests && `Passes when: ${draft.tests}`,
    subGoals.length > 0 &&
      ["Sub-goals:", ...subGoals.map((g) => `- ${g}`)].join("\n"),
    personaBits.length > 0 && `Persona: ${personaBits.join(", ")}`,
    draft.maxTurns && `Max turns: ${draft.maxTurns}`,
    draft.backgroundNoise && `Background noise: ${draft.backgroundNoise}`,
  ];
  return lines.filter(Boolean).join("\n");
};

// Which surfaces have a human on the other end (a caller to shape) and which of
// those are voice, where call constraints like max turns and background noise
// apply. A browser or coding agent has neither.
export const CONVERSATIONAL_SURFACES = [
  "voice",
  "chat",
  "messaging",
  "email",
  "multi",
];
export const VOICE_ONLY_SURFACES = ["voice", "multi"];

export const TONE_OPTIONS = [
  "neutral",
  "polite",
  "urgent",
  "impatient",
  "sceptical",
  "angry",
  "confused",
  "apologetic",
];
export const STYLE_OPTIONS = [
  "concise",
  "verbose",
  "formal",
  "casual",
  "chatty",
  "terse",
];
export const ACCENT_OPTIONS = ["US", "UK", "IN", "BR", "AE", "JP", "other"];
export const LANGUAGE_OPTIONS = [
  "English",
  "Spanish",
  "Portuguese",
  "Hindi",
  "Japanese",
  "Arabic",
];
// The server lists noise on/off as "present" and "quiet line"; a scenario stores them as true/false.
// Fields whose values the server names in `level_labels`: noise beds, sub-goals and coverage levels.
export const withServedLabels = (fields = [], levelLabels = {}) =>
  fields.map((field) =>
    field.value === "background_noise" ||
    field.value === "sub_goals" ||
    String(field.value).startsWith("coverage.")
      ? { ...field, choiceLabels: levelLabels }
      : field,
  );

export const noiseKey = (value) => {
  if (typeof value === "string") return value;
  if (value === true) return "present";
  return value === false ? "quiet line" : "";
};
export const noiseValue = (key) => {
  if (key === "present") return true;
  return key === "quiet line" ? false : key;
};

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
  const tone =
    TONE_OPTIONS.find((o) => traits.some((t) => t.includes(o))) || "neutral";
  const style =
    STYLE_OPTIONS.find((o) => traits.some((t) => t.includes(o))) ||
    (traits.includes("terse") ? "terse" : "casual");
  return {
    tone,
    style,
    accent: parseAccent(persona?.voice),
    language: "English",
  };
};

// Persona trait "background noise" → high, else none.
export const deriveNoise = (persona) => {
  const traits = (persona?.traits || []).map((t) => t.toLowerCase());
  return traits.some((t) => t.includes("noise")) ? "high" : "none";
};

// Sub-goals arrive as strings or {label|text|title} objects; the textarea edits
// them as one-per-line, so flatten to labels on open and re-split on change.
export const subTaskLabel = (s) =>
  typeof s === "string" ? s : s?.label || s?.text || s?.title || "";

// The same sub-goals as { id, label } for display, empty ones dropped. The
// table cell and the list view both render from this, so they can't disagree
// about which shapes they understand.
export const normaliseSubTasks = (subTasks) =>
  (subTasks || [])
    .map((st, i) => ({ id: st?.id || `st-${i}`, label: subTaskLabel(st) }))
    .filter((st) => st.label);

export const subTasksToText = (subTasks) =>
  (subTasks || []).map(subTaskLabel).filter(Boolean).join("\n");

export const textToSubTasks = (text) =>
  (text || "")
    .split("\n")
    .map((t) => t.trim())
    .filter(Boolean);
