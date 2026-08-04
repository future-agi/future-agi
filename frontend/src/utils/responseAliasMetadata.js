// Generated-alias provenance is live-object metadata only. The Axios bridge
// attaches it only for user-owned metadata subtrees that need display-time
// filtering. It is intentionally non-enumerable and does not survive JSON
// serialization, structured clones, or object spread; render/cache boundaries
// should normalize before copying.
const GENERATED_CAMEL_ALIAS_KEYS = Symbol("generatedCamelAliasKeys");

export function markGeneratedCamelAlias(obj, aliasKey) {
  if (!obj || typeof obj !== "object") return;

  try {
    let aliases = obj[GENERATED_CAMEL_ALIAS_KEYS];
    if (!aliases) {
      aliases = new Set();
      Object.defineProperty(obj, GENERATED_CAMEL_ALIAS_KEYS, {
        value: aliases,
        enumerable: false,
        configurable: false,
        writable: false,
      });
    }
    aliases.add(aliasKey);
  } catch {
    // Ignore read-only / frozen objects; the alias itself is best-effort too.
  }
}

export function isGeneratedCamelAlias(obj, key) {
  if (!obj || typeof obj !== "object") return false;
  return Boolean(obj[GENERATED_CAMEL_ALIAS_KEYS]?.has(key));
}
