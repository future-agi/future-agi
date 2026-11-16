// The scenario editor's starting-records section: its own words, and how a seeded value reads.

export const STARTING_RECORDS_COPY = {
  heading: "Starting records",
  hint: "The seeded rows this scenario's facts name. Every run starts from this snapshot of the world.",
  none: "This scenario names no seeded record, so it starts from the world as seeded.",
  loadError: "Couldn't load the starting records",
  loading: "Loading starting records",
};

// A seeded value in one line: a list or object as its JSON.
export const seededValueText = (value) =>
  value !== null && typeof value === "object"
    ? JSON.stringify(value)
    : String(value);
