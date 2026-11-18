// @ts-nocheck
import { zodResolver } from "@hookform/resolvers/zod";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  IconButton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import { emptySetValidationSchema } from "./validation";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "src/components/snackbar";
import { useNavigate } from "react-router";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import logger from "src/utils/logger";

/**
 * !IMPORTANT
 * This component is reused in multiple places like
 * - AddRowDrawer
 * - AddRowDrawer in simulate
 * if you make any change here please make sure you test in both places
 */

const SetEmptyRow = ({
  open,
  onClose,
  refreshGrid,
  datasetId,
  closeDrawer,
  onSuccess,
}) => {
  const { control, handleSubmit, reset } = useForm({
    defaultValues: { row: 5 },
    resolver: zodResolver(emptySetValidationSchema),
  });

  const onCloseClick = () => {
    onClose();
    reset();
  };

  const _navigate = useNavigate();

  const { mutate: createEmptyDataset, isPending } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.addEmptyRow(datasetId), data, {
        headers: { "Content-Type": "application/json" },
      }),
    onSuccess: () => {
      trackEvent(Events.addRowsSuccess, {
        [PropertyName.method]: "add empty row",
      });
      enqueueSnackbar("Dataset created successfully", {
        variant: "success",
      });
      onSuccess?.();
      refreshGrid(null, true);
      onCloseClick();
      closeDrawer();
    },
  });

  const onSubmit = (data) => {
    const requestBody = {
      num_rows: data.row,
    };
    createEmptyDataset(requestBody);
  };

  const onError = (errors) => {
    logger.error("Validation Errors:", errors);
  };

  return (
    <Dialog
      open={open}
      onClose={onCloseClick}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 560 } }}
      fullWidth
    >
      <Box padding={2}>
        <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <Typography
              variant="m3"
              fontWeight={"fontWeightBold"}
              color="text.primary"
            >
              Add Empty Rows
            </Typography>
            <IconButton onClick={onCloseClick}>
              <Iconify icon="solar:close-linear" color="text.primary" />
            </IconButton>
          </Box>
          <HelperText text="Add empty rows and columns to your dataset" />
        </Box>
        <DialogContent sx={{ padding: 0, margin: 0 }}>
          <FormSearchSelectFieldControl
            control={control}
            fieldName="row"
            size="small"
            label="No. of rows to create"
            options={Array.from({ length: 10 }, (_, i) => ({
              label: String(i + 1),
              value: i + 1,
            }))}
            style={{ width: "100%", marginTop: "10px" }}
          />
        </DialogContent>
        <DialogActions sx={{ padding: 0, margin: "32px 0 0" }}>
          <Button size="small" onClick={onCloseClick} variant="outlined">
            Cancel
          </Button>
          <LoadingButton
            onClick={handleSubmit(onSubmit, onError)}
            variant="contained"
            color="primary"
            size="small"
            loading={isPending}
          >
            Next
          </LoadingButton>
        </DialogActions>
      </Box>
    </Dialog>
  );
};

SetEmptyRow.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  datasetId: PropTypes.string,
  closeDrawer: PropTypes.func,
  onSuccess: PropTypes.func,
};

export default SetEmptyRow;
