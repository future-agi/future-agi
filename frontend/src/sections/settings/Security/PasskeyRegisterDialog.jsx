import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { startRegistration } from "@simplewebauthn/browser";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";

const STEP_START = 0;
const STEP_NAME = 1;

const PasskeyRegisterDialog = ({ open, onClose, onSuccess }) => {
  const theme = useTheme();
  const [step, setStep] = useState(STEP_START);
  const [passkeyName, setPasskeyName] = useState("Passkey");
  const [attestationResponse, setAttestationResponse] = useState(null);

  const { mutate: getRegisterOptions, isPending: isGettingOptions } =
    useMutation({
      mutationFn: async () => {
        const response = await axios.post(endpoints.passkey.registerOptions);
        return response.data;
      },
      onSuccess: async (options) => {
        try {
          const attResp = await startRegistration({ optionsJSON: options });
          setAttestationResponse(attResp);
          setStep(STEP_NAME);
        } catch (error) {
          if (error.name === "NotAllowedError" || error.name === "AbortError") {
            enqueueSnackbar("Passkey creation was cancelled", {
              variant: "info",
            });
          } else {
            enqueueSnackbar(error?.message || "Failed to create passkey", {
              variant: "error",
            });
          }
        }
      },
      onError: (error) => {
        const msg =
          error?.response?.data?.error ||
          error?.response?.data?.message ||
          error?.message ||
          "Failed to get registration options";
        enqueueSnackbar(msg, { variant: "error" });
      },
    });

  const { mutate: verifyRegistration, isPending: isVerifying } = useMutation({
    mutationFn: async ({ name, credential }) => {
      const response = await axios.post(endpoints.passkey.registerVerify, {
        name,
        credential: JSON.stringify(credential),
      });
      return response.data;
    },
    onSuccess: (data) => {
      enqueueSnackbar("Passkey added successfully", { variant: "success" });
      handleClose();
      onSuccess(data);
    },
    onError: (error) => {
      const msg =
        error?.response?.data?.error ||
        error?.response?.data?.message ||
        error?.message ||
        "Failed to register passkey";
      enqueueSnackbar(msg, { variant: "error" });
    },
  });

  const handleContinue = () => {
    getRegisterOptions();
  };

  const handleSave = () => {
    if (!passkeyName.trim()) {
      enqueueSnackbar("Please enter a name for your passkey", {
        variant: "warning",
      });
      return;
    }
    verifyRegistration({
      name: passkeyName.trim(),
      credential: attestationResponse,
    });
  };

  const handleClose = () => {
    setStep(STEP_START);
    setPasskeyName("Passkey");
    setAttestationResponse(null);
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 560 } }}
      fullWidth
    >
      {step === STEP_START && (
        <>
          <DialogTitle>
            <Typography
              variant="s1"
              sx={{
                fontWeight: "fontWeightSemiBold",
                color: "text.primary",
              }}
            >
              Add a passkey
            </Typography>
          </DialogTitle>
          <DialogContent>
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: theme.spacing(2),
                paddingY: theme.spacing(2),
              }}
            >
              <Iconify
                icon="mdi:fingerprint"
                sx={{
                  width: "48px",
                  height: "48px",
                  color: "primary.main",
                }}
              />
              <Typography
                variant="s2"
                sx={{
                  color: "text.secondary",
                  fontWeight: "fontWeightRegular",
                  textAlign: "center",
                }}
              >
                Your browser will prompt you to create a passkey using your
                device&apos;s biometrics, security key, or lock screen. Follow
                the on-screen instructions to continue.
              </Typography>
            </Box>
          </DialogContent>
          <DialogActions sx={{ padding: theme.spacing(2) }}>
            <Button onClick={handleClose} variant="outlined" size="small">
              <Typography variant="s2" fontWeight="fontWeightMedium">
                Cancel
              </Typography>
            </Button>
            <LoadingButton
              loading={isGettingOptions}
              onClick={handleContinue}
              variant="contained"
              color="primary"
              size="small"
            >
              <Typography variant="s2" fontWeight="fontWeightMedium">
                Continue
              </Typography>
            </LoadingButton>
          </DialogActions>
        </>
      )}

      {step === STEP_NAME && (
        <>
          <DialogTitle>
            <Typography
              variant="s1"
              sx={{
                fontWeight: "fontWeightSemiBold",
                color: "text.primary",
              }}
            >
              Name your passkey
            </Typography>
          </DialogTitle>
          <DialogContent>
            <Typography
              variant="s2"
              sx={{
                color: "text.secondary",
                fontWeight: "fontWeightRegular",
                marginBottom: theme.spacing(3),
              }}
            >
              Give your passkey a name so you can identify it later.
            </Typography>
            <TextField
              autoFocus
              fullWidth
              label="Passkey name"
              value={passkeyName}
              onChange={(e) => setPasskeyName(e.target.value)}
              size="small"
              placeholder="e.g., MacBook Pro, iPhone"
              sx={{ mt: 2 }}
            />
          </DialogContent>
          <DialogActions sx={{ padding: theme.spacing(2) }}>
            <Button onClick={handleClose} variant="outlined" size="small">
              <Typography variant="s2" fontWeight="fontWeightMedium">
                Cancel
              </Typography>
            </Button>
            <LoadingButton
              loading={isVerifying}
              onClick={handleSave}
              variant="contained"
              color="primary"
              size="small"
              disabled={!passkeyName.trim()}
            >
              <Typography variant="s2" fontWeight="fontWeightMedium">
                Save
              </Typography>
            </LoadingButton>
          </DialogActions>
        </>
      )}
    </Dialog>
  );
};

PasskeyRegisterDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onSuccess: PropTypes.func.isRequired,
};

export default PasskeyRegisterDialog;
