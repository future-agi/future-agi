// Whether any one of an entry's keys is enough for a live run: true when
// each model a live run can use (the manifest's models.live) reads at most
// one key and every key listed (keys, each with a name) belongs to one of
// them. The entry page's Keys panel follows the same rule.
export function anyOneKey(keys, models) {
  const live = Array.isArray(models?.live) ? models.live : [];
  const keysOf = (row) => (Array.isArray(row?.keys) ? row.keys : []);
  const modelKeys = live.map(keysOf);
  const named = new Set(modelKeys.flat());
  return (
    live.length > 0 &&
    Array.isArray(keys) &&
    modelKeys.every((list) => list.length <= 1) &&
    keys.every((key) => named.has(key?.name))
  );
}
