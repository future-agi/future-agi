export const convertToISO = (dateArray) => {
  return dateArray.map((date) => {
    const d = new Date(date);
    // d.setHours(0, 0, 0, 0); // Set time to 00:00:00.000
    return d.toISOString();
  });
};

export const normalizeTimestamp = (timestamp) => {
  if (!timestamp) return timestamp;

  // Keep the instant intact. The API marks UTC with an explicit offset
  // (+00:00, +05:30, Z); a bare timestamp is UTC as well. Stripping that
  // marker made the browser read the point as local time, so every chart
  // shifted by the viewer's offset from UTC.
  if (typeof timestamp !== "string") return timestamp;
  return /([+-]\d{2}:\d{2}|Z)$/.test(timestamp) ? timestamp : `${timestamp}Z`;
};
