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

// These are the concrete Go leaf catalogs, not the broader reference API IDs.
// Keep aligned with internal/guardrails/{pii,contentmod,secrets} constructors.
const SELECTORS = {
  "pii-detection": {
    field: "entities",
    values: [
      "email",
      "phone",
      "ssn",
      "credit_card",
      "ipv4",
      "ipv6",
      "dob",
      "passport",
      "drivers_license",
      "iban",
      "zip_code",
      "aws_key",
      "api_key",
      "url_credentials",
      "mac_address",
      "ein",
      "mrn",
      "bitcoin",
    ],
  },
  "content-moderation": {
    field: "categories",
    values: ["toxicity", "hate_speech", "violence", "sexual", "self_harm"],
  },
  "secret-detection": {
    field: "types",
    values: [
      "aws_access_key",
      "aws_secret_key",
      "github_token",
      "gitlab_token",
      "slack_token",
      "stripe_key",
      "openai_key",
      "private_key",
      "connection_string",
      "jwt",
      "password_assign",
    ],
  },
};

const BUILTIN_CONFIG_HELP = {
  ...Object.fromEntries(
    Object.entries(SELECTORS).map(([name, { field, values }]) => [
      name,
      `Use a ${field} array with these exact names: ${values.join(", ")}. Omitted or empty selectors use the default detectors.`,
    ]),
  ),
  "language-detection":
    'Use an allowed_scripts array of strings, for example {"allowed_scripts":["latin"]}. Script names are case-insensitive. Empty settings impose no restriction.',
  "topic-restriction":
    'Use allowed_topics or forbidden_topics arrays of objects, each with an optional string name and a nonempty keywords array of strings. Example: {"forbidden_topics":[{"name":"restricted","keywords":["restricted"]}]}. Empty settings impose no restriction.',
  "input-validation":
    "Use deny_patterns and require_patterns arrays of Go regex strings. Length limits (min_message_length, max_message_length, max_total_length) are optional nonnegative integers; zero disables a limit. Pattern checks here are partial: common syntax errors, lookarounds and backreferences are rejected. Character ranges, repetition bounds and Unicode class names are not checked here. The Go runtime validates remaining syntax; saving does not prove a pattern is valid.",
};

export const hasBuiltinConfigEditor = (name) =>
  Object.hasOwn(BUILTIN_CONFIG_HELP, name);
export const builtinConfigHelp = (name) => BUILTIN_CONFIG_HELP[name];
export const isConfigObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const isText = (value) =>
  typeof value === "string" && !/[\uD800-\uDFFF]/u.test(value);
const isStringArray = (value) => Array.isArray(value) && value.every(isText);

