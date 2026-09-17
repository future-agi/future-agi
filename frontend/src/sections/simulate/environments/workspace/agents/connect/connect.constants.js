import { BUILD_TONES } from "../../../buildEnvironment/buildTones";

// Field kinds a per-reach schema can declare. DynamicField switches on these to
// pick the right control; the schema is data, so a new reach is a data change.
export const FIELD = {
  TEXT: "text",
  URL: "url",
  SELECT: "select",
  SECRET: "secret",
};

// Selection accent for the reach picker cards. buildTones is the one build
// module allowed to hold hex, so the literal never leaks into a component.
export const SELECTION_ACCENT = BUILD_TONES.accent;

// How the new version is reached. A new version is the same agent pointed at a
// new place, so the pick shapes only the connection fields below — not the
// agent type, which the version inherits from the agent it belongs to.
export const REACH_KINDS = [
  {
    id: "endpoint",
    label: "Running endpoint",
    blurb: "Probe a deployed agent at its URL.",
    icon: "solar:server-linear",
  },
  {
    id: "repo",
    label: "Source repository",
    blurb: "Read the agent from a git repo at a ref.",
    icon: "solar:code-square-linear",
  },
  {
    id: "platform",
    label: "Hosted platform",
    blurb: "Reach a provider-hosted agent by credentials.",
    icon: "solar:widget-linear",
  },
  {
    id: "mcp",
    label: "MCP — your agent connects to us",
    blurb: "Point your own MCP client at the environment.",
    icon: "solar:plug-circle-linear",
  },
];

// Git ref kinds for the repo reach — stored as values.ref = { kind, value } so
// the A1 agent helpers (deriveTypeLine, connectionRowsFor) read it back.
export const REF_KINDS = [
  { id: "branch", label: "Branch", placeholder: "main" },
  { id: "tag", label: "Tag", placeholder: "v1.4.0" },
  { id: "commit", label: "Commit", placeholder: "9ca9be1" },
];

// Provider options for the hosted-platform reach.
export const PROVIDER_OPTIONS = [
  { value: "vapi", label: "Vapi" },
  { value: "retell", label: "Retell" },
  { value: "elevenlabs", label: "ElevenLabs" },
  { value: "openai", label: "OpenAI" },
  { value: "custom", label: "Custom" },
];

// The connection fields each reach collects, rendered through DynamicField. Keys
// match what the A1 helpers read off an agent's `values` (endpoint / repoUrl /
// provider / agentId / mcpUrl) so a minted version renders in the A2 cards. The
// repo reach's ref is handled by a dedicated kind+value control in the form.
export const REACH_FIELDS = {
  endpoint: [
    {
      key: "endpoint",
      label: "Running agent URL",
      type: FIELD.URL,
      required: true,
      placeholder: "https://api.yourapp.com/agent",
      help: "We'll probe the deployed agent and infer its shape from how it answers.",
    },
  ],
  repo: [
    {
      key: "repoUrl",
      label: "Repository URL",
      type: FIELD.URL,
      required: true,
      placeholder: "https://github.com/your-org/your-agent",
    },
  ],
  platform: [
    {
      key: "provider",
      label: "Provider",
      type: FIELD.SELECT,
      required: true,
      options: PROVIDER_OPTIONS,
    },
    {
      key: "apiKey",
      label: "API key",
      type: FIELD.SECRET,
      required: true,
      placeholder: "sk-…",
    },
    {
      key: "agentId",
      label: "Agent / assistant id",
      type: FIELD.TEXT,
      placeholder: "asst_…",
    },
  ],
};

export const ADD_VERSION_COPY = {
  reachHeading: "How do we reach this version?",
  connectionHeading: "Connection",
  refHeading: "Pin to",
  noteHeading: "Note",
  notePlaceholder: 'e.g. "GPT-4o rewrite, comparison against source"',
  noteHelp: "Optional. Shows next to this version in the history.",
  subtitle:
    "Point the new version at its endpoint or source. The previous version stays in the history — you can switch back any time.",
  cancel: "Cancel",
};
