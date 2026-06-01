import React from "react";
import PropTypes from "prop-types";
import { useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import EvalFeedbackDrawer from "src/sections/common/EvalFeedback/EvalFeedbackDrawer";
import { LEGACY_DATASET_ACTION_TYPES } from "src/sections/evals/EvalDetails/EvalsFeedback/feedbackConstant";
import { useAddDevelopEvalFeedbackStore } from "../../states";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";

// Develop-side wrapper around the shared EvalFeedbackDrawer.
// Handles both `module="dataset"` (default) and `module="experiment"` — the
// only difference between the two is the endpoint set and the `source` /
// `source_id` keys in the create payload.

// Values must match `valid_actions` on the dataset BE handler
// (model_hub/views/develop_dataset.py:10971) — `retune`, `recalculate_row`,
// `recalculate_dataset` are the strings its dispatcher branches on. The
// Observe wrapper (next FE PR) uses the canonical ACTION_TYPES enum instead.
// TH-5604 will canonicalize this handler onto FeedbackActionType, after
// which this wrapper switches over too.
const DEVELOP_RETUNE_OPTIONS = [
  {
    value: LEGACY_DATASET_ACTION_TYPES.RETUNE,
    title: "Re-tune",
    description:
      "We’ll create a new version of this metric and use it in all future invocations",
  },
  {
    value: LEGACY_DATASET_ACTION_TYPES.RECALCULATE_ROW,
    title: "Re-calculate for this row",
    description:
      "We’ll create a new version of this metric and use it in all future invocations. We’ll also recalculate it on this row. This might take a while.",
  },
  {
    value: LEGACY_DATASET_ACTION_TYPES.RECALCULATE_DATASET,
    title: "Re-tune and re-calculate for this dataset",
    description:
      "We’ll create a new version of this metric and use it in all future invocations. We’ll also recalculate it on every run in this dataset. This might take a while.",
  },
];

const resolveDevelopEndpoints = (isExperimentModule, experimentId) => {
  if (isExperimentModule) {
    return {
      getDetails: endpoints.develop.experiment.feedback.getDetails(experimentId),
      getTemplate:
        endpoints.develop.experiment.feedback.getTemplate(experimentId),
      create: endpoints.develop.experiment.feedback.create(experimentId),
      submit: endpoints.develop.experiment.feedback.submit(experimentId),
    };
  }
  return {
    getDetails: endpoints.develop.eval.getFeedbackDetails,
    getTemplate: endpoints.develop.eval.getFeedbackTemplate,
    create: endpoints.develop.eval.addFeedback,
    submit: endpoints.develop.eval.updateFeedback,
  };
};

const AddDevelopEvalFeedbackDrawer = ({
  module = "dataset",
  onRefreshGrid,
}) => {
  const {
    addDevelopEvalFeedbackTarget: target,
    setAddDevelopEvalFeedbackTarget,
  } = useAddDevelopEvalFeedbackStore();
  const isExperimentModule = module === "experiment";
  const { refreshGrid: contextRefreshGrid } = useDevelopDetailContext();
  const refreshGrid = onRefreshGrid ?? contextRefreshGrid;
  const queryClient = useQueryClient();
  const { dataset, experimentId } = useParams();

  const onClose = () => setAddDevelopEvalFeedbackTarget(null);

  // BE wire shape is snake_case everywhere (DRF default JSONRenderer, no
  // camelization layer). Every dispatcher into this store builds the target
  // in snake_case to match — see CustomCellRender.jsx:215,
  // ExperimentDetailDrawerContent.jsx:1048, DatapointDrawerV2.jsx:974,
  // DatapointDrawer.jsx:778.
  const open = Boolean(target);
  const metricId = isExperimentModule
    ? target?.user_eval_metric_id
    : target?.source_id;
  const rowId = target?.row_data?.row_id;
  const apis = resolveDevelopEndpoints(isExperimentModule, experimentId);

  const existingFeedbackQueryKey = [
    "fetch-feedback-details",
    metricId,
    rowId,
    isExperimentModule ? experimentId : null,
  ];
  const templateQueryKey = [
    "fetchFeedbackDetails",
    metricId,
    isExperimentModule ? experimentId : null,
  ];

  // BE returns snake_case (model_hub/views/develop_dataset.py — `output_type`,
  // `action_type`, etc.). Hand the raw shape through; the core reads snake_case
  // directly.
  const fetchExistingFeedback = async () => {
    if (!metricId || !rowId) return null;
    const res = await axios.get(apis.getDetails, {
      params: { user_eval_metric_id: metricId, row_id: rowId },
    });
    return res.data?.result?.feedback?.[0] ?? null;
  };

  const fetchTemplate = async () => {
    if (!metricId) return null;
    const res = await axios.get(apis.getTemplate, {
      params: { user_eval_metric_id: metricId },
    });
    return res.data?.result ?? null;
  };

  const submitEntry = async ({ value, explanation }) => {
    const payload = {
      value,
      explanation,
      user_eval_metric: metricId,
      source: isExperimentModule ? "experiment" : "dataset",
      source_id: isExperimentModule ? target?.source_id : target?.id,
      row_id: rowId,
    };
    const res = await axios.post(apis.create, payload);
    enqueueSnackbar("Feedback submitted successfully!", { variant: "success" });
    refreshGrid?.();
    return { feedbackId: res.data?.result?.id, raw: res };
  };

  const submitAction = async ({ actionValue, feedbackId, entryPayload }) => {
    const payload = {
      action_type: actionValue,
      user_eval_metric_id: metricId,
      feedback_id: feedbackId,
      ...(entryPayload && {
        value: entryPayload.value,
        explanation: entryPayload.explanation,
      }),
    };
    const res = await axios.post(apis.submit, payload);
    enqueueSnackbar("Feedback submitted successfully!", { variant: "success" });
    refreshGrid?.();
    return res;
  };

  const onAnalyticsEntrySubmit = () => {
    trackEvent(Events.datasetSubmitFeedbackClicked, {
      [PropertyName.datasetId]: dataset,
      [PropertyName.evalId]: target?.source_id,
      [PropertyName.rowIdentifier]: rowId,
    });
  };

  const onSubmitted = () => {
    queryClient.invalidateQueries({ queryKey: existingFeedbackQueryKey });
  };

  return (
    <EvalFeedbackDrawer
      open={open}
      onClose={onClose}
      target={target}
      fetchExistingFeedback={fetchExistingFeedback}
      existingFeedbackQueryKey={existingFeedbackQueryKey}
      fetchTemplate={fetchTemplate}
      templateQueryKey={templateQueryKey}
      submitEntry={submitEntry}
      submitAction={submitAction}
      onAnalyticsEntrySubmit={onAnalyticsEntrySubmit}
      onSubmitted={onSubmitted}
      retuneOptions={DEVELOP_RETUNE_OPTIONS}
    />
  );
};

AddDevelopEvalFeedbackDrawer.propTypes = {
  module: PropTypes.oneOf(["dataset", "experiment"]),
  onRefreshGrid: PropTypes.func,
};

export default AddDevelopEvalFeedbackDrawer;
