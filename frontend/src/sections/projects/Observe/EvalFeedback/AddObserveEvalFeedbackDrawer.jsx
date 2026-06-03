import React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import EvalFeedbackDrawer from "src/sections/common/EvalFeedback/EvalFeedbackDrawer";
import { OUTPUT_TYPES } from "src/sections/common/EvalFeedback/constants";
import { ACTION_TYPES } from "src/sections/evals/EvalDetails/EvalsFeedback/feedbackConstant";
import useAddObserveEvalFeedbackStore from "./useAddObserveEvalFeedbackStore";

// Observe-side wrapper around the shared EvalFeedbackDrawer. Handles the
// three Observe target types — span / trace / session — by shaping the
// polymorphic anchor id into every payload + invalidating the right query
// key on success.
//
// Uses the CANONICAL ACTION_TYPES vocabulary (matches FeedbackActionType on
// the BE — model_hub/models/choices.py:309-311 — enforced at the serializer
// layer by SubmitFeedbackActionTypeSerializer.action_type = ChoiceField).
// The Develop wrapper uses LEGACY_DATASET_ACTION_TYPES instead until
// TH-5604 canonicalizes the dataset BE handler.

const TARGET_TYPES = Object.freeze({
  SPAN: "span",
  TRACE: "trace",
  SESSION: "session",
});

// Stage-2 radio option list. The "Re-calculate for this {span|trace|session}"
// label is rendered dynamically off target.target_type below.
const buildRetuneOptions = (targetType, hasEvalTask) => [
  {
    value: ACTION_TYPES.RETUNE,
    title: "Re-tune",
    description:
      "We’ll create a new version of this metric and use it in all future invocations.",
  },
  {
    value: ACTION_TYPES.RECALCULATE,
    title: `Re-calculate for this ${targetType ?? "row"}`,
    description: `We’ll create a new version of this metric and use it in all future invocations. We’ll also recalculate it on this ${targetType ?? "row"}. This might take a while.`,
  },
  {
    value: ACTION_TYPES.RETUNE_RECALCULATE,
    title: "Re-tune and re-calculate for this eval run",
    description: hasEvalTask
      ? "We’ll create a new version of this metric and use it in all future invocations. We’ll also recalculate it on every row in this eval run."
      : "This eval isn’t part of an eval task — batch recalculation isn’t available for ad-hoc evaluations.",
    disabled: !hasEvalTask,
  },
];

// Observe ``output_type`` normalizer.
//
// Discipline: ONLY add a key here after observing it live in an API response
// against one of the three Observe surfaces below. If a new value lands, the
// drawer falls through to the REASON free-text fallback (in fetchTemplate)
// so the user can still submit — and we notice the gap in QA and add the
// value here with its source citation.
//
// Observed values (curl'd 2026-06-02):
//
//   GET /tracer/trace-session/{id}/eval_logs/ → items[].detail.output_type
//     - "pass_fail"      (session_pass_fail_tox)
//     - "percentage"     (session_score_sumamry)
//     - "deterministic"  (session_choices_tone, tone_task_15_may_2026_14_27)
//     Backed by EvalTemplate.output_type_normalized
//     (model_hub/models/evals_metric.py:165).
//
//   Surfaces that ALSO feed the drawer but have not yet been curl-verified:
//     - EvalsTabView (trace drawer) — reads `ev.output_type` from
//       collectAllEvalsFromEntry(entry).eval_scores. Source in BE is
//       trace.py:1391-1395, same `output_type_normalized` field — likely
//       same three values, but unverified live so not seeded here.
//     - VoiceRightPanel — voice evals_metrics. BE source in
//       observation_span.py:227-285 returns a different vocabulary
//       ("str_list" / "bool" / "Pass/Fail" / "float" / legacy `config.output`).
//       Not seeded here until observed live.
const _OUTPUT_TYPE_SYNONYMS = new Map([
  ["pass_fail", OUTPUT_TYPES.PASS_FAIL],
  ["percentage", OUTPUT_TYPES.SCORE],
  ["deterministic", OUTPUT_TYPES.CHOICES],
]);

const normalizeOutputType = (raw) => {
  if (raw == null) return null;
  return _OUTPUT_TYPE_SYNONYMS.get(String(raw)) ?? null;
};