// Deliberately bounded syntax checks, NOT JS RegExp compilation or an RE2
// parser. Track classes, escaping and Go quoting so valid Go-only syntax is
// preserved. Ranges, repetition bounds and Unicode class names are left to Go.
const boundedGoPatternError = (pattern) => {
  let depth = 0;
  let inClass = false;
  let classFirst = false;
  for (let i = 0; i < pattern.length; i += 1) {
    const char = pattern[i];
    if (char === "\\") {
      if (i + 1 === pattern.length) return "Pattern has a trailing escape.";
      const escaped = pattern[i + 1];
      if (escaped === "Q" && !inClass) {
        const end = pattern.indexOf("\\E", i + 2);
        if (end < 0) break; // Go permits quoting through the end of the pattern.
        i = end + 1;
        continue;
      }
      if (
        (/[1-9]/.test(escaped) &&
          (/[89]/.test(escaped) || !/[0-7]/.test(pattern[i + 2] || ""))) ||
        (/[kg]/.test(escaped) && /[<{']/.test(pattern[i + 2] || ""))
      ) {
        return "Pattern uses a backreference unsupported by Go.";
      }
      i += 1;
      if (inClass) classFirst = false;
      continue;
    }
    if (inClass) {
      if (pattern.startsWith("[:", i)) {
        const end = pattern.indexOf(":]", i + 2);
        if (end >= 0) {
          i = end + 1;
          classFirst = false;
          continue;
        }
      }
      if (char === "]" && !classFirst) inClass = false;
      classFirst = false;
    } else if (char === "[") {
      inClass = true;
      classFirst = true;
      if (pattern[i + 1] === "^") i += 1;
    } else if (char === "(") {
      if (
        ["(?=", "(?!", "(?<=", "(?<!", "(?P="].some((prefix) =>
          pattern.startsWith(prefix, i),
        )
      ) {
        return "Pattern uses lookaround or a backreference unsupported by Go.";
      }
      depth += 1;
    } else if (char === ")") {
      if (depth === 0) return "Pattern has an unmatched closing group.";
      depth -= 1;
    }
  }
  if (inClass) return "Pattern has an unterminated character class.";
  if (depth) return "Pattern has an unterminated group.";
  return null;
};

// Opt-in only: selected key policies validate effective enabled config. Custom
// names and the shared org editor keep their own contracts. No coercion, mutation
// or raw parameter values in errors. Missing config intentionally uses defaults;
// explicit null/scalars/arrays do not.
export const selectedConfigErrors = (name, config = {}) => {
  if (name === "keyword-blocklist") return keywordConfigErrors(config);
  if (!hasBuiltinConfigEditor(name)) return {};
  if (!isConfigObject(config))
    return { config: "Config must be a JSON object." };
  const errors = {};
  if (Object.hasOwn(SELECTORS, name)) {
    const { field, values } = SELECTORS[name];
    if (
      field in config &&
      (!isStringArray(config[field]) ||
        config[field].some((item) => !values.includes(item)))
    ) {
      errors[field] = `${field} must be an array of supported detector names.`;
    }
  } else if (name === "language-detection") {
    if ("allowed_scripts" in config && !isStringArray(config.allowed_scripts)) {
      errors.allowed_scripts = "allowed_scripts must be an array of strings.";
    }
  } else if (name === "topic-restriction") {
    ["allowed_topics", "forbidden_topics"].forEach((field) => {
      if (!(field in config)) return;
      const topics = config[field];
      if (!Array.isArray(topics)) {
        errors[field] = `${field} must be an array of topic objects.`;
      } else if (topics.some((topic) => !isConfigObject(topic))) {
        errors[field] = `${field} must contain only topic objects.`;
      } else if (
        topics.some((topic) => "name" in topic && !isText(topic.name))
      ) {
        errors[field] = `${field} topic name must be a string.`;
      } else if (
        topics.some(
          (topic) =>
            !isStringArray(topic.keywords) || topic.keywords.length === 0,
        )
      ) {
        errors[field] =
          `${field} keywords must be a nonempty array of strings.`;
      }
    });
  } else if (name === "input-validation") {
    ["deny_patterns", "require_patterns"].forEach((field) => {
      if (!(field in config)) return;
      if (!isStringArray(config[field])) {
        errors[field] = `${field} must be an array of strings.`;
        return;
      }
      const error = config[field].map(boundedGoPatternError).find(Boolean);
      if (error) errors[field] = `${field}: ${error}`;
    });
    ["min_message_length", "max_message_length", "max_total_length"].forEach(
      (field) => {
        if (
          field in config &&
          (!Number.isInteger(config[field]) ||
            config[field] < 0 ||
            config[field] >= 2 ** 63)
        ) {
          errors[field] = `${field} must be a nonnegative integer below 2^63.`;
        }
      },
    );
    ["max_message_length", "max_total_length"].forEach((field) => {
      if (
        !errors.min_message_length &&
        !errors[field] &&
        config[field] > 0 &&
        config.min_message_length > config[field]
      ) {
        errors[field] = `min_message_length must not exceed enabled ${field}.`;
      }
    });
  }
  return errors;
};

export const hasNoRestriction = (name, config = {}) => {
  if (Object.keys(selectedConfigErrors(name, config)).length) return false;
  if (name === "language-detection") return !config.allowed_scripts?.length;
  if (name === "topic-restriction")
    return !config.allowed_topics?.length && !config.forbidden_topics?.length;
  return false;
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
    name: "input-validation",
    label: "Input Validation",
    icon: "solar:shield-check-linear",
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
