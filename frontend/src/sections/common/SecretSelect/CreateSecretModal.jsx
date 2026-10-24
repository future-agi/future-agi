import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { LoadingButton } from "@mui/lab";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const CreateSecretModal = ({ open, onClose }) => {
  const { control, handleSubmit, reset } = useForm({
    defaultValues: {
      name: "",
      description: "",
      key: "",
    },
  });

  const onCloseClick = () => {
    reset();
    onClose();
  };

  const queryClient = useQueryClient();

  const { mutateAsync: createSecret, isPending } = useMutation({
    mutationFn: (data) => {
      return axios.post(endpoints.secrets.create, data);
    },
    onSuccess: () => {
      enqueueSnackbar("Secret created successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["secrets"] });
      onCloseClick();
    },
  });

  // "secretType": "API_KEY",

  return (
    <Dialog
      open={open}
      onClose={onCloseClick}
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
    >
      <Box
        component="form"
        onSubmit={(e) => {
          e.preventDefault();
          e.stopPropagation();
          handleSubmit(createSecret)(e);
        }}
      >
        <DialogTitle sx={{ padding: 2 }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            Create Secret
            <IconButton onClick={onCloseClick}>
              <Iconify icon="solar:close-linear" />
            </IconButton>
          </Box>
        </DialogTitle>
        <DialogContent
          sx={{ display: "flex", flexDirection: "column", gap: 2, padding: 2 }}
        >
          <Box sx={{ paddingTop: 0.5 }} />
          <FormTextFieldV2
            label="Name"
            control={control}
            fieldName="name"
            placeholder="Enter name"
            size="small"
          />
          <FormTextFieldV2
            label="Description"
            control={control}
            placeholder="Enter description"
            fieldName="description"
            size="small"
          />
          <FormTextFieldV2
            label="Secret"
            control={control}
            fieldName="key"
            placeholder="Enter key"
            size="small"
          />
        </DialogContent>
        <DialogActions sx={{ padding: 2 }}>
          <Button variant="outlined" color="inherit" onClick={onCloseClick}>
            Cancel
          </Button>
          <LoadingButton
            variant="contained"
            color="primary"
            type="submit"
            loading={isPending}
          >
            Create
          </LoadingButton>
        </DialogActions>
      </Box>
    </Dialog>
  );
};

CreateSecretModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
};

export default CreateSecretModal;
