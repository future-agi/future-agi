import { zodResolver } from "@hookform/resolvers/zod";
import { LoadingButton } from "@mui/lab";
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
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React from "react";
import { useForm } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import axios, { endpoints } from "src/utils/axios";
import { z } from "zod";

const DuplicatePersonas = ({ open, onClose, personaId }) => {
  const queryClient = useQueryClient();
  const {
    control,
    handleSubmit,
    reset,
    setValue: _setValue,
    formState,
  } = useForm({
    defaultValues: {
      name: "",
    },
    resolver: zodResolver(
      z.object({ name: z.string().min(1, "Name is required") }),
    ),
  });

  const { mutate: duplicatePersona, isPending } = useMutation({
    mutationFn: ({ name, id }) => {
      return axios.post(endpoints.persona.duplicate(id), { name });
    },
    onSuccess: () => {
      enqueueSnackbar("Persona duplicated successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["personas"] });
      handleClose();
    },
    onError: (err) => {
      enqueueSnackbar(err?.result || "Failed to duplicate persona", {
        variant: "error",
      });
    },
  });

  const onDuplicate = (data) => {
    duplicatePersona({
      id: personaId,
      name: data.name,
    });
  };

  const handleClose = () => {
    reset();
    onClose();
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
              fontWeight={"fontWeightMedium"}
              color="text.primary"
              typography="m2"
            >
              Duplicate persona
            </Typography>
            <IconButton onClick={handleClose}>
              <Iconify icon="mdi:close" color="text.primary" />
            </IconButton>
          </Box>
          <HelperText text="Duplicate and create a new persona with same configurations" />
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
              placeholder="Enter persona name"
              label="Persona Name"
              size="small"
              control={control}
              fieldName="name"
              fullWidth
              inputProps={{ "aria-label": "Duplicate persona name" }}
            />
          </DialogContent>
          <DialogActions sx={{ padding: 0, marginTop: 4 }}>
            <Button onClick={handleClose} variant="outlined">
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

export default DuplicatePersonas;

DuplicatePersonas.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  personaId: PropTypes.string,
  onSubmit: PropTypes.func,
};
