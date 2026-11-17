import { getGatewayAuthenticationErrorMessage } from "src/utils/errorUtils";
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import { useCreateApiKey } from "./hooks/useApiKeys";
import AccessListField from "./AccessListField";
import AllowedProvidersField from "./AllowedProvidersField";
import { LABEL_ABOVE_STYLE } from "./labelAbove";
import { useKeyAccessOptions } from "./useKeyAccessOptions";

// Create an API key. The secret comes back once; the page shows it in the
// one-time reveal (onCreated), never here.
const CreateKeyDialog = ({ open, onClose, onCreated, gatewayId }) => {
  const [name, setName] = useState("");
  const [allowedModels, setAllowedModels] = useState([]);
  const [allowedProviders, setAllowedProviders] = useState([]);

  const createMutation = useCreateApiKey();
  const { modelOptions } = useKeyAccessOptions(gatewayId);

  const resetForm = () => {
    setName("");
    setAllowedModels([]);
    setAllowedProviders([]);
    createMutation.reset?.();
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  const handleCreate = () => {
    const keyName = name.trim();
    createMutation.mutate(
      {
        gateway_id: gatewayId,
        name: keyName,
        allowed_models: allowedModels,
        allowed_providers: allowedProviders,
      },
      {
        onSuccess: (result) => {
          onCreated({
            name: result?.name || keyName,
            secret: result?.key || "",
            id: result?.id,
            gatewayKeyId: result?.gatewayKeyId ?? result?.gateway_key_id,
            allowedModels:
              result?.allowedModels ?? result?.allowed_models ?? allowedModels,
            allowedProviders:
              result?.allowedProviders ?? result?.allowed_providers ?? allowedProviders,
            // A legacy reply may normalize omitted lists to []. Retain the
            // submitted restrictions too, rather than widening the example.
            requestedModels: allowedModels,
            requestedProviders: allowedProviders,
          });
          resetForm();
          onClose();
        },
      },
    );
  };

  const errorCode =
    createMutation.error?.response?.data?.code ?? createMutation.error?.code;
  const errorMessage =
    errorCode === "personal_key_workspace_required"
      ? "Choose or join a workspace to create API keys."
      : getGatewayAuthenticationErrorMessage(createMutation.error) ||
        createMutation.error?.message ||
        (typeof createMutation.error?.result === "string"
          ? createMutation.error.result
          : null) ||
        "Failed to create key";

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle>Create API key</DialogTitle>
      <DialogContent>
        <Stack spacing={2} mt={1}>
          <TextField
            label="Name"
            fullWidth
            required
            size="small"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. checkout-service"
            autoFocus
            InputLabelProps={{ style: LABEL_ABOVE_STYLE }}
          />
          <Typography typography="s2" color="text.secondary">
            This personal key belongs to your signed-in account.
          </Typography>
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
          {createMutation.isError && (
            <Alert severity="error">{errorMessage}</Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>Cancel</Button>
        <Button
          variant="contained"
          color="primary"
          onClick={handleCreate}
          disabled={!name.trim() || createMutation.isPending}
        >
          {createMutation.isPending ? "Creating..." : "Create"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

CreateKeyDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onCreated: PropTypes.func.isRequired,
  gatewayId: PropTypes.string,
};

export default CreateKeyDialog;
