import { BUILD_TONES } from "src/sections/simulate/environments/buildEnvironment/buildTones";

// The one monospace stack the contract uses for observation fields, action
// verbs and verifier names. Kept as a constant so the string is not repeated
// across the part components.
export const MONO_FONT = "ui-monospace, Menlo, monospace";

// The three kinds of verifier that make up the reward spec. The verifiers ARE
// the evals applied to the environment, given weights — grouped here only for
// presentation. Colours read from BUILD_TONES.
export const VERIFIER_KINDS = [
  { id: "goal", label: "Goal", color: BUILD_TONES.green, blurb: "Did the task get done. Terminal, sparse." },
  { id: "rubric", label: "Rubric", color: BUILD_TONES.blue, blurb: "How well, judged per turn. Dense." },
  { id: "constraint", label: "Constraints", color: BUILD_TONES.red, blurb: "What must never happen. Penalty." },
];

// Every copy string on the contract tab, so nothing is inlined in the JSX.
export const CONTRACT_COPY = {
  heading: "Environment contract",
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
        "Four fields are the same in every modality. The adapter fills the rest — which is why one runner serves all six.",
    },
    dynamics: {
      title: "Transition dynamics",
      blurb: "What moves the world between steps — and it is never only the agent.",
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
    "Everything below is derived from this. Connecting a different kind of agent rewrites the observation and action spaces, the fidelity controls and the runtime connection together — so the modality changes on the Agent step, not here.",

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
  truncateBlurb: "Not a failure, and not terminal — bootstrap from the last state.",
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
      "The persona is who your agent is serving — the one whose goal the task is. An actor is someone else: you are trying to book a cab, and your colleague is saying let's get pizza instead.",
    calloutLink: "See scenarios",
    create: "Create actor",
    castTitle: (n) => `In this environment (${n})`,
    castSubtitle: "Injected into every run, at the version pinned here",
    emptyIcon: "solar:users-group-two-rounded-linear",
    emptyTitle: "No actors yet",
    emptyBody:
      "Without one, every run is a clean two-party conversation — which is rarely what happens in the wild.",
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
