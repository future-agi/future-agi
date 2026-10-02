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
  Chip,
  Autocomplete,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useUpdateApiKey } from "./hooks/useApiKeys";

const EditKeyDialog = ({ open, onClose, keyData }) => {
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [allowedModels, setAllowedModels] = useState([]);
  const [allowedProviders, setAllowedProviders] = useState([]);

  const updateMutation = useUpdateApiKey();

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
        name,
        owner,
        allowed_models: allowedModels,
        allowed_providers: allowedProviders,
      },
      {
        onSuccess: () => {
          enqueueSnackbar("API key updated", { variant: "success" });
          onClose();
        },
        onError: () => {
          enqueueSnackbar("Failed to update API key", { variant: "error" });
        },
      },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Edit API Key</DialogTitle>
      <DialogContent>
        <Stack spacing={2.5} mt={1}>
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
          <Autocomplete
            multiple
            freeSolo
            size="small"
            options={[]}
            value={allowedModels}
            onChange={(_, val) => setAllowedModels(val)}
            renderTags={(value, getTagProps) =>
              value.map((option, index) => (
                <Chip
                  label={option}
                  size="small"
                  {...getTagProps({ index })}
                  key={option}
                />
              ))
            }
            renderInput={(params) => (
              <TextField
                {...params}
                label="Allowed Models"
                placeholder="Type model name + Enter (empty = all)"
                helperText="Leave empty to allow all models"
              />
            )}
          />
          <Autocomplete
            multiple
            freeSolo
            size="small"
            options={[]}
            value={allowedProviders}
            onChange={(_, val) => setAllowedProviders(val)}
            renderTags={(value, getTagProps) =>
              value.map((option, index) => (
                <Chip
                  label={option}
                  size="small"
                  {...getTagProps({ index })}
                  key={option}
                />
              ))
            }
            renderInput={(params) => (
              <TextField
                {...params}
                label="Allowed Providers"
                placeholder="Type provider name + Enter (empty = all)"
                helperText="Leave empty to allow all providers"
              />
            )}
          />
          {updateMutation.isError && (
            <Alert severity="error">
              {updateMutation.error?.message || "Failed to update key"}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button size="small" onClick={onClose}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          onClick={handleSave}
          disabled={!name.trim() || updateMutation.isPending}
        >
          {updateMutation.isPending ? "Saving..." : "Save"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

EditKeyDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  keyData: PropTypes.object,
};

export default EditKeyDialog;
