import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Tooltip,
  Typography,
  useTheme,
  CircularProgress,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { browserSupportsWebAuthn } from "@simplewebauthn/browser";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import PasskeyRegisterDialog from "./PasskeyRegisterDialog";
import RecoveryCodesDialog from "./RecoveryCodesDialog";

const PasskeySection = ({ passkey: _passkey, onStatusChange }) => {
  const theme = useTheme();
  const queryClient = useQueryClient();
  const [registerOpen, setRegisterOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [recoveryCodes, setRecoveryCodes] = useState([]);
  const [recoveryDialogOpen, setRecoveryDialogOpen] = useState(false);

  const supportsWebAuthn = browserSupportsWebAuthn();

  const { data: passkeys, isLoading: isLoadingPasskeys } = useQuery({
    queryKey: ["passkeys"],
    queryFn: async () => {
      const res = await axios.get(endpoints.passkey.list);
      return res.data;
    },
  });

  const { mutate: deletePasskey, isPending: isDeleting } = useMutation({
    mutationFn: async (id) => {
      const response = await axios.delete(endpoints.passkey.detail(id));
      return response.data;
    },
    onSuccess: () => {
      enqueueSnackbar("Passkey deleted", { variant: "success" });
      setDeleteOpen(false);
      setDeleteTarget(null);
      queryClient.invalidateQueries({ queryKey: ["passkeys"] });
      queryClient.invalidateQueries({ queryKey: ["2fa-status"] });
      onStatusChange();
    },
    onError: (error) => {
      enqueueSnackbar(error?.message || "Failed to delete passkey", {
        variant: "error",
      });
    },
  });

  const handleDeleteClick = (pk) => {
    setDeleteTarget(pk);
    setDeleteOpen(true);
  };

  const handleDeleteConfirm = () => {
    if (deleteTarget) {
      deletePasskey(deleteTarget.id);
    }
  };

  const handleDeleteClose = () => {
    setDeleteOpen(false);
    setDeleteTarget(null);
  };

  const handleRegisterSuccess = (data) => {
    queryClient.invalidateQueries({ queryKey: ["passkeys"] });
    queryClient.invalidateQueries({ queryKey: ["2fa-status"] });
    onStatusChange();
    if (data?.recovery_codes) {
      setRecoveryCodes(data.recovery_codes);
      setRecoveryDialogOpen(true);
    }
  };

  const formatDate = (dateString) => {
    if (!dateString) return "Never";
    return new Date(dateString).toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
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
              Passkeys
            </Typography>
            <Typography
              variant="s2"
              component="div"
              sx={{
                color: "text.secondary",
                fontWeight: "fontWeightRegular",
                marginTop: theme.spacing(0.5),
              }}
            >
              Passkeys allow you to sign in securely without a password using
              biometrics, security keys, or your device lock screen.
            </Typography>
          </Box>

          <Tooltip
            title={
              !supportsWebAuthn ? "Your browser does not support passkeys" : ""
            }
          >
            <span>
              <Button
                variant="outlined"
                color="primary"
                size="small"
                onClick={() => setRegisterOpen(true)}
                disabled={!supportsWebAuthn}
              >
                <Typography variant="s2" fontWeight="fontWeightMedium">
                  Add passkey
                </Typography>
              </Button>
            </span>
          </Tooltip>
        </Box>

        {!supportsWebAuthn && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: theme.spacing(1),
              marginTop: theme.spacing(1.5),
              backgroundColor: "info.lighter",
              borderRadius: theme.spacing(0.5),
              padding: theme.spacing(1),
            }}
          >
            <Iconify
              icon="solar:info-circle-linear"
              sx={{ width: "18px", height: "18px", color: "info.main" }}
            />
            <Typography
              variant="s2"
              sx={{ color: "info.dark", fontWeight: "fontWeightRegular" }}
            >
              Your browser does not support WebAuthn. Please use a supported
              browser to add passkeys.
            </Typography>
          </Box>
        )}

        {/* Passkey List */}
        {isLoadingPasskeys ? (
          <Box
            sx={{
              display: "flex",
              justifyContent: "center",
              padding: theme.spacing(2),
            }}
          >
            <CircularProgress size={24} />
          </Box>
        ) : (
          passkeys &&
          passkeys.length > 0 && (
            <Box sx={{ marginTop: theme.spacing(2) }}>
              {passkeys.map((pk, index) => (
                <Box
                  key={pk.id}
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    paddingY: theme.spacing(1.5),
                    ...(index < passkeys.length - 1 && {
                      borderBottom: "1px solid",
                      borderColor: "divider",
                    }),
                  }}
                >
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: theme.spacing(1.5),
                    }}
                  >
                    <Iconify
                      icon="solar:key-linear"
                      sx={{
                        width: "20px",
                        height: "20px",
                        color: "text.secondary",
                      }}
                    />
                    <Box>
                      <Typography
                        variant="s2"
                        sx={{
                          color: "text.primary",
                          fontWeight: "fontWeightMedium",
                        }}
                      >
                        {pk.name}
                      </Typography>
                      <Typography
                        variant="s2"
                        sx={{
                          color: "text.disabled",
                          fontWeight: "fontWeightRegular",
                          fontSize: "12px",
                        }}
                      >
                        Added {formatDate(pk.created_at)}
                        {pk.last_used_at &&
                          ` \u00B7 Last used ${formatDate(pk.last_used_at)}`}
                      </Typography>
                    </Box>
                  </Box>

                  <Tooltip title="Delete passkey">
                    <IconButton
                      size="small"
                      onClick={() => handleDeleteClick(pk)}
                    >
                      <Iconify
                        icon="solar:trash-bin-trash-linear"
                        sx={{
                          width: "18px",
                          height: "18px",
                          color: "error.main",
                        }}
                      />
                    </IconButton>
                  </Tooltip>
                </Box>
              ))}
            </Box>
          )
        )}
      </Box>

      {/* Register Dialog */}
      <PasskeyRegisterDialog
        open={registerOpen}
        onClose={() => setRegisterOpen(false)}
        onSuccess={handleRegisterSuccess}
      />

      {/* Recovery Codes Dialog */}
      {recoveryCodes.length > 0 && (
        <RecoveryCodesDialog
          open={recoveryDialogOpen}
          onClose={() => {
            setRecoveryDialogOpen(false);
            setRecoveryCodes([]);
          }}
          codes={recoveryCodes}
        />
      )}

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={deleteOpen}
        onClose={handleDeleteClose}
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
            Delete Passkey
          </Typography>
        </DialogTitle>
        <DialogContent>
          <Typography
            variant="s2"
            sx={{
              color: "text.secondary",
              fontWeight: "fontWeightRegular",
            }}
          >
            Are you sure you want to delete the passkey &quot;
            {deleteTarget?.name}&quot;? This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ padding: theme.spacing(2) }}>
          <Button onClick={handleDeleteClose} variant="outlined" size="small">
            <Typography variant="s2" fontWeight="fontWeightMedium">
              Cancel
            </Typography>
          </Button>
          <LoadingButton
            loading={isDeleting}
            onClick={handleDeleteConfirm}
            variant="contained"
            color="error"
            size="small"
          >
            <Typography variant="s2" fontWeight="fontWeightMedium">
              Delete
            </Typography>
          </LoadingButton>
        </DialogActions>
      </Dialog>
    </>
  );
};

PasskeySection.propTypes = {
  passkey: PropTypes.shape({
    enabled: PropTypes.bool,
    count: PropTypes.number,
  }),
  onStatusChange: PropTypes.func.isRequired,
};

export default PasskeySection;