// Fetch the eval template behind a custom_eval_config so we can recover
// ``choices`` for ``deterministic`` evals (the session eval_logs response
// doesn't carry them today). Soft-fails — drawer falls back to free-text
// instead of leaving the user with a blank form.
const fetchTemplateChoices = async (customEvalConfigId) => {
  if (!customEvalConfigId) return null;
  try {
    const cfgRes = await axios.get(
      endpoints.project.updateEvalTaskConfig(customEvalConfigId),
    );
    const cfg = cfgRes.data?.result ?? cfgRes.data ?? {};
    const templateId = cfg.eval_template ?? cfg.eval_template_id;
    if (!templateId) return null;
    const tplRes = await axios.get(endpoints.develop.eval.getEvalDetail(templateId));
    const tpl = tplRes.data?.result ?? tplRes.data ?? {};
    return Array.isArray(tpl.choices) && tpl.choices.length > 0
      ? tpl.choices
      : null;
  } catch {
    return null;
  }
};

// The anchor-id keys vary by target_type. Centralize the picking so payload
// builders and query-key builders agree.
const anchorIdFor = (target) => {
  if (!target) return null;
  if (target.target_type === TARGET_TYPES.SESSION) return target.trace_session_id;
  if (target.target_type === TARGET_TYPES.TRACE) return target.trace_id;
  return target.observation_span_id;
};

// Polymorphic id-fields the BE serializer expects on every POST. Keeps the
// payload free of unknown keys when reject_unknown_fields=True on the BE.
const polymorphicIdsFor = (target) => {
  const ids = {};
  if (target.observation_span_id) ids.observation_span_id = target.observation_span_id;
  if (target.trace_id) ids.trace_id = target.trace_id;
  if (target.trace_session_id) ids.trace_session_id = target.trace_session_id;
  return ids;
};

