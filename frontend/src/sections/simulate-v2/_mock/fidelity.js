/**
 * Production messiness, per modality.
 *
 * A scenario that runs clean is a scenario that tests the happy path twice.
 * Real callers talk over the agent, real users type half a sentence and change
 * their mind, real screens move under a cursor, real repos are dirty. Each
 * modality is messy in its own way, so the controls differ by modality rather
 * than being one generic "difficulty" dial.
 *
 * Fidelity is recorded with the run. If a score moves, it has to be
 * attributable to the agent rather than to someone quietly turning the noise
 * down, which is the same reason environments and personas are versioned.
 */

export const MODALITY_FOR = {
  voice: "voice",
  chat: "chat",
  messaging: "chat",
  email: "chat",
  browser: "cua",
  computer: "cua",
  cli: "coding",
  api: "coding",
  mcp: "coding",
  sim: "cua",
  multi: "voice",
};

const slider = (id, label, help, def, marks) => ({ kind: "slider", id, label, help, def, marks });
const toggle = (id, label, help, def) => ({ kind: "toggle", id, label, help, def });
const chips = (id, label, help, options, def) => ({ kind: "chips", id, label, help, options, def });

export const FIDELITY = {
  voice: {
    label: "Voice",
    blurb: "How the line actually sounds, and how callers actually behave on it.",
    groups: [
      {
        title: "The caller",
        controls: [
          chips("accents", "Accents", "Drawn per run from the ones you pick.",
            ["RP", "Glaswegian", "Geordie", "Indian English", "US Southern", "Nigerian English", "Australian"],
            ["RP", "Glaswegian", "Indian English"]),
          chips("demographics", "Demographics", "Age and register shift how a caller phrases things.",
            ["18–29", "30–49", "50–69", "70+"], ["30–49", "50–69"]),
          chips("tone", "Tone and personality", "Applied on top of the persona's own traits.",
            ["calm", "impatient", "anxious", "chatty", "curt", "distressed"], ["impatient", "calm"]),
        ],
      },
      {
        title: "The line",
        controls: [
          chips("noise", "Background noise", "Played under the caller's audio.",
            ["quiet room", "street", "café", "call centre", "car", "TV on"], ["street", "café"]),
          slider("noiseLevel", "Noise level", "How loud, relative to the caller.", 35),
          slider("bargeIn", "Barge-in rate", "How often the caller talks over the agent.", 25),
          slider("silence", "Dead air tolerance", "How long the caller waits before prompting again.", 40),
        ],
      },
    ],
  },

  chat: {
    label: "Chat",
    blurb: "How people actually type when they are annoyed and on a phone.",
    groups: [
      {
        title: "How they write",
        controls: [
          slider("typos", "Typos", "Rate of misspellings and transpositions.", 20),
          slider("fragments", "Text gaps", "Half-sentences, missing context, messages sent in pieces.", 30),
          chips("styling", "Tone and styling", "How the message is dressed.",
            ["formal", "casual", "shouty caps", "emoji", "no punctuation"], ["casual", "no punctuation"]),
        ],
      },
      {
        title: "How the conversation moves",
        controls: [
          slider("drift", "Multi-turn drift", "How far the user wanders from the original request.", 35),
          slider("impatience", "Repeat pressure", "How quickly they re-ask if the answer is slow.", 30),
          toggle("contextLoss", "Assume prior context", "The user refers to an earlier chat the agent cannot see.", true),
        ],
      },
    ],
  },

  cua: {
    label: "Computer use",
    blurb: "Screens that move, states that change, and what happens when a step fails.",
    groups: [
      {
        title: "The journey",
        controls: [
          slider("depth", "Journey depth", "How many screens a task spans before it can be finished.", 55),
          toggle("multiApp", "Cross-application", "The task cannot be completed inside one app.", true),
        ],
      },
      {
        title: "The environment fights back",
        controls: [
          slider("domChurn", "App / DOM change", "How often the page shifts under the agent between steps.", 30),
          slider("latency", "Render latency", "How long elements take to appear.", 25),
          toggle("errorRecovery", "Inject failures", "A step fails once and must be recovered from, not restarted.", true),
          chips("failures", "Failure kinds", "What goes wrong mid-journey.",
            ["stale element", "modal steals focus", "session timeout", "network drop", "layout shift"],
            ["stale element", "modal steals focus"]),
        ],
      },
    ],
  },

  coding: {
    label: "Coding",
    blurb: "The repo as it really is, not as a clean checkout.",
    groups: [
      {
        title: "Repo state",
        controls: [
          chips("repoState", "Starting state", "What the working tree looks like when the task begins.",
            ["clean", "uncommitted changes", "failing tests", "merge conflict", "stale lockfile"],
            ["uncommitted changes", "failing tests"]),
          slider("spread", "Multi-file span", "How many files a task has to touch to be correct.", 45),
        ],
      },
      {
        title: "How it is graded",
        controls: [
          toggle("testGraded", "Test-graded outcomes", "The repo's own tests decide pass or fail, not a judge.", true),
          toggle("mustNotBreak", "No collateral breakage", "Existing passing tests must still pass afterwards.", true),
          slider("budget", "Step budget", "How much room the agent gets before the task is called.", 60),
        ],
      },
    ],
  },
};

export const fidelityFor = (env) => FIDELITY[MODALITY_FOR[env?.surface] || "chat"];

/**
 * The delivery controls, without the caller identity fields.
 *
 * Fidelity used to be its own page whose "Caller" section duplicated persona
 * (accent, tone, demographics), and whose "Line" section — noise, barge-in,
 * typos, DOM churn, repo state — was really about how each individual persona
 * *comes through*, not something the environment applies uniformly. So the
 * Fidelity page is gone and these move onto the persona; identity and delivery
 * live together now.
 *
 * The caller-identity group (voice: accents/demographics/tone; chat: styling)
 * is stripped, because those are already fields on the persona editor. What
 * remains is genuinely per-persona channel behaviour.
 */
const CALLER_GROUP_TITLES = new Set(["The caller"]);
const CALLER_CONTROL_IDS = new Set(["styling"]);

export const deliveryControls = (env) => {
  const spec = fidelityFor(env);
  if (!spec) return [];
  return spec.groups
    .filter((g) => !CALLER_GROUP_TITLES.has(g.title))
    .map((g) => ({
      ...g,
      controls: g.controls.filter((c) => !CALLER_CONTROL_IDS.has(c.id)),
    }))
    .filter((g) => g.controls.length);
};

/** Defaults, flattened, so a run always has a full record of what it ran with. */
export const defaultFidelity = (env) => {
  const spec = fidelityFor(env);
  const out = {};
  spec.groups.forEach((g) => g.controls.forEach((c) => { out[c.id] = c.def; }));
  return out;
};

/** A line of sample output, so a setting reads as a consequence not a number. */
export const fidelitySample = (modality, values) => {
  if (modality === "chat") {
    const typo = (values.typos || 0) > 15;
    return typo
      ? "wheres my refund it said 3 days that was last wek"
      : "Where is my refund? It said three days, and that was last week.";
  }
  if (modality === "voice") {
    const barge = (values.bargeIn || 0) > 20;
    return barge
      ? "Caller cuts in at 1.2s: “—no, I already told the last person that”"
      : "Caller waits for the agent to finish, then answers.";
  }
  if (modality === "cua") {
    return (values.domChurn || 0) > 25
      ? "Row order changes between reading the list and clicking the target."
      : "Page stays still between steps.";
  }
  return (values.repoState || []).includes("failing tests")
    ? "Two tests already red before the agent starts — fixing them is not the task."
    : "Clean tree, all tests green.";
};
