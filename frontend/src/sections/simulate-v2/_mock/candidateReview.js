/*
  Reviewing an improvement's changes before they run (roadmap
  improve_next.md): what each could break, the one line a person can edit,
  and the change with that edit applied. The search then tries only the
  changes kept (cfg.candidates).
*/

/** The changes the search would try first — not its later revisions. */
export const reviewableOf = (pool = []) =>
  pool.filter((p) => !String(p.id).includes("~r"));

/** What the change could break beyond the cases it's aimed at. */
export const riskOfCandidate = (p) => {
  if (p.overReach)
    return "the skill may load on requests it wasn't written for.";
  switch (p.kind) {
    case "Skill":
      return `changes when ${p.skillId} loads, so requests near it can behave differently.`;
    case "System prompt":
      return "a line in the instructions applies to every conversation, not only these cases.";
    case "Tool description":
      return `changes when ${p.step || "the tool"} is chosen, in every conversation.`;
    case "Agent setting":
      return "a setting applies to every conversation.";
    case "Model":
      return "a different model changes cost and behaviour everywhere.";
    default:
      return "it can change behaviour beyond these cases.";
  }
};

/** The text a person may edit: a skill's description, or the line it adds. */
export const editableTextOf = (p) => {
  if (p.kind === "Skill") return p.newDescription ?? null;
  const add = (p.diff || []).find((d) => d.type === "add");
  return add ? add.text : null;
};

/** The change with its editable text replaced. */
export const editedCandidateOf = (p, text) => {
  if (p.kind === "Skill")
    return {
      ...p,
      newDescription: text,
      diff: (p.diff || []).map((d) =>
        d.type === "add" && d.text.startsWith("description:")
          ? { ...d, text: `description: ${text}` }
          : d,
      ),
      editedByYou: true,
    };
  let done = false;
  return {
    ...p,
    diff: (p.diff || []).map((d) => {
      if (done || d.type !== "add") return d;
      done = true;
      return { ...d, text };
    }),
    editedByYou: true,
  };
};
