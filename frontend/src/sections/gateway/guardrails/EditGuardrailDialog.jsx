import React, { useState, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Stack,
  Alert,
  MenuItem,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useUpdateGuardrail } from "../providers/hooks/useGatewayConfig";
import {
  GUARDRAIL_STAGES,
  GUARDRAIL_STAGE_LABELS,
  getGuardrailStage,
} from "./guardrailStage";

const ACTIONS = ["block", "warn", "log"];

function resolveExecutionMode(guardrail) {
  return guardrail?.mode === "async" ? "async" : "sync";
}

const EditGuardrailDialog = ({ open, onClose, guardrail, gatewayId }) => {
  const [action, setAction] = useState("block");
  const [stage, setStage] = useState("pre");
  const [threshold, setThreshold] = useState("");

  const updateGuardrail = useUpdateGuardrail();
  const stageInfo = getGuardrailStage(guardrail);
  // A fixed-stage guardrail is saved with the stage it always runs at.
  const savedStage = stageInfo.configurable ? stage : stageInfo.stage;

  useEffect(() => {
    if (guardrail && open) {
      setAction(guardrail.action || "block");
      setStage(getGuardrailStage(guardrail).stage);
      setThreshold(guardrail.threshold ?? "");
    }
  }, [guardrail, open]);

  const handleSave = () => {
    const config = {
      ...guardrail,
      action,
      stage: savedStage,
      mode: resolveExecutionMode(guardrail),
    };
    if (threshold !== "") config.threshold = Number(threshold);

    updateGuardrail.mutate(
      { gatewayId, name: guardrail.name, config },
      {
        onSuccess: () => {
          enqueueSnackbar(`Guardrail "${guardrail.name}" updated`, {
            variant: "success",
          });
          onClose();
        },
        onError: () => {
          enqueueSnackbar("Failed to update guardrail", { variant: "error" });
        },
      },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Edit Guardrail: {guardrail?.name}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} mt={1}>
          <TextField
            label="Action"
            select
            fullWidth
            value={action}
            onChange={(e) => setAction(e.target.value)}
          >
            {ACTIONS.map((item) => (
              <MenuItem key={item} value={item}>
                {item}
              </MenuItem>
            ))}
          </TextField>
          {stageInfo.configurable ? (
            <TextField
              label="Stage"
              select
              fullWidth
              value={stage}
              onChange={(e) => setStage(e.target.value)}
            >
              {GUARDRAIL_STAGES.map((item) => (
                <MenuItem key={item} value={item}>
                  {GUARDRAIL_STAGE_LABELS[item]}
                </MenuItem>
              ))}
            </TextField>
          ) : (
            <TextField
              label="Stage"
              fullWidth
              disabled
              value={GUARDRAIL_STAGE_LABELS[stageInfo.stage]}
              helperText="This guardrail always runs at this stage"
            />
          )}
          {/* The gateway records an org block after the LLM but returns the
              response until it acts on post-stage errors. */}
          {action === "block" && savedStage !== "pre" && (
            <Alert severity="warning">
              Blocking after the LLM is not enforced yet: a flagged response is
              recorded and still returned.
            </Alert>
          )}
          <TextField
            label="Threshold"
            type="number"
            fullWidth
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            helperText="Numeric threshold for the guardrail (optional)"
          />
          {updateGuardrail.isError && (
            <Alert severity="error">
              {updateGuardrail.error?.message || "Failed to update guardrail"}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={updateGuardrail.isPending}
        >
          {updateGuardrail.isPending ? "Saving..." : "Save"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

EditGuardrailDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  guardrail: PropTypes.object,
  gatewayId: PropTypes.string,
};

export default EditGuardrailDialog;
