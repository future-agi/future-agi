import React, { memo, useCallback, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogContent,
  DialogActions,
  Typography,
  IconButton,
  Radio,
  RadioGroup,
  FormControlLabel,
  FormControl,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { palette } from "src/theme/palette";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { useSnackbar } from "src/components/snackbar";
import logger from "src/utils/logger";

// Constants moved outside component to prevent recreation on each render
const FEEDBACK_OPTIONS = {
  RETUNE: "retune",
  RECALCULATE: "recalculate",
};

const DIALOG_PAPER_STYLES = {
  maxWidth: 720,
  borderRadius: 2,
  // eslint-disable-next-line no-restricted-syntax -- ds: overlay shadow
  boxShadow: "0px 8px 24px rgba(0, 0, 0, 0.15)",
};

const CLOSE_BUTTON_STYLES = {
  position: "absolute",
  right: 5,
  top: 5,
  color: "text.secondary",
};

const RADIO_STYLES = {
  "&:hover": {
    backgroundColor: "transparent",
  },
  marginTop: "-6px",
  mr: 0.7,
};

const CANCEL_BUTTON_STYLES = {
  fontSize: "12px",
  height: "30px",
  borderColor: palette("light").whiteScale["500"],
  color: "text.primary",
  fontWeight: 500,
  textTransform: "none",
  borderRadius: "8px",
};

const APPLY_BUTTON_STYLES = {
  fontSize: "12px",
  bgcolor: "primary.main",
  color: "primary.contrastText",
  "&:hover": {
    bgcolor: "primary.dark",
  },
  fontWeight: 500,
  height: "30px",
  textTransform: "none",
  borderRadius: "8px",
};

const FeedbackSubmissionModal = memo(
  ({
    open,
    onClose,
    feedbackType = "QA_Correctness",
    customEvalConfigId,
    observeId,
    feedbackId,
  }) => {
    const [selectedOption, setSelectedOption] = useState(
      FEEDBACK_OPTIONS.RETUNE,
    );
    const { enqueueSnackbar } = useSnackbar();

    // Memoized callbacks to prevent unnecessary re-renders
    const handleOptionChange = useCallback((event) => {
      setSelectedOption(event.target.value);
    }, []);

    const handleCancel = useCallback(() => {
      onClose();
    }, [onClose]);

    const { mutate: handleSubmitForm, isPending: isLoading } = useMutation({
      mutationFn: useCallback(
        (formData) =>
          axios.post(endpoints.project.applySubmitFeedback, formData),
        [],
      ),
      onSuccess: useCallback(() => {
        onClose();
        enqueueSnackbar("Your feedback has been applied", {
          variant: "success",
        });
      }, [onClose, enqueueSnackbar]),
      onError: useCallback(
        (error) => {
          logger.error("Feedback submission failed:", error);
          enqueueSnackbar("Failed to apply feedback. Please try again.", {
            variant: "error",
          });
        },
        [enqueueSnackbar],
      ),
    });

    const handleApply = useCallback(() => {
      if (!observeId || !customEvalConfigId) {
        enqueueSnackbar("Missing required parameters", {
          variant: "error",
        });
        return;
      }

      const formData = {
        observation_span_id: observeId,
        custom_eval_config_id: customEvalConfigId,
        action_type: selectedOption,
        feedback_id: feedbackId,
      };

      handleSubmitForm(formData);
    }, [
      observeId,
      customEvalConfigId,
      selectedOption,
      handleSubmitForm,
      enqueueSnackbar,
      feedbackId,
    ]);

    const RetuneOption = memo(() => (
      <FormControlLabel
        value={FEEDBACK_OPTIONS.RETUNE}
        control={<Radio sx={RADIO_STYLES} />}
        label={
          <Box>
            <Typography variant="s1" sx={{ fontWeight: 500 }}>
              Re-tune
            </Typography>
            <Typography
              variant="body2"
              sx={{ color: "text.secondary", mt: 0.2 }}
            >
              We&apos;ll create a new version of this metric and use it in all
              future invocations
            </Typography>
          </Box>
        }
        sx={{ mb: 3, alignItems: "flex-start" }}
      />
    ));
    RetuneOption.displayName = "RetuneOption";

    const RecalculateOption = memo(() => (
      <FormControlLabel
        value={FEEDBACK_OPTIONS.RECALCULATE}
        control={<Radio sx={RADIO_STYLES} />}
        label={
          <Box>
            <Typography variant="s1" sx={{ fontWeight: 500 }}>
              Re-calculate for this trace
            </Typography>
            <Typography
              variant="body2"
              sx={{ color: "text.secondary", mt: 0.2 }}
            >
              We&apos;ll create a new version of this metric and use it in all
              future invocations. We&apos;ll also recalculate it on this span.
              This might take a while.
            </Typography>
          </Box>
        }
        sx={{ alignItems: "flex-start" }}
      />
    ));
    RecalculateOption.displayName = "RecalculateOption";

    if (!open) return null;

    return (
      <Dialog
        open={open}
        onClose={onClose}
        maxWidth={false}
        fullWidth
        PaperProps={{ sx: DIALOG_PAPER_STYLES }}
        disablePortal={false}
        keepMounted={false}
      >
        <Box sx={{ position: "relative", py: 1.7 }}>
          <IconButton
            aria-label="close"
            onClick={handleCancel}
            sx={CLOSE_BUTTON_STYLES}
          >
            <Iconify
              icon="solar:close-linear"
              width={24}
              color="text.primary"
            />
          </IconButton>

          <Typography sx={{ fontWeight: 600, fontSize: "16px", mb: 2, px: 2 }}>
            Your feedback is submitted
          </Typography>

          <DialogContent sx={{ px: 2 }}>
            <Box
              sx={{
                bgcolor: "background.default",
                p: 1.3,
                borderRadius: 1,
                mb: 1,
              }}
            >
              <Typography
                variant="s1"
                sx={{ fontWeight: 500, mb: 1, color: "text.secondary" }}
              >
                {feedbackType}
              </Typography>
              <Box>
                <Typography variant="s3">
                  Measure whether the LLM&apos;s response is supported by (or
                  backed in) the context provided.
                </Typography>
                <Typography variant="s2">
                  Context Adherence can be auto improved via continuous learning
                  from human feedback. Give feedback via hovering over any
                  metric value that seems to be incorrect. Future AGI will
                  automatically incorporate your feedback into the metric.
                </Typography>
              </Box>
            </Box>

            <Box sx={{ mt: 1 }}>
              <Typography variant="s1" sx={{ fontWeight: 500, mb: 2 }}>
                Select one of the options
              </Typography>

              <FormControl component="fieldset" sx={{ mt: 2, ml: 0.2 }}>
                <RadioGroup
                  aria-label="feedback-options"
                  name="feedback-options"
                  value={selectedOption}
                  onChange={handleOptionChange}
                >
                  <RetuneOption />
                  <RecalculateOption />
                </RadioGroup>
              </FormControl>
            </Box>
          </DialogContent>

          <DialogActions sx={{ p: 0, px: 2, mt: 6, mb: 0.5 }}>
            <Button
              onClick={handleCancel}
              variant="outlined"
              fullWidth
              sx={CANCEL_BUTTON_STYLES}
              disabled={isLoading}
            >
              Cancel
            </Button>
            <LoadingButton
              onClick={handleApply}
              variant="contained"
              loading={isLoading}
              fullWidth
              sx={APPLY_BUTTON_STYLES}
            >
              Apply
            </LoadingButton>
          </DialogActions>
        </Box>
      </Dialog>
    );
  },
);

FeedbackSubmissionModal.displayName = "FeedbackSubmissionModal";

FeedbackSubmissionModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  feedbackType: PropTypes.string,
  observeId: PropTypes.string.isRequired,
  customEvalConfigId: PropTypes.oneOfType([PropTypes.string, PropTypes.number])
    .isRequired,
  feedbackId: PropTypes.string,
};

export default FeedbackSubmissionModal;
