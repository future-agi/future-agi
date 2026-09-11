// An eval mapping value is an attribute path. A non-string reaches neither a
// path walker nor a React child, so both questions are answered here only.
export const INVALID_MAPPING_LABEL = "invalid mapping";

export function isMappingPath(value) {
  return typeof value === "string" && value !== "";
}

// A cleared field reaches the payload as "" or null (whitespace-only counts as
// cleared). A non-string is deliberately NOT cleared: it is forwarded so the
// API rejects it with a message, rather than being dropped here and taking the
// variable down with it.
export function isClearedMappingValue(value) {
  return value == null || (typeof value === "string" && value.trim() === "");
}

export function mappingPathLabel(value) {
  if (isMappingPath(value)) return value;
  return value == null || value === "" ? "" : INVALID_MAPPING_LABEL;
}

// A cleared value has no label, so the chip is the key alone rather than a
// dangling separator. `separator` exists because the develop-detail chips use
// ": " while the eval chips use " -> " — same label rule, one owner.
export function mappingChipLabel(key, value, separator = " → ") {
  const label = mappingPathLabel(value);
  return label ? `${key}${separator}${label}` : key;
}
