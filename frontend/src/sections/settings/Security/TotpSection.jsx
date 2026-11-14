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
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import TotpSetupDialog from "./TotpSetupDialog";

const TotpSection = ({ totp, onStatusChange }) => {
  const theme = useTheme();
  const queryClient = useQueryClient();
  const [setupOpen, setSetupOpen] = useState(false);
  const [disableOpen, setDisableOpen] = useState(false);
  const [disableCode, setDisableCode] = useState("");

  const { mutate: disableTotp, isPending: isDisabling } = useMutation({
    meta: { errorHandled: true },
    mutationFn: async (code) => {
      const response = await axios.delete(endpoints.twoFactor.totp.disable, {
        data: { code },
      });
      return response.data;
    },
    onSuccess: () => {
      enqueueSnackbar("Two-factor authentication disabled", {
        variant: "success",
      });
      setDisableOpen(false);
      setDisableCode("");
      queryClient.invalidateQueries({ queryKey: ["2fa-status"] });
      onStatusChange();
    },
    onError: (error) => {
      const apiMessage =
        error?.response?.data?.message ||
        error?.response?.data?.error ||
        error?.result;
      enqueueSnackbar(
        apiMessage || "Failed to disable two-factor authentication",
        { variant: "error" },
      );
    },
  });

  const handleDisable = () => {
    if (!disableCode.trim()) {
      enqueueSnackbar("Please enter your TOTP code or a recovery code", {
        variant: "warning",
      });
      return;
    }
    disableTotp(disableCode);
  };

  const handleDisableClose = () => {
    setDisableOpen(false);
    setDisableCode("");
  };

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
            alignItems: "flex-start",
          }}
        >
          <Box sx={{ flex: 1, marginRight: theme.spacing(2) }}>
            <Typography
              variant="s1"
              component="div"
              sx={{
                fontWeight: "fontWeightSemiBold",
                color: "text.primary",
              }}
            >
              Authenticator App
            </Typography>
            {totp?.enabled ? (
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: theme.spacing(0.5),
                  marginTop: theme.spacing(0.5),
                }}
              >
                <Iconify
                  icon="solar:check-circle-bold"
                  sx={{
                    width: "16px",
                    height: "16px",
                    color: "success.main",
                  }}
                />
                <Typography
                  variant="s2"
                  sx={{
                    color: "success.main",
                    fontWeight: "fontWeightMedium",
                  }}
                >
                  Configured
                </Typography>
              </Box>
            ) : (
              <Typography
                variant="s2"
                component="div"
                sx={{
                  color: "text.secondary",
                  fontWeight: "fontWeightRegular",
                  marginTop: theme.spacing(0.5),
                }}
              >
                Add an authenticator app to protect your account with time-based
                one-time passwords.
              </Typography>
            )}
          </Box>

          {totp?.enabled ? (
            <Button
              variant="outlined"
              color="error"
              size="small"
              onClick={() => setDisableOpen(true)}
            >
              <Typography variant="s2" fontWeight="fontWeightMedium">
                Disable
              </Typography>
            </Button>
          ) : (
            <Button
              variant="outlined"
              color="primary"
              size="small"
              onClick={() => setSetupOpen(true)}
            >
              <Typography variant="s2" fontWeight="fontWeightMedium">
                Set up
              </Typography>
            </Button>
          )}
        </Box>
      </Box>

      {/* Setup Dialog */}
      <TotpSetupDialog
        open={setupOpen}
        onClose={() => setSetupOpen(false)}
        onStatusChange={onStatusChange}
      />

      {/* Disable Confirmation Dialog */}
      <Dialog
        open={disableOpen}
        onClose={handleDisableClose}
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
            Disable Two-Factor Authentication
          </Typography>
        </DialogTitle>
        <DialogContent>
          <Typography
            variant="s2"
            sx={{
              color: "text.secondary",
              fontWeight: "fontWeightRegular",
              marginBottom: theme.spacing(2),
            }}
          >
            Enter your authenticator code or a recovery code to confirm
            disabling two-factor authentication. This will make your account
            less secure.
          </Typography>
          <TextField
            autoFocus
            fullWidth
            label="Authentication or recovery code"
            placeholder="Enter code"
            sx={{ marginTop: theme.spacing(2) }}
            value={disableCode}
            onChange={(e) => setDisableCode(e.target.value)}
            size="small"
          />
        </DialogContent>
        <DialogActions sx={{ padding: theme.spacing(2) }}>
          <Button onClick={handleDisableClose} variant="outlined" size="small">
            <Typography variant="s2" fontWeight="fontWeightMedium">
              Cancel
            </Typography>
          </Button>
          <LoadingButton
            loading={isDisabling}
            onClick={handleDisable}
            variant="contained"
            color="error"
            size="small"
            disabled={!disableCode.trim()}
          >
            <Typography variant="s2" fontWeight="fontWeightMedium">
              Disable 2FA
            </Typography>
          </LoadingButton>
        </DialogActions>
      </Dialog>
    </>
  );
};

TotpSection.propTypes = {
  totp: PropTypes.shape({
    enabled: PropTypes.bool,
    confirmed_at: PropTypes.string,
  }),
  onStatusChange: PropTypes.func.isRequired,
};

export default TotpSection;
