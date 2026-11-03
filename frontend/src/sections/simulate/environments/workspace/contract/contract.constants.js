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
    notInWorld: "not an action of this world",
    inUse: (name, count) =>
      `${name} is in ${count === 1 ? "1 scenario" : `${count} scenarios`}. Take them out of ${count === 1 ? "it" : "those scenarios"} first, by editing the scenario or asking the builder chat.`,
    showView: "What they see",
    hideView: "Hide what they see",
    sees: "Sees",
    neverSees: "Never sees",
    comesIn: "Comes in",
    loudnessShare: (ratio) =>
      `${Math.round(ratio * 100)}% as loud as the caller`,
    loudnessAutomatic: "Loudness automatic",
    atStart: "From the start",
    whenCalled: (action) => `When the agent calls ${action}`,
    ifNoAnswer: "If they do not answer",
    graderMaterial: "Grader material",
    othersPrivate: (fields, count) =>
      `What the ${count === 1 ? "other actor keeps" : `${count} other actors each keep`} to themselves: ${fields}`,
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
      moreDetails: "More details (not used by runs yet)",
      lessDetails: "Hide more details",
      moreDetailsHelp:
        "Saved with the actor, but a run plays them from each scenario's actions, so these do not change what happens yet.",
      createCallerTitle: "Add someone on the caller's side",
      editTitle: (name) => `Edit ${name}`,
      id: "Id",
      idHelp:
        "Made from the name; change it now if you like. 1 to 64 lowercase letters, digits or underscores. It never changes once saved.",
      idTaken: (name) => `Already used by ${name}; choose another id`,
      idInvalid:
        "Use 1 to 64 lowercase letters, digits or underscores, such as sam_lee",
      permittedUnknown: (ids) =>
        `${ids.join(", ")} ${ids.length === 1 ? "is not an action" : "are not actions"} of this world, so a run cannot use ${ids.length === 1 ? "it" : "them"}; remove ${ids.length === 1 ? "it" : "them"} or pick from the list`,
      idFixed:
        "Fixed once saved, so scenarios and past runs keep pointing at this actor.",
      name: "Name",
      nameHelp: "What others call them, such as Priya Raman",
      role: "Role (optional)",
      roleHelp:
        "Who they are, in a few words, such as a manager or a family member",
      atLimit: (max) => `At most ${max} characters`,
      kind: "Kind (optional)",
      kinds: { person: "A person", agent: "Another agent", system: "A system" },
      parent: "Brought in by (optional)",
      parentHelp: "Someone already in the world who brings them in, if anyone",
      nobody: "No one",
      goal: "What they want (optional)",
      goalHelp:
        "Their own aim, which is not the agent's task, such as approve only refunds she can justify",
      mayAgree: "May agree to (optional, one per line)",
      private: "Keeps to themselves (optional, one per line)",
      escalateWhen: "Hands over when (optional, one per line)",
      stopWhen: "Is done when (optional, one per line)",
      facts: "What they know (optional, one per line)",
      factsHelp: "Facts they can use, such as the refund limit is $500",
      style: "How they behave (optional)",
      styleHelp: "Their manner, such as brisk and decides quickly",
      answers: "Answers",
      immediately: "Straight away",
      afterDelay: "After a delay",
      seconds: "Seconds",
      permitted: "World actions they may take (optional)",
      permittedHelp:
        "Pick from this environment's actions. The world refuses anything else they try and records the attempt.",
      noWorldActions:
        "This environment has no world actions: its tools run inside the agent, so no one in the world can act here.",
      trigger: "Comes in",
      triggerHelp: "When they first act in a call",
      timeout: "Waits, in seconds (optional)",
      timeoutHelp:
        "How long the world waits for them; 0 waits as long as the call lasts",
      fallback: "If they do not answer",
      fallbacks: { silent: "Nothing happens", decline: "They decline" },
      answeredBy: (name) => `${name} answers instead`,
      fallbackLabel: (id) =>
        ({ silent: "Nothing happens", decline: "They decline" })[id] ||
        `${id} answers instead`,
      channel: "How they join the call",
      loudness: "How loud, next to the caller (optional)",
      loudnessAutomatic: (low, high) =>
        `Automatic: each call picks between ${low} and ${high} of the caller's voice`,
      loudnessFixed: (share) => `${share} of the caller's voice`,
      voice: "How they sound (optional)",
      voiceHelp:
        "Picks their voice on a call; left empty, a voice is chosen for them",
      gender: "Gender",
      genders: { "": "Any", male: "Male", female: "Female" },
      ageGroup: "Age",
      ageGroups: {
        "": "Any",
        "18-31": "Under 32",
        "32-49": "32 to 49",
        "50-59": "50 to 59",
        "60+": "60 or older",
      },
      accent: "Accent",
      accents: ["", "American", "British", "Indian", "Australian", "Neutral"],
      anyAccent: "Any",
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
