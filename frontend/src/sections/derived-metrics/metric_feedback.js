const fieldHelp = (attr, run) => {
  if (/^(definition\.)?formula(?:\.|$)/.test(attr))
    return ["Formula", "Open Calculation and correct the custom formula."];
  if (/^(definition\.)?filters(?:\.|$)/.test(attr))
    return run
      ? [
          "Run filters",
          "Open Run filters and source and correct the field or value.",
        ]
      : [
          "Definition filters",
          "Open Advanced configuration and correct the definition filters.",
        ];
  if (/^(definition\.)?inputs(?:\.|$)/.test(attr))
    return ["Data inputs", "Check the source and evaluator binding in Data."];
  if (/^window_(start|end)$/.test(attr))
    return ["Run window", "Correct the start and end boundaries."];
  return [
    attr ? attr.replaceAll("_", " ") : "Metric definition",
    "Check the highlighted definition fields before retrying.",
  ];
};

// Both Axios-normalized bodies and ordinary response errors use the same field
// paths. Keep nested validation messages associated with their input, not JSON.
function validationItems(value, attr = "") {
  if (typeof value === "string") return [{ attr, message: value }];
  if (Array.isArray(value))
    return value.flatMap((item) => validationItems(item, attr));
  if (!value || typeof value !== "object") return [];
  const path = value.attr || attr;
  const message = value.detail ?? value.message ?? value.details;
  if (message !== undefined) return validationItems(message, path);
  return Object.entries(value).flatMap(([key, detail]) =>
    ["attr", "type", "code", "statusCode"].includes(key)
      ? []
      : validationItems(detail, path ? `${path}.${key}` : key),
  );
}

export function metricFailureText(failure, run = false) {
  const body = failure?.response?.data || failure || {};
  const status = failure?.statusCode || failure?.response?.status;
  if (status === 409)
    return "This definition changed. Reload the saved definition before editing again; your draft has not been overwritten.";
  if (body.type === "validation_error" || status === 400 || status === 422) {
    const details =
      body.details ??
      body.errors ??
      body.detail ??
      body.message ??
      "Invalid value.";
    // Root field dictionaries already identify each field. The envelope attr
    // identifies only its first error and is a fallback for unkeyed messages.
    const keyedDetails =
      details &&
      typeof details === "object" &&
      !Array.isArray(details) &&
      !["detail", "message", "details"].some((key) => key in details);
    const items = validationItems(details, keyedDetails ? "" : body.attr || "");
    return (
      items
        .map(({ attr, message }) => {
          const [label, help] = fieldHelp(attr, run);
          return `${label}: ${message} ${help}`;
        })
        .join(" ") || "Check the metric definition fields before retrying."
    );
  }
  return (
    body.message ||
    (typeof body.detail === "string" ? body.detail : "") ||
    "The action failed. Check the saved state before retrying."
  );
}
