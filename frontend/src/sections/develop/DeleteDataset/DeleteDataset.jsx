import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React from "react";
import { useNavigate } from "react-router";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import axios, { endpoints } from "src/utils/axios";
import { trackEvent, Events } from "src/utils/Mixpanel";

const DeleteDataset = ({ open, onClose, refreshGrid, selected, redirect }) => {
  const navigate = useNavigate();
  const { mutate: handleDeleteDataset, isPending } = useMutation({
    mutationFn: () => {
      const payload = {
        data: { dataset_ids: selected?.map((item) => item.id) },
      };
      return axios.delete(endpoints.develop.deleteDataset(), payload);
    },
    onSuccess: () => {
      trackEvent(Events.datasetDeleteClicked);
      enqueueSnackbar(
        `${selected?.length > 1 ? `${selected?.length} Datasets have been deleted` : `Dataset has been deleted`}`,
        {
          variant: "success",
        },
      ),
        onClose();
      refreshGrid?.();
      if (redirect) {
        navigate("/dashboard/develop");
      }
    },
  });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 400 } }}
      fullWidth
    >
      <Box sx={{ padding: 2 }}>
        <DialogTitle sx={{ padding: 0, margin: 0 }}>
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
              Delete Dataset
              {selected?.length > 1 && "s"}
            </Typography>
            <IconButton onClick={onClose}>
              <Iconify icon="solar:close-linear" color="text.primary" />
            </IconButton>
          </Box>
          <HelperText
            text={`Are you sure you want to delete ${selected?.length > 1 ? `the selected ${selected?.length} datasets?` : "this dataset?"}`}
          />
        </DialogTitle>
        <Box>
          <DialogActions sx={{ padding: 0, marginTop: "32px" }}>
            <Button
              onClick={() => {
                trackEvent(Events.datasetDeleteCancelled);
                onClose();
              }}
              variant="outlined"
            >
              <Typography variant="s2" fontWeight={"fontWeightSemiBold"}>
                Cancel
              </Typography>
            </Button>
            <LoadingButton
              variant="contained"
              loading={isPending}
              onClick={handleDeleteDataset}
              sx={{
                backgroundColor: "status.danger.solid",
                color: "common.white",
              }}
              // sx={{ color: "#fff", backgroundColor: "rgba(219, 47, 45, 1)" }}
            >
              <Typography variant="s2" fontWeight={"fontWeightSemiBold"}>
                Delete
              </Typography>
            </LoadingButton>
          </DialogActions>
        </Box>
      </Box>
    </Dialog>
  );
};

DeleteDataset.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  selected: PropTypes.any,
  redirect: PropTypes.bool,
};

export default DeleteDataset;
