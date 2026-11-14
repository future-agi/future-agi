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
import React, { useEffect } from "react";
import { useForm } from "react-hook-form";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import { z } from "zod";
import axios, { endpoints } from "src/utils/axios";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import {
  useDevelopFilterStoreShallow,
  useEditColumnNameStore,
} from "../states";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import { useSearchParams } from "react-router-dom";
import PropTypes from "prop-types";

const EditColumnName = ({ onSuccess }) => {
  const { dataset: paramId } = useParams();
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();

  const dataset = paramId ?? searchParams.get("datasetId");
  const { editColumnName, setEditColumnName } = useEditColumnNameStore();
  const { refreshGrid } = useDevelopDetailContext();
  const resetFilters = useDevelopFilterStoreShallow((s) => s.resetFilters);

  const onClose = () => {
    setEditColumnName(null);
  };

  const defaultValues = { newColumnName: editColumnName?.name };

  const {
    control,
    handleSubmit,
    reset: formReset,
  } = useForm({
    defaultValues,
    resolver: zodResolver(
      z.object({
        newColumnName: z.string().min(1, "Dataset name is required"),
      }),
    ),
  });

  useEffect(() => {
    if (!editColumnName) return;
    formReset({ newColumnName: editColumnName?.name });
  }, [editColumnName, formReset]);

  const {
    mutate: updateColumn,
    isPending,
    reset: resetMutation,
  } = useMutation({
    mutationFn: (d) =>
      axios.put(
        endpoints.develop.updateColumnName(dataset, editColumnName.id),
        d,
      ),
    onSuccess: () => {
      enqueueSnackbar("Column name updated successfully", {
        variant: "success",
      });
      onSuccess?.(null, true);
      refreshGrid();
      resetFilters();
      // Invalidate derived variables cache since column names affect dot notation paths
      queryClient.invalidateQueries({
        queryKey: ["dataset-derived-variables", dataset],
      });
      queryClient.invalidateQueries({
        predicate: (query) =>
          query.queryKey[0] === "derived-variables" ||
          query.queryKey[0] === "derived-variable-schema",
      });
      queryClient.invalidateQueries({
        queryKey: ["json-column-schema", dataset],
      });
      onCloseCallback();
    },
  });

  const onCloseCallback = () => {
    formReset();
    onClose();
    resetMutation();
  };

  const onSubmit = (data) => {
    // @ts-ignore
    updateColumn({ new_column_name: data.newColumnName });
    trackEvent(Events.editColumnNameSuccessful, {
      [PropertyName.columnName]: {
        dataset: dataset,
        columnId: editColumnName.id,
        old_column_name: data.newColumnName,
        new_column_name: editColumnName.name,
      },
    });
  };

  return (
    <Dialog
      open={Boolean(editColumnName)}
      onClose={onCloseCallback}
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
            Edit Column Name
          </Typography>
          <IconButton onClick={onCloseCallback}>
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>
      </DialogTitle>
      <form onSubmit={handleSubmit(onSubmit)}>
        <DialogContent
          sx={{
            paddingTop: 0.5,
          }}
        >
          <FormTextFieldV2
            autoFocus
            label="Column Name"
            placeholder="Enter column name"
            size="small"
            control={control}
            fieldName="newColumnName"
            fullWidth
          />
        </DialogContent>
        <DialogActions sx={{ padding: 2 }}>
          <Button onClick={onCloseCallback} variant="outlined">
            Cancel
          </Button>
          <LoadingButton
            variant="contained"
            color="primary"
            loading={isPending}
            type="submit"
          >
            Save
          </LoadingButton>
        </DialogActions>
      </form>
    </Dialog>
  );
};

EditColumnName.propTypes = {
  onSuccess: PropTypes.func,
};

export default EditColumnName;
