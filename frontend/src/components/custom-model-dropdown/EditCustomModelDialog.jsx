import React, { useRef } from "react";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Box,
  Typography,
  Chip,
  useTheme,
} from "@mui/material";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import LoadingButton from "@mui/lab/LoadingButton";
import APIKeyForm from "./APIKeyForm";
import Iconify from "../iconify";
import PropTypes from "prop-types";

const schema = z.object({
  provider: z.string().min(1, "Provider is required"),
  key: z.string().min(1, "Key is required"),
});

const EditCustomModelDialog = ({
  open,
  onClose,
  data,
  onSubmit,
  isPending,
}) => {
  const {
    control,
    handleSubmit,
    clearErrors,
    setError,
    setValue,
    formState: { isDirty },
  } = useForm({
    defaultValues: {
      key: JSON.stringify(data?.configJson || {}, null, 2),
      provider: data?.provider,
    },
    resolver: zodResolver(schema),
  });
  const theme = useTheme();
  const hasClearedOnceRef = useRef(false);
  const handleFormSubmit = (formData) => {
    try {
      const configJson = JSON.parse(formData?.key);
      clearErrors("key");

      const payload = {
        modelName: data.model_name,
        modelProvider: data.modelProvider,
        inputTokenCost: data.inputTokenCost,
        outputTokenCost: data.outputTokenCost,
        configJson,
      };

      onSubmit({ ...formData, configJson, payload });
    } catch (e) {
      setError("key", {
        type: "manual",
        message: "Invalid JSON format",
      });
    }
  };

  const onFocusInput = () => {
    if (hasClearedOnceRef.current) return;

    clearErrors("key");
    setValue("key", ""); // Clear on first focus only
    hasClearedOnceRef.current = true;

    // trackEvent(Events.apiKeyBoxClicked);
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{
        sx: {
          maxWidth: 720,
          minWidth: "720px",
          borderRadius: theme.spacing(1),
          padding: theme.spacing(2),
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(2),
        },
      }}
    >
      <DialogTitle sx={{ padding: 0 }}>
        <Typography
          typography={"m3"}
          color={"text.primary"}
          fontWeight={"fontWeightSemiBold"}
        >
          Edit {data?.userModelId}
        </Typography>
      </DialogTitle>
      <IconButton
        onClick={onClose}
        sx={{
          position: "absolute",
          top: theme.spacing(1.5),
          right: theme.spacing(1.5),
          color: "text.primary",
        }}
      >
        <Iconify icon="solar:close-linear" />
      </IconButton>
      <DialogContent sx={{ padding: 0 }}>
        <Box py={1} display={"flex"} flexDirection={"column"} gap={1}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSubmit(handleFormSubmit)();
            }}
          >
            <Chip
              label={"JSON"}
              sx={{
                marginBottom: 1,
                width: "max-content",
                backgroundColor: "action.hover",
                borderBottomLeftRadius: theme.spacing(0.5),
                color: "primary.main",
                typography: "s2",
                "& .MuiChip-label": {
                  padding: 0,
                },
                "&:hover": {
                  backgroundColor: "action.hover",
                  color: "primary.main",
                },
              }}
            />

            <APIKeyForm
              control={control}
              isJsonKey={true}
              readOnly={false}
              onFocusInput={onFocusInput}
            />

            <Box sx={{ display: "flex", justifyContent: "flex-end", mt: 2 }}>
              <LoadingButton
                variant="contained"
                color="primary"
                type="submit"
                loading={isPending}
                disabled={!isDirty}
                sx={{
                  minWidth: "200px",
                }}
              >
                Save
              </LoadingButton>
            </Box>
          </form>
        </Box>
      </DialogContent>
    </Dialog>
  );
};

EditCustomModelDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  data: PropTypes.object,
  onSubmit: PropTypes.func,
  isPending: PropTypes.bool,
};

export default EditCustomModelDialog;
