// HO D3 (W4 §9 integration): the filter panel's Collaboration fields over the
// live keys the server answers at query time (MOCK collabFilters.js):
//   system:assigned_to    me | anyone | nobody | <user id>
//   system:open_comments  open | mentions_me
//   system:language       <code> | __other__
// `equals` sends `in` (keep), `not equals` sends `not_in` (drop); those are
// the only two operators a key answers, so each field offers only them
// (`operators`: the panel narrows a property's list by it). Every person
// and language comes from the server (assignments and translate reads);
// nothing is named here. Pure.

export const COLLAB_CATEGORY = Object.freeze({
  key: "collaboration",
  label: "Collaboration",
  icon: "mdi:account-group-outline",
});

export const COLLAB_FIELD = Object.freeze({
  assignedTo: "system:assigned_to",
  comments: "system:open_comments",
  language: "system:language",
});

// The live keys answer keep (`in`) and drop (`not_in`) only; a substring or
// null operator would be read as keep (MOCK collabFilters.js).
export const COLLAB_OPERATORS = Object.freeze(["in", "not_in"]);

const IDS = new Set(Object.values(COLLAB_FIELD));
export const isCollabField = (id) => IDS.has(String(id ?? ""));

const personOf = (p) => ({
  id: String(p?.id ?? p?.user_id ?? ""),
  name: p?.name ?? p?.full_name ?? p?.email ?? "",
});

/**
 * The three panel properties. `assignments` = the assignments read
 * (`{me, people, members}`), `translate` = the translate read
 * (`{languages, dataset_language_name}`); a field whose read has nothing to
 * offer is left out.
 */
export function collabFieldProperties({ assignments, translate } = {}) {
  const me = assignments?.me ? personOf(assignments.me) : null;
  const people = [
    ...(assignments?.members ?? []),
    ...(assignments?.people ?? []),
  ]
    .map(personOf)
    .filter((p) => p.id && p.name && p.id !== me?.id)
    .filter((p, i, all) => all.findIndex((x) => x.id === p.id) === i);
  const base = {
    type: "string",
    panelType: "string",
    category: COLLAB_CATEGORY.key,
    originType: "system",
    operators: [...COLLAB_OPERATORS],
  };
  const out = [
    {
      ...base,
      id: COLLAB_FIELD.assignedTo,
      name: "Assigned to",
      enumValues: ["me", "anyone", "nobody", ...people.map((p) => p.id)],
      enumLabels: {
        me: "Me",
        anyone: "Anyone",
        nobody: "Nobody",
        ...Object.fromEntries(people.map((p) => [p.id, p.name])),
      },
    },
    {
      ...base,
      id: COLLAB_FIELD.comments,
      name: "Comments",
      enumValues: ["open", "mentions_me"],
      enumLabels: {
        open: "Has open comments",
        mentions_me: "Mentions me",
      },
    },
  ];
  const languages = (translate?.languages ?? []).filter((l) => l?.code);
  if (languages.length) {
    const own = translate?.dataset_language_name;
    out.push({
      ...base,
      id: COLLAB_FIELD.language,
      name: "Language",
      enumValues: [...languages.map((l) => l.code), "__other__"],
      enumLabels: {
        ...Object.fromEntries(languages.map((l) => [l.code, l.name])),
        __other__: own
          ? `Not ${own} (the dataset's language)`
          : "Not the dataset's language",
      },
    });
  }
  return out;
}

/** A chip's value with its label (`Mentions me`, `Spanish`). */
export function collabChipValue(filter, properties) {
  const prop = (properties ?? []).find((p) => p.id === filter?.column_id);
  if (!prop?.enumLabels) return filter;
  const label = (v) => prop.enumLabels[v] ?? v;
  const v = filter.filter_config?.filter_value;
  return {
    ...filter,
    filter_config: {
      ...filter.filter_config,
      filter_value: Array.isArray(v) ? v.map(label) : label(v),
    },
  };
}
