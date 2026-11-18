import { LoadingButton } from "@mui/lab";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import SvgColor from "src/components/svg-color";
import { useAlertStore } from "../store/useAlertStore";

export default function DuplicateAlert({ open, onClose, onAction, isLoading }) {
  const theme = useTheme();
  const { duplicateAlertName: value, setDuplicateAlertName } = useAlertStore();

  const onChange = (value) => {
    setDuplicateAlertName(value);
  };

  const handleClose = () => {
    setDuplicateAlertName("");
    onClose();
  };

  return (
    <Dialog
      PaperProps={{
        sx: {
          p: 2,
          maxWidth: "560px",
          minWidth: "560px",
        },
      }}
      open={open}
      onClose={handleClose}
    >
      <DialogTitle
        sx={{
          display: "flex",
          flexDirection: "row",
          justifyContent: "space-between",
          alignItems: "center",
          padding: 0,
          gap: 4,
        }}
      >
        <Stack direction={"column"} gap={0.25}>
          <Typography
            variant="m2"
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            Duplicate alert
          </Typography>
          <Typography>
            Duplicate and create a new alert with same configurations
          </Typography>
        </Stack>
        <IconButton onClick={handleClose}>
          <SvgColor
            sx={{
              bgcolor: "text.primary",
            }}
            src="/assets/icons/ic_close.svg"
          />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{
          padding: `${theme.spacing(2.5, 0, 0, 0)} !important`,
        }}
      >
        <TextField
          fullWidth
          label={"Alert Name"}
          placeholder="Alert name"
          size="small"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      </DialogContent>
      <DialogActions
        sx={{
          p: 0,
          mt: 4,
        }}
      >
        <Button
          size="small"
          onClick={() => {
            handleClose();
          }}
          variant="outlined"
        >
          Cancel
        </Button>
        <LoadingButton
          loading={isLoading}
          disabled={!value}
          color={"primary"}
          size="small"
          variant="contained"
          onClick={() => {
            if (!value) return;
            onAction();
          }}
        >
          Duplicate
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
}

DuplicateAlert.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onAction: PropTypes.func,
  isLoading: PropTypes.bool,
};
