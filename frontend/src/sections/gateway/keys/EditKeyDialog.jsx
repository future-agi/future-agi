import { getGatewayAuthenticationErrorMessage } from "src/utils/errorUtils";
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
} from "@mui/material";
import { enqueueSnackbar } from "notistack";

import { useUpdateApiKey } from "./hooks/useApiKeys";
import AccessListField from "./AccessListField";
import AllowedProvidersField from "./AllowedProvidersField";
import { useKeyAccessOptions } from "./useKeyAccessOptions";

const EditKeyDialog = ({ open, onClose, keyData, gatewayId }) => {
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [allowedModels, setAllowedModels] = useState([]);
  const [allowedProviders, setAllowedProviders] = useState([]);

  const updateMutation = useUpdateApiKey();
  const { modelOptions } = useKeyAccessOptions(gatewayId);

  useEffect(() => {
    if (keyData && open) {
      setName(keyData.name || "");
      setOwner(keyData.owner || "");
      setAllowedModels(keyData.allowedModels || []);
      setAllowedProviders(keyData.allowedProviders || []);
    }
  }, [keyData, open]);

  const handleSave = () => {
    updateMutation.mutate(
      {
        keyId: keyData.id,
        name: name.trim(),
        owner: owner.trim(),
        allowed_models: allowedModels,
        allowed_providers: allowedProviders,
      },
      {
        onSuccess: () => {
          enqueueSnackbar("API key updated", { variant: "success" });
          onClose();
        },
        onError: (error) => {
          enqueueSnackbar(
            getGatewayAuthenticationErrorMessage(error) ||
              "Failed to update API key",
            { variant: "error" },
          );
        },
      },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
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
          />
          <TextField
            label="Owner"
            fullWidth
            size="small"
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
          />
          <AccessListField
            label="Allowed models"
            value={allowedModels}
            onChange={setAllowedModels}
            options={modelOptions}
            emptyMeaning="Leave empty to allow every model."
          />
          <AllowedProvidersField
            gatewayId={gatewayId}
            label="Allowed providers"
            value={allowedProviders}
            onChange={setAllowedProviders}
          />
          {updateMutation.isError && (
            <Alert severity="error">
              {getGatewayAuthenticationErrorMessage(updateMutation.error) ||
                updateMutation.error?.message ||
                "Failed to update key"}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          color="primary"
          onClick={handleSave}
          disabled={!name.trim() || updateMutation.isPending}
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
