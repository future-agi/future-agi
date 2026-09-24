// Where each derived fact came from. The builder reads an agent's source and
// comes back with tools and rules; every fact carries an origin (what kind of
// place it was found in) so the derivation stays reviewable. Ported verbatim
// from the designer's _mock/provenance.js ORIGIN_KINDS, with the hex colours
// lifted into BUILD_TONES.
import { BUILD_TONES } from "./buildTones";

export const ORIGIN_KINDS = {
  code: {
    id: "code",
    label: "enforced in code",
    short: "CODE",
    color: BUILD_TONES.green,
    note: "Found in a branch that actually refuses the request. The agent cannot talk its way past this one, so a scenario probing it is testing the wiring rather than the instruction.",
  },
  config: {
    id: "config",
    label: "from config",
    short: "CONFIG",
    color: BUILD_TONES.blue,
    note: "Declared in a manifest the agent loads at startup. Machine-written and machine-read, so it says what it means.",
  },
  prompt: {
    id: "prompt",
    label: "prompt only",
    short: "PROMPT",
    color: BUILD_TONES.amber,
    note: "Stated in the system prompt and nowhere else. Nothing enforces it at runtime — which is precisely why it is worth grading rather than assuming.",
  },
  doc: {
    id: "doc",
    label: "prose only",
    short: "PROSE",
    color: BUILD_TONES.red,
    note: "Read out of a comment or a README. Anything in the repo can write prose — a vendored dependency, a stale note, someone who wanted a softer grader — so a rule found here is recorded and held back until you confirm it.",
  },
  callGraph: {
    id: "callGraph",
    label: "read from the call-graph",
    short: "CALL-GRAPH",
    color: BUILD_TONES.sky,
    note: "The tool is invoked by name from the agent's own code. Not just declared — actually reached at runtime.",
  },
  policy: {
    id: "policy",
    label: "read from policy.yaml",
    short: "POLICY.YAML",
    color: BUILD_TONES.blue,
    note: "Declared in a policy manifest the agent loads at startup. Data, not judgement — the rule is what the file says.",
  },
  fixture: {
    id: "fixture",
    label: "seeded from a fixture",
    short: "FIXTURE",
    color: BUILD_TONES.accent,
    note: "The world starts with these rows every run. Change the fixture, change the seeded state.",
  },
  inferred: {
    id: "inferred",
    label: "inferred",
    short: "INFERRED",
    color: BUILD_TONES.amber,
    note: "Nothing in the agent's code or policy stated this — the reader guessed based on prompt phrasing or call patterns. Confirm before you rely on it.",
  },
};

export const ORIGIN_ID = {
  CODE: "code",
  CONFIG: "config",
  PROMPT: "prompt",
  DOC: "doc",
  CALL_GRAPH: "callGraph",
  POLICY: "policy",
  FIXTURE: "fixture",
  INFERRED: "inferred",
};
