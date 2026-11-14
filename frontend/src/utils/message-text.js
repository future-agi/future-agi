/** Serialize telemetry without executing its coercion hooks or accessors. */
export function serializeMessageValue(value, space) {
  const ancestors = new WeakSet();
  function visit(item, depth) {
    if (item === null || typeof item === "string" || typeof item === "boolean")
      return item;
    if (typeof item === "number")
      return Number.isFinite(item) ? item : String(item);
    if (typeof item === "bigint" || typeof item === "symbol")
      return String(item);
    if (typeof item === "undefined") return "[undefined]";
    if (typeof item === "function") return "[Function]";
    if (ancestors.has(item)) return "[Circular]";
    if (depth >= 64) return "[Max depth]";
    ancestors.add(item);
    try {
      const result = Array.isArray(item) ? [] : Object.create(null);
      // Copy data properties only: JSON.stringify's replacer runs AFTER toJSON,
      // so a replacer alone cannot safely serialize untrusted telemetry.
      for (const key of Object.keys(item)) {
        const descriptor = Object.getOwnPropertyDescriptor(item, key);
        const text =
          descriptor && Object.hasOwn(descriptor, "value")
            ? visit(descriptor.value, depth + 1)
            : "[Accessor]";
        Object.defineProperty(result, key, {
          value: text,
          enumerable: true,
          configurable: true,
          writable: true,
        });
      }
      return result;
    } catch {
      return "[Unserializable]";
    } finally {
      ancestors.delete(item);
    }
  }
  return JSON.stringify(visit(value, 0), null, space);
}

/** Only scalar text is display text; preserve malformed blocks as JSON. */
export function messageBlockText(block) {
  if (typeof block === "string" || typeof block === "number")
    return String(block);
  if (block && typeof block === "object") {
    const descriptor = Object.getOwnPropertyDescriptor(block, "text");
    const text = descriptor?.value;
    if (typeof text === "string" || typeof text === "number")
      return String(text);
  }
  return serializeMessageValue(block);
}
