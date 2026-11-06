const normalizeOutputType = (outputType) =>
  String(outputType || "")
    .replace(/[/ ]/g, "_")
    .toUpperCase();

const normalizeVerdict = (value) => {
  if (value === true) return "pass";
  if (value === false) return "fail";
  if (typeof value !== "string") return null;
  const normalized = value.trim().toLowerCase();
  return normalized === "pass" || normalized === "true"
    ? "pass"
    : normalized === "fail" || normalized === "false"
      ? "fail"
      : null;
};

const isMarker = (value) =>
  value &&
  typeof value === "object" &&
  !Array.isArray(value) &&
  (value.error === true || typeof value.status === "string");

const isCountObject = (value) =>
  value &&
  typeof value === "object" &&
  !Array.isArray(value) &&
  !isMarker(value) &&
  Object.values(value).every(
    (count) => typeof count === "number" && Number.isFinite(count),
  );

const normalizeChoiceTone = (choice) =>
  typeof choice === "string" ? choice : choice?.tone || "neutral";

export const buildEvalCellModel = (value, outputType, choicesMap = {}) => {
  if (isMarker(value)) return { kind: "marker", marker: value };

  if (isCountObject(value)) {
    const isPassFail = normalizeOutputType(outputType) === "PASS_FAIL";
    const chips = Object.entries(value)
      .filter(([, count]) => count !== 0)
      .map(([label, count], index) => ({
        key: `${label}-${index}`,
        count,
        label: isPassFail ? `${count} ${label}` : `${count} ${label}`,
        tone: isPassFail
          ? label === "pass"
            ? "success"
            : label === "fail"
              ? "error"
              : "neutral"
          : normalizeChoiceTone(choicesMap?.[label]),
      }));
    return { kind: "counts", chips };
  }

  const verdict = normalizeVerdict(value);
  if (verdict) return { kind: "verdict", verdict };

  if (typeof value === "number" && Number.isFinite(value)) {
    return {
      kind: "score",
      value,
      // This only controls color. Values are already scaled by the backend.
      tone: value >= 50 ? "pass" : "fail",
    };
  }

  return { kind: "scalar", value };
};

// Chip text is 13px; 7.5px per character errs wide for common labels.
const EVAL_COLUMN_CHAR_WIDTH = 7.5;
const MAX_EVAL_COLUMN_WIDTH = 280;

/** A starting width that fits an eval column's header and longest label. */
export const evalColumnMinWidth = (column, fallback) => {
  const labels = Array.isArray(column?.choices) ? column.choices : [];
  // A count chip reads "12 label"; the header adds two icons.
  const chipChars = Math.max(
    0,
    ...labels.map((label) => String(label).length + 3),
  );
  const headerChars = String(column?.name || "").length + 6;
  const width = Math.round(
    Math.max(chipChars, headerChars) * EVAL_COLUMN_CHAR_WIDTH + 40,
  );
  return Math.min(MAX_EVAL_COLUMN_WIDTH, Math.max(fallback, width));
};

export { normalizeVerdict };
