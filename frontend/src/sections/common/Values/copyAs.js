// Copy-as (R2-SPEC P6 §3.2): one value → text in a chosen format.
// Frozen (PRIMITIVES.md "Values"): copyAs(value, format, {path}).
// Pure; never throws.
import { stringify as yamlStringify } from "yaml";

export const COPY_FORMATS = ["value", "json", "yaml", "path", "curl"];

/** Toast text for a format: `Copied as YAML`. */
export const COPY_TOAST = {
  value: "Value copied",
  json: "Copied as JSON",
  yaml: "Copied as YAML",
  path: "Path copied",
  curl: "Copied as cURL-safe string",
};

const parseMaybe = (v) => {
  if (typeof v !== "string") return v;
  const t = v.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return v;
  try {
    return JSON.parse(t);
  } catch {
    return v;
  }
};

const valueText = (v) => {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
};

/** Single-quoted shell string: `it's` → `'it'\''s'`. */
export const shellQuote = (s) => `'${String(s).replace(/'/g, `'\\''`)}'`;

/**
 * @param {*} value  as stored (JSON text is parsed for JSON/YAML)
 * @param {"value"|"json"|"yaml"|"path"|"curl"} format
 * @param {{path?: string}} [opts]
 * @returns {string}
 */
export function copyAs(value, format, { path } = {}) {
  switch (format) {
    case "json": {
      const v = parseMaybe(value);
      try {
        return JSON.stringify(v === undefined ? null : v, null, 2);
      } catch {
        return valueText(value);
      }
    }
    case "yaml": {
      const v = parseMaybe(value);
      if (typeof v === "string") return v;
      try {
        return yamlStringify(v === undefined ? null : v).replace(/\n$/, "");
      } catch {
        return valueText(value);
      }
    }
    case "path":
      return path ?? "";
    case "curl": {
      const v = parseMaybe(value);
      const text =
        typeof v === "string"
          ? v
          : (() => {
              try {
                return JSON.stringify(v);
              } catch {
                return String(v);
              }
            })();
      return shellQuote(text ?? "");
    }
    default:
      return valueText(value);
  }
}

/** YAML text of a value (the YAML view mode); strings stay as they are. */
export function toYaml(value) {
  return copyAs(value, "yaml");
}
