// The list a check needs before it does anything. Keyword Blocklist without
// words, Topic Restriction without a topic and Script Detection without an
// allowed script pass every request (internal/guardrails blocklist, topic and
// language), so the console saves such a check, and switches it on, only with
// its list. Each reason says what to add.

const LIST_TYPES = new Set(["multiline-list", "multiselect", "topic-list"]);

const isEmpty = (v) =>
  v === undefined ||
  v === null ||
  (Array.isArray(v) ? v.length === 0 : String(v).trim() === "");

// meta is a catalog entry (GuardrailConfigTab); read(key) is the field's value
// as the check stores it. null when nothing is missing.
export function missingListReason(meta, read) {
  if (!meta?.fields) return null;
  const oneOf = meta.requiresOneOf;
  if (oneOf && oneOf.keys.every((key) => isEmpty(read(key)))) {
    return oneOf.reason;
  }
  const missing = meta.fields.find(
    (f) => f.required && LIST_TYPES.has(f.type) && isEmpty(read(f.key)),
  );
  return missing ? missing.requiredReason || `Add ${missing.label}.` : null;
}

// read(key) for a check as stored: a field lives at the top level or in its
// config.
export const storedReader = (check) => (key) =>
  check?.[key] ?? check?.config?.[key];

// Topic Restriction's topics (topic.New: a list of {name, keywords}) as the
// form shows them, one per line: "name: keyword, keyword". A line without a
// colon is a topic whose one keyword is its name.
export function topicsToText(topics) {
  if (!Array.isArray(topics)) return typeof topics === "string" ? topics : "";
  return topics
    .filter((t) => t && typeof t === "object")
    .map((t) => {
      const keywords = Array.isArray(t.keywords) ? t.keywords : [];
      return `${t.name ?? ""}: ${keywords.join(", ")}`;
    })
    .join("\n");
}

export function textToTopics(text) {
  if (Array.isArray(text)) return text;
  if (typeof text !== "string") return [];
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const colon = line.indexOf(":");
      const name = (colon === -1 ? line : line.slice(0, colon)).trim();
      const keywords =
        colon === -1
          ? [name]
          : line
              .slice(colon + 1)
              .split(",")
              .map((k) => k.trim())
              .filter(Boolean);
      return { name, keywords: keywords.length > 0 ? keywords : [name] };
    })
    .filter((t) => t.name);
}
