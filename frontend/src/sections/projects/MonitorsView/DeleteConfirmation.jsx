import React from "react";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";

const DeleteConfirmation = ({ open, onClose, onConfirm }) => {
  const theme = useTheme();
  return (
    <Dialog
      open={open}
      onClose={onClose}
      aria-labelledby="delete-dialog"
      fullWidth
      maxWidth="xs"
    >
      <DialogTitle
        id="delete-dialog"
        sx={{
          gap: theme.spacing(1.25),
          display: "flex",
          flexDirection: "column",
          padding: theme.spacing(2),
          paddingBottom: theme.spacing(0),
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: theme.spacing(1),
            }}
          >
            <SvgColor src="/assets/icons/ic_delete.svg" />
            <Typography
              variant="m3"
              color={"text.primary"}
              fontWeight={"fontWeightBold"}
            >
              Delete Alerts
            </Typography>
          </Box>
          <IconButton onClick={onClose}>
            <Iconify
              icon="mdi:close"
              width={24}
              height={24}
              color="text.primary"
            />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent
        sx={{ paddingX: theme.spacing(2), paddingTop: theme.spacing(0) }}
      >
        <Typography
          variant="s1"
          fontWeight={"fontWeightRegular"}
          color="text.secondary"
        >
          Are you sure you want to delete these Alerts?
        </Typography>
      </DialogContent>

      <DialogActions
        sx={{ paddingX: theme.spacing(2), paddingBottom: theme.spacing(2) }}
      >
        <Button
          variant="outlined"
          color="inherit"
          onClick={onClose}
          sx={{ width: "90px" }}
        >
          <Typography
            variant="s2"
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            Cancel
          </Typography>
        </Button>
        <Button
          variant="contained"
          color="error"
          onClick={onConfirm}
          sx={{ width: "90px" }}
        >
          <Typography variant="s2" fontWeight={"fontWeightMedium"}>
            Delete
          </Typography>
        </Button>
      </DialogActions>
    </Dialog>
  );
};

DeleteConfirmation.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onConfirm: PropTypes.func.isRequired,
};

export default DeleteConfirmation;
