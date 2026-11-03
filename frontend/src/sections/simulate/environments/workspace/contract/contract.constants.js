import { BUILD_TONES } from "src/sections/simulate/environments/buildEnvironment/buildTones";

// The one monospace stack the contract uses for observation fields, action
// verbs and verifier names. Kept as a constant so the string is not repeated
// across the part components.
export const MONO_FONT = "ui-monospace, Menlo, monospace";

// The three kinds of verifier that make up the reward spec. The verifiers ARE
// the evals applied to the environment, given weights — grouped here only for
// presentation. Colours read from BUILD_TONES.
export const VERIFIER_KINDS = [
  {
    id: "goal",
    label: "Goal",
    color: BUILD_TONES.green,
    blurb: "Did the task get done. Terminal, sparse.",
  },
  {
    id: "rubric",
    label: "Rubric",
    color: BUILD_TONES.blue,
    blurb: "How well, judged per turn. Dense.",
  },
  {
    id: "constraint",
    label: "Constraints",
    color: BUILD_TONES.red,
    blurb: "What must never happen. Penalty.",
  },
];

// Every copy string on the contract tab, so nothing is inlined in the JSX.
export const CONTRACT_COPY = {
  endConditions: {
    title: "Run end conditions",
    subtitle: "When a run is over, and which kind of over it was",
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

  subActors: {
    title: (n) => `Sub-actors (${n})`,
    subtitle:
      "People on the person's side who are not the person. Change them in the builder chat.",
    channels: {
      audio_mix: "Heard nearby",
      audio_handoff: "Takes the phone",
      ivr: "Phone menu",
    },
    scenarios: (n) => `In ${n} scenario${n === 1 ? "" : "s"}`,
  },

  worldActors: {
    title: (n) => `Actors (${n})`,
    subtitle:
      "Everyone the agent deals with who is not the agent: people beside the caller on the call, and people and systems in the world. Edits wait in the draft until you save.",
    unsaved: "Unsaved changes: saved with the environment's other changes",
    callerSide: "On the caller's side",
    callerSideAbout:
      "People with the caller who speak on the call. They know the caller's situation, never the caller's own details.",
    callerSideEmpty: "No one with the caller yet.",
    addCallerSide: "Add someone on the caller's side",
    worldSide: "In the world",
    worldSideAbout:
      "People and systems who act through the world's own actions, such as a manager approving in the CRM. They never speak to the agent.",
    add: "Add someone in the world",
    edit: "Edit",
    remove: "Remove",
    empty: "No one in the world yet. Add one, or ask the builder chat.",
    wants: "Wants",
    mayDo: "May do",
    nothing: "Nothing in the world",
    sees: "Sees",
    neverSees: "Never sees",
    comesIn: "Comes in",
    atStart: "From the start",
    whenCalled: (action) => `When the agent calls ${action}`,
    ifNoAnswer: "If they do not answer",
    graderMaterial: "Grader material",
    othersPrivate: (id) => `${id}'s`,
    // What the view's inputs are called on screen; an input a newer release adds shows by its own name.
    inputs: {
      "actor.name": "Name",
      "actor.role": "Role",
      "actor.persona": "Persona",
      "actor.goal": "What they want",
      "actor.constraints": "What they may agree to and keep to themselves",
      "actor.behaviour": "How they behave",
      "actor.prompt": "Their prompt",
      "actor.surface": "How they reach the agent",
      "actor.knowledge": "What they know",
      "scenario.instruction": "What they do in this scenario",
      "scenario.knowledge": "What they know in this scenario",
      "caller.situation":
        "The caller's situation, without the caller's own details",
    },
    form: {
      createTitle: "Add someone in the world",
      createCallerTitle: "Add someone on the caller's side",
      editTitle: (name) => `Edit ${name}`,
      id: "Id",
      idHelp:
        "1 to 64 lowercase letters, digits or underscores, such as store_manager. It never changes.",
      name: "Name",
      nameHelp: "What others call them, such as Priya Raman",
      role: "Role",
      roleHelp:
        "Who they are to the others, such as the store manager who approves refunds",
      kind: "Kind",
      kinds: { person: "A person", agent: "Another agent", system: "A system" },
      parent: "Brought in by",
      parentHelp: "Someone already in the world who brings them in, if anyone",
      nobody: "No one",
      goal: "What they want",
      goalHelp:
        "Their own aim, which is not the agent's task, such as approve only refunds she can justify",
      mayAgree: "May agree to (one per line)",
      private: "Keeps to themselves (one per line)",
      escalateWhen: "Hands over when (one per line)",
      stopWhen: "Is done when (one per line)",
      facts: "What they know (one per line)",
      factsHelp: "Facts they can use, such as the refund limit is $500",
      style: "How they behave",
      styleHelp: "Their manner, such as brisk and decides quickly",
      answers: "Answers",
      immediately: "Straight away",
      afterDelay: "After a delay",
      seconds: "Seconds",
      permitted: "World actions they may take",
      permittedHelp:
        "Pick from this environment's actions. The world refuses anything else they try and records the attempt.",
      noWorldActions:
        "This environment has no world actions: its tools run inside the agent, so no one in the world can act here.",
      trigger: "Comes in",
      triggerHelp: "When they first act in a call",
      timeout: "Waits (seconds)",
      timeoutHelp:
        "How long the world waits for them; 0 waits as long as the call lasts",
      fallback: "If they do not answer",
      fallbacks: { silent: "Nothing happens", decline: "They decline" },
      answeredBy: (name) => `${name} answers instead`,
      fallbackLabel: (id) =>
        ({ silent: "Nothing happens", decline: "They decline" })[id] ||
        `${id} answers instead`,
      channel: "How they join the call",
      loudness: "How loud, next to the caller",
      loudnessHelp:
        "A share of the caller's voice, above 0 and up to 1 (1 is as loud as the caller). Empty picks one per call.",
      cancel: "Cancel",
      save: "Save to draft",
    },
  },

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
