// The live builder chat for the environment workspace.
//
// Reads the hosted conversation off the SAME ["harness-job", id] react-query poll
// the workspace already runs (react-query dedupes to one network request), projects
// its messages + events into the BuilderConsole turn/step model, and posts user
// turns back through POST …/conversation/messages/. Poll parity, not streaming:
// assistant_delta is ignored; a whole assistant_message renders on the next poll,
// with a "working" cue in between (the projector's heartbeat step + the brief
// mutation-pending pulse).
//
// The return shape { turns, running, send } is unchanged so BuilderConsole and
// BuildingStage stay untouched; `frozen`, `frozenReason` and `stop` are additive.
import { useCallback, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";

import {
  sendHarnessConversationMessage,
  harnessIdempotencyKey,
} from "src/api/harness/harness";
import { getBuilderMode } from "src/sections/simulate/environments/buildEnvironment/console/builderModeBus";
import {
  getScenarioSelection,
  clearScenarioSelection,
} from "src/sections/simulate/environments/buildEnvironment/console/scenarioSelectionBus";
import { terminalStages } from "src/pages/dashboard/harness/harnessShared";
import { harnessJobQuery } from "./environment";
import { projectConversation, conversationInFlight } from "./conversationProjection";

const RUNTIME_WARMING =
  "The agent runtime is warming up. Chat opens once it's ready.";
const NO_WORKSPACE =
  "This run has no saved workspace to restore, so chat isn't available.";
const NOT_A_HARNESS_ENV = "Chat connects once this environment is built.";

// A harness-origin env carries the job id as env.id (env.id === job.job_id). Only
// those have a live ALK conversation; template/adopted client envs have none.
const jobIdFor = (env, source) =>
  source === "harness" || env?.origin === "harness" ? env?.id : null;

export function useWorkspaceChat(env, { source } = {}) {
  const jobId = jobIdFor(env, source);
  const queryClient = useQueryClient();

  const jobQuery = useQuery(harnessJobQuery(jobId, { enabled: Boolean(jobId) }));
  const conversation = jobQuery.data?.conversation ?? null;

  // Keep the latest conversation/jobId in refs so `send` stays referentially
  // stable — the projected turns are memoised, and an unstable `send` would defeat
  // the memo (BuilderConsole autoscrolls on a new turns reference).
  const conversationRef = useRef(conversation);
  conversationRef.current = conversation;
  const jobIdRef = useRef(jobId);
  jobIdRef.current = jobId;

  // Optimistic user turns held only while their POST is in flight — BuilderConsole
  // clears the draft before onSend, so without this the text vanishes until the
  // 202 returns the persisted message.
  const [pending, setPending] = useState([]);

  const mutation = useMutation({
    mutationFn: ({ id, payload }) => sendHarnessConversationMessage(id, payload),
    onSuccess: async (value, { id, requestId }) => {
      setPending((prev) => prev.filter((p) => p.id !== requestId));
      if (!value) return;
      // Key off the job that SUBMITTED this send, not the currently-mounted one:
      // if the workspace navigated to another env mid-flight, `jobIdRef.current`
      // now points at it and we'd write this conversation into the wrong cache.
      const key = ["harness-job", id];
      await queryClient.cancelQueries({ queryKey: key });
      queryClient.setQueryData(key, (prev) =>
        prev ? { ...prev, conversation: value } : prev,
      );
    },
    onError: (err, { requestId }) => {
      // Surface the backend's structured error ({message, retryable}); the axios
      // interceptor spreads the response body onto the rejected error.
      setPending((prev) =>
        prev.map((p) =>
          p.id === requestId
            ? {
                ...p,
                failed: true,
                errorMessage: err?.message,
                retryable: err?.retryable !== false,
              }
            : p,
        ),
      );
    },
  });
  // react-query's `mutate` is stable across renders (unlike the mutation object),
  // so depending on it keeps `send`/`stop` stable and preserves the turns memo.
  const { mutate } = mutation;

  const send = useCallback(
    (text) => {
      const content = (text || "").trim();
      const id = jobIdRef.current;
      if (!content || !id) return;

      // A blocking question turns the next message into a reply — but only while
      // it is genuinely open. If a message already answers it (the reply hasn't
      // been processed by the agent yet, so blocking_input is still set), send a
      // fresh user_message instead: the backend 409s a second reply to an
      // already-answered question ("reply_to does not identify the open question").
      const conv = conversationRef.current;
      const blocking = conv?.blocking_input || null;
      const answered =
        blocking && (conv?.messages || []).some((m) => m.reply_to === blocking.message_id);
      const open = blocking && !answered ? blocking : null;
      const kind = open
        ? open.kind === "confirmation_requested"
          ? "approval"
          : "user_response"
        : "user_message";
      const replyTo = open ? open.message_id : null;

      // A message sent with scenario rows selected is a bulk edit against exactly
      // those rows; snapshot and clear so the "Editing N scenarios" chip drops.
      const selection = getScenarioSelection();
      const scenarioIds = selection?.ids?.length ? selection.ids : undefined;
      if (scenarioIds) clearScenarioSelection();

      const requestId = harnessIdempotencyKey();
      // Snapshot the exact command so Retry replays THIS payload and id — the
      // selection has already been cleared and the blocking question may have
      // moved on, so re-deriving it on retry would change the scope and mint a
      // new client_request_id, defeating server-side deduplication.
      const variables = {
        id,
        requestId,
        payload: {
          content,
          client_request_id: requestId,
          kind,
          ...(replyTo ? { reply_to: replyTo } : {}),
          payload: {
            mode: getBuilderMode(),
            ...(scenarioIds ? { scenario_ids: scenarioIds } : {}),
          },
        },
      };
      setPending((prev) => [...prev, { id: requestId, text: content, variables }]);
      mutate(variables);
    },
    [mutate],
  );

  const stop = useCallback(() => {
    const id = jobIdRef.current;
    if (!id) return;
    const requestId = harnessIdempotencyKey();
    // Soft interrupt of the current turn — distinct from cancelling the job.
    // `content` must be non-blank (the serializer rejects an empty string); the
    // interrupt kind is what the backend acts on, not the text.
    mutate({
      id,
      requestId,
      payload: {
        content: "Stop",
        client_request_id: requestId,
        kind: "interrupt",
        payload: {},
      },
    });
  }, [mutate]);

  const turns = useMemo(() => {
    const projected = projectConversation(conversation);
    // Attach the reply callback to open `ask` steps (the projector stays pure).
    // A resolved question renders its summary from props, so it needs no handler.
    return projected.map((turn) => {
      if (
        turn.role !== "builder" ||
        !turn.steps?.some((s) => s.kind === "ask" && !s.resolved)
      ) {
        return turn;
      }
      return {
        ...turn,
        steps: turn.steps.map((step) =>
          step.kind === "ask" && !step.resolved
            ? { ...step, onSubmit: (answers) => send((answers || []).join(", ")) }
            : step,
        ),
      };
    });
  }, [conversation, send]);

  // Retry a failed send by replaying its original variables (same payload, same
  // client_request_id) rather than composing a fresh send from current state.
  const retry = useCallback(
    (marker) => {
      if (!marker?.variables) return;
      setPending((prev) =>
        prev.map((p) =>
          p.id === marker.id
            ? { ...p, failed: false, errorMessage: undefined, retryable: undefined }
            : p,
        ),
      );
      mutate(marker.variables);
    },
    [mutate],
  );

  // Optimistic user turns (+ any failed-send markers) tack onto the end.
  const withPending = useMemo(() => {
    if (!pending.length) return turns;
    const extra = pending.flatMap((p) =>
      p.failed
        ? [
            { id: `${p.id}-u`, role: "user", text: p.text },
            {
              id: `${p.id}-e`,
              role: "builder",
              steps: [
                {
                  id: `${p.id}-es`,
                  kind: "error",
                  text: p.errorMessage || "Couldn't send that message.",
                  onRetry: p.retryable ? () => retry(p) : undefined,
                },
              ],
            },
          ]
        : [{ id: `${p.id}-u`, role: "user", text: p.text }],
    );
    return [...turns, ...extra];
  }, [turns, pending, retry]);

  // `running` blocks the composer only for the brief POST round-trip, so the user
  // can still interject while the agent works autonomously (the heartbeat step is
  // the persistent cue).
  const running = mutation.isPending;

  const runtimeUnavailable = conversation
    ? conversation.runtime?.available === false
    : false;
  // A terminal run with no saved workspace is permanently unavailable (send 409s
  // retryable:false); a live run that's merely cold is still warming up.
  const terminal = terminalStages.has(jobQuery.data?.status?.stage);
  const frozen = !jobId || runtimeUnavailable;
  const frozenReason = !jobId
    ? NOT_A_HARNESS_ENV
    : runtimeUnavailable
      ? terminal
        ? NO_WORKSPACE
        : RUNTIME_WARMING
      : undefined;

  return {
    turns: withPending,
    running,
    send,
    stop,
    frozen,
    frozenReason,
    inFlight: conversationInFlight(conversation),
  };
}
