// API response objects can contain both a snake_case key and a camelCase alias
// for the same value. Those aliases are plain enumerable own-properties, so
// `Object.keys(obj)` returns both keys and dynamic UI lists can render
// duplicate fields.
//
// These helpers only de-dupe an object that already has both keys. They do
// not add aliases or mutate response payloads. Filtering is key-shape-only;
// it does not verify value equality or generated-alias provenance, so do not
// use it for user-owned metadata where real camelCase siblings must remain.
const SNAKE_TO_CAMEL_ALIAS_RE = /_([a-z0-9])/g;

// Forward-mapping is robust to digit separators
// (e.g. `tone_17_apr_2026` -> `tone17Apr2026`), which a reverse regex on
// camelCase cannot recover.
const buildAliasSet = (obj) => {
  const aliases = new Set();
  const keys = Object.keys(obj);
  for (let i = 0; i < keys.length; i += 1) {
    const k = keys[i];
    if (!k.includes("_")) continue;
    const alias = k.replace(SNAKE_TO_CAMEL_ALIAS_RE, (_, c) => c.toUpperCase());
    if (alias !== k) aliases.add(alias);
  }
  return aliases;
};

export const canonicalKeys = (obj) => {
  if (!obj || typeof obj !== "object") return [];
  const aliases = buildAliasSet(obj);
  return Object.keys(obj).filter((key) => !aliases.has(key));
};

export const canonicalEntries = (obj) => {
  if (!obj || typeof obj !== "object") return [];
  const aliases = buildAliasSet(obj);
  return Object.entries(obj).filter(([key]) => !aliases.has(key));
};

export const canonicalValues = (obj) => {
  if (!obj || typeof obj !== "object") return [];
  return canonicalKeys(obj).map((key) => obj[key]);
};
