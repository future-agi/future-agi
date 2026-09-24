export const MAX_PARALLELISM = 8;

export const clampParallelism = (value) =>
  Math.min(MAX_PARALLELISM, Math.max(1, Math.trunc(Number(value) || 1)));

export const PARALLELISM_COPY = {
  label: "Parallel worlds",
  enabledHint:
    "Requested concurrent scenarios in one sandbox. Resources and certified limits may reduce the effective value; remaining scenarios wait for a free world.",
  disabledHint:
    "Parallel execution is not yet enabled for this environment, so runs use a single world.",
  admitted: (admitted, requested) =>
    admitted < requested
      ? `Preflight admits ${admitted} of ${requested} requested.`
      : `Preflight admits ${admitted}.`,
  fact: "Parallel worlds",
  factValue: ({ requested, admitted, effective }) => {
    const shown = effective ?? admitted ?? requested;
    return shown < requested ? `${shown} of ${requested}` : String(shown);
  },
  degradedTitle: (effective, requested) =>
    `Parallelism reduced to ${effective} (requested ${requested})`,
};

export const PARALLELISM_DEGRADE_COPY = {
  fixed_port:
    "The agent declares a fixed network port, so scenarios ran one at a time.",
  conformance_gate_failed:
    "The environment failed its parallel-readiness check, so scenarios ran one at a time.",
  resource_limited:
    "The sandbox had fewer resources than requested, running {effective} scenario(s) at a time.",
  literal_local_endpoint:
    "An environment value points at a fixed local address, so scenarios ran one at a time.",
  world_start_failed:
    "Some parallel copies of the environment failed to start, continuing with {effective}.",
};

export const degradeReasonCopy = (reason, effective) => {
  const template = PARALLELISM_DEGRADE_COPY[reason];
  if (!template) return `Parallelism was reduced to ${effective}.`;
  return template.replace("{effective}", String(effective));
};
