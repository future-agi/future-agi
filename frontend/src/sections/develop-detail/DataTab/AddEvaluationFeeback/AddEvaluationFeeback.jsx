import { zodResolver } from "@hookform/resolvers/zod";
import {
  Box,
  Button,
  Checkbox,
  Drawer,
  FormControl,
  FormControlLabel,
  FormHelperText,
  IconButton,
  LinearProgress,
  Radio,
  RadioGroup,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useRef } from "react";
import { Controller, useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import { feedbackFormSchema } from "./validation";
import { LoadingButton } from "@mui/lab";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
// Assuming you are using axios for API requests
import { enqueueSnackbar } from "notistack"; // For success/error notifications
import axios, { endpoints } from "src/utils/axios";
import { FormSelectField } from "src/components/FormSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import CellMarkdown from "src/sections/common/CellMarkdown";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useParams } from "react-router";
import { useAddEvaluationFeebackStore } from "../../states";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import {
  FEEDBACK_OUTPUT_TYPES as OUTPUT,
  getCurrentValue,
  getReason,
  serializeFeedbackValue,
  toArray,
} from "./feedback_value";

// Subtle grey tint used behind the eval explanation / value panels.
const PANEL_TINT_BG = "rgba(147, 143, 163, 0.08)";
// Re-tune radio group: strip the field's default border/padding.
const RETUNE_GROUP_SX = {
  border: "none",
  borderRadius: 0,
  padding: 0,
  marginTop: "10px",
};

const AddEvaluationFeeback = ({ module = "dataset", onRefreshGrid }) => {
  const { addEvaluationFeeback: data, setAddEvaluationFeeback } =
    useAddEvaluationFeebackStore();
  const isExperimentModule = module === "experiment";
  const { refreshGrid: contextRefreshGrid } = useDevelopDetailContext();
  const refreshGrid = onRefreshGrid ?? contextRefreshGrid;
  const onClose = () => {
    setAddEvaluationFeeback(null);
  };
  const { experimentId } = useParams();
  const metricId = isExperimentModule ? data?.userEvalMetricId : data?.sourceId;
  const rowId = data?.rowData?.row_id;
  const detailsEndpoint = isExperimentModule
    ? endpoints.develop.experiment.feedback.getDetails(experimentId)
    : endpoints.develop.eval.getFeedbackDetails;

  const { isLoading, data: feedbackData } = useQuery({
    queryKey: [
      "fetch-feedback-details",
      metricId,
      rowId,
      isExperimentModule ? experimentId : null,
    ],
    queryFn: () =>
      axios.get(detailsEndpoint, {
        params: {
          user_eval_metric_id: metricId,
          row_id: rowId,
        },
      }),
    enabled:
      !!(isExperimentModule
        ? data?.userEvalMetricId && experimentId
        : data?.sourceId) && !!rowId,
    select: (d) => d.data?.result?.feedback?.[0],
  });

  return (
    <Drawer
      anchor="right"
      open={Boolean(data)}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "10px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      {isLoading && (
        <Box sx={{ minWidth: "600px" }}>
          <LinearProgress />
        </Box>
      )}
      {!isLoading && (
        <EvaluationFeeback
          onClose={onClose}
          data={data}
          refreshGrid={refreshGrid}
          existingFeedback={feedbackData}
          isExperimentModule={isExperimentModule}
        />
      )}
    </Drawer>
  );
};

export default AddEvaluationFeeback;

const parseFeedbackValue = (existingFeedback, isMulti) => {
  const raw = existingFeedback?.value ?? "";
  if (isMulti) return toArray(raw);
  return raw;
};

const getDefaultValues = (existingFeedback, isMulti) => ({
  value: existingFeedback
    ? parseFeedbackValue(existingFeedback, isMulti)
    : isMulti
      ? []
      : "",
  explanation: existingFeedback?.comment || "",
  actionType: existingFeedback?.actionType || "",
});

