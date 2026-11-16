/**
 * Every "start a run" action used to pass a tool-fit dialog first. The tool
 * fit is now a row in the run plan (run/ToolFitRow.jsx), beside the other
 * readiness checks, so a run goes straight to the plan. A run started without
 * the plan still says it on the run itself: LiveRunView names the tools the
 * environment can't answer.
 *
 *   const { check, dialog } = useRunFitCheck(env, envState, patch);
 *   <Button onClick={() => check(() => openRunPlan())}>Run</Button>
 *   {dialog}
 *
 * The signature stays so the callers don't change.
 */
// eslint-disable-next-line no-unused-vars
export default function useRunFitCheck(env, envState, patch, options = {}) {
  const check = (run) => run();
  return { check, dialog: null };
}
