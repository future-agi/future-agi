export function getPickerOptionValue(option) {
  if (typeof option === "string") return option;
  return option?.value ?? option?.label ?? "";
}

export function getPickerOptionLabel(option) {
  if (typeof option === "string") return option;
  return option?.label ?? option?.value ?? "";
}

export function getPickerOptionSecondaryLabel(option) {
  if (typeof option === "string") return "";
  const label = getPickerOptionLabel(option);
  const email = option?.email || option?.description || "";
  return email && email !== label ? email : "";
}

export function getPickerOptionSearchText(option) {
  if (typeof option === "string") return option;
  return [
    option?.label,
    option?.name,
    option?.email,
    option?.description,
    option?.value,
  ]
    .filter(Boolean)
    .join(" ");
}

export function getPickerOptionExactMatches(option) {
  if (typeof option === "string") return [option];
  return [
    option?.value,
    option?.label,
    option?.name,
    option?.email,
    option?.description,
  ]
    .filter(Boolean)
    .map(String);
}
