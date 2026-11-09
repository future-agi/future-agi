import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import React, { useEffect } from "react";
import { useForm } from "react-hook-form";
import { useParams } from "react-router";
import { z } from "zod";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { FormSelectField } from "src/components/FormSelectField";
import { LoadingButton } from "@mui/lab";
import { ConfirmDialog } from "src/components/custom-dialog";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import {
  useDevelopFilterStoreShallow,
  useEditColumnTypeStore,
} from "../states";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";

// - "text"
// - "boolean"
// - "integer"
// - "float"
// - "json"
// - "array"
// - "datetime"
const ColumnTypes = [
  { label: "Text", value: "text" },
  { label: "Boolean", value: "boolean" },
  { label: "Integer", value: "integer" },
  { label: "Float", value: "float" },
  { label: "JSON", value: "json" },
  { label: "Array", value: "array" },
  { label: "Datetime", value: "datetime" },
  { label: "Image", value: "image" },
  { label: "Images", value: "images" },
  { label: "Audio", value: "audio" },
  { label: "Document", value: "document" },
];

const EditColumnType = () => {
  const { editColumnType, setEditColumnType } = useEditColumnTypeStore();
  const resetFilters = useDevelopFilterStoreShallow((s) => s.resetFilters);
  const [isAlert, setIsAlert] = React.useState(false);
  const { dataset } = useParams();
  const defaultValues = {
    newColumnType: editColumnType?.dataType || "",
  };
  const { refreshGrid } = useDevelopDetailContext();
  const queryClient = useQueryClient();

  const onClose = () => {
    setEditColumnType(null);
  };

  const {
    control,
    handleSubmit,
    reset: formReset,
    setValue,
  } = useForm({
    defaultValues,
    resolver: zodResolver(
      z.object({
        newColumnType: z.string().min(1, "Dataset name is required"),
      }),
    ),
  });

  useEffect(() => {
    if (editColumnType?.dataType) {
      setValue("newColumnType", editColumnType?.dataType);
    }
  }, [editColumnType, setValue]);

  const {
    mutate: updateColumn,
    isPending,
    error,
    isError,
    reset: resetMutation,
  } = useMutation({
    mutationFn: (d) =>
      axios.put(
        endpoints.develop.updateColumnType(dataset, editColumnType.id),
        d,
      ),
    onSuccess: () => {
      enqueueSnackbar("Column type updated successfully", {
        variant: "success",
      });
      refreshGrid();
      queryClient.invalidateQueries({
        queryKey: ["json-column-schema", dataset],
      });
      resetFilters();
      onCloseCallback();
      formReset();
    },
  });

  const onCloseCallback = () => {
    setIsAlert(false);
    formReset();
    resetMutation();
    onClose();
  };

  const onSubmit = (data) => {
    // @ts-ignore
    if (editColumnType?.dataType !== data?.newColumnType) {
      setIsAlert(true);
    } else {
      setIsAlert(false);
      updateColumn({
        new_column_type: data.newColumnType,
        preview: false,
        force_update: false,
      });
    }
    trackEvent(Events.editColumnTypeSuccessful, {
      [PropertyName.columnType]: {
        dataset: dataset,
        columnId: editColumnType.id,
        old_column_type: data.newColumnType,
        new_column_type: editColumnType.dataType,
      },
    });
  };

  const forceUpdate = (data) => {
    // @ts-ignore
    updateColumn({
      new_column_type: data.newColumnType,
      preview: false,
      force_update: true,
    });
  };

  return (
    <>
      {!isAlert && (
        <Dialog
          open={Boolean(editColumnType)}
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
                Update Column Type
              </Typography>
              <IconButton onClick={onCloseCallback}>
                <Iconify icon="solar:close-linear" />
              </IconButton>
            </Box>
          </DialogTitle>
          <form onSubmit={handleSubmit(onSubmit)}>
            <DialogContent
              sx={{
                paddingY: 1,
                display: "flex",
                flexDirection: "column",
                gap: 1,
              }}
            >
              <FormSelectField
                label="New Column Type"
                size="small"
                control={control}
                fieldName="newColumnType"
                fullWidth
                options={ColumnTypes}
                helperText="Select the new column type"
                MenuProps={{
                  sx: {
                    maxHeight: "250px",
                  },
                }}
              />
              {isError && (
                <Alert severity="error">
                  {error?.result?.message}{" "}
                  <b>You can also force update at your own risk</b>
                </Alert>
              )}
            </DialogContent>
            <DialogActions sx={{ padding: 2 }}>
              <Button onClick={onCloseCallback} size="small" variant="outlined">
                Cancel
              </Button>

              <LoadingButton
                variant="contained"
                color="primary"
                loading={isPending}
                disabled={isAlert}
                type="submit"
                size="small"
              >
                Update
              </LoadingButton>

              {isError && (
                <LoadingButton
                  variant="contained"
                  color="error"
                  loading={isPending}
                  disabled={!isError}
                  onClick={handleSubmit(forceUpdate)}
                >
                  Force Update
                </LoadingButton>
              )}
            </DialogActions>
          </form>
        </Dialog>
      )}
      {isAlert && (
        <ConfirmDialog
          content="Are you sure you want to proceed? This action can cause loss of data."
          action={
            <Button
              variant="contained"
              color="error"
              size="small"
              onClick={handleSubmit(forceUpdate)} // Ensure this triggers submission
            >
              Confirm
            </Button>
          }
          open={isAlert}
          onClose={onCloseCallback}
          title="Confirm Action"
          message="Are you sure you want to proceed? This action can cause your data loss."
        />
      )}
    </>
  );
};

EditColumnType.propTypes = {};

export default EditColumnType;