const EvaluationFeeback = ({
  onClose,
  data,
  refreshGrid,
  existingFeedback,
  isExperimentModule,
}) => {
  const { control, handleSubmit, reset } = useForm({
    defaultValues: getDefaultValues(existingFeedback, false),
    resolver: zodResolver(feedbackFormSchema),
  });
  const pendingRef = useRef(null);
  // Persist the feedback id created in step 1 so that if step 2 (submitAction)
  // fails and the user resubmits, we reuse that record instead of creating a
  // duplicate (orphaning the first). Cleared on full success and per row.
  const createdFeedbackIdRef = useRef(null);
  const queryClient = useQueryClient();
  const existingFeedbackId = existingFeedback?.id;
  const { dataset, experimentId } = useParams();
  const metricId = isExperimentModule ? data?.userEvalMetricId : data?.sourceId;
  const rowId = data?.rowData?.row_id;
  // Drop any half-created id when the drawer switches to a different row.
  useEffect(() => {
    createdFeedbackIdRef.current = null;
  }, [rowId]);
  const feedbackEndpoints = isExperimentModule
    ? {
        getTemplate:
          endpoints.develop.experiment.feedback.getTemplate(experimentId),
        create: endpoints.develop.experiment.feedback.create(experimentId),
        submit: endpoints.develop.experiment.feedback.submit(experimentId),
      }
    : {
        getTemplate: endpoints.develop.eval.getFeedbackTemplate,
        create: endpoints.develop.eval.addFeedback,
        submit: endpoints.develop.eval.updateFeedback,
      };
  const feedbackQueryKey = [
    "fetch-feedback-details",
    metricId,
    rowId,
    isExperimentModule ? experimentId : null,
  ];

  const { data: feedbackData } = useQuery({
    queryKey: [
      "fetchFeedbackDetails",
      metricId,
      isExperimentModule ? experimentId : null,
    ],
    queryFn: () =>
      axios.get(feedbackEndpoints.getTemplate, {
        params: { user_eval_metric_id: metricId },
      }),
    enabled: !!metricId,
    select: (d) => d.data?.result,
    refetchOnMount: true,
  });

  const outputType = feedbackData?.output_type;
  const isMulti =
    outputType === OUTPUT.CHOICES && Boolean(feedbackData?.multi_choice);

  // Re-seed defaults once the template (and the multi-choice flag) is known,
  // so the value field starts as an array for multi-choice evals.
  useEffect(() => {
    if (feedbackData) {
      reset(getDefaultValues(existingFeedback, isMulti));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedbackData, existingFeedback, isMulti]);

  // Submit the chosen re-tune action (and the value/explanation) against the
  // created/existing feedback record. Both submit endpoints (dataset and
  // experiment) read row_id off the feedback record itself; sending it in
  // the payload is redundant and the experiment endpoint's strict
  // serializer rejects it.
  const submitAction = (feedbackId) => {
    submitFeedbackField({
      action_type: pendingRef.current.actionType,
      user_eval_metric_id: metricId,
      feedback_id: feedbackId,
      value: pendingRef.current.value,
      explanation: pendingRef.current.explanation,
    });
  };

  // Step 1 — create the feedback record, then chain into the action submit.
  const { mutate: createFeedback, isPending: creating } = useMutation({
    mutationFn: (formData) => axios.post(feedbackEndpoints.create, formData),
    onSuccess: (resp) => {
      const newId = resp?.data?.result?.id;
      // Guard: without a feedback id the action submit would send
      // feedback_id: undefined and silently fail.
      if (!newId) {
        enqueueSnackbar(
          "Couldn't create the feedback record. Please try again.",
          {
            variant: "error",
          },
        );
        return;
      }
      // Remember the record so a resubmit after a step-2 failure reuses it.
      createdFeedbackIdRef.current = newId;
      submitAction(newId);
    },
  });

  // Step 2 — submit the re-tune action; closes the drawer on success.
  const { mutate: submitFeedbackField, isPending: submittingAction } =
    useMutation({
      mutationFn: (formData) => axios.post(feedbackEndpoints.submit, formData),
      onSuccess: () => {
        // Fully submitted — the record is now linked, so drop the retry id.
        createdFeedbackIdRef.current = null;
        enqueueSnackbar("Feedback submitted successfully!", {
          variant: "success",
        });
        refreshGrid?.();
        reset();
        onClose();
        queryClient.invalidateQueries({ queryKey: feedbackQueryKey });
      },
    });

  const onSubmit = (formData) => {
    const value = serializeFeedbackValue(formData.value);
    pendingRef.current = {
      actionType: formData.actionType,
      value,
      explanation: formData.explanation,
    };
    trackEvent(Events.datasetSubmitFeedbackClicked, {
      [PropertyName.datasetId]: dataset,
      [PropertyName.evalId]: data?.sourceId,
      [PropertyName.rowIdentifier]: rowId,
    });
    // Reuse an existing record, or one created on a previous attempt whose
    // action submit failed — avoids orphaning a duplicate feedback record.
    const reuseFeedbackId = existingFeedbackId ?? createdFeedbackIdRef.current;
    if (reuseFeedbackId) {
      submitAction(reuseFeedbackId);
      return;
    }
    createFeedback({
      value,
      explanation: formData.explanation,
      user_eval_metric: metricId,
      source: isExperimentModule ? "experiment" : "dataset",
      source_id: isExperimentModule ? data?.sourceId : data?.id,
      row_id: rowId,
    });
  };

  const isSubmitting = creating || submittingAction;

  return (
    <Box sx={{ display: "flex", height: "100vh" }}>
      <Box
        sx={{
          padding: "20px",
          display: "flex",
          flexDirection: "column",
          gap: 2,
          height: "100%",
          width: "600px",
        }}
        component="form"
        onSubmit={handleSubmit(onSubmit)}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Typography fontWeight={700} color="text.primary">
            Add feedback
          </Typography>
          <IconButton onClick={onClose} size="small">
            <Iconify icon="mingcute:close-line" />
          </IconButton>
        </Box>
        <div style={{ borderBottom: "1px solid var(--border-light)" }} />
        <FeedBackForm
          control={control}
          data={data}
          feedbackData={feedbackData}
          outputType={outputType}
          isMulti={isMulti}
        />
        <Box display="flex" gap={1} justifyContent="flex-end">
          <Button
            variant="outlined"
            color="inherit"
            size="small"
            onClick={onClose}
            sx={{ minWidth: 160 }}
          >
            Cancel
          </Button>
          <LoadingButton
            variant="contained"
            color="primary"
            type="submit"
            size="small"
            loading={isSubmitting}
            sx={{ minWidth: 160 }}
          >
            Submit feedback
          </LoadingButton>
        </Box>
      </Box>
    </Box>
  );
};

export const FeedBackForm = ({
  control,
  data,
  feedbackData,
  outputType,
  isMulti,
}) => {
  const choices = feedbackData?.choices || [];
  const choiceScores = feedbackData?.choice_scores;
  const reasonText = getReason(data);
  const currentValue = getCurrentValue(data, choiceScores);

  const renderValueInput = () => {
    if (!feedbackData) return null;

    // When choice_scores is defined the LLM always emits a choice label (the
    // score is derived from the map), so the feedback widget must be a picker
    // regardless of the eval's raw output_type. Multi-choice evals still get
    // checkboxes; single-choice ones get radios.
    if (choiceScores && Object.keys(choiceScores).length > 0) {
      const labels = Object.keys(choiceScores);
      if (isMulti) {
        return (
          <ChoiceCheckboxGroup
            control={control}
            label="Select the right value(s)"
            choices={labels}
            renderLabel={(label) => `${label} (score ${choiceScores[label]})`}
          />
        );
      }
      return (
        <RadioField
          label="Select a right value"
          control={control}
          fieldName="value"
          options={labels.map((label) => ({
            label: `${label} (score ${choiceScores[label]})`,
            value: label,
          }))}
        />
      );
    }

    if (outputType === OUTPUT.REASON) {
      return (
        <AllInputField
          label="Write a right value"
          placeholder="Improve the tone and grammar of the prompt"
          size="small"
          control={control}
          fieldName="value"
          variant="filled"
          multiline
          rows={3}
        />
      );
    }

    if (outputType === OUTPUT.SCORE) {
      return (
        <AllInputField
          label="Write a right value"
          placeholder="Add Number"
          size="small"
          control={control}
          fieldName="value"
          variant="filled"
          type="number"
          inputProps={{ min: 0, max: 1, step: "any" }}
          helperText="Enter a number between 0 and 1"
        />
      );
    }

    if (outputType === OUTPUT.PASS_FAIL || outputType === OUTPUT.CHOICES) {
      if (isMulti) {
        return (
          <ChoiceCheckboxGroup
            control={control}
            label="Select the right value(s)"
            choices={choices}
          />
        );
      }
      return (
        <RadioField
          label="Select a right value"
          control={control}
          fieldName="value"
          options={choices.map((value) => ({ label: value, value }))}
        />
      );
    }

    if (outputType === OUTPUT.SELECT) {
      return (
        <AllSelectField
          label="Select a right value"
          control={control}
          options={[{ value: "user", label: "User" }]}
          fieldName="value"
          fullWidth
        />
      );
    }

    return null;
  };

  return (
    <Box
      sx={{
        gap: 2,
        display: "flex",
        flexDirection: "column",
        flex: 1,
        overflow: "auto",
        paddingBottom: "10px",
      }}
    >
      <Typography
        sx={{
          fontSize: "18px",
          fontWeight: "600",
          lineHeight: "26px",
        }}
      >
        {feedbackData?.eval_name || data?.name}
      </Typography>
      <Typography
        sx={{
          fontSize: "14px",
          fontWeight: "400",
          lineHeight: "21px",
        }}
      >
        Help us refine this eval. Share any issues, and we’ll use your feedback
        to improve it automatically.
      </Typography>

      <Box
        border={"1px solid var(--border-default)"}
        bgcolor={PANEL_TINT_BG}
        borderRadius={1}
        padding={1.5}
      >
        {currentValue && (
          <Box mb={reasonText ? 1.25 : 0}>
            <Typography
              fontSize={12}
              fontWeight={700}
              letterSpacing="0.02em"
              color="text.secondary"
              textTransform="uppercase"
            >
              Current output
            </Typography>
            <Typography fontSize={14} color="text.primary">
              {currentValue}
            </Typography>
          </Box>
        )}
        {reasonText ? (
          <CellMarkdown spacing={0} text={reasonText} />
        ) : (
          !currentValue && (
            <Typography color="text.disabled" fontSize={14}>
              Unable to fetch Explanation
            </Typography>
          )
        )}
      </Box>

      <div style={{ borderBottom: "1px solid var(--border-light)" }} />

      {renderValueInput()}

      <AllInputField
        label="Write the right explanation"
        placeholder="Write the explanation the eval should have given for this result, and why it's correct"
        size="small"
        control={control}
        fieldName="explanation"
        variant="filled"
        multiline
        rows={4}
      />

      <div style={{ borderBottom: "1px solid var(--border-light)" }} />

      <Typography
        sx={{
          fontSize: "16px",
          fontWeight: "600",
          lineHeight: "21px",
        }}
      >
        Select one of the options
      </Typography>
      <RadioField
        control={control}
        fieldName={"actionType"}
        label={""}
        options={RETUNE_OPTIONS}
        groupSx={RETUNE_GROUP_SX}
      />
    </Box>
  );
};

// Multi-choice value input — checkboxes. `renderLabel` optionally decorates
// the visible label (e.g. "Polite (score 1)") while the stored value stays
// the raw choice key.
const ChoiceCheckboxGroup = ({ control, label, choices, renderLabel }) => {
  return (
    <Controller
      name="value"
      control={control}
      render={({ field, fieldState: { error } }) => {
        const arr = Array.isArray(field.value) ? field.value : [];
        return (
          <FormControl
            component="fieldset"
            error={!!error}
            sx={{ width: "100%" }}
          >
            {label && (
              <Typography
                sx={{
                  fontSize: "14px",
                  fontWeight: "700",
                  lineHeight: "18.2px",
                  letterSpacing: "0.02em",
                  color: "text.secondary",
                  marginBottom: "10px",
                }}
              >
                {label}
              </Typography>
            )}
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                borderRadius: "8px",
                border: "1px solid var(--border-default)",
                padding: "8px",
              }}
            >
              {choices.map((choice, index) => (
                <FormControlLabel
                  key={`${choice}-${index}`}
                  control={
                    <Checkbox
                      size="small"
                      disableRipple
                      checked={arr.includes(choice)}
                      onChange={(e) => {
                        if (e.target.checked) field.onChange([...arr, choice]);
                        else field.onChange(arr.filter((c) => c !== choice));
                      }}
                    />
                  }
                  label={renderLabel ? renderLabel(choice) : choice}
                />
              ))}
            </Box>
            {error && <FormHelperText>{error.message}</FormHelperText>}
          </FormControl>
        );
      }}
    />
  );
};

