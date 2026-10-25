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
  MASK_ACTION_HELP,
  actionOptionsFor,
} from "../constants/guardrailCheckActions";

function resolveExecutionMode(guardrail) {
  return guardrail?.mode === "async" ? "async" : "sync";
}

// What the dialog shows for a check of a section stored as a checks map: the
// gateway runs such a check stored without an action as log, and without a
// threshold at 0 (the config push forwards the map as stored).
function shownForChecksMap(guardrail) {
  return {
    action: guardrail?.action || "log",
    threshold:
      guardrail?.confidence_threshold ?? guardrail?.confidenceThreshold ?? 0,
  };
}

// storedAsChecksMap: the org's section is stored as a checks map, which
// update-guardrail edits as the map (the gateway contract takes only enabled,
// action, confidence_threshold and config on such a check). The dialog then
// opens on what the gateway runs, and sends only what the admin changed, by
// the map's names, so saving changes nothing else the check does.
const EditGuardrailDialog = ({
  open,
  onClose,
  guardrail,
  gatewayId,
  storedAsChecksMap = false,
}) => {
  const [action, setAction] = useState("block");
  const [threshold, setThreshold] = useState("");

  const updateGuardrail = useUpdateGuardrail();

  useEffect(() => {
    if (guardrail && open) {
      if (storedAsChecksMap) {
        const shown = shownForChecksMap(guardrail);
        setAction(shown.action);
        setThreshold(shown.threshold);
      } else {
        setAction(guardrail.action || "block");
        setThreshold(guardrail.threshold ?? "");
      }
    }
  }, [guardrail, open, storedAsChecksMap]);

  // The keys of a checks-map check the admin changed, or null when none.
  const checksMapEdit = () => {
    const shown = shownForChecksMap(guardrail);
    const edit = {};
    if (action !== shown.action) edit.action = action;
    if (threshold !== "" && Number(threshold) !== Number(shown.threshold)) {
      edit.confidence_threshold = Number(threshold);
    }
    return Object.keys(edit).length ? edit : null;
  };

  // No stage: the gateway never receives one. An org's check runs where its
  // type runs, and only data leakage and hallucination checks read the
  // answer, so offering "post" or "both" would promise a check on the answer.
  const handleSave = () => {
    let config;
    if (storedAsChecksMap) {
      config = checksMapEdit();
      if (!config) {
        onClose();
        return;
      }
    } else {
      config = {
        ...guardrail,
        action,
        mode: resolveExecutionMode(guardrail),
      };
      if (threshold !== "") config.threshold = Number(threshold);
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

  // Mask only for a check that can rewrite the request (pii-detection, or
  // pii-detector in a section stored as rules); a stored action the check
  // does not take stays listed, disabled.
  const actionOptions = actionOptionsFor(guardrail?.name, action);
  const storedOnly = actionOptions.some(
    (o) => o.disabled && o.value === action,
  );

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
            helperText={
              storedOnly
                ? "This check cannot take this action; the gateway runs it as log."
                : action === "mask"
                  ? MASK_ACTION_HELP
                  : undefined
            }
          >
            {actionOptions.map(({ value, disabled }) => (
              <MenuItem key={value} value={value} disabled={disabled}>
                {value}
                {disabled ? " (runs as log)" : ""}
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
  storedAsChecksMap: PropTypes.bool,
};

export default EditGuardrailDialog;
