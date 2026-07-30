import React, { useState, useMemo, useCallback } from "react";
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  IconButton,
  Radio,
  RadioGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
// purple import removed - using theme tokens for dark mode support
import FeedbackSubmissionModal from "./submit-feedback-form";
import { LoadingButton } from "@mui/lab";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import logger from "src/utils/logger";
import CellMarkdown from "src/sections/common/CellMarkdown";

// Constants
const OUTPUT_TYPES = {
  TEXT: "text",
  BOOL: "bool",
  FLOAT: "float",
  INT: "int",
  STR_LIST: "str_list",
};

const RADIO_VALUES = {
  PASSED: "passed",
  FAILED: "failed",
};

const ACTION_TYPES = {
  RETUNE: "retune",
  RECALCULATE: "recalculate",
};

// Validation schema
const createValidationSchema = () =>
  z.object({
    actionType: z.enum([ACTION_TYPES.RETUNE, ACTION_TYPES.RECALCULATE]),
    feedbackValue: z.union([
      z.string().min(1, "Feedback value is required"),
      z.boolean(),
      z.number(),
      z.array(z.string()).min(1, "At least one choice must be selected"),
    ]),
    feedbackExplanation: z.string().min(1, "Feedback improvement is required"),
  });

const AddFeedbackForm = (props) => {
  const { open, onClose, selectedAddFeedback } = props;
  const [feedbackId, setFeedbackId] = useState("");
  const [openSubmissionModal, setOpenSubmissionModal] = useState(false);

  // Memoized derived values
  const derivedValues = useMemo(() => {
    const feedbackExplanation = selectedAddFeedback?.explanation;
    const list = selectedAddFeedback?.id?.split("**");
    const customEvalConfigId = list?.[0];
    const observationSpanId = list?.[1];
    const outputType = selectedAddFeedback?.outputType ?? OUTPUT_TYPES.TEXT;

    return {
      feedbackExplanation,
      customEvalConfigId,
      observationSpanId,
      outputType,
    };
  }, [selectedAddFeedback]);

  const {
    feedbackExplanation,
    customEvalConfigId,
    observationSpanId,
    outputType,
  } = derivedValues;

  // Form setup
  const { control, reset, handleSubmit, watch } = useForm({
    defaultValues: {
      actionType: ACTION_TYPES.RETUNE,
      feedbackValue: outputType === OUTPUT_TYPES.STR_LIST ? [] : "",
      feedbackExplanation: "",
    },
    resolver: zodResolver(createValidationSchema()),
  });

  // Mutations and queries
  const { mutate: handleSubmitForm, isPending: isLoading } = useMutation({
    mutationFn: (formData) =>
      axios.post(endpoints.project.submitFeedback, formData),
    onSuccess: (data) => {
      setFeedbackId(
        data?.data?.result?.feedback_id || data?.data?.result?.feedbackId || "",
      );
      reset();
      onClose();
      setOpenSubmissionModal(true);
    },
    onError: (error) => {
      logger.error("Failed to submit feedback:", error);
      // You might want to show an error toast here
    },
  });

  // Only fetch feedback data when outputType is 'str_list'
  const { data: feedbackDatas, error: feedbackError } = useQuery({
    queryKey: ["fetchFeedbackDetails", customEvalConfigId],
    queryFn: () =>
      axios.get(
        endpoints.develop.eval.getFeedbackTemplateTrace(customEvalConfigId),
      ),
    select: (d) => d?.data?.config,
    refetchOnMount: true,
    enabled: outputType === OUTPUT_TYPES.STR_LIST && !!customEvalConfigId,
    retry: 2,
    staleTime: 5 * 60 * 1000, // 5 minutes
  });

  // Watch form values
  const feedbackValue = watch("feedbackValue");
  const feedbackImprovement = watch("feedbackExplanation");

  // Memoized submit button state
  const isSubmitEnabled = useMemo(() => {
    if (!feedbackImprovement) return false;

    if (outputType === OUTPUT_TYPES.STR_LIST) {
      return Array.isArray(feedbackValue) && feedbackValue.length > 0;
    }

    return Boolean(feedbackValue);
  }, [feedbackImprovement, feedbackValue, outputType]);

  // Event handlers
  const handleFormSubmit = useCallback(
    (data) => {
      let processedFeedbackValue = data.feedbackValue;

      // For str_list, stringify the array of strings
      if (
        outputType === OUTPUT_TYPES.STR_LIST &&
        Array.isArray(data.feedbackValue)
      ) {
        processedFeedbackValue = JSON.stringify(data.feedbackValue);
      }

      const formData = {
        observation_span_id: observationSpanId,
        feedback_value: processedFeedbackValue,
        feedback_explanation: feedbackExplanation,
        feedback_improvement: data.feedbackExplanation,
        custom_eval_config_id: customEvalConfigId,
      };

      handleSubmitForm(formData);
    },
    [
      outputType,
      observationSpanId,
      feedbackExplanation,
      customEvalConfigId,
      handleSubmitForm,
    ],
  );

  const handleClose = useCallback(() => {
    onClose();
    reset();
  }, [onClose, reset]);

  const handleCloseSubmissionModal = useCallback(() => {
    setOpenSubmissionModal(false);
  }, []);

  // Render helpers
  const renderFeedbackValueInput = () => {
    switch (outputType) {
      case OUTPUT_TYPES.TEXT:
        return (
          <FormTextFieldV2
            fieldName="feedbackValue"
            control={control}
            fullWidth
            multiline
            placeholder="Write a right value here"
            helperText=""
            sx={{
              "& .MuiOutlinedInput-root": {
                backgroundColor: "action.hover",
              },
            }}
            rows={4}
          />
        );

      case OUTPUT_TYPES.BOOL:
        return (
          <Controller
            name="feedbackValue"
            control={control}
            render={({ field }) => (
              <FormControl fullWidth>
                <RadioGroup
                  {...field}
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    p: 2,
                  }}
                >
                  <FormControlLabel
                    value={RADIO_VALUES.PASSED}
                    control={<Radio />}
                    label="Passed"
                  />
                  <FormControlLabel
                    value={RADIO_VALUES.FAILED}
                    control={<Radio />}
                    label="Failed"
                  />
                </RadioGroup>
              </FormControl>
            )}
          />
        );

      case OUTPUT_TYPES.FLOAT:
      case OUTPUT_TYPES.INT:
        return (
          <FormTextFieldV2
            fieldName="feedbackValue"
            control={control}
            fullWidth
            type="number"
            placeholder="Add Number"
            helperText=""
            sx={{
              "& .MuiOutlinedInput-root": {
                backgroundColor: "action.hover",
                height: "38px",
              },
              "& .MuiInputBase-input": {
                height: "100%",
                boxSizing: "border-box",
              },
            }}
            inputProps={{
              step: outputType === OUTPUT_TYPES.FLOAT ? "0.01" : "1",
            }}
          />
        );

      case OUTPUT_TYPES.STR_LIST:
        if (feedbackError) {
          return (
            <Typography color="error" variant="body2">
              Failed to load choices. Please try again.
            </Typography>
          );
        }

        if (!feedbackDatas?.choices || feedbackDatas.choices.length === 0) {
          return (
            <Typography variant="body2" sx={{ color: "text.secondary" }}>
              No choices available
            </Typography>
          );
        }

        return (
          <Controller
            name="feedbackValue"
            control={control}
            render={({ field }) => (
              <FormControl fullWidth>
                <Box
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    p: 2,
                  }}
                >
                  {feedbackDatas.choices.map((choice, index) => (
                    <div key={`${choice}-${index}`}>
                      <FormControlLabel
                        control={
                          <Checkbox
                            checked={field.value?.includes(choice) || false}
                            onChange={(e) => {
                              const currentValues = field.value || [];
                              if (e.target.checked) {
                                // Add choice to array
                                field.onChange([...currentValues, choice]);
                              } else {
                                // Remove choice from array
                                field.onChange(
                                  currentValues.filter(
                                    (item) => item !== choice,
                                  ),
                                );
                              }
                            }}
                          />
                        }
                        label={choice}
                      />
                    </div>
                  ))}
                </Box>
              </FormControl>
            )}
          />
        );

      default:
        return null;
    }
  };

  return (
    <Box>
      <Dialog
        open={open}
        onClose={handleClose}
        PaperProps={{
          sx: {
            width: "616px",
            maxWidth: "70%",
            maxHeight: "85vh",
            overflow: "auto",
          },
        }}
      >
        <Box sx={{ mt: -1.5 }}>
          <IconButton
            onClick={handleClose}
            sx={{
              position: "absolute",
              top: 5,
              right: 5,
              color: "grey",
              zIndex: 1,
            }}
          >
            <Iconify icon="eva:close-fill" width={24} color="text.primary" />
          </IconButton>
          <DialogTitle
            sx={{
              paddingBottom: 0,
              paddingLeft: 2,
            }}
          >
            <Typography variant="m3">Feedbacks of Auto Learning</Typography>
          </DialogTitle>

          <DialogContent sx={{ px: 2 }}>
            {/* Subheading */}
            <Typography
              variant="s1"
              sx={{
                color: "text.secondary",
                display: "flex",
                alignItems: "center",
                mb: 2,
              }}
            >
              Help us refine evals. Share my issues and we&rsquo;ll use your
              feedback to improve it automatically.
            </Typography>

            {/* Explanation Heading */}
            <Typography
              sx={{
                fontWeight: 500,
                mb: 1,
                color: "text.primary",
                fontSize: "14px",
              }}
            >
              Explanation
            </Typography>
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                p: 2,
                marginBottom: 2,
              }}
            >
              <Typography variant="body2" sx={{ color: "text.primary" }}>
                <CellMarkdown
                  spacing={0}
                  text={selectedAddFeedback?.explanation}
                />
              </Typography>
            </Box>

            {/* Write a Right Value */}
            <Typography
              sx={{
                fontWeight: 500,
                mb: 1,
                color: "text.primary",
                fontSize: "14px",
              }}
            >
              {outputType === OUTPUT_TYPES.BOOL ||
              outputType === OUTPUT_TYPES.STR_LIST
                ? "Choose a right value"
                : "Write a right value"}
            </Typography>

            {/* Conditional rendering based on outputType */}
            {renderFeedbackValueInput()}

            {/* What Would You Like to Improve? */}
            <Typography
              sx={{
                fontWeight: 500,
                mb: 1,
                mt: 1,
                color: "text.primary",
                fontSize: "14px",
              }}
            >
              What would you like to improve
            </Typography>
            <FormTextFieldV2
              fieldName="feedbackExplanation"
              control={control}
              fullWidth
              multiline
              placeholder="What was wrong with the original explanation? Please be specific as possible in your argument"
              helperText=""
              sx={{
                "& .MuiOutlinedInput-root": {
                  backgroundColor: "action.hover",
                },
              }}
              rows={8}
            />
          </DialogContent>
          <DialogActions
            sx={{
              display: "flex",
              justifyContent: "space-evenly",
              mt: 1.5,
            }}
          >
            <Button
              fullWidth
              sx={{ height: "30px", fontSize: "12px", fontWeight: 500 }}
              variant="outlined"
              onClick={handleClose}
            >
              Cancel
            </Button>
            <LoadingButton
              fullWidth
              color="primary"
              variant="contained"
              loading={isLoading}
              disabled={!isSubmitEnabled}
              sx={{
                height: "30px",
                backgroundColor: "primary.main",
                fontSize: "12px",
                fontWeight: 500,
              }}
              onClick={handleSubmit(handleFormSubmit)}
            >
              Submit Feedback
            </LoadingButton>
          </DialogActions>
        </Box>
      </Dialog>

      {/* Feedback Submission Modal */}
      <FeedbackSubmissionModal
        open={openSubmissionModal}
        onClose={handleCloseSubmissionModal}
        feedbackType="QA_Correctness"
        observeId={observationSpanId}
        customEvalConfigId={customEvalConfigId}
        feedbackId={feedbackId}
      />
    </Box>
  );
};

AddFeedbackForm.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  selectedAddFeedback: PropTypes.object,
  onSubmit: PropTypes.func,
  evalId: PropTypes.any,
};

export default AddFeedbackForm;
