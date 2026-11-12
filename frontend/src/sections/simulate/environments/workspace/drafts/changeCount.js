// A draft's change count: the sum of its per-kind counts.
export const changeCount = (changes) =>
  Object.values(changes || {}).reduce((n, v) => n + (Number(v) || 0), 0);
