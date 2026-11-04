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
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React from "react";
import { useForm } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";

const NewVariableName = ({ open, onClose, handleAddVariable }) => {
  const {
    control,
    handleSubmit: handleFormSubmit,
    reset,
  } = useForm({
    defaultValues: {
      name: "",
    },
  });

  const onSubmitCreateColumn = (data) => {
    if (!data.name) {
      enqueueSnackbar("Please enter a name", { variant: "error" });
      return;
    }
    // const formData = new FormData();
    // formData.append("model_type", selected.datasetType);
    // formData.append("new_dataset_name", data.name);
    // mutate(formData);
    handleFormSubmit(handleAddVariable(data.name))();
  };

  const handleClose = () => {
    reset();
    onClose();
  };
  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle
        sx={{
          gap: "10px",
          display: "flex",
          flexDirection: "column",
          padding: 2,
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Typography fontWeight={600} fontSize="16px">
            Duplicate Dataset
          </Typography>
          <IconButton onClick={handleClose}>
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>
      </DialogTitle>
      <Box component="form" onSubmit={handleFormSubmit(onSubmitCreateColumn)}>
        <Box
          sx={{
            display: "flex",
            gap: 1,
            alignItems: "center",
            margin: "0 0 20px 20px",
          }}
        >
          <Iconify icon="solar:info-circle-bold" color="text.disabled" />
          <Typography fontSize="12px" color="text.primary">
            Enter a name to the new variable
          </Typography>
        </Box>
        <DialogContent
          sx={{
            paddingTop: 0.5,
          }}
        >
          <FormTextFieldV2
            autoFocus
            helperText="Enter a name"
            placeholder="Enter name"
            size="small"
            control={control}
            fieldName="name"
            fullWidth
          />
        </DialogContent>
        <DialogActions sx={{ padding: 2 }}>
          <Button onClick={handleClose} variant="outlined">
            Cancel
          </Button>
          <LoadingButton variant="contained" color="primary" type="submit">
            Add
          </LoadingButton>
        </DialogActions>
      </Box>
    </Dialog>
  );
};

NewVariableName.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  selected: PropTypes.any,
  handleAddVariable: PropTypes.func,
};

export default NewVariableName;
