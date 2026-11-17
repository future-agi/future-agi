// How a read-only block (RecordedOnly) reads a stored value.

/** Whether a stored value is worth a row (not empty, not an empty list or object). */
export const isStored = (value) =>
  value !== null &&
  value !== undefined &&
  value !== "" &&
  !(Array.isArray(value) && value.length === 0) &&
  !(
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).length === 0
  );

/** A stored secret as a screen may show it: never its value. */
export const secretState = (value) => (isStored(value) ? "Set" : "Not set");

/** On/off for a stored switch. */
export const onOff = (value) => (value ? "On" : "Off");

/** A stored number with thousands separators. */
export const count = (value) => Number(value).toLocaleString("en-US");

/** A stored list, comma-separated. */
export const list = (value) =>
  Array.isArray(value) ? value.join(", ") : String(value);
