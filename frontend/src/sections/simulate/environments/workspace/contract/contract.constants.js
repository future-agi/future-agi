// The one monospace stack the contract uses for observation fields, action
// verbs and verifier names. Kept as a constant so the string is not repeated
// across the part components.
export const MONO_FONT = "ui-monospace, Menlo, monospace";

// The three kinds of verifier that make up the reward spec. The verifiers ARE
// the evals applied to the environment, given weights — grouped here only for
// presentation. Each kind is a category, so it takes a data-palette series
// (`theme.palette.data[series]`), not a status colour.
export const VERIFIER_KINDS = [
  { id: "goal", label: "Goal", series: 0, blurb: "Did the task get done. Terminal, sparse." },
  { id: "rubric", label: "Rubric", series: 1, blurb: "How well, judged per turn. Dense." },
  { id: "constraint", label: "Constraints", series: 2, blurb: "What must never happen. Penalty." },
];

const plural = (n, unit) => `${n} ${unit}${n === 1 ? "" : "s"}`;

// The run-end card in plain words, with no RL vocabulary (terminate, truncate,
// bootstrap). The platform records which way each call ended rather than
// deciding it up front. The limits have different scopes: the turn limit is
// each scenario's own, while the time limit is the whole run's wall-clock
// ceiling (every scenario together), not a per-conversation allowance.
const END_CONDITIONS_COPY = {
  title: "Run end conditions",
  subtitle: "How each conversation can end, and the limits on a run",
  notAuthored: "Not authored yet",
  // A built environment's build writes them; a template or fork is never built.
  notAuthoredBuilt: "The build writes these once it has read how a run of this agent ends.",
  notAuthoredUnbuilt: "This environment hasn't been built, so it has none.",
  endsLabel: "How a conversation can end",
  endsBlurb: "Each call records which of these ended it.",
  limitsLabel: "Limits",
  limitsBlurb: "What stops a run early.",
  // The raw code sits beside its plain label: call logs show the code.
  codeTitle: "How call logs record this ending",
  // Plain words for the canonical ended reasons a hosted call can carry
  // (futureagi/simulate/utils/ended_reason.py). Any other code is shown as words
  // made from the code itself.
  endedReasons: {
    "assistant-ended-call": {
      label: "Your agent ended the conversation",
      note: "Your agent wrapped up, or the session closed.",
    },
    cancelled: {
      label: "Cancelled",
      note: "Stopped before the conversation finished.",
    },
    "customer-ended-call": {
      label: "Customer ended the conversation",
      note: "The simulated customer finished or dropped off.",
    },
    // A per-conversation timeout (the SDK's conversation_timeout), not the
    // run's time limit shown under Limits.
    "max-duration-reached": {
      label: "Time limit reached",
      note: "This conversation hit its own time limit.",
    },
    "no-answer": {
      label: "No answer",
      note: "Your agent couldn't be reached.",
    },
    "silence-timed-out": {
      label: "Silence timed out",
      note: "Nobody spoke for a while, so the conversation closed.",
    },
  },
  turnsLimit: (n) => plural(n, "turn"),
  turnsNote: "The most any scenario allows. Each scenario sets its own.",
  durationLimit: (seconds) => {
    if (seconds > 0 && seconds % 3600 === 0) return plural(seconds / 3600, "hour");
    if (seconds > 0 && seconds % 60 === 0) return plural(seconds / 60, "minute");
    return plural(seconds, "second");
  },
  durationNote: "The whole run, every scenario together, must finish inside this time.",
};

// Every copy string on the contract tab, so nothing is inlined in the JSX.
export const CONTRACT_COPY = {
  endConditions: END_CONDITIONS_COPY,
  headingBlurb:
    "What makes this an environment rather than a test script. Everything read from your agent compiles into these five parts, and everything downstream reads them back.",
  compiled: (done, total) => `${done}/${total} compiled`,

  parts: {
    adapter: {
      title: "Modality adapter",
      blurb: "Fixes what an observation and an action are. Everything else is generic.",
    },
    spaces: {
      title: "Observation and action spaces",
      blurb:
        "Four fields are the same in every modality. The adapter fills the rest, which is why one runner serves all six.",
    },
    dynamics: {
      title: "Transition dynamics",
      blurb: "What moves the world between steps, and it is never only the agent.",
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

  // Kept for the episode card: the same plain copy as the run-end card.
  terminateLabel: END_CONDITIONS_COPY.endsLabel,
  terminateBlurb: END_CONDITIONS_COPY.endsBlurb,
  truncateLabel: END_CONDITIONS_COPY.limitsLabel,
  truncateBlurb: END_CONDITIONS_COPY.limitsBlurb,
  clockLabel: (mode) => `Clock · ${mode}`,
  seedTitle: "Deterministic seed",

  gapsTitle: "Unresolved contract fields",
  gapsSubtitle: "What compiling could not settle on its own",
  gapsFixGrading: "Fix in Evaluations",
  gapsReviewHere: "Review below",

  actors: {
    heading: "Actors",
    headingBlurb:
      "Other parties in the world, each with a goal that is not the task. They pull the episode off-course, which is what makes them part of the environment's dynamics rather than part of the task.",
    calloutLead: "Not the same as a persona.",
    calloutBody:
      "The persona is who your agent is serving, the one whose goal the task is. An actor is someone else: you are trying to book a cab, and your colleague is saying let's get pizza instead.",
    calloutLink: "See scenarios",
    create: "Create actor",
    castTitle: (n) => `In this environment (${n})`,
    castSubtitle: "Injected into every run, at the version pinned here",
    emptyIcon: "solar:users-group-two-rounded-linear",
    emptyTitle: "No actors yet",
    emptyBody:
      "Without one, every run is a clean two-party conversation, which is rarely what happens in the wild.",
    wants: "Wants:",
    builtIn: "built in",
    usedBy: (n) => `used by ${n}`,
    detail: {
      does: "What they do",
      tests: "What it tests",
      entry: "When they enter",
      modalities: "Modalities",
      traits: "Traits",
      versions: "Versions",
    },
  },
};

export const GAP_LEGEND_ORDER = ["blocking", "assumed", "resolved"];
