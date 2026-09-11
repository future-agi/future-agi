export const OBSERVE_FILTER_SOURCES = ["traces", "sessions", "users"];

export function usesFreeTextValue(fieldType, source) {
  if (fieldType === "text") return true;
  return fieldType === "string" && !OBSERVE_FILTER_SOURCES.includes(source);
}

export function getPickerOptionValue(option) {
  let value;
  if (
    typeof option === "string" ||
    typeof option === "number" ||
    typeof option === "boolean"
  ) {
    value = option;
  } else {
    value = option?.value ?? option?.label ?? "";
  }
  // Applied picker values are normalized by `normalizePickerValues`. Do the
  // same at the option boundary so retained/catalog values such as `"True "`
  // compare equal to the applied `"True"` value and render as selected.
  return typeof value === "string" ? value.trim() : value;
}

export function getPickerOptionType(option) {
  if (!option || typeof option !== "object") return undefined;
  return ["string", "number", "boolean", "array", "map", "json"].includes(
    option.type,
  )
    ? option.type
    : undefined;
}

export function getPickerValueIdentity(value, storageType) {
  return `${storageType || ""}:${typeof value}:${JSON.stringify(value)}`;
}

export function getPickerOptionLabel(option) {
  if (
    typeof option === "string" ||
    typeof option === "number" ||
    typeof option === "boolean"
  ) {
    return String(option);
  }
  return option?.label ?? option?.value ?? "";
}

export function getPickerOptionSecondaryLabel(option) {
  if (typeof option === "string") return "";
  const label = getPickerOptionLabel(option);
  const email = option?.email || option?.description || "";
  return email && email !== label ? email : "";
}

export function getPickerOptionSearchText(option) {
  if (
    typeof option === "string" ||
    typeof option === "number" ||
    typeof option === "boolean"
  ) {
    return String(option);
  }
  return [
    option?.label,
    option?.name,
    option?.email,
    option?.description,
    option?.value,
  ]
    .filter((value) => value !== undefined && value !== null && value !== "")
    .map(String)
    .join(" ");
}

export function getPickerOptionExactMatches(option) {
  if (
    typeof option === "string" ||
    typeof option === "number" ||
    typeof option === "boolean"
  ) {
    return [String(option)];
  }
  return [
    option?.value,
    option?.label,
    option?.name,
    option?.email,
    option?.description,
  ]
    .filter((value) => value !== undefined && value !== null && value !== "")
    .map(String);
}

export function normalizePickerValues(values) {
  const rawValues = Array.isArray(values)
    ? values
    : values !== undefined && values !== null && values !== ""
      ? [values]
      : [];
  const cleanValues = rawValues
    .map((item) => getPickerOptionValue(item))
    .map((item) => (typeof item === "string" ? item.trim() : item))
    .filter(
      (item) =>
        (typeof item === "string" && item.length > 0) ||
        typeof item === "boolean" ||
        (typeof item === "number" && Number.isFinite(item)),
    );
  const byIdentity = new Map();
  for (const item of cleanValues) {
    byIdentity.set(`${typeof item}:${JSON.stringify(item)}`, item);
  }
  return Array.from(byIdentity.values());
}
