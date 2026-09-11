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
import React, { useMemo, useState } from "react";
import { ShowComponent } from "src/components/show";
import { enqueueSnackbar } from "notistack";
import { copyToClipboard } from "src/utils/utils";
import SvgColor from "src/components/svg-color";

const CreateApiKey = ({ open, onClose, refreshGrid }) => {
  const [keyName, setKeyName] = useState("");
  const [showKeys, setShowKeys] = useState(false);

  const handleClose = () => {
    setKeyName("");
    setShowKeys(false);
    reset();
    onClose();
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
      // trackEvent(Events.saveApiClicked, { [PropertyName.click]: true });
      refreshGrid();
    },
  });

  const isSecretKey = useMemo(() => {
    return createdKey && showKeys && open;
  }, [showKeys, createdKey, open]);

  const keyResult = createdKey?.data?.result || {};
  const keys = {
    apiKey: keyResult.api_key || keyResult.apiKey || "",
    secretKey: keyResult.secret_key || keyResult.secretKey || "",
    maskedApiKey: keyResult.masked_api_key || keyResult.maskedApiKey || "",
    maskedSecretKey:
      keyResult.masked_secret_key || keyResult.maskedSecretKey || "",
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
            <IconButton onClick={handleClose}>
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
              {/* <Typography
                typography={"s1"}
                fontWeight={"fontWeightRegular"}
                color="text.primary"
              >
                Please make sure to store this API key and secret key in a
                secure and accessible place. For security reasons, it won’t be
                visible again in your Future AGI account. If you lose it, you’ll
                need to generate a new one.
              </Typography> */}
              <Box display="flex" gap={1}>
                <TextField
                  label={"API key"}
                  value={keys?.maskedApiKey}
                  fullWidth
                  disabled
                  variant="outlined"
                  size="small"
                />
                <Box
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    padding: "8px 16px",
                    display: "flex",
                    alignItems: "center",
                  }}
                >
                  <SvgColor
                    src="/assets/icons/ic_copy.svg"
                    alt="Copy"
                    sx={{
                      width: "20px",
                      height: "20px",
                      cursor: "pointer",
                      color: "text.disabled",
                    }}
                    onClick={() => {
                      copyToClipboard(keys.apiKey);
                      enqueueSnackbar("Copied to clipboard", {
                        variant: "success",
                      });
                    }}
                  />
                </Box>
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
                <Box
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    padding: "8px 16px",
                    display: "flex",
                    alignItems: "center",
                  }}
                >
                  <SvgColor
                    src="/assets/icons/ic_copy.svg"
                    alt="Copy"
                    sx={{
                      width: "20px",
                      height: "20px",
                      cursor: "pointer",
                      color: "text.disabled",
                    }}
                    onClick={() => {
                      copyToClipboard(keys.secretKey);
                      enqueueSnackbar("Copied to clipboard", {
                        variant: "success",
                      });
                    }}
                  />
                </Box>
              </Box>
              <Divider orientation="horizontal" />
            </Box>
          </ShowComponent>
        </DialogContent>
        <ShowComponent condition={!isSecretKey}>
          <DialogActions sx={{ padding: 0, marginTop: 4 }}>
            <Button variant="outlined" onClick={handleClose} size="small">
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
            >
              Cancel
            </Button>
            <LoadingButton
              type="button"
              size="small"
              fullWidth
              variant="contained"
              color="primary"
              onClick={handleClose}
              loading={loading}
              disabled={!keyName}
            >
              Done
            </LoadingButton>
          </DialogActions>
        </ShowComponent>
      </Box>
    </Dialog>
  );
};

export default CreateApiKey;

CreateApiKey.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
};
