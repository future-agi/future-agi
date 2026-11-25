// The Values tab's pending change: values to set (new or changed) and names to
// remove, saved together as one new version.

const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
export const NAME_MAX = 128;

export const emptyDraft = () => ({ set: {}, remove: [] });

export const draftEmpty = (draft) =>
  !Object.keys(draft.set).length && !draft.remove.length;

export const setValue = (draft, name, value) => ({
  set: { ...draft.set, [name]: value },
  remove: draft.remove.filter((one) => one !== name),
});

export const removeName = (draft, name) => {
  const { [name]: _dropped, ...set } = draft.set;
  return { set, remove: [...new Set([...draft.remove, name])] };
};

export const undoName = (draft, name) => {
  const { [name]: _dropped, ...set } = draft.set;
  return { set, remove: draft.remove.filter((one) => one !== name) };
};

// What is wrong with a name typed for a new value, or null.
export function newNameProblem(name, rows, madeByEnvironment = []) {
  const trimmed = name.trim();
  if (!trimmed) return "Enter the variable's name";
  if (trimmed.length > NAME_MAX) return `Keep it under ${NAME_MAX} characters`;
  if (!NAME.test(trimmed))
    return "Letters, digits and underscores, not starting with a digit";
  if (madeByEnvironment.includes(trimmed))
    return "The test environment makes this value itself; yours isn't needed";
  if (rows.some((row) => row.name === trimmed))
    return "This agent already has this value; change it in its row";
  return null;
}

// The request body for the change: values to set and names to remove.
export const changeBody = (draft) => ({
  kind: "credentials",
  ...(Object.keys(draft.set).length ? { secret_values: draft.set } : {}),
  ...(draft.remove.length ? { remove_names: draft.remove } : {}),
});

// What the change does, by name: added, changed, removed; and whether the
// names change, which makes each environment prepare again.
export function draftSummary(draft, rows) {
  const held = new Set(rows.map((row) => row.name));
  const set = Object.keys(draft.set);
  const added = set.filter((name) => !held.has(name)).sort();
  const changed = set.filter((name) => held.has(name)).sort();
  const removed = draft.remove.filter((name) => held.has(name)).sort();
  const required = new Set(
    rows.filter((row) => row.required).map((row) => row.name),
  );
  return {
    added,
    changed,
    removed,
    namesChange: added.length > 0 || removed.length > 0,
    removedRequired: removed.filter((name) => required.has(name)),
  };
}

const list = (names) => names.join(", ");

export function summaryLines(summary) {
  return [
    summary.added.length && `Adds ${list(summary.added)}`,
    summary.changed.length && `Changes ${list(summary.changed)}`,
    summary.removed.length && `Removes ${list(summary.removed)}`,
  ].filter(Boolean);
}
