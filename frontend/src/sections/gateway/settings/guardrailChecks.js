export const GUARDRAIL_CHECKS = [
  {
    name: "pii-detection",
    label: "PII Detection",
    icon: "solar:shield-user-linear",
  },
  {
    name: "content-moderation",
    label: "Content Moderation",
    icon: "solar:shield-check-linear",
  },
  {
    name: "prompt-injection",
    label: "Prompt Injection",
    icon: "solar:shield-warning-linear",
  },
  {
    name: "keyword-blocklist",
    label: "Keyword Blocklist",
    icon: "solar:shield-cross-linear",
    description: "Block prompts containing specific keywords or phrases.",
    fields: [
      {
        key: "words",
        label: "Blocked Keywords",
        type: "multiline-list",
        required: true,
        placeholder: "Enter one keyword or phrase per line",
        helperText:
          "Add one keyword or phrase per line. Commas are also supported.",
      },
    ],
  },
  {
    name: "secret-detection",
    label: "Secret Detection",
    icon: "solar:shield-keyhole-linear",
  },
  {
    name: "topic-restriction",
    label: "Topic Restriction",
    icon: "solar:shield-keyhole-linear",
  },
  {
    name: "language-detection",
    label: "Language Detection",
    icon: "solar:translation-linear",
  },
  {
    name: "system-prompt-protection",
    label: "System Prompt Protection",
    icon: "solar:shield-linear",
  },
  {
    name: "hallucination-detection",
    label: "Hallucination Detection",
    icon: "solar:shield-linear",
  },
  {
    name: "data-leakage-prevention",
    label: "Data Leakage Prevention",
    icon: "solar:shield-cross-linear",
  },
];
