// KH integration (H-1 · EV-NA3): what "Add a group" adds to a step's eval list.
// Pure; AddEvalGroupButton renders it. A group is the eval-groups detail
// ({eval_group, members[]}) or a list item ({name, evals_count}).

/** The template id an entry of a step's eval list stands for. */
export const entryTemplateId = (entry) =>
  entry?.templateId ?? entry?.template_id ?? entry?.evalId ?? null;

/**
 * Members not on the step yet (by template id), and the step entries that
 * already cover the others (their names are what the step shows).
 * @returns {{add: object[], skipped: {member: object, entry: object}[]}}
 */
export function membersToAdd(group, existing = []) {
  const byTemplate = new Map();
  (existing ?? []).forEach((e) => {
    const id = entryTemplateId(e);
    if (id && !byTemplate.has(id)) byTemplate.set(id, e);
  });
  const add = [];
  const skipped = [];
  (group?.members ?? []).forEach((m) => {
    const entry = byTemplate.get(m.eval_template_id);
    if (entry) skipped.push({ member: m, entry });
    else add.push(m);
  });
  return { add, skipped };
}

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "Support essentials · 4 evaluations" */
export function groupLine(group) {
  const name = group?.eval_group?.name ?? group?.name ?? "";
  const n = group?.members?.length ?? group?.evals_count ?? 0;
  return `${name} · ${plural(n, "evaluation")}`;
}

/** The first three member names, then "+k". */
export function memberPreview(members = [], shown = 3) {
  const names = (members ?? []).map((m) => m.name);
  const head = names.slice(0, shown).join(", ");
  const rest = names.length - shown;
  return rest > 0 ? `${head} +${rest}` : head;
}

/** "A", "A and B", "A, B and C" */
export function andList(names = []) {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The toast after a group is added. */
export function addedGroupText(groupName, { added, skippedNames }) {
  if (!added) return `Every evaluation in ${groupName} is already added.`;
  const head = `Added ${plural(added, "evaluation")} from ${groupName}`;
  if (!skippedNames.length) return `${head}.`;
  const verb = skippedNames.length === 1 ? "was" : "were";
  return `${head} · ${andList(skippedNames)} ${verb} already added.`;
}
