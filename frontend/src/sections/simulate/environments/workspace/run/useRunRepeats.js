// How many times each scenario runs, shared by the header's Run, the Scenarios
// selection bar and the Runs tab's baseline. Kept in the environment's
// workspace state; three is the fewest where a scenario can be flaky.
export const DEFAULT_RUN_REPEATS = 3;

export default function useRunRepeats(envState, patch) {
  const repeats = envState?.runRepeats ?? DEFAULT_RUN_REPEATS;
  const setRepeats = (n) => patch?.({ runRepeats: n });
  return [repeats, setRepeats];
}
