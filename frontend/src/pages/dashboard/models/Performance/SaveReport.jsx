import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React from "react";
import { useForm } from "react-hook-form";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { useParams } from "react-router";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const SaveReport = ({
  open,
  onClose,
  datasets,
  filters,
  breakdown,
  aggregation,
  startDate,
  endDate,
}) => {
  const { control, handleSubmit, reset } = useForm({
    defaultValues: {
      name: "",
    },
  });

  const { id } = useParams();

  const onCloseClick = () => {
    reset();
    onClose();
  };

  const { mutate, isPending } = useMutation({
    mutationFn: (d) => axios.post(endpoints.performanceReport.create(id), d),
    onSuccess: () => {
      enqueueSnackbar("Report saved successfully", { variant: "success" });
      onCloseClick();
    },
  });

  const onSubmit = (d) => {
    mutate({
      name: d.name,
      datasets,
      filters,
      breakdown,
      aggregation,
      startDate,
      endDate,
    });
  };

  return (
    <Dialog open={open} onClose={onCloseClick} fullWidth maxWidth="xs">
      <DialogTitle>Save Report</DialogTitle>
      <DialogContent>
        <Box sx={{ paddingY: 1 }}>
          <FormTextFieldV2
            fullWidth
            size="small"
            label="Report Name"
            placeholder="Enter report name"
            control={control}
            fieldName="name"
          />
        </Box>
      </DialogContent>
      <DialogActions>
        <Button variant="outlined" size="small" onClick={onCloseClick}>
          Cancel
        </Button>
        <LoadingButton
          loading={isPending}
          variant="contained"
          size="small"
          type="submit"
          color="primary"
          onClick={handleSubmit(onSubmit)}
        >
          Save
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

SaveReport.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  datasets: PropTypes.object,
  filters: PropTypes.object,
  breakdown: PropTypes.object,
  aggregation: PropTypes.string,
  startDate: PropTypes.string,
  endDate: PropTypes.string,
};

export default SaveReport;
