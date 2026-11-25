export const evalVerdict = (ev) => {
  if (typeof ev?.passed === "boolean") return ev.passed;
  if (ev?.score != null) return ev.score >= 50;
  return null;
};

export const passedByEvalId = (evaluations) =>
  Object.fromEntries(
    (Array.isArray(evaluations) ? evaluations : [])
      .filter((one) => one?.id != null && typeof one.passed === "boolean")
      .map((one) => [String(one.id), one.passed]),
  );
