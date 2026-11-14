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

const ACTIONS = ["block", "warn", "log"];
const STAGES = ["pre", "post", "both"];

function resolveExecutionMode(guardrail) {
  return guardrail?.mode === "async" ? "async" : "sync";
}

const EditGuardrailDialog = ({ open, onClose, guardrail, gatewayId }) => {
  const [action, setAction] = useState("block");
  const [stage, setStage] = useState("pre");
  const [threshold, setThreshold] = useState("");

  const updateGuardrail = useUpdateGuardrail();

  useEffect(() => {
    if (guardrail && open) {
      setAction(guardrail.action || "block");
      setStage(guardrail.stage || guardrail.phase || "pre");
      setThreshold(guardrail.threshold ?? "");
    }
  }, [guardrail, open]);

  // A confidence threshold is a score from 0 to 1; the API refuses others.
  const thresholdInvalid =
    threshold !== "" && !(Number(threshold) >= 0 && Number(threshold) <= 1);

  const handleSave = () => {
    if (thresholdInvalid) return;
    const config = {
      ...guardrail,
      action,
      stage,
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
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
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
          <TextField
            label="Stage"
            select
            fullWidth
            value={stage}
            onChange={(e) => setStage(e.target.value)}
          >
            {STAGES.map((item) => (
              <MenuItem key={item} value={item}>
                {item}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label="Threshold"
            type="number"
            fullWidth
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            inputProps={{ min: 0, max: 1, step: 0.05 }}
            error={thresholdInvalid}
            helperText={
              thresholdInvalid
                ? "Enter a number from 0 to 1, for example 0.8"
                : "Confidence threshold from 0 to 1 (optional)"
            }
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
          disabled={updateGuardrail.isPending || thresholdInvalid}
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
