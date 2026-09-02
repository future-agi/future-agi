/**
 * Personas and supporting personas.
 *
 * A **persona** is the counterpart the agent deals with — the caller, the
 * customer, the operator on the other end. A **supporting persona** enters
 * partway through: a supervisor asked for by name, a fraud desk that has to
 * approve something, a third party on a transferred call.
 *
 * A scenario has always carried a persona. These are the reusable briefs those
 * personas are drawn from, plus the supporting cast that joins mid-run — which
 * is why they live in a library rather than inside one environment: the same
 * difficult caller is worth pointing at every agent you own.
 *
 * They are versioned for the same reason scenarios are. If a result moves, it
 * has to be attributable to the agent rather than to a brief somebody rewrote.
 *
 * Deliberately not called "actors": in RL, an actor is the policy being trained
 * (actor-critic) or a rollout worker, so on a screen that also exposes
 * reset/step/reward the word would point at the wrong thing entirely.
 */

const iso = (d) => new Date(Date.now() - d * 86400000).toISOString();

export const PERSONA_KINDS = [
  { id: "persona", label: "Persona", blurb: "The counterpart the agent is dealing with" },
  { id: "supporting", label: "Supporting persona", blurb: "Enters partway through — a supervisor, a third party" },
];

/** Which modalities a persona makes sense in. */
export const MODALITIES = ["voice", "chat", "cua", "coding"];

export const PERSONA_LIBRARY = [
  {
    id: "per-frustrated-repeat",
    kind: "persona",
    name: "Frustrated repeat caller",
    blurb: "Has called twice already about the same order and expects to be recognised.",
    modalities: ["voice", "chat"],
    traits: ["impatient", "interrupts", "well-informed"],
    version: "v3",
    versions: [
      { label: "v3", createdAt: iso(3), note: "Barge-in made more aggressive after the agent learned to talk over it." },
      { label: "v2", createdAt: iso(19), note: "Added the second call to their history." },
      { label: "v1", createdAt: iso(40), note: "First draft." },
    ],
    usedBy: 9,
    owner: "you",
  },
  {
    id: "per-guest-no-account",
    kind: "persona",
    name: "Guest with no account",
    blurb: "Checked out as a guest, has the order number in an email, nothing else.",
    modalities: ["voice", "chat"],
    traits: ["cooperative", "vague on detail"],
    version: "v2",
    versions: [
      { label: "v2", createdAt: iso(8), note: "Gives the order number only when asked twice." },
      { label: "v1", createdAt: iso(31), note: "First draft." },
    ],
    usedBy: 6,
    owner: "you",
  },
  {
    id: "per-non-native",
    kind: "persona",
    name: "Non-native speaker, noisy line",
    blurb: "Second-language English on a poor connection — tests recovery, not comprehension alone.",
    modalities: ["voice"],
    traits: ["hesitant", "repeats themselves", "background noise"],
    version: "v1",
    versions: [{ label: "v1", createdAt: iso(12), note: "Built from three real transcripts." }],
    usedBy: 4,
    owner: "system",
  },
  {
    id: "per-power-user",
    kind: "persona",
    name: "Power user, terse",
    blurb: "Knows the product better than the script does and types in fragments.",
    modalities: ["chat", "cua"],
    traits: ["terse", "skips pleasantries", "corrects the agent"],
    version: "v2",
    versions: [
      { label: "v2", createdAt: iso(5), note: "Typos and dropped words added." },
      { label: "v1", createdAt: iso(22), note: "First draft." },
    ],
    usedBy: 7,
    owner: "you",
  },
  {
    id: "sup-supervisor",
    kind: "supporting",
    name: "Supervisor",
    blurb: "Asked for by name. Joins only when the agent escalates, and expects a handover summary.",
    modalities: ["voice", "chat"],
    traits: ["brisk", "wants the summary first"],
    version: "v2",
    versions: [
      { label: "v2", createdAt: iso(6), note: "Now refuses the handover if no summary is given." },
      { label: "v1", createdAt: iso(28), note: "First draft." },
    ],
    usedBy: 5,
    owner: "you",
  },
  {
    id: "sup-fraud-desk",
    kind: "supporting",
    name: "Fraud desk",
    blurb: "Has to approve anything above the refund cap, and takes its time.",
    modalities: ["voice", "chat"],
    traits: ["procedural", "slow to answer"],
    version: "v1",
    versions: [{ label: "v1", createdAt: iso(15), note: "Approves 60% of the time." }],
    usedBy: 3,
    owner: "system",
  },
  {
    id: "sup-third-party",
    kind: "supporting",
    name: "Third party on the line",
    blurb: "A partner or family member speaking for the account holder — tests what the agent will disclose.",
    modalities: ["voice"],
    traits: ["helpful", "not authorised"],
    version: "v1",
    versions: [{ label: "v1", createdAt: iso(9), note: "Never has the security answers." }],
    usedBy: 2,
    owner: "you",
  },
  {
    id: "sup-flaky-tool",
    kind: "supporting",
    name: "Flaky downstream service",
    blurb: "Not a person — the payment API timing out mid-call. Tests recovery rather than dialogue.",
    modalities: ["voice", "chat", "cua", "coding"],
    traits: ["intermittent", "slow"],
    version: "v1",
    versions: [{ label: "v1", createdAt: iso(4), note: "Fails one call in four." }],
    usedBy: 6,
    owner: "system",
  },
];

/** Personas already injected into an environment, by modality fit. */
export const castFor = (env) => {
  const modality = env?.surface === "browser" ? "cua" : env?.surface === "cli" ? "coding" : env?.surface;
  return PERSONA_LIBRARY.filter((a) => a.modalities.includes(modality)).slice(0, 4).map((a) => a.id);
};

export const getPersona = (id) => PERSONA_LIBRARY.find((a) => a.id === id);