const AllInputField = ({ label, ...rest }) => {
  return (
    <Box sx={{ width: "100%" }}>
      {label && (
        <Typography
          sx={{
            fontSize: "14px",
            fontWeight: "700",
            lineHeight: "18.2px",
            letterSpacing: "0.02em",
            color: "text.secondary",
            marginBottom: "10px",
          }}
        >
          {label}
        </Typography>
      )}
      <FormTextFieldV2
        {...rest}
        fullWidth
        hiddenLabel
        sx={{ border: "1px solid var(--border-default)", borderRadius: "8px" }}
      />
    </Box>
  );
};

const AllSelectField = ({ label, ...rest }) => {
  return (
    <Box sx={{ width: "100%" }}>
      {label && (
        <Typography
          sx={{
            fontSize: "14px",
            fontWeight: "700",
            lineHeight: "18.2px",
            letterSpacing: "0.02em",
            color: "text.secondary",
            marginBottom: "10px",
          }}
        >
          {label}
        </Typography>
      )}
      <FormSelectField
        {...rest}
        fullWidth
        sx={{
          backgroundColor: PANEL_TINT_BG,
          "& .MuiOutlinedInput-root": {
            "&:hover .MuiOutlinedInput-notchedOutline": {
              border: "1px solid var(--border-default)",
            },
            "&.Mui-focused .MuiOutlinedInput-notchedOutline": {
              border: "1px solid var(--border-default)",
            },
          },
          "& .MuiOutlinedInput-notchedOutline": {
            border: "1px solid var(--border-default)",
          },
          "& .MuiSelect-select": {
            border: "1px solid var(--border-default)",
          },
        }}
      />
    </Box>
  );
};

