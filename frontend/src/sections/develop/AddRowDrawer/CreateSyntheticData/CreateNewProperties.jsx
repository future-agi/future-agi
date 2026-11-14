import { LoadingButton } from "@mui/lab";
import {
  Box,
  Dialog,
  DialogActions,
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
import HelperText from "src/sections/develop-detail/Common/HelperText";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

const CreateNewProperties = ({ open, onClose, data, setOptions, update }) => {
  const { control, reset, watch } = useForm({
    defaultValues: {
      label: "",
      description: "",
    },
  });
  const label = watch("label");
  const description = watch("description");

  const handleClose = () => {
    reset();
    onClose();
  };

  const handleAdd = () => {
    trackEvent(Events.syntheticDatasetNewPropertyCreated, {
      [PropertyName.propName]: label,
      [PropertyName.propDesc]: description,
    });
    setOptions({ label, description, value: label });
    update(Number(data), { type: label, value: "", category: "" });
    handleClose();
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      aria-labelledby="confirm-dialog-title"
      aria-describedby="confirm-dialog-description"
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 560 } }}
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
          <Typography
            fontWeight={"fontWeightMedium"}
            color="text.primary"
            variant="m2"
          >
            Add New Property
          </Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="solar:close-linear" color="text.primary" />
          </IconButton>
        </Box>
      </DialogTitle>
      <Box>
        <Box
          sx={{
            display: "flex",
            gap: 1,
            alignItems: "center",
            margin: "-10px 0 20px 15px",
          }}
        >
          <HelperText text="Create new properties to generate synthetic data" />
        </Box>
        <DialogContent sx={{ paddingX: 2 }}>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: "20px",
              paddingTop: "5px",
            }}
          >
            <FormTextFieldV2
              autoFocus
              label="Property Name"
              placeholder="Enter property name"
              size="small"
              control={control}
              fieldName="label"
              fullWidth
            />
            <FormTextFieldV2
              label="Description"
              size="small"
              control={control}
              fieldName="description"
              fullWidth
              multiline
              rows={3}
              placeholder="Enter property description"
            />
          </Box>
        </DialogContent>
      </Box>
      <DialogActions sx={{ padding: 2 }}>
        <LoadingButton onClick={handleClose} autoFocus variant="outlined">
          <Typography
            variant="s2"
            fontWeight={"fontWeightSemiBold"}
            color="text.secondary"
          >
            Cancel
          </Typography>
        </LoadingButton>
        <LoadingButton
          onClick={handleAdd}
          autoFocus
          variant="contained"
          color="primary"
        >
          <Typography variant="s2" fontWeight={"fontWeightSemiBold"}>
            Create
          </Typography>
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

export default CreateNewProperties;

CreateNewProperties.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  data: PropTypes.number,
  setOptions: PropTypes.func,
  update: PropTypes.func,
};
