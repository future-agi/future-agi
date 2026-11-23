// Where each derived fact came from. The builder reads an agent's source and
// comes back with tools and rules; every fact carries an origin (what kind of
// place it was found in) so the derivation stays reviewable. Ported verbatim
// from the designer's _mock/provenance.js ORIGIN_KINDS. An origin renders as a
// neutral tag, so the kinds carry no colour of their own.

export const ORIGIN_KINDS = {
  code: {
    id: "code",
    label: "enforced in code",
    short: "Code",
    note: "Found in a branch that actually refuses the request. The agent cannot talk its way past this one, so a scenario probing it is testing the wiring rather than the instruction.",
  },
  config: {
    id: "config",
    label: "from config",
    short: "Config",
    note: "Declared in a manifest the agent loads at startup. Machine-written and machine-read, so it says what it means.",
  },
  prompt: {
    id: "prompt",
    label: "prompt only",
    short: "Prompt",
    note: "Stated in the system prompt and nowhere else. Nothing enforces it at runtime, which is precisely why it is worth grading rather than assuming.",
  },
  provider_system_prompt: {
    id: "provider_system_prompt",
    label: "from provider system prompt",
    short: "System prompt",
    note: "Read from the system prompt returned by the hosted agent provider.",
  },
  provider_tool_list: {
    id: "provider_tool_list",
    label: "from provider tool list",
    short: "Tool list",
    note: "Read from the tool definitions returned by the hosted agent provider.",
  },
  system_prompt: {
    id: "system_prompt",
    label: "from the system prompt",
    short: "System prompt",
    note: "Read from the agent's system prompt.",
  },
  tool_list: {
    id: "tool_list",
    label: "from the tool list",
    short: "Tool list",
    note: "Read from the agent's declared tool definitions.",
  },
  file: {
    id: "file",
    label: "read from a file",
    short: "File",
    note: "Read from a file in the agent's source.",
  },
  platform_builtin: {
    id: "platform_builtin",
    label: "built into the platform",
    short: "Built-in",
    note: "A tool the hosted agent platform provides itself, not one the agent defines.",
  },
  provider_field: {
    id: "provider_field",
    label: "from the provider's agent config",
    short: "Provider field",
    note: "Read from a field of the agent's config on the hosted agent provider.",
  },
  doc: {
    id: "doc",
    label: "prose only",
    short: "Prose",
    note: "Read out of a comment or a README. Anything in the repo can write prose (a vendored dependency, a stale note, someone who wanted a softer grader), so a rule found here is recorded and held back until you confirm it.",
  },
  callGraph: {
    id: "callGraph",
    label: "read from the call-graph",
    short: "Call graph",
    note: "The tool is invoked by name from the agent's own code. Not just declared, actually reached at runtime.",
  },
  policy: {
    id: "policy",
    label: "read from policy.yaml",
    short: "policy.yaml",
    note: "Declared in a policy manifest the agent loads at startup. Data, not judgement: the rule is what the file says.",
  },
  fixture: {
    id: "fixture",
    label: "seeded from a fixture",
    short: "Fixture",
    note: "The world starts with these rows every run. Change the fixture, change the seeded state.",
  },
  inferred: {
    id: "inferred",
    label: "inferred",
    short: "Inferred",
    note: "Nothing in the agent's code or policy stated this. The reader guessed based on prompt phrasing or call patterns. Confirm before you rely on it.",
  },
};

export const ORIGIN_ID = {
  CODE: "code",
  CONFIG: "config",
  PROMPT: "prompt",
  PROVIDER_SYSTEM_PROMPT: "provider_system_prompt",
  PROVIDER_TOOL_LIST: "provider_tool_list",
  SYSTEM_PROMPT: "system_prompt",
  TOOL_LIST: "tool_list",
  FILE: "file",
  PLATFORM_BUILTIN: "platform_builtin",
  PROVIDER_FIELD: "provider_field",
  DOC: "doc",
  CALL_GRAPH: "callGraph",
  POLICY: "policy",
  FIXTURE: "fixture",
  INFERRED: "inferred",
};