const RadioField = ({
  control,
  fieldName,
  label,
  options,
  groupSx = {},
  ...other
}) => {
  return (
    <Controller
      render={({ field, fieldState: { error } }) => (
        <FormControl component="fieldset" error={!!error}>
          {label && (
            <Typography
              sx={{
                fontSize: "14px",
                fontWeight: "700",
                lineHeight: "18.2px",
                letterSpacing: "0.02em",
                color: "text.secondary",
                marginBottom: "10px",
              }}
            >
              {label}
            </Typography>
          )}
          <RadioGroup
            {...field}
            aria-labelledby={label || "label"}
            {...other}
            sx={{
              borderRadius: "8px",
              border: "1px solid var(--border-default)",
              padding: "10px",
              gap: "12px",
              ...groupSx,
            }}
          >
            {options.map((option) => (
              <FormControlLabel
                key={option.value}
                value={option.value}
                control={<Radio />}
                label={option.label}
                sx={{
                  alignItems: "start",
                  "& .MuiRadio-root	": {
                    marginTop: "-6px",
                  },
                }}
              />
            ))}
          </RadioGroup>
          {error && <FormHelperText>{error.message}</FormHelperText>}
        </FormControl>
      )}
      control={control}
      name={fieldName}
    />
  );
};

AddEvaluationFeeback.propTypes = {
  module: PropTypes.oneOf(["dataset", "experiment"]),
  onRefreshGrid: PropTypes.func,
};

