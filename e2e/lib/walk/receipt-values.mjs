// Scan the serialized receipt, not the mutable input: toJSON and JSON escaping
// must not bypass the gate. Errors contain only a category, never a value/path.
const shapes = [
  ["email", /[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+/i],
  ["UUID", /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i],
  [
    "key",
    /\b(?:(?:sk|pk|rk|gsk)[-_][A-Za-z0-9_-]{4,}|(?:fagi|ghp|gho|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{8,}|fi_[A-Za-z0-9_-]{16,})\b/i,
  ],
  ["key", /\b(?:Bearer|Basic)\s+[A-Za-z0-9+/_.=-]+/i],
  ["key", /\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/],
  ["key", /-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----/],
  ["key", /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/],
  ["key", /\bAIza[A-Za-z0-9_-]{35}\b/],
  [
    "key",
    /\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)\s*[:=]\s*\S+/i,
  ],
];
const credentialField =
  /^(?:api[-_]?key|secret[-_]?key|access[-_]?token|refresh[-_]?token|client[-_]?secret|authorization|password|secret|credential)$/i;
const refuse = (kind) => {
  throw new Error(`walk receipt refused: ${kind}-shaped value`);
};

function scan(text, path) {
  for (const [kind, pattern] of shapes) if (pattern.test(text)) refuse(kind);
  // Git identities are public evidence, and only these exact schema locations
  // carry raw commit IDs. Config/image digests use an explicit sha256: marker.
  if (
    ["realHead", "productHead", "images.head", "overlay.from"].includes(path) &&
    /^[a-f0-9]{40}$/.test(text)
  )
    return;
  const withoutDigests = text.replace(/\bsha256:[a-f0-9]{64}\b/g, "digest");
  if (/[a-f0-9]{32,}/i.test(withoutDigests)) refuse("key");
  // Opaque base64/url-safe key material (not ordinary hyphenated prose/paths).
  if (
    /(?:^|[^A-Za-z0-9+/=_-])(?=[A-Za-z0-9+/=_-]{32,}(?:$|[^A-Za-z0-9+/=_-]))(?=[A-Za-z0-9+/=_-]*[A-Z])(?=[A-Za-z0-9+/=_-]*[a-z])(?=[A-Za-z0-9+/=_-]*[0-9])[A-Za-z0-9+/=_-]+/.test(
      withoutDigests,
    )
  )
    refuse("key");
}

/** Refuse identities/credentials anywhere in a JSON receipt before any IO. */
export function assertValueFreeReceipt(json) {
  let body;
  try {
    body = JSON.parse(json);
  } catch {
    throw new Error("walk receipt refused: invalid JSON");
  }
  function visit(value, path = "") {
    if (typeof value === "string") scan(value, path);
    else if (Array.isArray(value))
      value.forEach((item, i) => visit(item, `${path}[${i}]`));
    else if (value && typeof value === "object") {
      for (const [name, item] of Object.entries(value)) {
        scan(name, "");
        if (
          credentialField.test(name) &&
          item !== null &&
          item !== "" &&
          item !== false
        )
          refuse("key");
        visit(item, path ? `${path}.${name}` : name);
      }
    }
  }
  visit(body);
}
