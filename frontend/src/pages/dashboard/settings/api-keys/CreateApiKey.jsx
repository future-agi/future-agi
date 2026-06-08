import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { LoadingButton } from "@mui/lab";
import axios, { endpoints } from "src/utils/axios";
import { useMutation } from "@tanstack/react-query";
import React, { useEffect, useMemo, useState } from "react";
import { RouterLink } from "src/routes/components";
import { ShowComponent } from "src/components/show";
import { enqueueSnackbar } from "notistack";
import { copyToClipboard } from "src/utils/utils";
import SvgColor from "src/components/svg-color";

const CreateApiKey = ({
  completionHref = "",
  initialKeyName = "",
  open,
  onClose,
  refreshGrid,
}) => {
  const [keyName, setKeyName] = useState("");
  const [showKeys, setShowKeys] = useState(false);
  const [copiedKeys, setCopiedKeys] = useState({
    apiKey: false,
    secretKey: false,
  });
  const normalizedInitialKeyName = initialKeyName.trim();

  useEffect(() => {
    if (open && normalizedInitialKeyName) {
      setKeyName(normalizedInitialKeyName);
    }
  }, [normalizedInitialKeyName, open]);

  const handleCopyCredential = (credentialType, credentialValue) => {
    copyToClipboard(credentialValue);
    setCopiedKeys((previousCopiedKeys) => ({
      ...previousCopiedKeys,
      [credentialType]: true,
    }));
    enqueueSnackbar("Copied to clipboard", {
      variant: "success",
    });
  };

  const {
    mutate: handleAddApiKey,
    data: createdKey,
    isPending: loading,
    reset,
  } = useMutation({
    mutationFn: () =>
      axios.post(endpoints.keys.generateSecretKey, {
        key_name: keyName,
      }),
    onSuccess: () => {
      setShowKeys(true);
      setCopiedKeys({ apiKey: false, secretKey: false });
      refreshGrid();
    },
  });

  const isSecretKey = useMemo(() => {
    return createdKey && showKeys && open;
  }, [showKeys, createdKey, open]);
  const completionActionProps = completionHref
    ? { component: RouterLink, href: completionHref }
    : {};
  const hasCopiedBothKeys = copiedKeys.apiKey && copiedKeys.secretKey;
  const requiresCopiedKeys = Boolean(completionHref) && isSecretKey;
  const canCloseDialog = !completionHref || hasCopiedBothKeys;
  const isCompletionDisabled =
    !keyName || (requiresCopiedKeys && !hasCopiedBothKeys);

  const keyResult = createdKey?.data?.result || {};
  const keys = {
    apiKey: keyResult.api_key || keyResult.apiKey || "",
    secretKey: keyResult.secret_key || keyResult.secretKey || "",
    maskedApiKey: keyResult.masked_api_key || keyResult.maskedApiKey || "",
    maskedSecretKey:
      keyResult.masked_secret_key || keyResult.maskedSecretKey || "",
  };

  const handleClose = () => {
    if (!canCloseDialog) {
      enqueueSnackbar(
        isSecretKey
          ? "Copy both keys before returning to trace setup."
          : "Create the key to continue trace setup.",
        { variant: "info" },
      );
      return;
    }

    setKeyName("");
    setShowKeys(false);
    setCopiedKeys({ apiKey: false, secretKey: false });
    reset();
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      aria-labelledby="api-key-dialog"
      fullWidth
      maxWidth="sm"
    >
      <Box sx={{ padding: 2 }}>
        <DialogTitle
          id="api-key-dialog"
          sx={{
            gap: 1,
            display: "flex",
            flexDirection: "column",
            padding: 0,
            margin: 0,
          }}
        >
          <Box
            display="flex"
            alignItems="center"
            justifyContent="space-between"
          >
            <Typography color="text.primary" fontWeight={700} fontSize="18px">
              {isSecretKey ? "Key’s Generated" : "Key Name"}
            </Typography>
            <IconButton onClick={handleClose} disabled={!canCloseDialog}>
              <Iconify icon="mdi:close" />
            </IconButton>
          </Box>
        </DialogTitle>

        <DialogContent sx={{ padding: 0, margin: 0 }}>
          <ShowComponent condition={!isSecretKey}>
            <Box sx={{ marginTop: 2 }}>
              <TextField
                label={"Key name"}
                value={keyName}
                onChange={(e) => setKeyName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    handleAddApiKey();
                  }
                }}
                fullWidth
                placeholder="Enter your key name"
                variant="outlined"
                required
                size="small"
              />
            </Box>
          </ShowComponent>
          <ShowComponent condition={isSecretKey}>
            <Box
              sx={{
                marginTop: 2,
                display: "flex",
                flexDirection: "column",
                gap: 2,
              }}
            >
              <Typography
                typography={"s1"}
                fontWeight={"fontWeightRegular"}
                color="text.primary"
              >
                Please make sure to store this API key and secret key in a
                secure and accessible place. For security reasons, it won’t be
                visible again in your Future AGI account. If you lose it, you’ll
                need to generate a new one.
              </Typography>
              {completionHref ? (
                <Typography
                  typography={"s1"}
                  fontWeight={"fontWeightMedium"}
                  color="text.primary"
                >
                  Copy both keys before returning to trace setup.
                </Typography>
              ) : null}
              <Box display="flex" gap={1}>
                <TextField
                  label={"API key"}
                  value={keys?.maskedApiKey}
                  fullWidth
                  disabled
                  variant="outlined"
                  size="small"
                />
                <Button
                  aria-label="Copy API key"
                  size="small"
                  variant="outlined"
                  startIcon={
                    <SvgColor
                      src="/assets/icons/ic_copy.svg"
                      alt="Copy"
                      sx={{
                        width: "20px",
                        height: "20px",
                        color: "text.disabled",
                      }}
                    />
                  }
                  sx={{ minWidth: 96 }}
                  onClick={() => handleCopyCredential("apiKey", keys.apiKey)}
                >
                  {copiedKeys.apiKey ? "Copied" : "Copy"}
                </Button>
              </Box>
              <Box display="flex" gap={1}>
                <TextField
                  label={"Secret key"}
                  value={keys.maskedSecretKey}
                  fullWidth
                  disabled
                  variant="outlined"
                  size="small"
                />
                <Button
                  aria-label="Copy secret key"
                  size="small"
                  variant="outlined"
                  startIcon={
                    <SvgColor
                      src="/assets/icons/ic_copy.svg"
                      alt="Copy"
                      sx={{
                        width: "20px",
                        height: "20px",
                        color: "text.disabled",
                      }}
                    />
                  }
                  sx={{ minWidth: 96 }}
                  onClick={() =>
                    handleCopyCredential("secretKey", keys.secretKey)
                  }
                >
                  {copiedKeys.secretKey ? "Copied" : "Copy"}
                </Button>
              </Box>
              <Divider orientation="horizontal" />
            </Box>
          </ShowComponent>
        </DialogContent>
        <ShowComponent condition={!isSecretKey}>
          <DialogActions sx={{ padding: 0, marginTop: 4 }}>
            <Button
              variant="outlined"
              onClick={handleClose}
              size="small"
              disabled={!canCloseDialog}
            >
              Cancel
            </Button>
            <LoadingButton
              type="button"
              size="small"
              variant="contained"
              color="primary"
              onClick={handleAddApiKey}
              loading={loading}
              disabled={!keyName}
            >
              Next
            </LoadingButton>
          </DialogActions>
        </ShowComponent>
        <ShowComponent condition={isSecretKey}>
          <DialogActions sx={{ padding: 0, marginTop: 4 }}>
            <Button
              variant="outlined"
              fullWidth
              onClick={handleClose}
              size="small"
              disabled={!canCloseDialog}
            >
              Cancel
            </Button>
            <LoadingButton
              {...completionActionProps}
              type="button"
              size="small"
              fullWidth
              variant="contained"
              color="primary"
              onClick={handleClose}
              loading={loading}
              disabled={isCompletionDisabled}
            >
              {completionHref ? "Back to trace setup" : "Done"}
            </LoadingButton>
          </DialogActions>
        </ShowComponent>
      </Box>
    </Dialog>
  );
};

export default CreateApiKey;

CreateApiKey.propTypes = {
  completionHref: PropTypes.string,
  initialKeyName: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
};
