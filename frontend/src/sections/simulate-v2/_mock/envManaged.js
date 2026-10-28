/*
  Where an environment is managed. "code" when it was built from the CLI
  (`agent-learn env build` / `run --mode local`) or exported with
  `agent-learn env pull`; otherwise "console", where edits here make the versions.
*/
export const managedOf = (env, envState) => {
  const explicit = envState?.managedIn || env?.managedIn;
  if (explicit) return explicit;
  if (
    env?.runsOn === "local" ||
    env?.builtFrom?.worldSource?.runsOn === "local"
  )
    return "code";
  return "console";
};

/** The repo or folder a code-managed environment lives in, when known. */
export const managedRepoOf = (env, envState) =>
  envState?.repo || env?.repo || null;
