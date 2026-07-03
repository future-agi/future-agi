import { zodResolver } from "@hookform/resolvers/zod";
import {
  Box,
  Checkbox,
  Drawer,
  FormControl,
  FormControlLabel,
  FormHelperText,
  IconButton,
  LinearProgress,
  MenuItem,
  Radio,
  RadioGroup,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useRef, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import {
  AddFeedbackValidationSchema,
  feedbackSubmittedValidationSchema,
} from "./validation";
import { LoadingButton } from "@mui/lab";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
// Assuming you are using axios for API requests
import { enqueueSnackbar } from "notistack"; // For success/error notifications
import axios, { endpoints } from "src/utils/axios";
import { FormCheckboxField } from "src/components/FormCheckboxField";
import { FormSelectField } from "src/components/FormSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import CellMarkdown from "src/sections/common/CellMarkdown";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useParams } from "react-router";
import { useAddEvaluationFeebackStore } from "../../states";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";

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
        <Box sx={{ minWidth: "550px" }}>
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

const getDefaultValues = (existingFeedback) => {
  if (existingFeedback) {
    return {
      value: existingFeedback?.value || "",
      explanation: existingFeedback?.comment || "",
    };
  }

  return {
    value: "",
    explanation: "",
  };
};

const EvaluationFeeback = ({
  onClose,
  data,
  refreshGrid,
  existingFeedback,
  isExperimentModule,
}) => {
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);
  const { control, handleSubmit, reset } = useForm({
    defaultValues: getDefaultValues(existingFeedback),
    resolver: zodResolver(AddFeedbackValidationSchema),
  });
  const newFeedback = useRef(null);
  const queryClient = useQueryClient();
  const existingFeedbackId = existingFeedback?.id;
  const { dataset, experimentId } = useParams();
  const metricId = isExperimentModule ? data?.userEvalMetricId : data?.sourceId;
  const rowId = data?.rowData?.row_id;
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

  const {
    control: fields,
    handleSubmit: fieldsSubmit,
    watch,
  } = useForm({
    defaultValues: {
      value: existingFeedback?.action_type || "",
    },
    resolver: zodResolver(feedbackSubmittedValidationSchema),
  });

  const valueField = watch("value");

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

  // Mutation for submitting feedback
  const {
    mutate: submitFeedback,
    isPending: isSubmitting,
    data: submittedData,
  } = useMutation({
    mutationFn: (formData) => axios.post(feedbackEndpoints.create, formData),
    onSuccess: () => {
      enqueueSnackbar("Feedback submitted successfully!", {
        variant: "success",
      });
      reset();
      refreshGrid?.();
      queryClient.invalidateQueries({ queryKey: feedbackQueryKey });
      setFeedbackSubmitted(true); // Close the modal
    },
  });

  // Mutation for submitting feedback
  const { mutate: submitFeedbackField, isPending: submittingFields } =
    useMutation({
      mutationFn: (formData) => axios.post(feedbackEndpoints.submit, formData),
      onSuccess: () => {
        enqueueSnackbar("Feedback submitted successfully!", {
          variant: "success",
        });
        refreshGrid?.();
        reset();
        onClose();
        queryClient.invalidateQueries({ queryKey: feedbackQueryKey });
      },
    });

  // Form submission handler
  const onSubmit = (formData) => {
    const payload = {
      value: formData.value,
      explanation: formData.explanation,
      user_eval_metric: metricId,
      source: isExperimentModule ? "experiment" : "dataset",
      source_id: isExperimentModule ? data.sourceId : data.id,
      row_id: rowId,
    };
    trackEvent(Events.datasetSubmitFeedbackClicked, {
      [PropertyName.datasetId]: dataset,
      [PropertyName.evalId]: data?.sourceId,
      [PropertyName.rowIdentifier]: rowId,
    });
    if (existingFeedbackId) {
      newFeedback.current = payload;
      enqueueSnackbar("Feedback submitted successfully!", {
        variant: "success",
      });
      setFeedbackSubmitted(true);
      return;
    }
    submitFeedback(payload);
  };

  const onFieldSubmit = (formData) => {
    const feedbackId = submittedData?.data?.result?.id || existingFeedbackId;
    const payload = {
      action_type: formData.value,
      user_eval_metric_id: metricId,
      feedback_id: feedbackId,
    };
    if (newFeedback?.current?.value) {
      payload.value = newFeedback?.current?.value;
      payload.explanation = newFeedback?.current?.explanation;
    }
    submitFeedbackField(payload);
  };

  useEffect(() => {
    if (submittedData?.data) {
      setFeedbackSubmitted(true);
    }
  }, [submittedData]);

  return (
    <Box sx={{ display: "flex", height: "100vh" }}>
      <Box
        sx={{
          padding: "20px",
          display: "flex",
          flexDirection: "column",
          gap: 2,
          height: "100%",
          width: "550px",
        }}
        component="form"
        onSubmit={
          feedbackSubmitted
            ? fieldsSubmit(onFieldSubmit)
            : handleSubmit(onSubmit)
        }
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
        {feedbackSubmitted ? (
          <SubmittedFeedback
            control={fields}
            data={data}
            feedbackData={feedbackData}
          />
        ) : (
          <FeedBackForm
            control={control}
            data={data}
            feedbackData={feedbackData}
          />
        )}
        <Box>
          <LoadingButton
            // onSubmit={handleSubmit(onSubmit)}
            variant="contained"
            color="primary"
            type="submit"
            fullWidth
            size="small"
            disabled={feedbackSubmitted && !valueField}
            loading={isSubmitting || submittingFields}
          >
            {feedbackSubmitted ? "Continue" : "Submit feedback"}
          </LoadingButton>
        </Box>
      </Box>
    </Box>
  );
};

