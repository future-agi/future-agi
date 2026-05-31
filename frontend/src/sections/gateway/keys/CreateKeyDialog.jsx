import React, { useState } from "react";
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
  Typography,
  IconButton,
  InputAdornment,
  Chip,
  Autocomplete,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useCreateApiKey } from "./hooks/useApiKeys";

const CreateKeyDialog = ({
  open,
  onClose,
  gatewayId,
  onDone,
  onKeyCreated,
}) => {
  const [step, setStep] = useState("form"); // "form" | "success"
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [allowedModels, setAllowedModels] = useState([]);
  const [allowedProviders, setAllowedProviders] = useState([]);
  const [createdKey, setCreatedKey] = useState("");
  const [createdKeyRecord, setCreatedKeyRecord] = useState(null);
  const [copied, setCopied] = useState(false);

  const createMutation = useCreateApiKey();

  const resetForm = () => {
    setStep("form");
    setName("");
    setOwner("");
    setAllowedModels([]);
    setAllowedProviders([]);
    setCreatedKey("");
    setCreatedKeyRecord(null);
    setCopied(false);
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  const handleDone = () => {
    const keyRecord = createdKeyRecord;
    resetForm();
    onDone?.(keyRecord);
    onClose();
  };

  const handleCreate = () => {
    createMutation.mutate(
      {
        gateway_id: gatewayId,
        name,
        owner,
        allowed_models: allowedModels,
        allowed_providers: allowedProviders,
      },
      {
        onSuccess: (result) => {
          setCreatedKey(result?.key || "");
          setCreatedKeyRecord(result || null);
          onKeyCreated?.(result, {
            allowedModels,
            allowedProviders,
            gatewayId,
            keyName: name,
            owner,
          });
          setStep("success");
        },
      },
    );
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(createdKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (step === "success") {
    return (
      <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
        <DialogTitle>API Key Created</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={1}>
            <Alert severity="warning">
              Copy this key now. It will not be shown again.
            </Alert>
            <TextField
              fullWidth
              value={createdKey}
              InputProps={{
                readOnly: true,
                sx: { fontFamily: "monospace", fontSize: "0.875rem" },
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton onClick={handleCopy} size="small">
                      {copied ? (
                        <Iconify
                          icon="mdi:check"
                          sx={{ color: "success.main" }}
                        />
                      ) : (
                        <Iconify icon="mdi:content-copy" />
                      )}
                    </IconButton>
                  </InputAdornment>
                ),
              }}
            />
            <Typography variant="body2" color="text.secondary">
              <strong>Name:</strong> {name}
              {owner && (
                <>
                  {" "}
                  &middot; <strong>Owner:</strong> {owner}
                </>
              )}
            </Typography>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button variant="contained" onClick={handleDone}>
            Done
          </Button>
        </DialogActions>
      </Dialog>
    );
  }

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>Create API Key</DialogTitle>
      <DialogContent>
        <Stack spacing={2.5} mt={1}>
          <TextField
            label="Name"
            fullWidth
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g., production-key"
          />
          <TextField
            label="Owner"
            fullWidth
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            placeholder="e.g., team-backend"
          />
          <Autocomplete
            multiple
            freeSolo
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
          {createMutation.isError && (
            <Alert severity="error">
              {createMutation.error?.message ||
                createMutation.error?.result ||
                "Failed to create key"}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>Cancel</Button>
        <Button
          variant="contained"
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
  gatewayId: PropTypes.string,
  onDone: PropTypes.func,
  onKeyCreated: PropTypes.func,
};

export default CreateKeyDialog;