const AddObserveEvalFeedbackDrawer = () => {
  const {
    addObserveEvalFeedbackTarget: target,
    setAddObserveEvalFeedbackTarget,
  } = useAddObserveEvalFeedbackStore();
  const queryClient = useQueryClient();

  // Render the drawer only while a target is set so all downstream state —
  // useEvalFeedbackFlow's stage, cached entry payload, both react-hook-form
  // instances, both mutation states — gets unmounted on close. Without this
  // gate, stale form text and `stage = ACTION` would leak into the next open.
  const onClose = () => setAddObserveEvalFeedbackTarget(null);
  if (!target) return null;

  const open = Boolean(target);
  const customEvalConfigId = target?.custom_eval_config_id ?? null;
  const anchorId = anchorIdFor(target);

  // The drawer hooks expect stable query keys even when target is null —
  // they're gated on `enabled: open && fetcher`.
  const existingFeedbackQueryKey = [
    "observe-feedback-details",
    customEvalConfigId,
    anchorId,
  ];
  const templateQueryKey = ["observe-eval-config", customEvalConfigId];

  // GET the most recent Observe Feedback row for this (target, eval) so the
  // drawer pre-fills the entry stage and skips to stage 2 when the user has
  // already given feedback once. Backed by
  // /tracer/observation-span/get_feedback/ (see tracer/views/observation_span.py).
  // The endpoint returns all-null fields when no prior feedback exists; we
  // surface that as `null` to the hook (the hook's "no existing feedback"
  // signal). The query is gated on `enabled: open && fetcher`.
  const fetchExistingFeedback = async () => {
    if (!target) return null;
    const res = await axios.get(endpoints.project.getObserveFeedback, {
      params: {
        target_type: target.target_type,
        ...polymorphicIdsFor(target),
        custom_eval_config_id: target.custom_eval_config_id,
      },
    });
    const fb = res.data?.result ?? null;
    if (!fb?.feedback_id) return null;
    // Hook shape (useEvalFeedbackFlow): `id` keys the "existing-feedback"
    // shortcut to stage 2; `comment` pre-fills the explanation field
    // (dataset wrapper uses the same name — that's the hook's vocabulary,
    // not ours). BE returns `explanation`; renamed at this boundary.
    return {
      id: fb.feedback_id,
      value: fb.value,
      comment: fb.explanation,
      feedback_improvement: fb.feedback_improvement,
      action_type: fb.action_type,
    };
  };

  // Resolve the entry-stage's template by normalizing whatever flavor of
  // ``output_type`` the dispatching surface attached to the target. Each BE
  // surface (eval_logs, voice evals_metrics, legacy aggregations) emits its
  // own vocabulary — see ``normalizeOutputType`` above. After normalization:
  //   PASS_FAIL  → choices hardcoded to ["Passed","Failed"] (BE truth: every
  //                pass_fail template's choices column equals this exactly).
  //   SCORE      → no choices needed.
  //   CHOICES    → prefer choices the target already carries; otherwise
  //                chain custom_eval_config → eval_template to recover them.
  //   REASON     → free-text.
  // Unknown ``output_type`` falls back to REASON so the user always has a
  // submittable field instead of a blank drawer.
  const fetchTemplate = async () => {
    const canonical = normalizeOutputType(target?.output_type);

    if (canonical === OUTPUT_TYPES.PASS_FAIL) {
      return { output_type: OUTPUT_TYPES.PASS_FAIL, choices: ["Passed", "Failed"] };
    }
    if (canonical === OUTPUT_TYPES.SCORE) {
      return { output_type: OUTPUT_TYPES.SCORE };
    }
    if (canonical === OUTPUT_TYPES.CHOICES) {
      const carried = Array.isArray(target?.choices) ? target.choices : null;
      const choices =
        carried && carried.length > 0
          ? carried
          : await fetchTemplateChoices(target?.custom_eval_config_id);
      if (Array.isArray(choices) && choices.length > 0) {
        return { output_type: OUTPUT_TYPES.CHOICES, choices };
      }
      // No choices recoverable — degrade to free-text so the user can still
      // submit corrective feedback.
      return { output_type: OUTPUT_TYPES.REASON };
    }
    if (canonical === OUTPUT_TYPES.REASON) {
      return { output_type: OUTPUT_TYPES.REASON };
    }
    // Unknown / missing output_type → safe default.
    return { output_type: OUTPUT_TYPES.REASON };
  };

  const submitEntry = async ({ value, explanation }) => {
    const payload = {
      target_type: target.target_type,
      ...polymorphicIdsFor(target),
      custom_eval_config_id: target.custom_eval_config_id,
      feedback_value: value,
      feedback_explanation: explanation,
    };
    const res = await axios.post(endpoints.project.submitFeedback, payload);
    enqueueSnackbar("Feedback submitted successfully!", { variant: "success" });
    return { feedbackId: res.data?.result?.feedback_id, raw: res };
  };

  const submitAction = async ({ actionValue, feedbackId, entryPayload }) => {
    const payload = {
      target_type: target.target_type,
      ...polymorphicIdsFor(target),
      custom_eval_config_id: target.custom_eval_config_id,
      feedback_id: feedbackId,
      action_type: actionValue,
      ...(entryPayload && {
        feedback_value: entryPayload.value,
        feedback_explanation: entryPayload.explanation,
      }),
    };
    const res = await axios.post(
      endpoints.project.applySubmitFeedback,
      payload,
    );
    const recalculatedCount = res.data?.result?.recalculated_count;
    enqueueSnackbar(
      typeof recalculatedCount === "number" && recalculatedCount > 0
        ? `Recalculating ${recalculatedCount} eval${recalculatedCount === 1 ? "" : "s"}.`
        : "Feedback submitted successfully!",
      { variant: "success" },
    );
    return res;
  };

  const onSubmitted = () => {
    // The eval list that surfaced this feedback chip lives behind different
    // query keys per target_type. Invalidate the one that matches so the
    // surface reflects the new feedback / rerun status next render.
    if (target.target_type === TARGET_TYPES.SESSION) {
      queryClient.invalidateQueries({ queryKey: ["sessionEvalLogs", anchorId] });
    } else {
      queryClient.invalidateQueries({ queryKey: ["traceEvalLogs", anchorId] });
    }
  };

  // Force remount when the user switches targets without closing in between
  // (clicking the chip on Row A then Row B). Otherwise useEvalFeedbackFlow's
  // stage / cached payload / form state would carry over from A into B.
  const remountKey = `${target.target_type}:${anchorId}:${customEvalConfigId}`;

  return (
    <EvalFeedbackDrawer
      key={remountKey}
      open={open}
      onClose={onClose}
      target={target}
      fetchExistingFeedback={fetchExistingFeedback}
      existingFeedbackQueryKey={existingFeedbackQueryKey}
      fetchTemplate={fetchTemplate}
      templateQueryKey={templateQueryKey}
      submitEntry={submitEntry}
      submitAction={submitAction}
      retuneOptions={buildRetuneOptions(
        target?.target_type,
        Boolean(target?.eval_task_id),
      )}
      onSubmitted={onSubmitted}
    />
  );
};

export default AddObserveEvalFeedbackDrawer;
export { TARGET_TYPES };
