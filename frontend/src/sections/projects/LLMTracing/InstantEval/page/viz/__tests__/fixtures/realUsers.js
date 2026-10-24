import packed from "./realUsers.json";

// The summary the server returned for the real 11,502-user question on
// harbor-scale (A2's seeded month, read 5 sessions a user at most): each
// user counted once, under their rolled-up answer. Packed to what the tests
// read: the run's totals and counts, its questions and crossed counts, the
// day × hour, model, model × day, turns and error groups, and the users'
// consistency; each entry's counts are an array in a fixed label order per
// question. This unpacks it to the server's shape.

const unpack = (row) => {
  const [key, label, n, errors, failed, pending, ...byConfig] = row;
  const counts = {};
  packed.configs.forEach((configId, i) => {
    const values = byConfig[i];
    if (!values) return;
    counts[configId] = {};
    packed.labels[configId].forEach((answer, j) => {
      if (values[j]) counts[configId][answer] = values[j];
    });
  });
  const entry = { key, n, errors, failed, pending, counts };
  if (label !== null) entry.label = label;
  return entry;
};

const groups = Object.fromEntries(
  Object.entries(packed.groups).map(([dimension, rows]) => [
    dimension,
    rows.map(unpack),
  ]),
);
groups.user = [];
groups.user_by_answer = {};
groups.user_consistency = packed.user_consistency;

const summary = { ...packed.head, groups };

export default summary;
