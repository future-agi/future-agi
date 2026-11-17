import {
  getInlineActionErrorMessage,
  getSafeActionErrorMessage,
} from "src/utils/errorUtils";
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
  Typography,
  Alert,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";

import { useUpdateApiKey } from "./hooks/useApiKeys";
import AccessListField from "./AccessListField";
import AllowedProvidersField from "./AllowedProvidersField";
import { LABEL_ABOVE_STYLE } from "./labelAbove";
import { useKeyAccessOptions } from "./useKeyAccessOptions";

const EditKeyDialog = ({ open, onClose, keyData, gatewayId }) => {
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [allowedModels, setAllowedModels] = useState([]);
  const [allowedProviders, setAllowedProviders] = useState([]);
  const [expiry, setExpiry] = useState("");
  const [expiryChanged, setExpiryChanged] = useState(false);
  const [expiryCleared, setExpiryCleared] = useState(false);
  // datetime-local is a wall-clock input; explicitly interpret it as UTC,
  // independent of the browser's local timezone or daylight-saving rules.
  const expiryDate = expiry ? new Date(`${expiry}Z`) : null;
  const expiryValid = !expiry || Number.isFinite(expiryDate.getTime());
  const expiryError = expiryChanged && !expiryCleared
    ? !expiry || !expiryValid
      ? "Enter a valid expiry or choose Clear expiry."
      : expiryDate.getTime() <= Date.now() ? "Choose a future expiry in UTC." : ""
    : "";

  const updateMutation = useUpdateApiKey();
  const { modelOptions } = useKeyAccessOptions(gatewayId);

  useEffect(() => {
    if (keyData && open) {
      setName(keyData.name || "");
      setOwner(keyData.owner || "");
      setAllowedModels(keyData.allowedModels || []);
      setAllowedProviders(keyData.allowedProviders || []);
      const stored = keyData.expiresAt ?? keyData.expires_at;
      const date = stored ? new Date(stored) : null;
      setExpiry(date && Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 19) : "");
      setExpiryChanged(false);
      setExpiryCleared(false);
    }
  }, [keyData, open]);

  const handleSave = () => {
    if (expiryError) return;
    updateMutation.mutate(
      {
        keyId: keyData.id,
        name: name.trim(),
        owner: owner.trim(),
        allowed_models: allowedModels,
        allowed_providers: allowedProviders,
        ...(expiryChanged ? { expires_at: expiryDate ? expiryDate.toISOString() : null } : {}),
      },
      {
        onSuccess: () => {
          enqueueSnackbar("API key updated", { variant: "success" });
          onClose();
        },
        onError: (error) => {
          enqueueSnackbar(
            getSafeActionErrorMessage(error, "Failed to update API key"),
            { variant: "error" },
          );
        },
      },
    );
  };

  // A refusal the workspace recovery handles shows one calm line.
  const updateErrorMessage = updateMutation.isError
    ? getInlineActionErrorMessage(updateMutation.error, "Failed to update key")
    : null;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle>Edit API key</DialogTitle>
      <DialogContent>
        <Stack spacing={2} mt={1}>
          <TextField
            label="Name"
            fullWidth
            required
            size="small"
            value={name}
            onChange={(e) => setName(e.target.value)}
            InputLabelProps={{ style: LABEL_ABOVE_STYLE }}
          />
          <TextField
            label="Owner"
            fullWidth
            size="small"
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            InputLabelProps={{ style: LABEL_ABOVE_STYLE }}
          />
          <AccessListField
            label="Allowed models"
            value={allowedModels}
            onChange={setAllowedModels}
            options={modelOptions}
            emptyMeaning="Leave empty to allow every model. Console examples use provider/model IDs. Bare model IDs are separate exact grants; neither grants the other."
          />
          <AllowedProvidersField
            gatewayId={gatewayId}
            label="Allowed providers"
            value={allowedProviders}
            onChange={setAllowedProviders}
          />
          <TextField
            label="Expiry (UTC)"
            type="datetime-local"
            size="small"
            fullWidth
            InputLabelProps={{ shrink: true, style: LABEL_ABOVE_STYLE }}
            inputProps={{ step: 1 }}
            value={expiry}
            onChange={(event) => {
              setExpiry(event.target.value);
              setExpiryChanged(true);
              setExpiryCleared(false);
            }}
            error={Boolean(expiryError)}
            helperText={expiryError || (expiry && expiryValid
              ? `Instant: ${expiryDate.toISOString()}`
              : "No expiry. All entered times are UTC, not local time.")}
          />
          <Button size="small" sx={{ alignSelf: "flex-start" }} disabled={!expiry && (!expiryChanged || expiryCleared)}
            onClick={() => { setExpiry(""); setExpiryChanged(true); setExpiryCleared(true); }}>
            Clear expiry
          </Button>
          <Typography typography="s2" color="text.secondary">
            Changes update the stored expiry; immediate gateway enforcement is not
            confirmed here. Fixed-expiry policies may refuse changes.
          </Typography>
          {updateErrorMessage && (
            <Alert severity="error">{updateErrorMessage}</Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          color="primary"
          onClick={handleSave}
          disabled={!name.trim() || updateMutation.isPending || Boolean(expiryError)}
        >
          {updateMutation.isPending ? "Saving..." : "Save Changes"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

EditKeyDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  keyData: PropTypes.object,
  gatewayId: PropTypes.string,
};

export default EditKeyDialog;
