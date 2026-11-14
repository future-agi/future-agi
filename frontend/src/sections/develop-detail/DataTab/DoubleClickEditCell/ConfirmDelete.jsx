import React from "react";
import {
  Box,
  Dialog,
  DialogTitle,
  DialogActions,
  Typography,
  IconButton,
  Button,
  useTheme,
} from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";
import Iconify from "src/components/iconify";
import HelperText from "../../Common/HelperText";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";

const dialogTitleMapper = {
  image: "Delete Image",
  audio: "Delete Audio",
  file: "Delete File",
};

const DeleteMediaDialog = ({
  open,
  onClose,
  onDelete,
  isPending = false,
  fileName = "this media", // default to "this media" in case no fileName is passed
  fileType = "image", // default to "image" if not passed, could also be "audio"
}) => {
  const theme = useTheme();

  // Determine the dialog title and confirmation message based on fileType
  const dialogTitle = dialogTitleMapper[fileType];
  const confirmationMessage = `Are you sure you want to delete ${fileName}?`;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 400 } }}
      fullWidth
    >
      <Box sx={{ padding: theme.spacing(2) }}>
        <DialogTitle sx={{ padding: 0, margin: 0 }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography
              fontWeight={"fontWeightMedium"}
              color="text.primary"
              variant="m2"
            >
              {dialogTitle}
            </Typography>
            <IconButton onClick={onClose}>
              <Iconify icon="solar:close-linear" color="text.primary" />
            </IconButton>
          </Box>
          <HelperText text={confirmationMessage} />
        </DialogTitle>
        <Box>
          <DialogActions sx={{ padding: 0, marginTop: theme.spacing(4) }}>
            <Button onClick={onClose} variant="outlined">
              <Typography variant="s2" fontWeight={"fontWeightSemiBold"}>
                Cancel
              </Typography>
            </Button>
            <LoadingButton
              variant="contained"
              loading={isPending}
              onClick={onDelete}
              sx={{
                backgroundColor: theme.palette.status.danger.solid,
                color: "common.white",
                "&:hover": {
                  backgroundColor: theme.palette.status.danger.solid,
                },
              }}
              startIcon={
                <SvgColor width={16}
                  src="/assets/icons/components/ic_delete.svg"
                  sx={{ color: theme.palette.background.paper,
                  }}
                />
              }
            >
              <Typography variant="s2" fontWeight={"fontWeightSemiBold"}>
                Delete
              </Typography>
            </LoadingButton>
          </DialogActions>
        </Box>
      </Box>
    </Dialog>
  );
};

DeleteMediaDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onDelete: PropTypes.func,
  isPending: PropTypes.bool,
  fileName: PropTypes.string,
  fileType: PropTypes.oneOf(["image", "audio", "file"]),
};

export default DeleteMediaDialog;
