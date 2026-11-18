import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { styled } from "@mui/system";
import { LoadingButton } from "@mui/lab";
import logger from "src/utils/logger";

const StyledBox = styled(Box)(() => ({
  display: "flex",
  alignItems: "center",
  gap: "12px",
  "& .MuiButtonBase-root": {
    padding: "0",
  },
  "& .MuiTypography-root ": {
    padding: "0",
  },
}));

const StyledIconButton = styled(IconButton)({
  position: "absolute",
  top: "12px",
  right: "12px",
});

const StyleDialog = styled(Dialog)({
  "& .MuiDialog-paper": {
    padding: "16px",
    maxWidth: "560px",
  },
});

const CustomDialog = ({
  title,
  preTitleIcon,
  color = "primary",
  actionButton,
  open,
  onClose,
  isData = true,
  onClickAction,
  loading,
  children,
  actionStartIcon,
  titleProps = {},
}) => {
  // Handle action click with error catching
  const handleActionClick = React.useCallback(() => {
    try {
      if (onClickAction) {
        onClickAction();
      }
    } catch (error) {
      logger.error("Error in dialog action:", error);
    }
  }, [onClickAction]);

  // Handle close with error catching
  const handleClose = React.useCallback(
    (event, reason) => {
      if (reason === "backdropClick" || reason === "escapeKeyDown") {
        // Prevent closing on backdrop click if loading
        if (loading) return;
      }

      if (onClose) {
        onClose(event, reason);
      }
    },
    [onClose, loading],
  );

  return (
    <StyleDialog
      open={open}
      onClose={handleClose}
      fullWidth
      aria-labelledby="custom-dialog-title"
    >
      <StyledBox>
        {preTitleIcon && (
          <IconButton size="small" disableRipple>
            <Iconify icon={preTitleIcon} sx={{ color: "text.primary" }} />
          </IconButton>
        )}
        <DialogTitle id="custom-dialog-title">
          <Typography
            typography="m2"
            fontWeight="fontWeightMedium"
            color="text.primary"
            {...titleProps}
          >
            {title}
          </Typography>
        </DialogTitle>
      </StyledBox>

      <StyledIconButton onClick={onClose} disabled={loading}>
        <Iconify icon="solar:close-linear" color="text.primary" />
      </StyledIconButton>

      {children}

      <DialogActions sx={{ padding: 0, margin: "32px 0 0" }}>
        <Box sx={{ display: "flex", justifyContent: "flex-end", gap: "12px" }}>
          <Button
            color="inherit"
            size="small"
            onClick={onClose}
            variant="outlined"
            disabled={loading}
          >
            Cancel
          </Button>
          <LoadingButton
            disabled={!isData || loading}
            loading={loading}
            size="small"
            variant="contained"
            color={color}
            sx={{ minWidth: "max-content" }}
            onClick={handleActionClick}
            startIcon={actionStartIcon ? actionStartIcon : undefined}
          >
            {actionButton}
          </LoadingButton>
        </Box>
      </DialogActions>
    </StyleDialog>
  );
};

CustomDialog.propTypes = {
  isData: PropTypes.bool,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  actionButton: PropTypes.oneOfType([PropTypes.node, PropTypes.string]),
  title: PropTypes.oneOfType([PropTypes.node, PropTypes.string]),
  children: PropTypes.node,
  className: PropTypes.string,
  color: PropTypes.string,
  preTitleIcon: PropTypes.string,
  onClickAction: PropTypes.func,
  loading: PropTypes.bool,
  actionStartIcon: PropTypes.node,
  titleProps: PropTypes.object,
};

export default CustomDialog;
