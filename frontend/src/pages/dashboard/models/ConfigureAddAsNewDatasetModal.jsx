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
import PropTypes from "prop-types";
import React from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import { useSnackbar } from "src/components/snackbar";
import axios, { endpoints } from "src/utils/axios";
import { useParams, useNavigate } from "react-router-dom";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { invalidateDatasetListCache } from "src/utils/cacheUtils";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const ConfigureAddAsNewDatasetModal = ({ open, onClose }) => {
  const { enqueueSnackbar } = useSnackbar();
  const navigate = useNavigate();
  const { individualExperimentId } = useParams();
  const queryClient = useQueryClient();

  const { control, handleSubmit, reset, watch } = useForm({
    defaultValues: {
      datasetName: "",
    },
  });

  const datasetName = watch("datasetName");

  const { mutate: addDatasetMutation, isPending: loading } = useMutation({
    mutationFn: () =>
      axios.post(endpoints.develop.addAsNewDataset(individualExperimentId), {
        name: datasetName,
      }),
    onSuccess: () => {
      trackEvent(Events.ddNewDatasetCreated, {
        [PropertyName.name]: datasetName,
      });

      enqueueSnackbar(`Dataset created Successfully`, {
        variant: "success",
      });

      // Invalidate dataset list cache to ensure dropdown refreshes
      invalidateDatasetListCache(queryClient);

      setTimeout(() => {
        navigate("/dashboard/develop/");
      }, 200);
    },
  });

  const handleSubmitForm = (data) => {
    addDatasetMutation(data);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  return (
    <Dialog open={open} onClose={handleClose} PaperProps={{}} fullWidth>
      <Box
        component="form"
        onSubmit={handleSubmit(handleSubmitForm)}
        sx={{ padding: 2 }}
      >
        <DialogTitle
          sx={{
            gap: "2px",
            display: "flex",
            flexDirection: "column",
            padding: 0,
            margin: 0,
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
              variant="m2"
              fontWeight={"fontWeightMedium"}
              color={"text.primary"}
            >
              Add as new dataset
            </Typography>
            <IconButton onClick={handleClose}>
              <Iconify icon="mdi:close" color="text.primary" />
            </IconButton>
          </Box>
          <HelperText text="Save this derived dataset as a new dataset" />
        </DialogTitle>

        <DialogContent
          sx={{
            display: "flex",
            flexDirection: "column",
            margin: 0,
            padding: 0,
          }}
        >
          <Box sx={{ paddingTop: 2 }}>
            <FormTextFieldV2
              label="Dataset Name"
              placeholder="Enter dataset name"
              autoFocus
              size="small"
              control={control}
              fieldName="datasetName"
              fullWidth
              required
            />
          </Box>
        </DialogContent>

        <DialogActions sx={{ padding: 0, marginTop: 4 }}>
          <Button onClick={handleClose} variant="outlined">
            <Typography
              variant="s2"
              fontWeight={"fontWeightSemiBold"}
              color="text.disabled"
            >
              Cancel
            </Typography>
          </Button>
          <LoadingButton
            type="submit"
            variant="contained"
            autoFocus
            color="primary"
            sx={{
              borderRadius: "10px",
            }}
            loading={loading} // Pass the loading state to the button
          >
            <Typography variant="s2" fontWeight={"fontWeightSemiBold"}>
              Save
            </Typography>
          </LoadingButton>
        </DialogActions>
      </Box>
    </Dialog>
  );
};

ConfigureAddAsNewDatasetModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};

export default ConfigureAddAsNewDatasetModal;
