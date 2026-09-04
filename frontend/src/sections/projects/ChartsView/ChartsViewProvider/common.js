export const convertToISO = (dateArray) => {
  return dateArray.map((date) => {
    const d = new Date(date);
    // d.setHours(0, 0, 0, 0); // Set time to 00:00:00.000
    return d.toISOString();
  });
};

export const normalizeTimestamp = (timestamp) => {
  if (!timestamp) return timestamp;
  if (typeof timestamp === "number") return timestamp;

  // Parse the timestamp with its offset intact and return epoch
  // milliseconds. Stripping the offset suffix used to leave a bare
  // local-time string, so every point was plotted shifted by the
  // viewer's distance from UTC. Offset-less strings still parse as
  // local time and pass through unchanged in value.
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return timestamp;
  return parsed.getTime();
};
