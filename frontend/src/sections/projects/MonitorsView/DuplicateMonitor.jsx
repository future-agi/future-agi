import React from "react";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { Controller } from "react-hook-form";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

const DuplicateMonitor = ({
  open,
  onClose,
  control,
  handleSubmit,
  onSubmit,
  isValid,
}) => {
  const theme = useTheme();
  return (
    <Dialog open={open} onClose={onClose} fullWidth>
      <DialogTitle
        id="delete-dialog"
        sx={{
          gap: "10px",
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
          <Typography
            variant="m3"
            color={"text.primary"}
            fontWeight={"fontWeightBold"}
          >
            Duplicate Alert
          </Typography>
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
        sx={{ paddingX: theme.spacing(2), paddingTop: theme.spacing(0.5) }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 3 }}>
          {/* <Iconify icon="solar:info-circle-bold" color="text.disabled" width={18} /> */}
          <Typography
            variant="s1"
            fontWeight={"fontWeightRegular"}
            color="text.secondary"
          >
            Create a new for the alert that you want to duplicate
          </Typography>
        </Box>
        <Controller
          name="monitorName"
          control={control}
          defaultValue=""
          rules={{
            required: "Alert name is required", // Validation rule
          }}
          render={({ field, fieldState }) => (
            <>
              <TextField
                {...field}
                label="Enter Alert Name"
                placeholder="Enter Alert Name"
                fullWidth
                variant="outlined"
                size="small"
                autoFocus
                error={!!fieldState.error}
              />
              {fieldState.error ? (
                <Typography color="error" fontSize="12px" mt={1}>
                  {fieldState.error.message}
                </Typography>
              ) : null}
            </>
          )}
        />
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
          color="primary"
          onClick={handleSubmit(onSubmit)}
          sx={{
            width: "90px",
            "&:disabled": {
              color: "common.white",
              backgroundColor: "action.hover",
            },
          }}
          disabled={!isValid}
        >
          <Typography
            variant="s2"
            color={"white.50"}
            fontWeight={"fontWeightMedium"}
          >
            Create
          </Typography>
        </Button>
      </DialogActions>
    </Dialog>
  );
};

DuplicateMonitor.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  control: PropTypes.object.isRequired,
  handleSubmit: PropTypes.func.isRequired,
  onSubmit: PropTypes.func.isRequired,
  isValid: PropTypes.bool.isRequired,
};

export default DuplicateMonitor;
