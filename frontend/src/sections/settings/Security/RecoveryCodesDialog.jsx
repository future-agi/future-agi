import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Typography,
  useTheme,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";

const RecoveryCodesDialog = ({ open, onClose, codes }) => {
  const theme = useTheme();
  const [saved, setSaved] = useState(false);

  const handleCopyAll = async () => {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      enqueueSnackbar("Recovery codes copied to clipboard", {
        variant: "success",
      });
    } catch {
      enqueueSnackbar("Failed to copy recovery codes", { variant: "error" });
    }
  };

  const handleDownload = () => {
    const content = [
      "FutureAGI Recovery Codes",
      "========================",
      "Save these codes in a safe place. Each code can only be used once.",
      "",
      ...codes,
      "",
      `Generated: ${new Date().toISOString()}`,
    ].join("\n");

    const blob = new Blob([content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "futureagi-recovery-codes.txt";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    enqueueSnackbar("Recovery codes downloaded", { variant: "success" });
  };

  const handleClose = () => {
    setSaved(false);
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={() => {}}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
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
          Save your recovery codes
        </Typography>
      </DialogTitle>
      <DialogContent>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: theme.spacing(2),
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: theme.spacing(1),
              backgroundColor: "success.lighter",
              borderRadius: theme.spacing(0.5),
              padding: theme.spacing(1),
            }}
          >
            <Iconify
              icon="solar:check-circle-bold"
              sx={{ width: "20px", height: "20px", color: "success.main" }}
            />
            <Typography
              variant="s2"
              sx={{ color: "success.dark", fontWeight: "fontWeightMedium" }}
            >
              Authenticator app configured!
            </Typography>
          </Box>

          <Box
            sx={{
              display: "flex",
              alignItems: "flex-start",
              gap: theme.spacing(1),
              backgroundColor: "warning.lighter",
              borderRadius: theme.spacing(0.5),
              padding: theme.spacing(1),
            }}
          >
            <Iconify
              icon="solar:danger-triangle-linear"
              sx={{
                width: "20px",
                height: "20px",
                color: "warning.main",
                flexShrink: 0,
                marginTop: "2px",
              }}
            />
            <Typography
              variant="s2"
              sx={{ color: "warning.dark", fontWeight: "fontWeightRegular" }}
            >
              Save these recovery codes in a safe place. If you lose access to
              your authenticator app, you can use these codes to sign in. Each
              code can only be used once.
            </Typography>
          </Box>

          {/* Recovery Codes Grid */}
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: theme.spacing(1),
              backgroundColor: "background.neutral",
              borderRadius: theme.spacing(0.5),
              padding: theme.spacing(2),
            }}
          >
            {codes.map((code, index) => (
              <Typography
                key={index}
                variant="s2"
                sx={{
                  fontFamily: "monospace",
                  color: "text.primary",
                  fontWeight: "fontWeightMedium",
                  textAlign: "center",
                }}
              >
                {code}
              </Typography>
            ))}
          </Box>

          {/* Copy and Download Buttons */}
          <Box sx={{ display: "flex", gap: theme.spacing(1) }}>
            <Button
              variant="outlined"
              size="small"
              startIcon={<Iconify width={16} icon="solar:copy-linear" />}
              onClick={handleCopyAll}
            >
              <Typography variant="s2" fontWeight="fontWeightMedium">
                Copy all
              </Typography>
            </Button>
            <Button
              variant="outlined"
              size="small"
              startIcon={
                <Iconify width={16} icon="solar:download-minimalistic-linear" />
              }
              onClick={handleDownload}
            >
              <Typography variant="s2" fontWeight="fontWeightMedium">
                Download
              </Typography>
            </Button>
          </Box>

          <FormControlLabel
            control={
              <Checkbox
                checked={saved}
                onChange={(e) => setSaved(e.target.checked)}
                size="small"
              />
            }
            label={
              <Typography
                variant="s2"
                sx={{
                  color: "text.primary",
                  fontWeight: "fontWeightRegular",
                }}
              >
                I have saved my recovery codes
              </Typography>
            }
          />
        </Box>
      </DialogContent>
      <DialogActions sx={{ padding: theme.spacing(2) }}>
        <Button
          onClick={handleClose}
          variant="contained"
          color="primary"
          size="small"
          disabled={!saved}
        >
          <Typography variant="s2" fontWeight="fontWeightMedium">
            Done
          </Typography>
        </Button>
      </DialogActions>
    </Dialog>
  );
};

RecoveryCodesDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  codes: PropTypes.arrayOf(PropTypes.string).isRequired,
};

export default RecoveryCodesDialog;
