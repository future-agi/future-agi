import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Drawer,
  IconButton,
  LinearProgress,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useQuery } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import EvalFeedbackEntryStage from "./EvalFeedbackEntryStage";
import EvalFeedbackActionStage from "./EvalFeedbackActionStage";
import useEvalFeedbackFlow from "./hooks/useEvalFeedbackFlow";
import { STAGE } from "./constants";

// Headless eval-feedback drawer. Owns the drawer chrome, the template fetch,
// the existing-feedback fetch, and the form submit button. Stages and form
// state come from useEvalFeedbackFlow. Surface-specific work (endpoints,
// payload shapes, analytics, post-submit cache invalidation) is delegated to
// the wrapper via props.

const paperSx = {
  height: "100vh",
  position: "fixed",
  zIndex: 9999,
  borderRadius: "10px",
  backgroundColor: "background.paper",
};

const formSx = {
  padding: "20px",
  display: "flex",
  flexDirection: "column",
  gap: 2,
  height: "100%",
  width: "550px",
};

const headerSx = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
};

const EvalFeedbackDrawer = ({
  open,
  onClose,
  target,
  fetchExistingFeedback,
  existingFeedbackQueryKey,
  fetchTemplate,
  templateQueryKey,
  submitEntry,
  submitAction,
  onAnalyticsEntrySubmit,
  onSubmitted,
  retuneOptions,
}) => {
  const {
    data: existingFeedback,
    isLoading: isLoadingExisting,
  } = useQuery({
    queryKey: existingFeedbackQueryKey,
    queryFn: fetchExistingFeedback,
    enabled: Boolean(open && fetchExistingFeedback),
  });

  const { data: templateData } = useQuery({
    queryKey: templateQueryKey,
    queryFn: fetchTemplate,
    enabled: Boolean(open && fetchTemplate),
    refetchOnMount: true,
  });

  const {
    stage,
    entryControl,
    actionControl,
    actionValueField,
    onEntrySubmit,
    onActionSubmit,
    isSubmittingEntry,
    isSubmittingAction,
  } = useEvalFeedbackFlow({
    existingFeedback,
    submitEntry,
    submitAction,
    onAnalyticsEntrySubmit,
    onSubmitted,
    onClose,
  });

  const onStageSubmit =
    stage === STAGE.ACTION ? onActionSubmit : onEntrySubmit;

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{ sx: paperSx }}
      ModalProps={{
        BackdropProps: { style: { backgroundColor: "transparent" } },
      }}
    >
      {isLoadingExisting && (
        <Box sx={{ minWidth: "550px" }}>
          <LinearProgress />
        </Box>
      )}
      {!isLoadingExisting && (
        <Box sx={{ display: "flex", height: "100vh" }}>
          <Box sx={formSx} component="form" onSubmit={onStageSubmit}>
            <Box sx={headerSx}>
              <Typography fontWeight={700} color="text.primary">
                Add feedback
              </Typography>
              <IconButton onClick={onClose} size="small">
                <Iconify icon="mingcute:close-line" />
              </IconButton>
            </Box>
            <div style={{ borderBottom: "1px solid var(--border-light)" }} />

            {stage === STAGE.ACTION ? (
              <EvalFeedbackActionStage
                control={actionControl}
                target={target}
                retuneOptions={retuneOptions}
              />
            ) : (
              <EvalFeedbackEntryStage
                control={entryControl}
                target={target}
                templateData={templateData}
              />
            )}

            <Box>
              <LoadingButton
                variant="contained"
                color="primary"
                type="submit"
                fullWidth
                size="small"
                disabled={stage === STAGE.ACTION && !actionValueField}
                loading={isSubmittingEntry || isSubmittingAction}
              >
                {stage === STAGE.ACTION ? "Continue" : "Submit feedback"}
              </LoadingButton>
            </Box>
          </Box>
        </Box>
      )}
    </Drawer>
  );
};

EvalFeedbackDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  target: PropTypes.object,
  fetchExistingFeedback: PropTypes.func.isRequired,
  existingFeedbackQueryKey: PropTypes.array.isRequired,
  fetchTemplate: PropTypes.func.isRequired,
  templateQueryKey: PropTypes.array.isRequired,
  submitEntry: PropTypes.func.isRequired,
  submitAction: PropTypes.func.isRequired,
  onAnalyticsEntrySubmit: PropTypes.func,
  onSubmitted: PropTypes.func,
  retuneOptions: PropTypes.array.isRequired,
};

export default EvalFeedbackDrawer;
