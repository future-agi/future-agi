export const SQL_EDITOR_LINE_HEIGHT = 23;
export const SQL_EDITOR_PADDING = 12;

// Derive height from text, not measured content height: wrapping/layout events
// cannot feed back into React sizing. Monaco scrolls beyond the upper bound.
export const getEditorHeight = (lineCount) =>
  Math.min(
    346,
    Math.max(116, lineCount * SQL_EDITOR_LINE_HEIGHT + SQL_EDITOR_PADDING * 2),
  );

export const isPreviousQuery = (submitted, current) =>
  Boolean(
    submitted &&
      (submitted.sql !== current.sql ||
        submitted.projectId !== current.projectId ||
        submitted.hours !== current.hours),
  );

// Presentation only: execution, admission and cancellation stay in the hook.
export const getQueryPresentation = ({ state, busy, waitingForAdmission }) => {
  if (state === "cancelled")
    return {
      title: "Query cancelled",
      description: "Run the query again when ready.",
    };
  if (waitingForAdmission)
    return {
      title: "Waiting for a query slot",
      description: "Results will appear when the query finishes.",
    };
  if (busy)
    return {
      title: `Query ${state || "submitting"}`,
      description: "Results will appear when the query finishes.",
    };
  return null;
};

export const describeQueryWindow = (hours) =>
  hours === 1
    ? "last 1 hour"
    : `last ${hours / 24} ${hours === 24 ? "day" : "days"}`;
