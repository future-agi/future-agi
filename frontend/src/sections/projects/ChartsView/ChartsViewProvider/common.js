export const convertToISO = (dateArray) => {
  return dateArray.map((date) => {
    const d = new Date(date);
    // d.setHours(0, 0, 0, 0); // Set time to 00:00:00.000
    return d.toISOString();
  });
};

export const normalizeTimestamp = (timestamp) => {
  if (timestamp === null || timestamp === undefined || timestamp === "") {
    return null;
  }

  const epochMs = new Date(timestamp).getTime();
  return Number.isFinite(epochMs) ? epochMs : null;
};
