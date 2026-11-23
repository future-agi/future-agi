// The one monospace stack the contract uses for observation fields, action
// verbs and verifier names. Kept as a constant so the string is not repeated
// across the part components.
export const MONO_FONT = "ui-monospace, Menlo, monospace";

// The three kinds of verifier that make up the reward spec. The verifiers ARE
// the evals applied to the environment, given weights — grouped here only for
// presentation. Each kind is a category, so it takes a data-palette series
// (`theme.palette.data[series]`), not a status colour.
export const VERIFIER_KINDS = [
  {
    id: "goal",
    label: "Goal",
    series: 0,
    blurb: "Did the task get done. Terminal, sparse.",
  },
  {
    id: "rubric",
    label: "Rubric",
    series: 1,
    blurb: "How well, judged per turn. Dense.",
  },
  {
    id: "constraint",
    label: "Constraints",
    series: 2,
    blurb: "What must never happen. Penalty.",
  },
];

// Every copy string on the contract tab, so nothing is inlined in the JSX.
export const CONTRACT_COPY = {
  endConditions: {
    title: "When a conversation ends",
    subtitle: "What ends a conversation, and what that means for its result",
    endsLabel: "It ends",
    endsBlurb: "The conversation is over, and its checks decide the result.",
    endsNone: "No ways to end recorded yet.",
    cutLabel: "It's cut off",
    cutBlurb: "A limit stops the conversation before it's over.",
    cutNone: "No limits recorded yet.",
    turns: (n) => `${n} ${n === 1 ? "turn" : "turns"}`,
    turnsNote:
      "The most turns across the suite. Each scenario can carry its own limit.",
    durationNote: "The longest the whole run can take.",
    timeLabel: (mode) => `Time · ${mode}`,
    clockNote: {
      "real-time": "Real time: the voice call runs as it happens.",
      stepped: "Stepped: the chat moves forward one turn at a time.",
    },
    clockNoteDefault: "How time is measured in a run.",
    seedValue: (value) => `World seed · ${value}`,
    seedControls: (controls) =>
      controls.includes("scenario_world_rng")
        ? "Controls how the world's records are set up at the start of each run. It does not make conversations or results repeat exactly."
        : "What this seed controls was not reported. It does not make conversations or results repeat exactly.",
    notAuthored: "Not authored yet",
    // A built environment's build writes them; a template or fork is never built.
    notAuthoredBuilt:
      "The build writes these once it has read how a run of this agent ends.",
    notAuthoredUnbuilt: "This environment hasn't been built, so it has none.",
  },
  headingBlurb:
    "What makes this an environment rather than a test script. Everything read from your agent compiles into these five parts, and everything downstream reads them back.",
  compiled: (done, total) => `${done}/${total} compiled`,

  parts: {
    adapter: {
      title: "Modality adapter",
      blurb:
        "Fixes what an observation and an action are. Everything else is generic.",
    },
    spaces: {
      title: "Observation and action spaces",
      blurb:
        "Four fields are the same in every modality. The adapter fills the rest, which is why one runner serves all six.",
    },
    dynamics: {
      title: "Transition dynamics",
      blurb:
        "What moves the world between steps, and it is never only the agent.",
    },
    reward: {
      title: "Reward spec",
      blurb:
        "The verifiers ARE the evals, given weights. If reward had its own definition you could train an agent that scores well and still fails the gate.",
    },
    episode: {
      title: "Episode contract",
      blurb: "When a run is over, and which kind of over it was.",
    },
  },

  adapterSurfaceNote: "· from this environment's surface",
  adapterDerivedNote:
    "Everything below is derived from this. Connecting a different kind of agent rewrites the observation and action spaces, the fidelity controls and the runtime connection together, so the modality changes on the Agent step, not here.",

  observationLabel: "Observation",
  actionLabel: "Action",
  coreChip: "core",

  openAction: "Open",

  rewardBuildNote:
    "Weights become editable after your first run, on the RL interface. Verifiers are the evals applied to this environment.",
  rewardNote:
    "Weights are editable on the interface, and the verifiers themselves are the evals applied to this environment.",
  interfaceAction: "Interface",

  terminateLabel: "Terminate",
  terminateBlurb: "The episode has a value. Goal verifiers settle.",
  truncateLabel: "Truncate",
  truncateBlurb:
    "Not a failure, and not terminal. Bootstrap from the last state.",
  clockLabel: (mode) => `Clock · ${mode}`,
  seedTitle: "Deterministic seed",

  gapsTitle: "Unresolved contract fields",
  gapsSubtitle: "What compiling could not settle on its own",
  gapsFixGrading: "Fix in Evaluations",
  gapsReviewHere: "Review below",
};

export const GAP_LEGEND_ORDER = ["blocking", "assumed", "resolved"];

export const CONTRACT_LISTS_COPY = {
  rules: {
    title: (n) => `Rules (${n})`,
    subtitle: "From the agent's prompt",
    empty: "No rules yet.",
    add: "Add rule",
    id: (n) => `R${n}`,
    noun: "rule",
    field: "Rule",
  },
  useCases: {
    title: (n) => `Use cases (${n})`,
    subtitle: "What your agent is actually for",
    empty: "No use cases yet.",
    add: "Add use case",
    id: (n) => `U${n}`,
    noun: "use case",
    field: "Use case",
  },
  rowActions: (noun, n) => `Actions for ${noun} ${n}`,
  edit: "Edit",
  remove: "Remove",
  busy: "The builder is busy. Try again once it finishes.",
  notLive: "You can change the contract once the environment is ready.",
};

export const CONTRACT_EDIT_COPY = {
  addTitle: (noun) => `Add a ${noun}`,
  editTitle: (noun, n) => `Edit ${noun} ${n}`,
  removeTitle: (noun, n) => `Remove ${noun} ${n}`,
  explain:
    "This asks the builder to make the change. It lands as a draft that you save as a new environment version, and the scenarios that depend on it are checked again.",
  removeExplain: (noun, text) =>
    `This asks the builder to remove the ${noun} "${text}". It lands as a draft that you save as a new environment version, and the scenarios that depend on it are checked again.`,
  submit: "Ask the builder",
  removeAction: (noun) => `Remove ${noun}`,
  sent: "The builder is updating the contract. The change shows as a draft for you to save.",
};

export const RULE_DETAIL_COPY = {
  enforcedBy: (by) => `Enforced by ${by.join(", ")}`,
  checkedBy: (goal) => `Checked by ${goal}`,
};

export const SUB_GOALS_COPY = {
  title: (n) => `Sub-goals (${n})`,
  subtitle: "What a run is checked on, and how each check reports",
  output: (output) => output.replaceAll("_", "/"),
};

export const ACTORS_COPY = {
  title: (n) => `Actors (${n})`,
  subtitle: "Other parties the agent's instructions name",
};
