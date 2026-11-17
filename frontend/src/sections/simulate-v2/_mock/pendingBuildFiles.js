/*
  What a connect form asked for the environment build that connecting starts:
  what to test and the files. A connect returns to Environments through a
  URL, which can't carry a file's contents, so they wait here, tied to the
  agent, and the build takes them once. Short-lived on purpose: an abandoned
  connect never leaks into a later build.
*/
const TTL_MS = 15 * 60 * 1000;
let pending = null;

/** Hold { files, request } for the build of this agent. */
export const holdBuild = (agentId, { files = [], request = "" } = {}) => {
  pending =
    files?.length || request?.trim()
      ? {
          agentId,
          files: files || [],
          request: request?.trim() || "",
          at: Date.now(),
        }
      : null;
};

/** What was held for this agent, once — then it's gone. */
export const takeBuild = (agentId) => {
  const none = { files: [], request: "" };
  if (!pending || pending.agentId !== agentId) return none;
  const fresh = Date.now() - pending.at < TTL_MS;
  const { files, request } = pending;
  pending = null;
  return fresh ? { files, request } : none;
};
