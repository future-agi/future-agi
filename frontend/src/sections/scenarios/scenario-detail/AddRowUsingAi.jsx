import {
  Box,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useForm } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import { AddRowUsingAiValidationSchema } from "./validation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { useDevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import { LoadingButton } from "@mui/lab";

const AddRowUsingAiForm = ({ scenarioId, onClose }) => {
  const { control, handleSubmit } = useForm({
    defaultValues: {
      description: "",
      numRows: 10,
    },
    resolver: zodResolver(AddRowUsingAiValidationSchema),
  });

  const { refreshGrid } = useDevelopDetailContext();

  const { mutate: addRowUsingAi, isPending } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.scenarios.addRowUsingAi(scenarioId), data),
    onSuccess: () => {
      enqueueSnackbar("Rows added successfully", { variant: "success" });
      refreshGrid();
      onClose();
    },
  });

  const onSubmit = (data) => {
    addRowUsingAi({
      num_rows: data?.numRows,
      description: data?.description,
    });
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <FormTextFieldV2
        control={control}
        fieldName="numRows"
        label="No.of rows"
        placeholder="No of rows to generate"
        fieldType="number"
        size="small"
        fullWidth
        required
      />
      <FormTextFieldV2
        control={control}
        fieldName="description"
        label="Description"
        placeholder="Describe what type of data you want to add in rows"
        size="small"
        fullWidth
        required
        multiline
        rows={5}
      />
      <LoadingButton
        size="small"
        variant="contained"
        color="primary"
        fullWidth
        onClick={handleSubmit(onSubmit)}
        loading={isPending}
      >
        Add
      </LoadingButton>
    </Box>
  );
};

AddRowUsingAiForm.propTypes = {
  scenarioId: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
};

const AddRowUsingAi = ({ open, onClose, scenarioId }) => {
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle
        sx={{
          padding: 2,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography variant="m3" fontWeight="fontWeightMedium">
          Add rows using AI
        </Typography>
        <IconButton onClick={onClose}>
          <Iconify icon="mdi:close" color="text.primary" />
        </IconButton>
      </DialogTitle>
      <DialogContent sx={{ padding: 2 }}>
        <Box sx={{ paddingY: 0.5 }}>
          <AddRowUsingAiForm scenarioId={scenarioId} onClose={onClose} />
        </Box>
      </DialogContent>
    </Dialog>
  );
};

AddRowUsingAi.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  scenarioId: PropTypes.string.isRequired,
};

export default AddRowUsingAi;
