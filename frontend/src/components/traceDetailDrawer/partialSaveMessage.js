// The toast for a save the server answered with some labels not saved: how
// many saved out of how many were sent, or that none were, then the labels
// that did not save by the names the drawer shows, in the drawer's order. A
// label the drawer does not list is counted in their place ("1 label",
// "2 labels").
export const partialSaveMessage = ({
  savedLabels = [],
  failedLabels = [],
  labels = [],
}) => {
  const failedIds = new Set((failedLabels ?? []).map(String));
  const namedIds = new Set();
  const names = [];
  (labels ?? []).forEach((label) => {
    const id = String(label?.id);
    if (failedIds.has(id) && !namedIds.has(id) && label?.name) {
      namedIds.add(id);
      names.push(label.name);
    }
  });
  const unnamed = failedIds.size - namedIds.size;
  if (unnamed > 0) {
    names.push(`${unnamed} ${unnamed === 1 ? "label" : "labels"}`);
  }
  const notSaved = `Not saved: ${names.join(", ")}.`;
  const saved = (savedLabels ?? []).length;
  if (saved === 0) {
    return `No labels were saved. ${notSaved}`;
  }
  return `Saved ${saved} of ${saved + failedIds.size} labels. ${notSaved}`;
};
