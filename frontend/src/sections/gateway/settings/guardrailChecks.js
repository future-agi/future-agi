// Opt-in validation for effective selected-key policies. The shared org editor
// keeps its existing contract; do not normalize strings used by the matcher.
export const keywordConfigErrors = (config) => {
  const errors = {};
  if (!config || typeof config !== "object" || Array.isArray(config)) {
    return { words: "Enter words as a nonempty array of strings." };
  }
  if (
    !Array.isArray(config.words) ||
    config.words.length === 0 ||
    config.words.some((word) => typeof word !== "string" || !word.trim())
  ) {
    errors.words =
      "Enter words as a nonempty array of strings, with no blank entries.";
  }
  if ("whole_word" in config && typeof config.whole_word !== "boolean") {
    errors.whole_word = "Whole word must be a boolean.";
  }
  if (
    "max_matches" in config &&
    (!Number.isInteger(config.max_matches) ||
      config.max_matches <= 0 ||
      config.max_matches >= 2 ** 63)
  ) {
    errors.max_matches = "Max matches must be a positive integer below 2^63.";
  }
  return errors;
};

// A line editor cannot losslessly represent malformed types or embedded
// newlines. Show the original JSON for explicit correction instead of coercing
// mixed entries to strings or silently dropping them.
export const keywordConfigNeedsJson = (config) => {
  if (config === undefined) return false;
  if (!config || typeof config !== "object" || Array.isArray(config))
    return true;
  const errors = keywordConfigErrors(config);
  return Boolean(
    errors.whole_word ||
      errors.max_matches ||
      ("words" in config &&
        (!Array.isArray(config.words) ||
          config.words.some(
            (word) => typeof word !== "string" || /[\r\n]/.test(word),
          ))),
  );
};

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
