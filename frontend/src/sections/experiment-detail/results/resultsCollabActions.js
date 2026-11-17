// W4 §9 (M6, the results SelectionBar integration): Send to review… (Lane C:
// Start review, a rubric review of the selected rows on the run in view),
// Assign to… and Comment… (Lane B). Pure: the ActionBar items.
// Assigning and starting a review need edit on the dataset (the guard's
// classes for assignments and queue create); commenting is
// view and takes one row at a time (the drawer's Review tab holds the
// thread keyed by row and run).

export const COMMENT_ONE_ROW = "Comment on one row at a time";

const editReason = (readOnly, access, verb) => {
  if (readOnly) return `Editors can ${verb}`;
  if (access?.can?.edit === false)
    return access.reason?.("edit") ?? "You need edit access to this dataset";
  return null;
};

export function resultsCollabActions({
  n,
  readOnly,
  access,
  qaRun,
  onSendToQa,
  onAssign,
  onComment,
}) {
  const assignWhy = editReason(readOnly, access, "assign rows");
  // Start review creates a queue on the dataset: edit, like assigning.
  const qaWhy =
    editReason(readOnly, access, "start a review") ??
    (qaRun ? null : "Pick a run first");
  const items = [
    {
      id: "sendToQa",
      label: "Send to QA…",
      keywords: ["annotation queue", "review", "rubric", "qa"],
      icon: "jam:write",
      priority: 2,
      disabled: Boolean(qaWhy),
      reason: qaWhy ?? undefined,
      run: onSendToQa,
    },
    {
      id: "assign",
      label: "Assign to…",
      keywords: ["assign", "due date", "owner"],
      icon: "mdi:account-arrow-right-outline",
      priority: 3,
      disabled: Boolean(assignWhy),
      reason: assignWhy ?? undefined,
      run: onAssign,
    },
  ];
  if (onComment)
    items.push({
      id: "comment",
      label: "Comment…",
      keywords: ["comment", "mention", "discuss"],
      icon: "mdi:comment-text-outline",
      priority: 3,
      disabled: n !== 1,
      reason: n !== 1 ? COMMENT_ONE_ROW : undefined,
      run: onComment,
    });
  return items;
}
