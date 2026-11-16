// The scenario editor's starting-records section: its own words, how a seeded value reads, and how a rework reads.

export const STARTING_RECORDS_COPY = {
  heading: "Starting records",
  hint: "The seeded rows this scenario's facts name. Every run starts from this snapshot of the world.",
  none: "This scenario names no seeded record, so it starts from the world as seeded.",
  loadError: "Couldn't load the starting records",
  loading: "Loading starting records",
  startFromAnother: "Start from another record",
  choose: "Record to start from",
  ask: "Ask the builder",
  cancel: "Cancel",
  noChoices: "The world seeds no other row of this table.",
  askFailed: "Couldn't send the request. Try again.",
  openChat: "Open the builder chat",
  showReply: "The builder's reply",
  openReply: "Open in the builder chat",
};

// A seeded value in one line: a list or object as its JSON.
export const seededValueText = (value) =>
  value !== null && typeof value === "object"
    ? JSON.stringify(value)
    : String(value);

// The latest rework request in one sentence; a refusal carries the server's reason.
export const reworkLine = (rework) => {
  const record = `${rework.table} ${rework.key_field} ${rework.key}`;
  if (rework.state === "pending")
    return `The builder is reworking this scenario to start from ${record}.`;
  if (rework.state === "waiting")
    return `The builder needs you to confirm its change to start from ${record}, in its chat.`;
  if (rework.state === "proven")
    return `Reworked to start from ${record} and proven. It's in your unsaved changes.`;
  return `Not reworked to start from ${record}: ${rework.reason}`;
};

// What a refused request says: its problems, else its message.
export const refusalText = (error, fallback) =>
  (error?.problems ?? []).map((one) => one.message).join(" ") ||
  error?.message ||
  fallback;
