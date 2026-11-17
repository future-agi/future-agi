// The checks built into the gateway (agentcc-gateway cmd/agentcc/main.go
// registry). Each description says what the Go check actually looks at.
// A check whose `fields` include a required one does nothing until it is set
// (an empty blocklist, no allowed scripts, no topics), so the console asks
// for those settings before it turns the check on.

const SCRIPTS = [
  "latin",
  "cyrillic",
  "arabic",
  "han",
  "japanese",
  "korean",
  "devanagari",
  "greek",
  "hebrew",
  "thai",
];

export const BUILT_IN_CHECKS = [
  {
    name: "pii-detection",
    label: "PII Detection",
    description:
      "Finds emails, phone numbers, SSNs, card numbers, IBANs and other personal data.",
  },
  {
    name: "secret-detection",
    label: "Secret Detection",
    description:
      "Finds API keys, tokens, private keys and passwords pasted into a prompt.",
  },
  {
    name: "prompt-injection",
    label: "Prompt Injection",
    description:
      "Detects instruction overrides, jailbreaks and system-prompt extraction.",
  },
  {
    name: "content-moderation",
    label: "Content Moderation",
    description:
      "Scores the prompt for hate, violence, sexual, self-harm and other harmful content.",
  },
  {
    name: "keyword-blocklist",
    label: "Keyword Blocklist",
    description: "Matches prompts that contain words or phrases you list.",
    fields: [
      {
        key: "words",
        label: "Blocked keywords",
        type: "multiline-list",
        required: true,
        placeholder: "Enter one keyword or phrase per line",
        helperText:
          "Add one keyword or phrase per line. Commas are also supported.",
      },
    ],
  },
  {
    name: "topic-restriction",
    label: "Topic Restriction",
    description:
      "Matches forbidden topics, or anything outside the topics you allow, by keyword.",
    requiresOneOf: ["forbidden_topics", "allowed_topics"],
    // Said beside a refused Save while both topic lists are empty.
    requiresOneOfReason: "Add at least one forbidden or allowed topic.",
    fields: [
      {
        key: "forbidden_topics",
        label: "Forbidden topics",
        type: "topic-list",
        placeholder: "Legal: lawsuit, attorney, sue",
        helperText:
          "One topic per line: a name, a colon, then its keywords. A prompt with any keyword matches.",
      },
      {
        key: "allowed_topics",
        label: "Allowed topics",
        type: "topic-list",
        placeholder: "Billing: invoice, refund, charge",
        helperText:
          "If set, a prompt must mention at least one allowed topic or it matches.",
      },
    ],
  },
  {
    // The check judges Unicode scripts, not languages; the id is unchanged.
    name: "language-detection",
    label: "Script Detection",
    description:
      "Matches text written in a script you do not allow, and Latin look-alike characters.",
    fields: [
      {
        key: "allowed_scripts",
        label: "Allowed scripts",
        type: "multiselect",
        required: true,
        options: SCRIPTS,
        defaultValue: ["latin"],
        // Said beside a refused Save while no script is chosen.
        emptyReason: "Choose at least one allowed script.",
      },
    ],
  },
  {
    name: "system-prompt-protection",
    label: "System Prompt Protection",
    description: "Detects attempts to read out or replace the system prompt.",
  },
  {
    name: "hallucination-detection",
    label: "Hallucination Detection",
    description:
      "Heuristic checks on the model's answer for claims that look made up.",
    stage: "response",
  },
  {
    name: "data-leakage-prevention",
    label: "Data Leakage Prevention",
    description:
      "Finds leaked data and code-injection patterns in the model's answer.",
    stage: "response",
  },
];
