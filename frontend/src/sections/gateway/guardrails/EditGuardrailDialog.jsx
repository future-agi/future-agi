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
  guardrailStage,
  RESPONSE_ONLY_CHECKS,
  requestOnlyGuardrail,
  withoutGuardrailRetries,
} from "../settings/guardrailOptions";

const ACTIONS = ["block", "warn", "log"];
const STAGES = ["pre", "post"];

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
      setStage(guardrailStage(guardrail.name, guardrail));
      setThreshold(guardrail.threshold ?? "");
    }
  }, [guardrail, open]);

  const handleSave = () => {
    const config = {
      ...guardrail,
      action,
      stage,
      mode: resolveExecutionMode(guardrail),
    };
    if (threshold !== "") config.threshold = Number(threshold);

    if (
      guardrail.config?.stage !== undefined ||
      guardrail.config?.on_block === "reflect" ||
      stage !== guardrailStage(guardrail.name, guardrail)
    ) {
      // Keep the resolved stage when removing retry's legacy stage inference.
      config.config = { ...guardrail.config, stage };
    }
    if (
      config.config &&
      (action !== "block" ||
        guardrailStage(guardrail.name, config) !== "post" ||
        config.mode !== "sync")
    ) {
      config.config = withoutGuardrailRetries(config.config);
    }

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
          <TextField
            label="Stage"
            select
            fullWidth
            value={stage}
            onChange={(e) => setStage(e.target.value)}
          >
            {STAGES.map((item) => (
              <MenuItem
                key={item}
                value={item}
                disabled={
                  item === "pre"
                    ? RESPONSE_ONLY_CHECKS.has(guardrail?.name)
                    : requestOnlyGuardrail(
                        guardrail?.name,
                        guardrail?.config || {},
                      )
                }
              >
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
