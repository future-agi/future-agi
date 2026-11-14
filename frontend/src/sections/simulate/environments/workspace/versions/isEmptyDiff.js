const list = (value) => (Array.isArray(value) ? value : []);

// True when a diff names no change at all, so the caller can say "Nothing changed".
export const isEmptyDiff = (piece, diff) => {
  if (!diff) return true;
  const names = (change) =>
    list(change?.added).length +
    list(change?.removed).length +
    list(change?.changed).length;
  if (piece === "suite") {
    return (
      !list(diff.added).length &&
      !list(diff.removed).length &&
      !Object.keys(diff.changed || {}).length
    );
  }
  if (piece === "agent") {
    return !diff.source && !diff.prompt && !names(diff.variables);
  }
  const contract = diff.contract || {};
  return (
    !names(contract.tools) &&
    !names(contract.rules) &&
    !names(contract.use_cases) &&
    !list(contract.fields).length &&
    !contract.modality &&
    !names(diff.sub_goals) &&
    diff.files !== null &&
    !names(diff.files)
  );
};
