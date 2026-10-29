/*
  Review changes (roadmap builder.md Week 1): every unsaved world edit in one
  place, grouped by what it changes, with where it came from — and each one
  undone on its own before the version is saved.

  An edit is recorded by envChangePatch as { kind, detail, at } and applied
  in place. The store stamps it as it lands (stampEdit): `before` keeps the
  fields that patch replaced, as they were, and `source` says who made it.
  Undo puts those fields back — only when no later edit changed the same
  ones, since that would undo the later edit too.
*/

/* Bookkeeping keys a patch carries that are not the edit itself. */
const BOOKKEEPING = new Set(["unsavedEnvChanges", "draftBase", "changeSource"]);
const ABSENT = "__absent__";

export const EDIT_AREAS = [
  {
    id: "rules",
    label: "Rules",
    kinds: ["rules", "contract"],
    tab: "contract",
  },
  {
    id: "state",
    label: "Starting state and records",
    kinds: ["seed"],
    tab: "world",
  },
  {
    id: "tools",
    label: "Tools",
    kinds: ["toolbehaviour", "tools-added", "tools-removed", "network"],
    tab: "world",
  },
  { id: "actors", label: "Actors", kinds: ["actors"], tab: "actors" },
  {
    id: "scenarios",
    label: "Scenarios",
    kinds: ["scenarios"],
    tab: "scenarios",
  },
];
export const areaOfEdit = (c) =>
  EDIT_AREAS.find((a) => a.kinds.includes(c.kind)) || {
    id: "other",
    label: "Other",
    tab: "settings",
  };

export const sourceLabelOf = (c) => {
  if (!c.source) return null;
  if (c.source === "chat") return "From the builder chat";
  if (c.source.startsWith("file:")) return `From ${c.source.slice(5)}`;
  return "By hand";
};

const isMap = (v) => v && typeof v === "object" && !Array.isArray(v);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/*
  What one field held before. A keyed map (tool settings, actor edits) keeps
  only the entries this edit changed, so edits to different tools stay
  separate: { __sub: { [entry]: old value } }.
*/
const beforeOf = (prevValue, nextValue) => {
  if (prevValue === undefined) return ABSENT;
  if (!isMap(prevValue) || !isMap(nextValue)) return prevValue;
  const keys = new Set([...Object.keys(prevValue), ...Object.keys(nextValue)]);
  const changed = [...keys].filter((k) => !same(prevValue[k], nextValue[k]));
  return {
    __sub: Object.fromEntries(
      changed.map((k) => [
        k,
        prevValue[k] === undefined ? ABSENT : prevValue[k],
      ]),
    ),
  };
};

/* The parts an edit touched: "field" or "field.entry". */
const partsOf = (before) =>
  Object.entries(before || {}).flatMap(([k, v]) =>
    v?.__sub ? Object.keys(v.__sub).map((sk) => `${k}.${sk}`) : [k],
  );
const overlaps = (a, b) =>
  a.some((x) =>
    b.some((y) => x === y || x === y.split(".")[0] || y === x.split(".")[0]),
  );

/**
 * Called by the store on every patch: when it adds an edit, remember what
 * the fields it replaces held, and who made it. Returns the patch to apply.
 */
export const stampEdit = (prev, patch) => {
  if (!patch || typeof patch !== "object") return patch;
  const { changeSource, ...rest } = patch;
  const before = prev?.unsavedEnvChanges || [];
  const after = rest.unsavedEnvChanges;
  if (!Array.isArray(after) || after.length !== before.length + 1) return rest;
  const last = after[after.length - 1];
  if (last.before) return rest;
  const keys = Object.keys(rest).filter((k) => !BOOKKEEPING.has(k));
  const snapshot = Object.fromEntries(
    keys.map((k) => [k, beforeOf(prev?.[k], rest[k])]),
  );
  return {
    ...rest,
    unsavedEnvChanges: [
      ...after.slice(0, -1),
      { ...last, before: snapshot, source: changeSource || "hand" },
    ],
  };
};

/** Whether edit `i` can be undone by itself, and why not. */
export const undoableOf = (list, i) => {
  const c = list[i];
  if (!c?.before)
    return {
      ok: false,
      why: "Made before single edits could be undone. Discard all puts it back.",
    };
  const mine = partsOf(c.before);
  const later = list
    .slice(i + 1)
    .find((l) => overlaps(partsOf(l.before), mine));
  if (later)
    return {
      ok: false,
      why: `“${later.detail}” changed the same part later. Undo that one first.`,
    };
  return { ok: true };
};

/** Put back what edit `i` replaced and drop it from the list. */
export const undoEditPatch = (envState, i) => {
  const list = envState?.unsavedEnvChanges || [];
  const c = list[i];
  if (!undoableOf(list, i).ok) return null;
  const restored = Object.fromEntries(
    Object.entries(c.before).map(([k, v]) => {
      if (v === ABSENT) return [k, undefined];
      if (!v?.__sub) return [k, v];
      const cur = { ...(envState?.[k] || {}) };
      Object.entries(v.__sub).forEach(([sk, old]) => {
        if (old === ABSENT) delete cur[sk];
        else cur[sk] = old;
      });
      return [k, cur];
    }),
  );
  const rest = [...list.slice(0, i), ...list.slice(i + 1)];
  return {
    ...restored,
    unsavedEnvChanges: rest,
    ...(!rest.length && { draftBase: undefined }),
  };
};
