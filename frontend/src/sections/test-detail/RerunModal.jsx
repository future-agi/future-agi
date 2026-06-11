import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  IconButton,
  LinearProgress,
  Radio,
  RadioGroup,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import { RerunTestOptions } from "./common";
import {
  useExecutionReproducibility,
  useRerunTest,
} from "src/api/tests/testDetails";
import { AGENT_TYPES } from "../agents/constants";

const riskColor = {
  high: "error.main",
  medium: "warning.main",
  low: "success.main",
};

const RerunModal = ({
  open,
  onClose,
  selectedNodes,
  selectAll,
  selectedCount,
  executionId,
  agentType,
}) => {
  const [rerunType, setRerunType] = useState(
    agentType === AGENT_TYPES.CHAT
      ? RerunTestOptions.EVAL_ONLY
      : RerunTestOptions.CALL_AND_EVAL,
  );

  const { mutate: rerunTest, isPending: isRerunPending } = useRerunTest(
    executionId,
    {
      onSuccess: () => {
        onClose();
      },
    },
  );
  const { data: reproReport, isFetching: isReproFetching } =
    useExecutionReproducibility(executionId, {
      enabled: open && !!executionId,
      retry: false,
    });

  const stabilizationPlan = reproReport?.stabilization_plan;
  const stabilizationActions = stabilizationPlan?.actions ?? [];

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle
        sx={{
          padding: 2,
          borderBottom: "1px solid",
          borderColor: "action.hover",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Typography typography="m3" fontWeight="fontWeightMedium">
          Rerun Test
        </Typography>
        <IconButton
          onClick={onClose}
          sx={{
            color: "text.primary",
          }}
          size="small"
        >
          <Iconify icon="akar-icons:cross" width={16} height={16} />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{
          padding: "16px !important",
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <Typography typography="m3" fontWeight="fontWeightMedium">
            {selectedCount} rows selected
          </Typography>
          <FormControl>
            {agentType === AGENT_TYPES.CHAT ? (
              <Typography typography="m3">
                Only evals can be re-run for selected chats
              </Typography>
            ) : (
              <RadioGroup
                name="radio-buttons-group"
                value={rerunType}
                onChange={(e) => setRerunType(e.target.value)}
              >
                <FormControlLabel
                  value={RerunTestOptions.EVAL_ONLY}
                  control={<Radio />}
                  label="Run Evals"
                />
                {agentType === AGENT_TYPES.VOICE && (
                  <FormControlLabel
                    value={RerunTestOptions.CALL_AND_EVAL}
                    control={<Radio />}
                    label="Run test + Evals"
                  />
                )}
              </RadioGroup>
            )}
          </FormControl>
        </Box>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            p: 1.5,
            display: "flex",
            flexDirection: "column",
            gap: 1,
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 1,
            }}
          >
            <Typography typography="m3" fontWeight="fontWeightMedium">
              Replay preflight
            </Typography>
            {stabilizationPlan?.risk_level && (
              <Typography
                typography="caption"
                sx={{
                  color:
                    riskColor[stabilizationPlan.risk_level] ?? "text.secondary",
                  textTransform: "capitalize",
                }}
              >
                {stabilizationPlan.risk_level} risk
              </Typography>
            )}
          </Box>
          {isReproFetching && <LinearProgress />}
          {stabilizationPlan ? (
            <>
              <Typography typography="caption" color="text.secondary">
                {stabilizationPlan.summary}
              </Typography>
              {stabilizationActions.slice(0, 3).map((action) => (
                <Box key={`${action.source}-${action.section}`}>
                  <Typography
                    typography="caption"
                    fontWeight="fontWeightMedium"
                  >
                    {action.title}
                  </Typography>
                  <Typography
                    typography="caption"
                    color="text.secondary"
                    display="block"
                  >
                    {action.action}
                  </Typography>
                </Box>
              ))}
            </>
          ) : (
            <Typography typography="caption" color="text.secondary">
              Preflight report is not available yet.
            </Typography>
          )}
        </Box>
        <Box
          sx={{
            display: "flex",
            flexDirection: "row",
            justifyContent: "flex-end",
            gap: 2,
            alignItems: "center",
          }}
        >
          <Button onClick={onClose} variant="outlined" size="small">
            Cancel
          </Button>
          <LoadingButton
            size="small"
            variant="contained"
            color="primary"
            loading={isRerunPending}
            onClick={() => {
              rerunTest({
                select_all: selectAll,
                rerun_type: rerunType,
                call_execution_ids: selectedNodes,
              });
            }}
          >
            Run
          </LoadingButton>
        </Box>
      </DialogContent>
    </Dialog>
  );
};

RerunModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  selectedNodes: PropTypes.array.isRequired,
  selectAll: PropTypes.bool.isRequired,
  selectedCount: PropTypes.number.isRequired,
  executionId: PropTypes.string.isRequired,
  agentType: PropTypes.string,
};

export default RerunModal;
