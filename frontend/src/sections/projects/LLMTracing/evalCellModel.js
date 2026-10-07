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
          : choicesMap?.[label] || "neutral",
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

export { normalizeVerdict };
