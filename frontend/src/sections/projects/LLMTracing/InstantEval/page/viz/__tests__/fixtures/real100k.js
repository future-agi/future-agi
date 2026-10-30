import packed from "./real100k.json";

// The summary the server returned for the real 100,000-trace run on
// harbor-scale (A's seeded month), packed to what the tests read: the run's
// totals and counts, its questions and crossed counts, and the day × hour,
// model, model × day, error, user and user-by-answer groups. Each entry's
// counts are an array in a fixed label order per question (exact, nothing
// rounded), and users picked for several answers are listed once. This
// unpacks it to the server's shape.

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

const users = packed.users.map(unpack);
const groups = Object.fromEntries(
  Object.entries(packed.groups).map(([dimension, rows]) => [
    dimension,
    rows.map(unpack),
  ]),
);
groups.user_by_answer = Object.fromEntries(
  Object.entries(packed.user_by_answer).map(([configId, lists]) => [
    configId,
    Object.fromEntries(
      Object.entries(lists).map(([answer, refs]) => [
        answer,
        refs.map((i) => users[i]),
      ]),
    ),
  ]),
);

const summary = { ...packed.head, groups };

export default summary;
