import React, { useEffect } from "react";
import PropTypes from "prop-types";
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
import { LoadingButton } from "@mui/lab";
import { Controller, useForm } from "react-hook-form";
import Iconify from "src/components/iconify";

export default function DuplicateTaskDialog({
  open,
  onClose,
  defaultName = "",
  onSubmit,
  isSubmitting = false,
}) {
  const theme = useTheme();

  const {
    control,
    handleSubmit,
    trigger,
    formState: { isValid },
  } = useForm({
    mode: "onChange",
    defaultValues: {
      taskName: defaultName,
    },
    resolver: async (values) => {
      const name = values.taskName ?? "";
      const validationErrors = {};
      if (!name || name.trim().length === 0) {
        validationErrors.taskName = {
          type: "required",
          message: "Task name is required",
        };
      } else if (name.length > 255) {
        validationErrors.taskName = {
          type: "maxLength",
          message: "Task name must not exceed 255 characters",
        };
      }
      return {
        values: Object.keys(validationErrors).length === 0 ? values : {},
        errors: validationErrors,
      };
    },
  });

  useEffect(() => {
    trigger("taskName");
  }, [trigger]);

  const handleFormSubmit = (data) => {
    if (data?.taskName) {
      onSubmit(data.taskName.trim());
    }
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle
        id="duplicate-task-dialog-title"
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
            color="text.primary"
            fontWeight="fontWeightBold"
          >
            Duplicate Task
          </Typography>
          <IconButton onClick={onClose} disabled={isSubmitting}>
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
          <Typography
            variant="s1"
            fontWeight="fontWeightRegular"
            color="text.secondary"
          >
            Create a new task by duplicating the current configuration
          </Typography>
        </Box>

        <Controller
          name="taskName"
          control={control}
          render={({ field, fieldState }) => (
            <TextField
              {...field}
              label="Task Name"
              placeholder="Enter Task Name"
              fullWidth
              variant="outlined"
              size="small"
              autoFocus
              disabled={isSubmitting}
              error={!!fieldState.error}
              helperText={fieldState.error?.message}
            />
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
          disabled={isSubmitting}
          sx={{ width: "90px" }}
        >
          <Typography
            variant="s2"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            Cancel
          </Typography>
        </Button>
        <LoadingButton
          variant="contained"
          color="primary"
          onClick={handleSubmit(handleFormSubmit)}
          loading={isSubmitting}
          disabled={!isValid}
          sx={{
            width: "90px",
            "&:disabled": {
              color: "common.white",
              backgroundColor: "action.hover",
            },
          }}
        >
          <Typography
            variant="s2"
            color="white.50"
            fontWeight="fontWeightMedium"
          >
            Create
          </Typography>
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
}

DuplicateTaskDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  defaultName: PropTypes.string,
  onSubmit: PropTypes.func.isRequired,
  isSubmitting: PropTypes.bool,
};
