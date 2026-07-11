import PropTypes from "prop-types";
import React from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  IconButton,
  TextField,
  Button,
  Box,
  Typography,
  useTheme,
  Divider,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "notistack";
import { Events, trackEvent } from "src/utils/Mixpanel";

const Share = ({ open, title = "Share as link", body, onClose }) => {
  const theme = useTheme();
  const typographyTheme = theme.typography;
  const shareLink = window.location.href;

  const handleCopy = () => {
    copyToClipboard(shareLink);
    enqueueSnackbar("Copied to clipboard", {
      variant: "success",
    });
    trackEvent(Events.pExperimentProjectShared);
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          width: 547,
          padding: theme.spacing(1),
        },
      }}
    >
      <DialogTitle
        sx={{
          paddingTop: theme.spacing(1),
          paddingBottom: theme.spacing(2.5),
          paddingX: theme.spacing(1.5),
        }}
      >
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Typography
            variant="subtitle1"
            fontWeight={typographyTheme.fontWeightBold}
          >
            {title}
          </Typography>
          <IconButton aria-label="close-share" onClick={onClose} sx={{ p: 0 }}>
            <Iconify icon="line-md:close" color="black" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent
        sx={{
          paddingX: theme.spacing(1.5),
          paddingTop: theme.spacing(2),
          paddingBottom: theme.spacing(2.5),
        }}
      >
        <Typography
          mb={2}
          variant="subtitle2"
          color="text.primary"
          fontWeight={typographyTheme.fontWeightRegular}
        >
          {body}
        </Typography>
        <Typography mb={2} variant="caption" color="text.secondary">
          Note: This creates a direct link without access control.
        </Typography>

        <Box display="flex" alignItems="center" gap={theme.spacing(1)} mt={2}>
          <Box flex={8}>
            <TextField
              value={shareLink}
              fullWidth
              size="small"
              sx={{
                color: "text.primary",
                fontSize: 14,
                fontWeight: 400,
              }}
              InputProps={{
                readOnly: true,
                startAdornment: (
                  <Iconify
                    icon="tabler:link"
                    width={20}
                    height={20}
                    sx={{ marginRight: 1, color: "text.primary" }}
                  />
                ),
              }}
            />
          </Box>

          <Box
            flex={2}
            display="flex"
            alignItems="center"
            justifyContent="center"
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              height: 40,
              cursor: "pointer",
            }}
            onClick={handleCopy}
          >
            <Iconify
              icon="tabler:copy"
              width={24}
              height={24}
              color={"text.disabled"}
            />
          </Box>
        </Box>
      </DialogContent>
      <Divider />

      <DialogActions
        sx={{
          paddingX: theme.spacing(1.5),
          paddingBottom: theme.spacing(2),
          paddingTop: theme.spacing(3),
        }}
      >
        <Box display="flex" width="100%" gap={theme.spacing(1)}>
          <Button
            aria-label="Cancel-share-project"
            variant="outlined"
            color="inherit"
            onClick={onClose}
            sx={{
              flex: 1,
              height: 36,
              fontSize: "13px",
              textTransform: "none",
            }}
          >
            Cancel
          </Button>
          <Button
            aria-label="close-share-project"
            variant="outlined"
            color="inherit"
            onClick={onClose}
            sx={{
              flex: 1,
              height: 36,
              fontSize: "13px",
            }}
          >
            Close
          </Button>
        </Box>
      </DialogActions>
    </Dialog>
  );
};

Share.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  title: PropTypes.string,
  body: PropTypes.string,
};

export default Share;
