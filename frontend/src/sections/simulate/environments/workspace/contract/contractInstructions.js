const DEPENDENTS = {
  rule: "sub-goals and checks",
  useCase: "scenarios and checks",
};

const NOUN = { rule: "rule", useCase: "use case" };

const KEEP = "and keep everything else as it is.";

export function contractInstruction({ target, op, index, text, next }) {
  const noun = NOUN[target];
  const dependents = DEPENDENTS[target];
  const position = index + 1;
  const before = String(text ?? "").trim();
  const after = String(next ?? "").trim();
  if (op === "add")
    return `Add this ${noun} to the contract: "${after}". Add the ${dependents} it needs, ${KEEP}`;
  if (op === "edit")
    return `Change ${noun} ${position} of the contract from "${before}" to "${after}". Update the ${dependents} that depend on it, ${KEEP}`;
  if (op === "remove")
    return `Remove ${noun} ${position} ("${before}") from the contract. Remove or update the ${dependents} that depend on it, ${KEEP}`;
  return "";
}
