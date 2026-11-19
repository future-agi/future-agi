import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React, { useEffect } from "react";
import { useForm } from "react-hook-form";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";

export default function EditKnowledgeBaseNameDialog({
  open,
  onClose,
  knowledgeId,
}) {
  const {
    control,
    reset,
    handleSubmit,
    setError,
    formState: { errors },
  } = useForm({
    defaultValues: { name: "" },
  });

  const queryClient = useQueryClient();

  const { data: knowledgeBaseList } = useKnowledgeBaseList();

  const currentKnowledge = knowledgeBaseList?.find(
    (option) => option.id === knowledgeId,
  );

  useEffect(() => {
    if (open && currentKnowledge) {
      reset({ name: currentKnowledge.name || "" });
    }
  }, [open, currentKnowledge, reset]);

  const { mutate, isPending } = useMutation({
    mutationFn: async (formData) => {
      const body = new FormData();
      body.append("name", formData?.name);
      body.append("kb_id", knowledgeId);

      return axios.patch(`${endpoints.knowledge.knowledgeBase}`, body, {
        headers: { "Content-Type": "multipart/form-data" },
      });
    },
    onSuccess: (data) => {
      enqueueSnackbar(
        `Knowledge base name has been update to ${data.data?.result?.name}`,
        { variant: "info" },
      );
      queryClient.invalidateQueries({ queryKey: ["knowledge-base"] });
      onClose(); // Close dialog after success
    },
    meta: {
      errorHandled: true,
    },
    onError: (data) => {
      if (data?.result?.includes("name must be unique")) {
        setError(
          "name",
          { type: "focus", message: data?.result },
          { shouldFocus: true },
        );
      }
    },
  });

  const onSubmit = (formData) => {
    if (!formData.name.trim()) {
      setError("name", { type: "manual", message: "Name is required" });
      return;
    }

    mutate(formData);
  };

  return (
    <Dialog
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 560 } }}
      open={open}
      onClose={onClose}
    >
      <DialogTitle sx={{ pb: "0" }}>
        <Stack
          direction={"row"}
          justifyContent={"space-between"}
          alignItems={"center"}
        >
          <Typography
            variant="m3"
            fontWeight={"fontWeightBold"}
            color={"text.primary"}
          >
            Edit Name
          </Typography>
          <IconButton
            disabled={isPending}
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
      </DialogTitle>
      <Box component={"form"} onSubmit={handleSubmit(onSubmit)}>
        <DialogContent>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 2,
              mt: "16px",
            }}
          >
            <FormTextFieldV2
              control={control}
              label={"Knowledge base"}
              fieldName="name"
              fullWidth
              placeholder="Name"
              size="small"
              errorMessage={errors.name?.message}
            />
          </Box>
        </DialogContent>

        <DialogActions sx={{ pt: "32px" }}>
          <Button
            sx={{ color: "text.disabled" }}
            onClick={onClose}
            disabled={isPending}
            variant="outlined"
            size="small"
          >
            Cancel
          </Button>
          <LoadingButton
            loading={isPending}
            variant="contained"
            color="primary"
            type="submit"
            size="small"
          >
            Save
          </LoadingButton>
        </DialogActions>
      </Box>
    </Dialog>
  );
}

EditKnowledgeBaseNameDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  knowledgeId: PropTypes.string,
};
