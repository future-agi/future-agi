export const NOT_PERSONAL = "plain";

export const PERSONAL_CHOICES = [
  {
    id: "keep",
    label: "Keep",
    note: "Real values stay as they are in the world.",
  },
  {
    id: "replace",
    label: "Replace with synthetic values",
    note: "Stand-ins that keep the same person the same across files.",
  },
  {
    id: "none",
    label: "No personal data present",
    note: "Nothing in this file identifies a person.",
  },
  // Shown only once a column is set apart from the file's choice.
  {
    id: "custom",
    label: "Custom, per column",
    note: "Set column by column below.",
  },
];

export const COLUMN_MODES = [
  { id: NOT_PERSONAL, label: "Not personal" },
  { id: "retain", label: "Keep privately" },
  { id: "mask", label: "Mask" },
  { id: "replace", label: "Replace consistently" },
  { id: "omit", label: "Leave out" },
];

const modeOfChoice = (choice) => {
  if (choice === "keep") return "retain";
  return choice === "none" ? NOT_PERSONAL : "replace";
};

/**
 * A table file's columns with how each is handled: what the person set,
 * else the builder's guess. `value` is the sample row's value.
 */
export const columnsOf = (source) => {
  const parsed = source.ui?.parsed;
  if (parsed?.kind !== "table") return [];
  const privacy = source.ui.privacy || {};
  return parsed.columns.map((column) => ({
    name: column.name,
    sensitive: Boolean(column.sensitive),
    value: String(parsed.sample?.[column.name] ?? ""),
    mode:
      privacy[column.name] ||
      (column.sensitive
        ? modeOfChoice(source.ui.personal_choice || "replace")
        : NOT_PERSONAL),
  }));
};

/**
 * The file's choice fills every column the builder found or the person marked
 * personal; "none" clears them all. Custom leaves the columns as they are.
 */
export const applyFileChoice = (columns, choice) =>
  choice === "custom"
    ? columns
    : columns.map((column) =>
        column.sensitive || column.mode !== NOT_PERSONAL
          ? { ...column, mode: modeOfChoice(choice) }
          : column,
      );

/**
 * The file-level choice the columns agree with, else custom. None goes first:
 * keep and replace also match a file with nothing personal.
 */
export const choiceOf = (columns, fileChoice) =>
  ["none", fileChoice, "keep", "replace"].find(
    (choice) =>
      choice &&
      choice !== "custom" &&
      applyFileChoice(columns, choice).every(
        (column, i) => column.mode === columns[i].mode,
      ),
  ) || "custom";

const isIdValue = (value) => /^[A-Z]{1,4}-?\d+$/.test(value);

// The same ID gets the same stand-in in every file, so joins survive.
export const standInId = (value) =>
  `CUST-${String(1000 + (parseInt(String(value).replace(/\D/g, ""), 10) || 0)).padStart(4, "0")}`;

/** What a sample value looks like once handled by `mode`. */
export const standInOf = (value, mode) => {
  if (mode === "omit") return "—";
  if (mode === "mask") return value.replace(/[^@.\s-]/g, "•");
  if (mode !== "replace") return value;
  if (value.includes("@")) return "customer1@sandbox.test";
  if (/^[A-Z][a-z]+ [A-Z]/.test(value)) return "Customer 1";
  if (isIdValue(value)) return standInId(value);
  return "X-100";
};
