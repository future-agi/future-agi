import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import RecoveryCodesDialog from "./RecoveryCodesDialog";

const RecoveryCodesSection = ({ remaining, hasTotp, onStatusChange }) => {
  const theme = useTheme();
  const queryClient = useQueryClient();
  const [regenOpen, setRegenOpen] = useState(false);
  const [regenInput, setRegenInput] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState(null);
  const [recoveryDialogOpen, setRecoveryDialogOpen] = useState(false);

  const { mutate: regenerateCodes, isPending: isRegenerating } = useMutation({
    mutationFn: async (payload) => {
      const response = await axios.post(
        endpoints.twoFactor.recoveryCodes.regenerate,
        payload,
      );
      return response.data;
    },
    onSuccess: (data) => {
      enqueueSnackbar("Recovery codes regenerated", { variant: "success" });
      setRegenOpen(false);
      setRegenInput("");
      setRecoveryCodes(data.recovery_codes);
      setRecoveryDialogOpen(true);
      queryClient.invalidateQueries({ queryKey: ["2fa-status"] });
      onStatusChange();
    },
    onError: (error) => {
      enqueueSnackbar(error?.message || "Failed to regenerate recovery codes", {
        variant: "error",
      });
    },
  });

  const handleRegenerate = () => {
    if (hasTotp) {
      if (regenInput.length !== 6) {
        enqueueSnackbar("Please enter a valid 6-digit code", {
          variant: "warning",
        });
        return;
      }
      regenerateCodes({ code: regenInput });
    } else {
      if (!regenInput) {
        enqueueSnackbar("Please enter your password", {
          variant: "warning",
        });
        return;
      }
      regenerateCodes({ password: regenInput });
    }
  };

  const handleRegenClose = () => {
    setRegenOpen(false);
    setRegenInput("");
  };

  const handleRecoveryDialogClose = () => {
    setRecoveryDialogOpen(false);
  };

  const isLow = remaining != null && remaining <= 2;

  return (
    <>
      <Box
        sx={{
          width: "100%",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: theme.spacing(1),
          padding: theme.spacing(2),
          backgroundColor: "background.paper",
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Box>
            <Typography
              variant="s1"
              component="div"
              sx={{
                fontWeight: "fontWeightSemiBold",
                color: "text.primary",
              }}
            >
              Recovery Codes
            </Typography>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: theme.spacing(1),
                marginTop: theme.spacing(0.5),
              }}
            >
              <Typography
                variant="s2"
                sx={{
                  color: "text.secondary",
                  fontWeight: "fontWeightRegular",
                }}
              >
                {remaining != null ? remaining : "..."} of 10 codes remaining
              </Typography>
              {isLow && (
                <Chip
                  icon={
                    <Iconify width={14} icon="solar:danger-triangle-linear" />
                  }
                  label="Low"
                  color="warning"
                  size="small"
                  variant="outlined"
                />
              )}
            </Box>
          </Box>

          <Button
            variant="outlined"
            color="primary"
            size="small"
            onClick={() => setRegenOpen(true)}
          >
            <Typography variant="s2" fontWeight="fontWeightMedium">
              Regenerate
            </Typography>
          </Button>
        </Box>
      </Box>

      {/* Regenerate Confirmation Dialog */}
      <Dialog
        open={regenOpen}
        onClose={handleRegenClose}
        maxWidth={false}
        PaperProps={{ sx: { maxWidth: 560 } }}
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
            Regenerate Recovery Codes
          </Typography>
        </DialogTitle>
        <DialogContent>
          <Typography
            variant="s2"
            sx={{
              color: "text.secondary",
              fontWeight: "fontWeightRegular",
              marginBottom: theme.spacing(4),
            }}
          >
            This will invalidate all existing recovery codes and generate new
            ones.{" "}
            {hasTotp
              ? "Enter your authenticator code to continue."
              : "Enter your password to continue."}
          </Typography>
          <TextField
            autoFocus
            fullWidth
            label={hasTotp ? "Authentication code" : "Password"}
            placeholder={hasTotp ? "000000" : undefined}
            type={hasTotp ? "text" : "password"}
            value={regenInput}
            onChange={(e) => {
              if (hasTotp) {
                const val = e.target.value.replace(/\D/g, "").slice(0, 6);
                setRegenInput(val);
              } else {
                setRegenInput(e.target.value);
              }
            }}
            inputProps={
              hasTotp ? { maxLength: 6, inputMode: "numeric" } : undefined
            }
            size="small"
            sx={{ mt: 2 }}
          />
        </DialogContent>
        <DialogActions sx={{ padding: theme.spacing(2) }}>
          <Button onClick={handleRegenClose} variant="outlined" size="small">
            <Typography variant="s2" fontWeight="fontWeightMedium">
              Cancel
            </Typography>
          </Button>
          <LoadingButton
            loading={isRegenerating}
            onClick={handleRegenerate}
            variant="contained"
            color="primary"
            size="small"
            disabled={hasTotp ? regenInput.length !== 6 : !regenInput}
          >
            <Typography variant="s2" fontWeight="fontWeightMedium">
              Regenerate
            </Typography>
          </LoadingButton>
        </DialogActions>
      </Dialog>

      {/* Recovery Codes Display Dialog */}
      <RecoveryCodesDialog
        open={recoveryDialogOpen}
        onClose={handleRecoveryDialogClose}
        codes={recoveryCodes || []}
      />
    </>
  );
};

RecoveryCodesSection.propTypes = {
  remaining: PropTypes.number,
  hasTotp: PropTypes.bool,
  onStatusChange: PropTypes.func.isRequired,
};

export default RecoveryCodesSection;
