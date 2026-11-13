export const convertToISO = (dateArray) => {
  return dateArray.map((date) => {
    const d = new Date(date);
    // d.setHours(0, 0, 0, 0); // Set time to 00:00:00.000
    return d.toISOString();
  });
};

export const normalizeTimestamp = (timestamp) => {
  if (!timestamp) return timestamp;

  if (typeof timestamp === "number") {
    return timestamp;
  }

  if (typeof timestamp !== "string") {
    const date = new Date(timestamp);
    return Number.isNaN(date.getTime()) ? timestamp : date.toISOString();
  }

  const trimmed = timestamp.trim();
  if (!trimmed) return timestamp;

  // Check if timestamp already has a timezone indicator (e.g. Z, +00:00, -05:00, +00)
  const hasTimezone = /(?:Z|[+-]\d{2}(?::?\d{2})?)$/i.test(trimmed);

  // Normalize space separator between date and time to 'T'
  let formatted = trimmed.replace(
    /^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)/,
    "$1T$2",
  );

  if (hasTimezone) {
    // If timezone is 2-digit [+-]HH without minutes, expand to [+-]HH:00 for standard ISO parsing
    formatted = formatted.replace(/([+-]\d{2})$/, "$1:00");
  } else {
    // If no timezone is specified, backend timestamps represent UTC
    formatted += "Z";
  }

  const parsedDate = new Date(formatted);
  return Number.isNaN(parsedDate.getTime()) ? timestamp : parsedDate.toISOString();
};
