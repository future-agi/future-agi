import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { useMutation } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  AddFeedbackValidationSchema,
  feedbackSubmittedValidationSchema,
} from "../validation";
import { STAGE } from "../constants";

// Stage-1 form defaults. If the user is editing an existing feedback row, we
// preload its value + comment; otherwise blank fields. Keys are snake_case
// because the BE returns them that way (no normalization layer in the stack).
const entryDefaults = (existingFeedback) => ({
  value: existingFeedback?.value ?? "",
  explanation: existingFeedback?.comment ?? "",
});

// Stage-2 form defaults. The radio value defaults to whatever action_type was
// previously chosen on the existing feedback (if any).
const actionDefaults = (existingFeedback) => ({
  value: existingFeedback?.action_type ?? "",
});

/**
 * Drives the two-stage eval-feedback drawer.
 *
 * The hook owns:
 *   - both react-hook-form instances (entry + action stages)
 *   - the entry-then-action state machine
 *   - the two mutations for submitting stage 1 / stage 2
 *   - the "we already have feedback for this row, skip the create call" shortcut
 *
 * The hook does NOT know:
 *   - which endpoints to hit (wrapper supplies submitEntry + submitAction)
 *   - the payload keys for those endpoints (wrapper builds them inside its callbacks)
 *   - which Mixpanel event to fire (wrapper supplies onAnalyticsEntrySubmit)
 *   - how to invalidate caches (wrapper does that inside onSubmitted)
 *
 * @param existingFeedback {object|null}
 * @param submitEntry {(formValues) => Promise<{ feedbackId, raw }>}
 * @param submitAction {(args) => Promise<unknown>}
 *        args = { actionValue, feedbackId, entryPayload?: { value, explanation } }
 *        entryPayload is set only when the entry stage was short-circuited
 *        because existingFeedback was present.
 * @param onAnalyticsEntrySubmit {(formValues) => void} fires on stage-1 submit
 * @param onSubmitted {(response) => void} fires after stage-2 success
 * @param onClose {() => void} called after stage-2 success
 */
export default function useEvalFeedbackFlow({
  existingFeedback,
  submitEntry,
  submitAction,
  onAnalyticsEntrySubmit,
  onSubmitted,
  onClose,
}) {
  const [stage, setStage] = useState(STAGE.ENTRY);

  // Cached entry payload — only used when the user had existing feedback and
  // we want to forward their updated value + explanation alongside the
  // chosen action type in the single stage-2 request.
  const cachedEntryPayload = useRef(null);

  // Result of the entry-stage mutation. The feedback_id from this response is
  // what stage 2 needs.
  const entryResultRef = useRef(null);

  const entryForm = useForm({
    defaultValues: entryDefaults(existingFeedback),
    resolver: zodResolver(AddFeedbackValidationSchema),
  });

  const actionForm = useForm({
    defaultValues: actionDefaults(existingFeedback),
    resolver: zodResolver(feedbackSubmittedValidationSchema),
  });

  const actionValueField = actionForm.watch("value");

  const entryMutation = useMutation({
    mutationFn: submitEntry,
    onSuccess: (result) => {
      entryResultRef.current = result;
      entryForm.reset();
      setStage(STAGE.ACTION);
    },
  });

  const actionMutation = useMutation({
    mutationFn: submitAction,
    onSuccess: (response) => {
      onSubmitted?.(response);
      actionForm.reset();
      onClose?.();
    },
  });

  // Stage 1 → Stage 2.
  // If the row already has a feedback record, we skip the create call entirely
  // and forward the entered value + explanation alongside the action in stage 2.
  const onEntrySubmit = entryForm.handleSubmit((formValues) => {
    onAnalyticsEntrySubmit?.(formValues);

    if (existingFeedback?.id) {
      cachedEntryPayload.current = {
        value: formValues.value,
        explanation: formValues.explanation,
      };
      setStage(STAGE.ACTION);
      return;
    }

    entryMutation.mutate(formValues);
  });

  // Stage 2 submit.
  const onActionSubmit = actionForm.handleSubmit((formValues) => {
    const feedbackId =
      entryResultRef.current?.feedbackId ?? existingFeedback?.id;

    actionMutation.mutate({
      actionValue: formValues.value,
      feedbackId,
      entryPayload: cachedEntryPayload.current,
    });
  });

  // Defensive: if existingFeedback arrives after mount (parent prop updates),
  // re-seed defaults so the form reflects it. Matches the original implicit
  // behavior since useForm only reads defaults on first render.
  useEffect(() => {
    if (existingFeedback) {
      entryForm.reset(entryDefaults(existingFeedback));
      actionForm.reset(actionDefaults(existingFeedback));
    }
  }, [existingFeedback?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    stage,
    entryControl: entryForm.control,
    actionControl: actionForm.control,
    actionValueField,
    onEntrySubmit,
    onActionSubmit,
    isSubmittingEntry: entryMutation.isPending,
    isSubmittingAction: actionMutation.isPending,
  };
}
