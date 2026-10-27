import { zodResolver } from "@hookform/resolvers/zod";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  FormHelperText,
  IconButton,
  Radio,
  RadioGroup,
  Stack,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useEffect, useRef, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { FormCheckboxField } from "src/components/FormCheckboxField";
import { FormSelectField } from "src/components/FormSelectField";
import Iconify from "src/components/iconify";
import { endpoints } from "src/utils/axios";
import axios from "src/utils/axios";
import {
  AddFeedbackValidationSchema,
  feedbackSubmittedValidationSchema,
} from "../AddEvaluationFeeback/validation";
import { enqueueSnackbar } from "notistack";
import { LoadingButton } from "@mui/lab";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const AllInputField = ({ label, ...rest }) => {
  return (
    <Box sx={{ width: "100%", display: "flex", flexDirection: "column" }}>
      {label && (
        <Typography
          variant="s1"
          fontWeight={"fontWeightMedium"}
          color={"text.primary"}
          sx={{
            marginBottom: "8px",
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
          variant="s1"
          fontWeight={"fontWeightMedium"}
          color={"text.primary"}
          sx={{
            marginBottom: "8px",
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

const Label1 = () => {
  return (
    <Box>
      <Typography sx={{ fontSize: "14px", fontWeight: "600" }}>
        Re-tune
      </Typography>
      <Typography sx={{ fontSize: "12px" }}>
        We&apos;ll create a new version of this metric and use it in all future
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
        We&apos;ll create a new version of this metric and use it in all future
        invocations. We&apos;ll also recalculate it on this row. This might take
        a while.
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
        We&apos;ll create a new version of this metric and use it in all future
        invocations. We&apos;ll also recalculate it on every run in this
        dataset. This might take a while.
      </Typography>
    </Box>
  );
};

const RadioField = ({ control, fieldName, label, options, ...other }) => {
  return (
    <Controller
      render={({ field, fieldState: { error } }) => (
        <FormControl component="fieldset" error={!!error}>
          {label && (
            <Typography
              variant="s1"
              fontWeight={"fontWeightMedium"}
              color={"text.primary"}
              sx={{
                marginBottom: "8px",
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
            {options?.map((option) => (
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
        variant="s1"
        color={"text.secondary"}
        fontWeight={"fontWeightRegular"}
      >
        Help us refine Context Adherence. Share any issues, and we’ll use your
        feedback to improve it automatically.
      </Typography>

      <Stack direction={"column"} gap={"8px"}>
        <Typography
          variant="s1"
          fontWeight={"fontWeightMedium"}
          color={"text.primary"}
        >
          Explanation
        </Typography>
        <Typography
          sx={{
            fontSize: "14px",
            fontWeight: "400",
            lineHeight: "21px",
            padding: "16px",
            borderRadius: "8px",
            border: "1px solid var(--border-default)",
            backgroundColor: "rgba(147, 143, 163, 0.08)",
          }}
        >
          {data?.valueInfos?.reason}
        </Typography>
      </Stack>

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
      {feedbackData?.dataType === "choice" && (
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          {[
            { option: "option1", value: "option1" },
            { option: "option2", value: "option2" },
          ].map((item, index) => (
            <FormCheckboxField
              key={index}
              label={item.option}
              control={control}
              fieldName={item.value}
              helperText=""
            />
          ))}
        </Box>
      )}

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
          {data?.eval_name}
        </Typography>
        <Typography sx={{ fontSize: "12px" }}>
          {data?.valueInfos?.reason}
        </Typography>
        <Typography sx={{ fontSize: "12px", fontWeight: "600" }}>
          1 row received your feedback.
        </Typography>
      </Box>
      <Typography
        variant="s1"
        fontWeight={"fontWeightMedium"}
        color={"text.primary"}
      >
        Select one of the options
      </Typography>
      <RadioField
        sx={{
          rowGap: "16px",
        }}
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

export default function CompareDatasetEvalsAddFeedbackForm({
  evalDetail,
  onClose,
  open,
}) {
  const queryClient = useQueryClient();
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);

  const { data: existingFeedback } = useQuery({
    queryKey: [
      "fetch-feedback-details",
      evalDetail?.sourceId,
      evalDetail?.cellRowId,
    ],
    queryFn: () =>
      axios.get(endpoints.develop.eval.getFeedbackDetails, {
        params: {
          user_eval_metric_id: evalDetail?.sourceId,
          row_id: evalDetail?.cellRowId,
        },
      }),
    enabled: !!evalDetail?.sourceId && !!evalDetail?.cellRowId && !!open,
    select: (d) => d.data?.result?.feedback?.[0],
  });
  const existingFeedbackId = existingFeedback?.id;
  const newFeedback = useRef(null);

  // Mutation for submitting feedback
  const {
    mutate: submitFeedback,
    isPending: isSubmitting,
    data: submittedData,
    reset: resetSubmittedFeedbackData,
  } = useMutation({
    mutationFn: (formData) =>
      axios.post(endpoints.develop.eval.addFeedback, formData),
    onSuccess: () => {
      reset(); // Reset the form after successful submission
      queryClient.invalidateQueries({
        queryKey: [
          "fetch-feedback-details",
          evalDetail?.sourceId,
          evalDetail?.cellRowId,
        ],
      });
      setFeedbackSubmitted(true); // Close the modal
    },
  });

  // Mutation for submitting feedback
  const { mutate: submitFeedbackField, isPending: submittingFields } =
    useMutation({
      mutationFn: (formData) =>
        axios.post(endpoints.develop.eval.updateFeedback, formData),
      onSuccess: () => {
        handleClose();
        enqueueSnackbar("Feedback submitted successfully!", {
          variant: "success",
        });
      },
    });

  // Form submission handler
  const onSubmit = (formData) => {
    const payload = {
      value: formData.value,
      explanation: formData.explanation,
      user_eval_metric: evalDetail.sourceId,
      source: "dataset",
      source_id: evalDetail?.evalId,
      row_id: evalDetail?.cellRowId,
    };
    if (existingFeedbackId) {
      newFeedback.current = payload;
      setFeedbackSubmitted(true);
      return;
    }
    submitFeedback(payload);
  };

  const { data: feedbackData } = useQuery({
    queryKey: ["fetchFeedbackDetails", evalDetail?.sourceId],
    enabled: !!evalDetail?.sourceId && !!open,
    queryFn: () =>
      axios.get(endpoints.develop.eval.getFeedbackTemplate, {
        params: {
          user_eval_metric_id: evalDetail?.sourceId,
        },
      }),
    select: (d) => d.data?.result,
    refetchOnMount: true,
  });

  const { control, handleSubmit, reset, setValue } = useForm({
    defaultValues: getDefaultValues(),
    resolver: zodResolver(AddFeedbackValidationSchema),
  });

  const {
    control: fields,
    handleSubmit: fieldsSubmit,
    watch,
    setValue: setSubmittedFeedBackValue,
  } = useForm({
    defaultValues: {
      value: "",
    },
    resolver: zodResolver(feedbackSubmittedValidationSchema),
  });

  useEffect(() => {
    if (existingFeedback && open) {
      setValue("value", existingFeedback?.value);
      setValue("explanation", existingFeedback?.comment);
      setSubmittedFeedBackValue("value", existingFeedback?.action_type);
    }
  }, [existingFeedback, open, setSubmittedFeedBackValue, setValue]);

  const valueField = watch("value");

  const onFieldSubmit = (formData) => {
    const feedbackId = submittedData?.data?.result?.id || existingFeedbackId;
    const payload = {
      action_type: formData.value,
      user_eval_metric_id: evalDetail?.sourceId,
      feedback_id: feedbackId,
      row_id: evalDetail?.cellRowId,
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

  const handleClose = () => {
    queryClient.invalidateQueries({
      queryKey: [
        "fetch-feedback-details",
        evalDetail?.sourceId,
        evalDetail?.cellRowId,
      ],
    });
    queryClient.invalidateQueries({
      queryKey: ["fetchFeedbackDetails", evalDetail?.sourceId],
    });
    onClose();
    reset();
    resetSubmittedFeedbackData();
    setFeedbackSubmitted(false);
  };

  const dialogTitle = `Feedback for ${evalDetail?.eval_name ?? ""}`;
  return (
    <Dialog
      open={open}
      onClose={() => {
        if (isSubmitting || submittingFields) return;
        handleClose();
      }}
      component={"form"}
      onSubmit={
        feedbackSubmitted ? fieldsSubmit(onFieldSubmit) : handleSubmit(onSubmit)
      }
      PaperProps={{
        sx: {
          width: "500px",
          maxWidth: "none",
          borderRadius: "16px !important",
          padding: "20px !important",
        },
      }}
    >
      <DialogTitle
        sx={{
          padding: 0,
        }}
      >
        <Stack direction={"column"}>
          <Stack
            direction={"row"}
            justifyContent={"space-between"}
            alignItems={"center"}
          >
            <Typography
              color={"text.primary"}
              variant="m3"
              fontWeight={"fontWeightMedium"}
            >
              {dialogTitle}
            </Typography>
            <IconButton
              disabled={isSubmitting || submittingFields}
              onClick={handleClose}
            >
              <Iconify icon="material-symbols:close-rounded" />
            </IconButton>
          </Stack>
          {/* <Typography
            variant="s1"
            fontWeight={"fontWeightRegular"}
            color={"text.secondary"}
          >
            Help us refined evals. Share any issues and we&apos;ll use your
            feedback to improve it automatically
          </Typography> */}
        </Stack>
      </DialogTitle>
      <DialogContent
        sx={{
          padding: 0,
          paddingBottom: "28px",
          display: "flex",
          flexDirection: "column",
          // rowGap: "16px",
        }}
      >
        {/* <Stack direction={"column"} gap={"8px"}>
          <Typography
            variant="s1"
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            Explanation
          </Typography>
          <Box
            sx={{
              border: "1px solid",
              borderRadius: "8px",
              borderColor: "action.hover",
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.primary",
              px: "16px",
            }}
          >
            <Markdown>{evalDetail?.valueInfos?.reason}</Markdown>
          </Box>
        </Stack> */}
        {/* <Stack direction={"column"} gap={"8px"}>
          <Typography
            variant="s1"
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            Write a right value
          </Typography>
          <Box
            sx={{
              border: "1px solid",
              borderRadius: "8px",
              borderColor: "action.hover",
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.primary",
              px: "16px",
            }}
          >
            <Markdown>{evalDetail?.valueInfos?.reason}</Markdown>
          </Box>
        </Stack>
        <Stack direction={"column"} gap={"8px"}>
          <Typography
            variant="s1"
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            What would you like to improve
          </Typography>
          <Box
            sx={{
              border: "1px solid",
              borderRadius: "8px",
              borderColor: "action.hover",
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.primary",
              px: "16px",
            }}
          >
            <Markdown>{evalDetail?.valueInfos?.reason}</Markdown>
          </Box>
        </Stack> */}
        {feedbackSubmitted ? (
          <SubmittedFeedback
            control={fields}
            data={evalDetail}
            feedbackData={feedbackData}
          />
        ) : (
          <FeedBackForm
            control={control}
            data={evalDetail}
            feedbackData={feedbackData}
          />
        )}
      </DialogContent>
      <DialogActions
        sx={{
          padding: 0,
          paddingTop: "8px",
        }}
      >
        <Button
          type="button"
          onClick={handleClose}
          variant="outlined"
          fullWidth
          disabled={isSubmitting || submittingFields}
          sx={{
            "&:hover": {
              borderColor: "divider",
            },
          }}
        >
          <Typography
            variant="s2"
            fontWeight={"fontWeightSemiBold"}
            color={"text.primary"}
          >
            Cancel
          </Typography>
        </Button>
        <LoadingButton
          type="submit"
          variant="contained"
          color="primary"
          fullWidth
          disabled={feedbackSubmitted && !valueField}
          loading={isSubmitting || submittingFields}
        >
          <Typography
            variant="s2"
            fontWeight={"fontWeightMedium"}
            color={"white"}
          >
            {feedbackSubmitted ? "Continue" : "Submit Feedback"}
          </Typography>
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
}

CompareDatasetEvalsAddFeedbackForm.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  evalDetail: PropTypes.object,
};

AllInputField.propTypes = {
  label: PropTypes.string,
};

FeedBackForm.propTypes = {
  control: PropTypes.any,
  data: PropTypes.object,
  feedbackData: PropTypes.object,
};

AllSelectField.propTypes = {
  label: PropTypes.string,
};

SubmittedFeedback.propTypes = {
  control: PropTypes.any,
  data: PropTypes.object,
  feedbackData: PropTypes.object,
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
