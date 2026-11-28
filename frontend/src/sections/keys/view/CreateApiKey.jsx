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
import { DatePicker } from "@mui/x-date-pickers/DatePicker";
import { startOfDay } from "date-fns";
import Iconify from "src/components/iconify";
import { LoadingButton } from "@mui/lab";
import axios, { endpoints } from "src/utils/axios";
import { useMutation } from "@tanstack/react-query";
import React, { useMemo, useState } from "react";
import { ShowComponent } from "src/components/show";
import { enqueueSnackbar } from "notistack";
import { copyToClipboard } from "src/utils/utils";
import SvgColor from "src/components/svg-color";
import { handleError } from "src/utils/queryErrorHandler";
import { expiresAtFromDate } from "./keyExpiry";

// KEY_NAME_EXISTS is sent as a message, with the same code as other 400s.
const isDuplicateName = (error) =>
  error?.statusCode === 400 &&
  error.result === "Keys with this already name exists.";

const CreateApiKey = ({ open, onClose, refreshGrid }) => {
  const [keyName, setKeyName] = useState("");
  const [expiryDate, setExpiryDate] = useState(null);
  const [showKeys, setShowKeys] = useState(false);

  const handleClose = () => {
    setKeyName("");
    setExpiryDate(null);
    setShowKeys(false);
    reset();
    onClose();
  };

  // A typed-in date can be incomplete or in the past; the picker only blocks
  // past dates in its calendar. Never fall back to "no expiry" for it.
  const expiresAt = expiresAtFromDate(expiryDate);
  const isExpiryInvalid =
    expiryDate !== null &&
    (expiresAt === null || new Date(expiresAt) <= new Date());

  const {
    mutate: handleAddApiKey,
    data: createdKey,
    isPending: loading,
    variables: submittedName,
    error,
    reset,
  } = useMutation({
    mutationFn: (name) =>
      axios.post(endpoints.keys.generateSecretKey, {
        key_name: name,
        ...(expiresAt && { expires_at: expiresAt }),
      }),
    meta: { errorHandled: true },
    onError: (err) => {
      if (!isDuplicateName(err)) handleError(err);
    },
    onSuccess: () => {
      setShowKeys(true);
      // trackEvent(Events.saveApiClicked, { [PropertyName.click]: true });
      refreshGrid();
    },
  });

  const nameTaken = submittedName === keyName && isDuplicateName(error);

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
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
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
            <Typography color="text.primary" fontWeight={600} fontSize="16px">
              {isSecretKey ? "Key’s Generated" : "Key Name"}
            </Typography>
            <IconButton onClick={handleClose}>
              <Iconify icon="solar:close-linear" />
            </IconButton>
          </Box>
        </DialogTitle>

        <DialogContent sx={{ padding: 0, margin: 0 }}>
          <ShowComponent condition={!isSecretKey}>
            <Box sx={{ marginTop: 2 }}>
              <TextField
                label={"Key name"}
                value={keyName}
                error={nameTaken}
                helperText={
                  nameTaken &&
                  "A key with this name already exists. Choose another name."
                }
                FormHelperTextProps={{ role: "alert" }}
                onChange={(e) => setKeyName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    if (!isExpiryInvalid) handleAddApiKey(keyName);
                  }
                }}
                fullWidth
                placeholder="Enter your key name"
                variant="outlined"
                required
                size="small"
              />
              <DatePicker
                label="Expires on (optional)"
                value={expiryDate}
                onChange={setExpiryDate}
                minDate={startOfDay(new Date())}
                disablePast
                slotProps={{
                  field: { clearable: true },
                  textField: {
                    size: "small",
                    fullWidth: true,
                    helperText:
                      "Valid through the end of this day. Leave empty for a key that never expires.",
                  },
                }}
                sx={{ marginTop: 2 }}
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
              <Typography variant="body2">
                Copy the API key and secret key before closing this dialog.
                Store both securely. You can only copy them here.
              </Typography>
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
              onClick={() => handleAddApiKey(keyName)}
              loading={loading}
              disabled={!keyName || isExpiryInvalid}
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
