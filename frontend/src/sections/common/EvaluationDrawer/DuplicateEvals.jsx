import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";

import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import axios, { endpoints } from "src/utils/axios";

const DuplicateEvals = ({ open, onClose, evalId, onSubmit }) => {
  const { control, handleSubmit, reset, setValue, formState } = useForm({
    mode: "onChange", // Evaluates isValid as the user types
    defaultValues: {
      name: "",
    },
    resolver: zodResolver(
      z.object({
        name: z
          .string()
          .trim()
          .min(1, "Name is required")
          .regex(
            /^[a-z0-9_-]+$/,
            "Only lowercase alphanumeric characters, dashes, and underscores are allowed",
          ),
      }),
    ),
  });

  const { mutate, isPending } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.eval.duplicateEvalsTemplate, data),
    onSuccess: (data) => {
      enqueueSnackbar("Evaluation has been duplicated", {
        variant: "success",
      });
      handleClose();
      onSubmit?.(data?.data?.result);
    },
    //When the backend returns a 400/500 or network error, the Axios error interceptor rejects with customError, React Query catches it, isPending turns false, the loading spinner stops, and nothing is shown to the user.
    onError: (error) => {
      const message =
        error?.response?.data?.message ||
        error?.response?.data?.detail ||
        error?.result ||
        error?.message ||
        "Failed to duplicate evaluation";
      enqueueSnackbar(message, { variant: "error" });
    },
  });

  const handleNameTransformation = (event) => {
    const originalValue = event.target.value;
    const transformedValue = originalValue
      .toLowerCase()
      .replace(/[^a-z0-9-]/g, "_");

    // shouldValidate and shouldDirty ensure formState.isValid updates immediately
    setValue("name", transformedValue, {
      shouldValidate: true,
      shouldDirty: true,
    });
  };

  const onDuplicate = (data) => {
    const payload = {
      name: data.name,
      eval_template_id: evalId,
    };
    mutate(payload);
  };

  const handleClose = () => {
    reset();
    onClose?.();
  };

  return (
    <Dialog
      onClick={(e) => e.stopPropagation()}
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
    >
      <Box sx={{ padding: 2 }}>
        <DialogTitle sx={{ padding: 0, margin: 0, gap: "2px" }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography
              fontWeight="fontWeightMedium"
              color="text.primary"
              typography="m2"
            >
              Duplicate evaluation
            </Typography>
            <IconButton onClick={handleClose}>
              <Iconify icon="mdi:close" color="text.primary" />
            </IconButton>
          </Box>
          <HelperText text="Duplicate and create a new evaluation with same configurations" />
        </DialogTitle>

        <Box component="form" onSubmit={handleSubmit(onDuplicate)}>
          <DialogContent
            sx={{
              padding: 0,
              display: "flex",
              flexDirection: "column",
              gap: "20px",
              paddingTop: 2,
            }}
          >
            <FormTextFieldV2
              autoFocus
              placeholder="Enter evaluation name"
              label="Evaluation Name"
              size="small"
              control={control}
              fieldName="name"
              onChange={handleNameTransformation}
              fullWidth
            />
          </DialogContent>

          <DialogActions sx={{ padding: 0, marginTop: 4 }}>
            <Button
              onClick={handleClose}
              variant="outlined"
              disabled={isPending}
            >
              Cancel
            </Button>
            <LoadingButton
              color="primary"
              variant="contained"
              loading={isPending}
              disabled={!formState.isValid}
              type="submit"
            >
              Duplicate
            </LoadingButton>
          </DialogActions>
        </Box>
      </Box>
    </Dialog>
  );
};

DuplicateEvals.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  evalId: PropTypes.string,
  onSubmit: PropTypes.func,
};

export default DuplicateEvals;