const FeedBackForm = ({ control, data, feedbackData }) => {
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
        {data?.name}
      </Typography>
      <Typography
        sx={{
          fontSize: "14px",
          fontWeight: "400",
          lineHeight: "21px",
        }}
      >
        Help us refine Context Adherence. Share any issues, and we’ll use your
        feedback to improve it automatically.
      </Typography>

      <Box
        border={"1px solid var(--border-default)"}
        bgcolor={"rgba(147, 143, 163, 0.08)"}
        borderRadius={1}
        padding={1.5}
      >
        <CellMarkdown spacing={0} text={data?.value_infos?.reason} />
      </Box>

      <div style={{ borderBottom: "1px solid var(--border-light)" }} />
      {feedbackData?.output_type === "reason" && (
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
      )}
      {feedbackData?.output_type === "score" && (
        <AllInputField
          label="Write a right value"
          placeholder="Add Number"
          size="small"
          control={control}
          fieldName="value"
          variant="filled"
          type="number"
          inputProps={{
            min: 0,
            max: 100,
          }}
          helperText="Enter a number between 0 and 100"
        />
      )}
      <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
        {feedbackData?.dataType === "choice" &&
          [
            { option: "option1", value: "option1" },
            { option: "option2", value: "option2" },
          ].map((item, index) => {
            return (
              <FormCheckboxField
                key={index}
                label={item.option}
                control={control}
                fieldName={item.value}
                helperText={""}
              />
            );
          })}
      </Box>
      {(feedbackData?.output_type === "Pass/Fail" ||
        feedbackData?.output_type === "choices") && (
        <RadioField
          label="Select a right value"
          control={control}
          fieldName="value"
          options={feedbackData.choices.map((value) => ({
            label: value,
            value,
          }))}
        />
      )}
      {feedbackData?.output_type === "select" && (
        <AllSelectField
          label="Select a right value"
          control={control}
          options={[{ value: "user", label: "User" }]}
          fieldName=""
          // valueSelector
          // helperText
          fullWidth
          // dropDownMaxHeight
          // onScrollEnd
          // loadingMoreOptions
          // allowClear
        />
      )}

      <AllInputField
        label="What would you like to improve?"
        placeholder="Enter what would you like to improve in the prompt"
        size="small"
        control={control}
        fieldName="explanation"
        variant="filled"
        multiline
        rows={6}
      />
    </Box>
  );
};

const SubmittedFeedback = ({ control, data }) => {
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
        Your feedback is submitted.
      </Typography>
      <Box
        sx={{
          padding: "12px",
          backgroundColor: "background.neutral",
          borderRadius: "12px",
        }}
      >
        <Typography sx={{ fontSize: "14px", fontWeight: "600" }}>
          {data?.name}
        </Typography>
        <Typography sx={{ fontSize: "12px" }}>
          {data?.value_infos?.reason}
        </Typography>
        <Typography sx={{ fontSize: "12px", fontWeight: "600" }}>
          1 row received your feedback.
        </Typography>
      </Box>
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
        fieldName={"value"}
        label={""}
        options={[
          { label: <Label1 />, value: "retune" },
          { label: <Label2 />, value: "recalculate_row" },
          { label: <Label3 />, value: "recalculate_dataset" },
        ]}
      />
    </Box>
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
          backgroundColor: "rgba(147, 143, 163, 0.08)",
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

const CheckboxField = ({ fieldName, label, control, options }) => {
  return (
    <Controller
      render={({ field: { onChange, value }, formState: { errors } }) => (
        <FormControl error={!!errors}>
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
              backgroundColor: "#71707613",
              borderRadius: "8px",
              border: "1px solid var(--border-default)",
            }}
          >
            {options.map((option) => {
              const selected = value.includes(option.value);

              return (
                <MenuItem key={option.value} value={option.value}>
                  <Checkbox
                    size="small"
                    disableRipple
                    checked={selected}
                    onChange={(e) => {
                      onChange({
                        ...e,
                        target: { ...e.target, value: option.value },
                      });
                    }}
                  />
                  {option.label}
                </MenuItem>
              );
            })}
          </Box>
        </FormControl>
      )}
      control={control}
      name={fieldName}
    />
  );
};

const RadioField = ({ control, fieldName, label, options, ...other }) => {
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
            // row={row}
            {...other}
            sx={{
              // backgroundColor: "#71707613",
              borderRadius: "8px",
              border: "1px solid var(--border-default)",
              padding: "10px",
              gap: "12px",
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
                // sx={{
                //   "&:not(:last-of-type)": {
                //     mb: spacing || 0,
                //   },
                //   ...(row && {
                //     mr: 0,
                //     "&:not(:last-of-type)": {
                //       mr: spacing || 2,
                //     },
                //   }),
                // }}
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
};

SubmittedFeedback.propTypes = {
  control: PropTypes.any,
  data: PropTypes.object,
  feedbackData: PropTypes.object,
};

AllInputField.propTypes = {
  label: PropTypes.string,
};

AllSelectField.propTypes = {
  label: PropTypes.string,
};

CheckboxField.propTypes = {
  control: PropTypes.any,
  fieldName: PropTypes.string.isRequired,
  helperText: PropTypes.any,
  label: PropTypes.string || undefined,
  options: PropTypes.arrayOf(
    PropTypes.shape({ label: PropTypes.string, value: PropTypes.string }),
  ),
};

RadioField.propTypes = {
  control: PropTypes.any,
  fieldName: PropTypes.string.isRequired,
  helperText: PropTypes.any,
  label: PropTypes.string || undefined,
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
        Re- calculate for this row
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
