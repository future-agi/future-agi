/*
  Sample-agent runs as Reference (roadmap catalogue.md): a ready world's
  copy runs its sample agent first; those runs are labelled Reference so
  they're never read as your agent's, and stay so after your agent takes
  the sample's place. Your agent is compared with the sample only on the
  cases both ran, with the rest listed — and if the world changed since the
  sample ran, it's said that the sample's result isn't the same test.
*/

/** Whether a run is the sample agent's — marked at the swap, or made while the sample is still the agent. */
export const isReferenceRun = (run, envState) =>
  !!run?.reference || (!!envState?.agent?.sample && run?.kind !== "trial");

/** The latest finished Reference run in an environment, or null. */
export const latestReferenceRun = (envState) =>
  [...(envState?.runs || [])]
    .filter((r) => r.reference && r.finishedAt)
    .sort((a, b) => String(b.finishedAt).localeCompare(String(a.finishedAt)))[0] || null;

/**
 * Your run against the sample's: the cases both ran, the ones only one of
 * them ran (left out of the comparison), and whether the world changed in
 * between. Null for a Reference run, or when there's no sample run to
 * compare with.
 */
export const sampleCompareOf = (envState, runId) => {
  const run = (envState?.runs || []).find((r) => r.id === runId);
  if (!run || isReferenceRun(run, envState)) return null;
  const ref = latestReferenceRun(envState);
  if (!ref) return null;
  const mine = new Set(run.scenarioIds || []);
  const theirs = new Set(ref.scenarioIds || []);
  const shared = [...mine].filter((id) => theirs.has(id));
  return {
    ref,
    shared,
    onlyMine: [...mine].filter((id) => !theirs.has(id)),
    onlyRef: [...theirs].filter((id) => !mine.has(id)),
    /* Graded content changed between the two runs: not the same test. */
    changed: !!ref.envVersion && !!run.envVersion && ref.envVersion !== run.envVersion
      ? { from: ref.envVersion, to: run.envVersion }
      : null,
  };
};
