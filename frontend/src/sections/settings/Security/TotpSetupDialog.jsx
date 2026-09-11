import React, { useState, useEffect, useRef } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { LoadingScreen } from "src/components/loading-screen";
import { LoadingButton } from "@mui/lab";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import RecoveryCodesDialog from "./RecoveryCodesDialog";

const TotpSetupDialog = ({ open, onClose, onStatusChange }) => {
  const theme = useTheme();
  const queryClient = useQueryClient();

  const [setupData, setSetupData] = useState(null);
  const [verifyCode, setVerifyCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState(null);
  const [recoveryDialogOpen, setRecoveryDialogOpen] = useState(false);
  const [secretCopied, setSecretCopied] = useState(false);
  const copyTimerRef = useRef(null);

  useEffect(() => () => clearTimeout(copyTimerRef.current), []);

  // Fetch setup data when dialog opens
  const { mutate: fetchSetup, isPending: isSettingUp } = useMutation({
    mutationFn: async () => {
      const response = await axios.post(endpoints.twoFactor.totp.setup);
      return response.data;
    },
    onSuccess: (data) => {
      setSetupData(data);
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.message || "Failed to initialize authenticator setup",
        { variant: "error" },
      );
      onClose();
    },
  });

  const { mutate: confirmTotp, isPending: isConfirming } = useMutation({
    mutationFn: async (code) => {
      const response = await axios.post(endpoints.twoFactor.totp.confirm, {
        code,
      });
      return response.data;
    },
    onSuccess: (data) => {
      enqueueSnackbar("Authenticator app configured successfully", {
        variant: "success",
      });
      setRecoveryCodes(data.recovery_codes);
      setRecoveryDialogOpen(true);
      queryClient.invalidateQueries({ queryKey: ["2fa-status"] });
      onStatusChange();
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.message || "Invalid verification code. Please try again.",
        { variant: "error" },
      );
    },
  });

  useEffect(() => {
    if (open) {
      setSetupData(null);
      setVerifyCode("");
      setRecoveryCodes(null);
      setRecoveryDialogOpen(false);
      setSecretCopied(false);
      fetchSetup();
    }
  }, [open]);

  const handleVerify = () => {
    if (verifyCode.length !== 6) {
      enqueueSnackbar("Please enter a valid 6-digit code", {
        variant: "warning",
      });
      return;
    }
    confirmTotp(verifyCode);
  };

  const handleCopySecret = async () => {
    try {
      await navigator.clipboard.writeText(setupData?.secret);
      setSecretCopied(true);
      enqueueSnackbar("Secret copied to clipboard", { variant: "success" });
      copyTimerRef.current = setTimeout(() => setSecretCopied(false), 2000);
    } catch {
      enqueueSnackbar("Failed to copy secret", { variant: "error" });
    }
  };

  const handleDialogClose = () => {
    if (!recoveryDialogOpen) {
      onClose();
    }
  };

  const handleRecoveryDialogClose = () => {
    setRecoveryDialogOpen(false);
    onClose();
  };

  return (
    <>
      <Dialog
        open={open && !recoveryDialogOpen}
        onClose={handleDialogClose}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          <Typography
            variant="s1"
            sx={{
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
            }}
          >
            Set up authenticator app
          </Typography>
        </DialogTitle>
        <DialogContent>
          {isSettingUp || !setupData ? (
            <LoadingScreen variant="orbit" sx={{ minHeight: "240px" }} />
          ) : (
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: theme.spacing(2),
              }}
            >
              <Typography
                variant="s2"
                sx={{
                  color: "text.secondary",
                  fontWeight: "fontWeightRegular",
                }}
              >
                Scan the QR code below with your authenticator app (such as
                Google Authenticator, Authy, or 1Password), then enter the
                verification code.
              </Typography>

              {/* QR Code */}
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "center",
                  padding: theme.spacing(2),
                }}
              >
                <img
                  src={setupData.qr_code}
                  alt="TOTP QR Code"
                  style={{ width: 200, height: 200 }}
                />
              </Box>

              {/* Manual Secret */}
              <Box>
                <Typography
                  variant="s2"
                  sx={{
                    color: "text.secondary",
                    fontWeight: "fontWeightRegular",
                    marginBottom: theme.spacing(0.5),
                  }}
                >
                  Or enter this code manually:
                </Typography>
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: theme.spacing(1),
                    backgroundColor: "background.neutral",
                    borderRadius: theme.spacing(0.5),
                    padding: theme.spacing(1),
                  }}
                >
                  <Typography
                    variant="s2"
                    sx={{
                      fontFamily: "monospace",
                      color: "text.primary",
                      fontWeight: "fontWeightMedium",
                      flex: 1,
                      wordBreak: "break-all",
                    }}
                  >
                    {setupData.secret}
                  </Typography>
                  <Tooltip title={secretCopied ? "Copied!" : "Copy secret"}>
                    <IconButton size="small" onClick={handleCopySecret}>
                      <Iconify
                        icon={secretCopied ? "mdi:check" : "mdi:content-copy"}
                        sx={{ width: "18px", height: "18px" }}
                      />
                    </IconButton>
                  </Tooltip>
                </Box>
              </Box>

              {/* Verification Code Input */}
              <TextField
                autoFocus
                fullWidth
                label="Verification code"
                placeholder="000000"
                value={verifyCode}
                onChange={(e) => {
                  const val = e.target.value.replace(/\D/g, "").slice(0, 6);
                  setVerifyCode(val);
                }}
                inputProps={{ maxLength: 6, inputMode: "numeric" }}
                size="small"
              />
            </Box>
          )}
        </DialogContent>
        <DialogActions sx={{ padding: theme.spacing(2) }}>
          <Button onClick={handleDialogClose} variant="outlined" size="small">
            <Typography variant="s2" fontWeight="fontWeightMedium">
              Cancel
            </Typography>
          </Button>
          <LoadingButton
            loading={isConfirming}
            onClick={handleVerify}
            variant="contained"
            color="primary"
            size="small"
            disabled={verifyCode.length !== 6 || !setupData}
          >
            <Typography variant="s2" fontWeight="fontWeightMedium">
              Verify
            </Typography>
          </LoadingButton>
        </DialogActions>
      </Dialog>

      {/* Recovery Codes Dialog */}
      <RecoveryCodesDialog
        open={recoveryDialogOpen}
        onClose={handleRecoveryDialogClose}
        codes={recoveryCodes || []}
      />
    </>
  );
};

TotpSetupDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onStatusChange: PropTypes.func.isRequired,
};

export default TotpSetupDialog;
