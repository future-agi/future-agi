export const convertToISO = (dateArray) => {
  return dateArray.map((date) => {
    const d = new Date(date);
    // d.setHours(0, 0, 0, 0); // Set time to 00:00:00.000
    return d.toISOString();
  });
};

export const normalizeTimestamp = (timestamp) => {
  if (timestamp == null || timestamp === "") return timestamp;
  if (typeof timestamp === "number") return timestamp;
  if (typeof timestamp !== "string") return NaN;

  const value = timestamp.trim();
  // Naive server timestamps have historically represented UTC. Preserve that
  // contract while respecting an explicit Z or numeric timezone offset.
  const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  const isoValue = hasOffset ? value : `${value.replace(" ", "T")}Z`;
  const parsed = Date.parse(isoValue);
  return Number.isFinite(parsed) ? parsed : NaN;
};
