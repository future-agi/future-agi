import React, { useState } from "react";
import PricingDialog from "../rate-limit-modal/UpgradeNowModal";
import { useNavigate, useParams } from "react-router";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "../iconify";
import { ShowComponent } from "../show";
import PropTypes from "prop-types";
import { LoadingButton } from "@mui/lab";

import { ERROR_MESSAGES, OPEN_BILLING_LABEL } from "src/constants/usageCopy";

const FailedEvaluationsDialog = ({ open, onClose, data }) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const [showUpgradeModal, setUpgradeModal] = useState(false);
  const { runId } = useParams();

  const handleAddCredits = () => {
    navigate("/dashboard/settings/billing");
  };

  const { mutate: reRunEval } = useMutation({
    mutationFn: ({ evaluationId, projectVersionId }) =>
      axios.post(endpoints.project.reRunTracerEvalutation, {
        project_version_id: projectVersionId,
        custom_eval_config_id: evaluationId,
      }),
    onSuccess: (_res, variables) => {
      enqueueSnackbar(
        `${variables?.name}  is being re-run, please wait for some time`,
        {
          variant: "info",
        },
      );
    },
  });

  const handleReRunEval = () => {
    const evalData = { ...data };
    onClose();
    reRunEval({
      evaluationId: evalData?.evaluationId,
      projectVersionId: runId,
      name: evalData?.name,
    });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby="failed-evaluations-dialog-title"
    >
      <DialogTitle
        id="failed-evaluations-dialog-title"
        sx={{
          padding: (theme) => theme.spacing(2),
          typography: "m3",
          color: "text.primary",
          fontWeight: "fontWeightBold",
        }}
      >
        <Typography
          variant="m3"
          fontWeight="fontWeightBold"
          color="text.primary"
        >
          {data?.errorTitle}
        </Typography>
        <IconButton
          aria-label="close"
          onClick={onClose}
          sx={{
            position: "absolute",
            right: 8,
            top: 8,
            color: "text.primary",
          }}
        >
          <Iconify icon="oui:cross" sx={{ color: "text.primary" }} />
        </IconButton>
      </DialogTitle>

      <DialogContent
        sx={{
          paddingX: (theme) => theme.spacing(2),
        }}
      >
        <Box flex={1}>
          <Box
            sx={{
              display: "flex",
              gap: (theme) => theme.spacing(1.5),
              padding: (theme) => theme.spacing(1.5),
              backgroundColor: "background.neutral",
              borderRadius: (theme) => theme.spacing(1.5),
              marginBottom: (theme) => theme.spacing(1.5),
            }}
          >
            <Box width={18} height={18}>
              <Iconify
                icon="fluent:warning-24-regular"
                color={data?.errorType == "PARTIAL" ? "orange.400" : "red.500"}
              />
            </Box>

            <Box
              display="flex"
              flexDirection="column"
              gap={(theme) => theme.spacing(0.25)}
            >
              <Typography
                variant="s1"
                fontWeight="fontWeightMedium"
                color="text.secondary"
              >
                {data?.name}
              </Typography>
              <Typography
                variant="s2"
                fontWeight="fontWeightRegular"
                color={theme?.palette?.common?.black}
              >
                {data?.errorMessage || ERROR_MESSAGES.SUPPORT_MESSAGE}
              </Typography>
            </Box>
          </Box>
        </Box>
      </DialogContent>

      <DialogActions sx={{ padding: (theme) => theme.spacing(2) }}>
        <Box display="flex" gap={(theme) => theme.spacing(1)}>
          <Button
            onClick={onClose}
            variant="outlined"
            fullWidth
            size="small"
            sx={{
              paddingY: (theme) => theme.spacing(0.75),
              paddingX: (theme) => theme.spacing(3),
              borderColor: "divider",
              borderRadius: (theme) => theme.spacing(1),
              color: "text.disabled",
              width: "90px",
              typography: "s2",
              fontWeight: "fontWeightMedium",
            }}
          >
            Dismiss
          </Button>
          <ShowComponent condition={data?.showRateLimitButton}>
            <Button
              onClick={() => setUpgradeModal(!showUpgradeModal)}
              variant="contained"
              color="primary"
              fontWeight="fontWeightRegular"
              size="small"
              sx={{
                typography: "s2",
                paddingY: (theme) => theme.spacing(0.75),
                paddingX: (theme) => theme.spacing(3),
              }}
            >
              Upgrade Plan
            </Button>
          </ShowComponent>
          <ShowComponent condition={data?.showCreditLimitMessage}>
            <Button
              onClick={handleAddCredits}
              variant="contained"
              color="primary"
              fontWeight="fontWeightRegular"
              size="small"
              sx={{
                typography: "s2",
                paddingY: (theme) => theme.spacing(0.75),
                paddingX: (theme) => theme.spacing(3),
              }}
            >
              {OPEN_BILLING_LABEL}
            </Button>
          </ShowComponent>
          <ShowComponent condition={data?.showReRunButton}>
            <LoadingButton
              onClick={handleReRunEval}
              variant="contained"
              color="primary"
              fontWeight="fontWeightRegular"
              size="small"
              sx={{
                typography: "s2",
                paddingY: (theme) => theme.spacing(0.75),
                paddingX: (theme) => theme.spacing(3),
              }}
            >
              Re-run Evaluation
            </LoadingButton>
          </ShowComponent>
        </Box>
      </DialogActions>

      <PricingDialog
        open={showUpgradeModal}
        onClose={() => {
          setUpgradeModal(false);
        }}
        title={"Want to process more data"}
        description={
          "Upgrade your plan or contact sales to increase your API limits"
        }
      />
    </Dialog>
  );
};

FailedEvaluationsDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  data: PropTypes.object,
};

export default FailedEvaluationsDialog;
