import React from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogTitle,
  DialogActions,
  Button,
  Typography,
  IconButton,
  Box,
  Stack,
} from "@mui/material";
import Iconify from "src/components/iconify";

export default function SdkInfoDialog({ open, onClose, onSubmit }) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      aria-labelledby="confirm-dialog-title"
      aria-describedby="confirm-dialog-description"
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 560 } }}
    >
      <DialogTitle
        sx={{
          gap: "10px",
          display: "flex",
          flexDirection: "column",
          padding: 2,
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            rowGap: "2px",
          }}
        >
          <Stack
            direction={"row"}
            alignItems={"center"}
            justifyContent={"space-between"}
          >
            <Typography
              variant="m3"
              color={"text.primary"}
              fontWeight={"fontWeightBold"}
            >
              Large files must be shared with SDK
            </Typography>
            <IconButton
              sx={{
                color: "text.primary",
                padding: 0,
                margin: 0,
              }}
              onClick={onClose}
            >
              <Iconify icon="solar:close-linear" />
            </IconButton>
          </Stack>
          <Typography
            variant="s1"
            color={"text.secondary"}
            fontWeight={"fontWeightRegular"}
          >
            Attachments larger than 5 MB should be used using SDK. For this use
            Create knowledge base by using SDK option.
          </Typography>
        </Box>
      </DialogTitle>

      <DialogActions sx={{ padding: 2 }}>
        <Button
          onClick={onClose}
          sx={{
            color: "text.disabled",
            minWidth: "90px",
          }}
          variant="outlined"
        >
          <Typography
            variant="s2"
            fontWeight={"fontWeightMedium"}
            fontSize={"13px"}
          >
            Cancel
          </Typography>
        </Button>
        <Button
          sx={{
            minWidth: "90px",
          }}
          onClick={onSubmit}
          variant="contained"
          autoFocus
          color="primary"
        >
          <Typography
            variant="s2"
            fontWeight={"fontWeightSemiBold"}
            fontSize={"13px"}
          >
            Ok,got it
          </Typography>
        </Button>
      </DialogActions>
    </Dialog>
  );
}

SdkInfoDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onSubmit: PropTypes.func,
};
