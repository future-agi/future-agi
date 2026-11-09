const object = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const own = (value, key) =>
  Object.prototype.hasOwnProperty.call(value || {}, key)
    ? value[key]
    : undefined;

// The server's field diff is authoritative. Expand objects into readable schema
// paths, but preserve arrays, missing values and null distinctly. Deep schemas
// remain intact at the boundary rather than risking unbounded recursion.
function expand(path, before, after, depth = 0) {
  if (object(before) && object(after) && depth < 12) {
    return [...new Set([...Object.keys(before), ...Object.keys(after)])]
      .sort()
      .flatMap((key) =>
        expand(`${path}.${key}`, own(before, key), own(after, key), depth + 1),
      );
  }
  if (JSON.stringify(before) === JSON.stringify(after)) return [];
  return [{ path, before, after }];
}

export function revisionChanges(row) {
  const diffs = row.diff?.length
    ? row.diff
    : [
        ...new Set([
          ...Object.keys(row.previous_definition || {}),
          ...Object.keys(row.definition || {}),
        ]),
      ]
        .sort()
        .map((field) => ({
          field,
          before: own(row.previous_definition, field),
          after: own(row.definition, field),
        }));
  return diffs.flatMap(({ field, before, after }) =>
    expand(field, before, after),
  );
}
