import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useWorkspace } from "src/contexts/WorkspaceContext";

import { useSandboxesApi } from "../api/SandboxesApiContext";
import { sandboxKeys } from "../api/queries";
import {
  EXEC_IDLE,
  applyExecEnd,
  applyExecEvent,
  isExecLive,
  startExec,
} from "../lib/execOutput";

/**
 * One streamed command at a time for a sandbox. `run(command)` opens the exec
 * SSE stream; `cancel()` asks EE to signal the command's process group (the
 * stream then ends with an `exit` event marked cancelled). Leaving the page
 * closes the stream, which EE treats as a cancel too.
 */
export default function useExecStream(sandboxId) {
  const api = useSandboxesApi();
  const queryClient = useQueryClient();
  const { currentWorkspaceId } = useWorkspace();
  const [state, setState] = useState(EXEC_IDLE);
  const stream = useRef(null);
  const latest = useRef(state);
  latest.current = state;

  useEffect(() => () => stream.current?.close(), []);

  const run = useCallback(
    (command, options = {}) => {
      if (isExecLive(latest.current)) return;
      stream.current?.close();
      setState(startExec(command));
      const handle = api.exec(
        sandboxId,
        { command, ...options },
        {
          onEvent: (event) =>
            setState((current) => applyExecEvent(current, event)),
        },
      );
      stream.current = handle;
      handle.done
        .then((result) => setState((current) => applyExecEnd(current, result)))
        .catch(() =>
          setState((current) => applyExecEnd(current, { ok: false })),
        )
        .finally(() => {
          if (stream.current === handle) stream.current = null;
          queryClient.invalidateQueries({
            queryKey: sandboxKeys.all(currentWorkspaceId ?? "none"),
          });
        });
    },
    [api, sandboxId, queryClient, currentWorkspaceId],
  );

  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState(null);
  const cancel = useCallback(async () => {
    const { execId } = latest.current;
    if (!execId) return;
    setCancelling(true);
    setCancelError(null);
    try {
      await api.cancelExec(sandboxId, execId);
    } catch (error) {
      setCancelError(error.message);
    } finally {
      setCancelling(false);
    }
  }, [api, sandboxId]);

  const clear = useCallback(() => {
    if (!isExecLive(latest.current)) setState(EXEC_IDLE);
  }, []);

  return { state, run, cancel, cancelling, cancelError, clear };
}
