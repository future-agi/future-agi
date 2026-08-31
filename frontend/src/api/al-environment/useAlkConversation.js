import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import alkAxios from "./client";
import { ALK_KEYS } from "./alEnvironment";
import { streamHarness } from "./streamHarness";

/**
 * Turn one stream event into a transcript entry, or null for the control events.
 *
 * `status` and `done` drive the page rather than the conversation: they say the turn is
 * over and everything should resync, which is not something to render as a message.
 */
const asMessage = (event) => {
  switch (event.kind) {
    case "text":
      // Marked so consecutive chunks can be merged — see append().
      return { role: "tester", text: event.text, streamed: true };
    case "tool":
      return { tool: event.tool, detail: event.detail };
    case "result":
      // `is_error` is how the harness says a tool refused. Without reading it a refusal
      // arrives in the thread wearing a green tick, which is the one thing a reader of a
      // running turn must not be told wrongly.
      return {
        tool: event.tool || "result",
        text: event.text,
        ok: !event.detail?.is_error,
        detail: event.detail,
      };
    case "exchange":
      return { role: event.detail?.speaker || "tester", text: event.text };
    case "result_card":
      return { role: "verdict", text: event.text, detail: event.detail };
    case "artifact":
      // A signal to refresh the artifact tabs, not something to say in the thread.
      return null;
    case "done":
      // A stage can end badly without the transport failing: the server synthesises
      // {outcome: "stopped"|"failed", error} on an interrupt or a crash. Without reading it
      // a failed stage just stops, leaving the operator with a turn that went quiet.
      return event.detail?.outcome === "failed" && event.detail?.error
        ? { role: "error", text: event.detail.error }
        : null;
    default:
      return null;
  }
};

/** What the strip says while a turn runs, following whatever the stream is doing. */
const labelFor = (event) => {
  if (event.kind === "tool") return event.detail?.label || event.tool;
  if (event.kind === "exchange") return "the conversation is running";
  if (event.kind === "result_card") return "grading";
  if (event.kind === "text" || event.kind === "result") return "working";
  return null;
};

/** Everything the tabs and header read, refreshed once a turn finishes. */
const TOUCHED_BY_A_TURN = [
  ALK_KEYS.status,
  ALK_KEYS.history,
  ALK_KEYS.contract,
  ALK_KEYS.world,
  ALK_KEYS.scenarios,
  ALK_KEYS.simulations,
  ALK_KEYS.subgoals,
  ALK_KEYS.runs,
  ALK_KEYS.environments,
];

/**
 * Holds the turn that is happening right now. Stored history comes from React Query; this
 * is only the part that is still arriving, so the two are concatenated for display.
 */
export const useAlkConversation = () => {
  const queryClient = useQueryClient();
  const [live, setLive] = useState([]);
  const [streaming, setStreaming] = useState(false);
  const [thinking, setThinking] = useState("");
  const [error, setError] = useState("");
  const abortRef = useRef(null);

  const append = useCallback((message) => {
    if (!message) return;
    setLive((all) => {
      const last = all[all.length - 1];
      // Prose arrives one chunk at a time. Pushing each chunk separately would turn a
      // single sentence into a column of fragments, each with its own speaker label, so
      // consecutive chunks join up. Any tool call in between ends the paragraph, which is
      // what makes the next chunk start a fresh one.
      if (message.streamed && last?.streamed) {
        return [...all.slice(0, -1), { ...last, text: last.text + message.text }];
      }
      return [...all, message];
    });
  }, []);

  const drive = useCallback(
    async (path, body, opening) => {
      setError("");
      setStreaming(true);
      setThinking(path.endsWith("/run") ? "running the scenarios" : "thinking");
      if (opening) append(opening);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await streamHarness({
          path,
          body,
          signal: controller.signal,
          onEvent: (event) => {
            const said = labelFor(event);
            if (said) setThinking(said);
            // An artifact lands mid-stage — the build stage runs for minutes — so the tabs
            // refresh as it arrives rather than only once the whole turn finishes.
            if (event.kind === "artifact") {
              TOUCHED_BY_A_TURN.forEach((queryKey) =>
                queryClient.invalidateQueries({ queryKey })
              );
            }
            append(asMessage(event));
          },
        });
        // The harness writes the turn to disk as it ends, so refetched history will contain
        // what `live` is still holding. Stay "streaming" until that refetch lands: the view
        // shows the live half only while streaming, so this is what stops a finished turn
        // appearing twice without blinking it out in between.
        TOUCHED_BY_A_TURN.filter((queryKey) => queryKey !== ALK_KEYS.history).forEach(
          (queryKey) => queryClient.invalidateQueries({ queryKey })
        );
        await queryClient.invalidateQueries({ queryKey: ALK_KEYS.history });
      } catch (failed) {
        // Pressing Stop aborts the reader on purpose. That is not something to report.
        const stopped =
          failed?.name === "AbortError" || /aborted/i.test(failed?.message || "");
        if (!stopped) {
          // A refusal is an ordinary outcome — the harness takes one request at a time — so
          // it belongs in the thread, where the turn it interrupted is.
          const said = failed?.message || "The harness could not be reached.";
          setError(said);
          append({ role: "error", text: said });
        }
      } finally {
        setStreaming(false);
        setThinking("");
        abortRef.current = null;
      }
    },
    [append, queryClient]
  );

  const say = useCallback(
    (text) => drive("/say", { text }, { role: "you", text }),
    [drive]
  );

  /** An empty string runs every scenario; names separated by spaces run only those. */
  const runScenarios = useCallback((names = "") => drive("/run", { text: names }, null), [drive]);

  const stop = useCallback(async () => {
    abortRef.current?.abort();
    try {
      await alkAxios.post("/stop");
    } catch {
      // Stopping is best-effort: if the turn already ended there is nothing to interrupt.
    }
  }, []);

  /** Live messages are folded into stored history once the turn is saved server-side. */
  const clearLive = useCallback(() => setLive([]), []);

  /** Error lines are ours alone and never reach stored history, so dismissing one is
   * dropping it from `live` — identity is enough, the objects are never recreated. */
  const dismissLive = useCallback(
    (message) => setLive((all) => all.filter((one) => one !== message)),
    [],
  );

  return { live, streaming, thinking, error, say, runScenarios, stop, clearLive, dismissLive };
};
