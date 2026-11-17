// `Rerun with changes…` submissions on a real server, one per experiment,
// held here rather than in the dialog. The server keeps at most one
// unconfirmed submission per experiment and refuses every other change
// until that exact request is sent again, so its body must outlive the
// dialog: closing it, opening it from another run, leaving the page or a
// failed background read never loses the one retry that can confirm it.
// The results header reads the same entry to keep the menu item open.
//
// Logout puts this store back to its initial state, but a request already
// sent keeps going. Each submission therefore writes its own attempt number
// into its entry and answers only while that entry is still there: after a
// logout (the entry is gone) or once a newer submission took the experiment
// (another number), a late answer is dropped. It never brings back the
// signed-out person's settings, draft or body, never counts a start and
// never runs their callbacks in the next session.
import { create } from "src/utils/zustand";
import { isUnknownRequestOutcome } from "src/sections/common/Runs/httpResendCopy";

// byExperiment[id] = {status, attempt, body?, error?, source, draft, label}:
//   "sending"      the request is in flight; body is what it sent.
//   "unconfirmed"  no answer said whether it started; body is the only
//                  request that may be sent again, unchanged.
//   "refused"      the server refused it and nothing started; the draft can
//                  be edited again, so there is no body to resend.
// started[id] counts the submissions the server confirmed started.
const useRerunSubmissions = create(() => ({ byExperiment: {}, started: {} }));

// Counted outside the store, so a logout reset never hands out a number an
// earlier request still holds.
let attempts = 0;
// What a submission resolves to when it was dropped before it was sent.
const DROPPED = Symbol("dropped");

const setEntry = (experimentId, entry) =>
  useRerunSubmissions.setState((state) => {
    const byExperiment = { ...state.byExperiment };
    if (entry) byExperiment[experimentId] = entry;
    else delete byExperiment[experimentId];
    return { byExperiment };
  });

/** The experiment's submission ({status, body?, error?, ...}) or null. */
export const useRerunSubmission = (experimentId) =>
  useRerunSubmissions((state) => state.byExperiment[experimentId] ?? null);

/** Whether the experiment has a submission in flight or one to send again. */
export const useRerunSubmissionHeld = (experimentId) =>
  useRerunSubmissions((state) =>
    Boolean(state.byExperiment[experimentId]?.body),
  );

/** How many submissions for the experiment the server confirmed started. */
export const useRerunStarts = (experimentId) =>
  useRerunSubmissions((state) => state.started[experimentId] ?? 0);

/**
 * Send one new variant for the experiment with `send(body)`. A held
 * submission is always sent again exactly as it was (its body, settings and
 * draft), whatever the caller built; one in flight is never sent twice.
 * `onStarted(result)` runs when the server confirms the start, whether or
 * not a dialog is still open, but never after a logout or a newer
 * submission took this one's place.
 */
export function sendRerunSubmission(
  experimentId,
  { body, source, draft, label },
  { send, onStarted },
) {
  const held = useRerunSubmissions.getState().byExperiment[experimentId];
  if (!experimentId || held?.status === "sending") return;
  const kept = held?.body
    ? {
        body: held.body,
        source: held.source,
        draft: held.draft,
        label: held.label,
      }
    : { body, source, draft, label };
  attempts += 1;
  const attempt = attempts;
  // Whether this submission still owns the experiment's entry.
  const current = () =>
    useRerunSubmissions.getState().byExperiment[experimentId]?.attempt ===
    attempt;
  setEntry(experimentId, { ...kept, status: "sending", error: null, attempt });
  Promise.resolve()
    .then(() => (current() ? send(kept.body) : DROPPED))
    .then(
      (result) => {
        if (result === DROPPED || !current()) return;
        useRerunSubmissions.setState((state) => {
          const byExperiment = { ...state.byExperiment };
          delete byExperiment[experimentId];
          return {
            byExperiment,
            started: {
              ...state.started,
              [experimentId]: (state.started[experimentId] ?? 0) + 1,
            },
          };
        });
        onStarted?.(result);
      },
      (error) => {
        if (!current()) return;
        // A 4xx answer admitted nothing. Any other outcome may have saved
        // the variant and started it, so only the same body may follow.
        const { body: sent, ...rest } = kept;
        setEntry(
          experimentId,
          isUnknownRequestOutcome(error)
            ? { ...rest, body: sent, status: "unconfirmed", error, attempt }
            : { ...rest, status: "refused", error, attempt },
        );
      },
    );
}

/** Closing the dialog forgets a refusal; a submission in flight or unconfirmed stays. */
export function forgetRerunRefusal(experimentId) {
  const entry = useRerunSubmissions.getState().byExperiment[experimentId];
  if (entry?.status === "refused") setEntry(experimentId, null);
}
