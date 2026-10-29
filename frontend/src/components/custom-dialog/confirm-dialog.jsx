import React from "react";
import PropTypes from "prop-types";

import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogTitle from "@mui/material/DialogTitle";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import { Box, IconButton, Typography, useTheme } from "@mui/material";
import Iconify from "../iconify";

// ----------------------------------------------------------------------

export default function ConfirmDialog({
  title,
  content,
  action,
  open,
  onClose,
  closeDisabled = false,
  ...other
}) {
  const theme = useTheme();
  return (
    <Dialog fullWidth maxWidth="xs" open={open} onClose={onClose} {...other}>
      <Box sx={{ padding: 2 }}>
        <DialogTitle sx={{ padding: 0, margin: 0 }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography
              color="text.primary"
              fontWeight={"fontWeightBold"}
              typography="m2"
            >
              {title}
            </Typography>
            <IconButton
              sx={{
                color: "text.primary",
              }}
              onClick={onClose}
              disabled={closeDisabled}
            >
              <Iconify icon="mdi:close" />
            </IconButton>
          </Box>
        </DialogTitle>

        {content && (
          <DialogContent
            sx={{
              color: "text.secondary",
              marginTop: theme.spacing(1),
              padding: 0,
            }}
          >
            {content}
          </DialogContent>
        )}

        <DialogActions sx={{ padding: 0, marginTop: 3 }}>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onClose}
            disabled={closeDisabled}
            sx={{ paddingX: "24px" }}
          >
            Cancel
          </Button>
          {action}
        </DialogActions>
      </Box>
    </Dialog>
  );
}

ConfirmDialog.propTypes = {
  action: PropTypes.node,
  closeDisabled: PropTypes.bool,
  content: PropTypes.node,
  onClose: PropTypes.func,
  open: PropTypes.bool,
  title: PropTypes.string,
};
