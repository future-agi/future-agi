import { useState } from "react";
import { currentAgentVersion } from "../_mock/versions";
import { toolFit } from "../_mock/toolFit";
import { mcpRunGapOf } from "../_mock/worldTools";
import RunFitDialog from "./RunFitDialog";

/**
 * Wrap any "start a run" action with the tool-fit check.
 *
 *   const { check, dialog } = useRunFitCheck(env, envState, patch);
 *   <Button onClick={() => check(() => startRun())}>Run</Button>
 *   {dialog}
 *
 * If the agent version fits the pinned environment version the run starts at
 * once; otherwise the dialog asks first, on the screen the user is already on.
 * It warns, never blocks. `locked` (a template preview) leaves out the fixes
 * that change the world. A secret that failed its access check is said by the
 * run view itself, where every way of starting a run lands (LiveRunView).
 */
export default function useRunFitCheck(env, envState, patch, { locked = false } = {}) {
  const [pending, setPending] = useState(null);

  const check = (run, { agent } = {}) => {
    const label = agent || currentAgentVersion(envState)?.label;
    /* The agent's own MCP tools count too: a run gets no answer for those the world doesn't have. */
    if (!env || (toolFit(env, envState, { agent: label }).fits && !mcpRunGapOf(env, envState).missing.length)) {
      run();
      return;
    }
    setPending({ run, agent: label });
  };

  const dialog = (
    <RunFitDialog
      open={!!pending}
      onClose={() => setPending(null)}
      env={env}
      envState={envState}
      patch={patch}
      agent={pending?.agent}
      locked={locked}
      onRun={() => pending?.run()}
    />
  );

  return { check, dialog };
}