EvaluationFeeback.propTypes = {
  onClose: PropTypes.func,
  data: PropTypes.object,
  refreshGrid: PropTypes.func,
  existingFeedback: PropTypes.object,
  isExperimentModule: PropTypes.bool,
};

FeedBackForm.propTypes = {
  control: PropTypes.any,
  data: PropTypes.object,
  feedbackData: PropTypes.object,
  outputType: PropTypes.string,
  isMulti: PropTypes.bool,
};

ChoiceCheckboxGroup.propTypes = {
  control: PropTypes.any,
  label: PropTypes.string,
  choices: PropTypes.array,
  renderLabel: PropTypes.func,
};

AllInputField.propTypes = {
  label: PropTypes.string,
};

AllSelectField.propTypes = {
  label: PropTypes.string,
};

RadioField.propTypes = {
  control: PropTypes.any,
  fieldName: PropTypes.string.isRequired,
  helperText: PropTypes.any,
  label: PropTypes.string || undefined,
  groupSx: PropTypes.object,
  options: PropTypes.arrayOf(
    PropTypes.shape({ label: PropTypes.string, value: PropTypes.string }),
  ),
};

const Label1 = () => {
  return (
    <Box>
      <Typography sx={{ fontSize: "14px", fontWeight: "600" }}>
        Re-tune
      </Typography>
      <Typography sx={{ fontSize: "12px" }}>
        We’ll create a new version of this metric and use it in all future
        invocations
      </Typography>
    </Box>
  );
};

const Label2 = () => {
  return (
    <Box>
      <Typography sx={{ fontSize: "14px", fontWeight: "600" }}>
        Re-calculate for this row
      </Typography>
      <Typography sx={{ fontSize: "12px" }}>
        We’ll create a new version of this metric and use it in all future
        invocations. We’ll also recalculate it on this row. This might take a
        while.
      </Typography>
    </Box>
  );
};
const Label3 = () => {
  return (
    <Box>
      <Typography sx={{ fontSize: "14px", fontWeight: "600" }}>
        Re-tune and re-calculate for this dataset
      </Typography>
      <Typography sx={{ fontSize: "12px" }}>
        We’ll create a new version of this metric and use it in all future
        invocations. We’ll also recalculate it on every run in this dataset.
        This might take a while.
      </Typography>
    </Box>
  );
};

// Static (label components take no props) — hoisted so it isn't re-allocated
// and handed fresh to the child's `options` prop on every render.
const RETUNE_OPTIONS = [
  { label: <Label1 />, value: "retune" },
  { label: <Label2 />, value: "recalculate_row" },
  { label: <Label3 />, value: "recalculate_dataset" },
];
