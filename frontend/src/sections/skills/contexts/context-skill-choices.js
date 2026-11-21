// The skills a reviewer may require, taken from the context's own accepted links so
// nothing is typed. Shared by the captured-example review and the saved-example editor.
export const contextSkillChoices = (descriptor) =>
  (descriptor?.links || []).map((link) => ({
    id: link.skillId,
    label: (link.mount || link.skillId).replace(/^skills\//, '').replace(/\/$/, ''),
  }));
