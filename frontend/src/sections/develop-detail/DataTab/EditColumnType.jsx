import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
import { COLUMN_TYPES, typeChangeConfirmText } from "./columnTypeText";

const ColumnTypes = COLUMN_TYPES;

const EditColumnType = () => {
  const { editColumnType, setEditColumnType } = useEditColumnTypeStore();
  const resetFilters = useDevelopFilterStoreShallow((s) => s.resetFilters);
  const [isAlert, setIsAlert] = React.useState(false);
  // The type the confirm is about (the form's pick when Update was pressed).
  const [pendingType, setPendingType] = React.useState(null);
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
    onSuccess: (res) => {
      // The server says what happened (how many values were emptied).
      enqueueSnackbar(
        res?.data?.result?.message || "Column type updated successfully",
        { variant: "success" },
      );
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
    setPendingType(null);
    formReset();
    resetMutation();
    onClose();
  };

  const onSubmit = (data) => {
    // @ts-ignore
    if (editColumnType?.dataType !== data?.newColumnType) {
      setPendingType(data.newColumnType);
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

  // K4F (merge M12): before a forced change, ask how many values do not fit
  // (`preview: true` changes nothing and answers `invalid_count`), so the
  // confirm says plainly what is emptied, in every version.
  const preview = useQuery({
    queryKey: ["column-type-preview", dataset, editColumnType?.id, pendingType],
    queryFn: () =>
      axios.put(
        endpoints.develop.updateColumnType(dataset, editColumnType.id),
        {
          new_column_type: pendingType,
          preview: true,
          force_update: true,
        },
      ),
    select: (res) => res?.data?.result ?? null,
    enabled: Boolean(isAlert && pendingType && editColumnType?.id),
    retry: false,
    staleTime: 0,
    gcTime: 0,
  });
  const invalidCount = preview.isError
    ? null
    : preview.isSuccess
      ? Number(preview.data?.invalid_count ?? 0)
      : undefined;
  const pendingLabel =
    ColumnTypes.find((t) => t.value === pendingType)?.label ?? pendingType;
  const confirmText = typeChangeConfirmText({
    invalidCount,
    type: pendingType,
    label: pendingLabel,
  });

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
              <IconButton
                aria-label="Close edit column type"
                onClick={onCloseCallback}
              >
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
          content={
            <Typography
              variant="body2"
              color="text.secondary"
              data-testid="edit-column-type-confirm-text"
            >
              {confirmText}
            </Typography>
          }
          action={
            <LoadingButton
              variant="contained"
              color="error"
              size="small"
              sx={{ px: 2.5 }}
              loading={isPending}
              disabled={invalidCount === undefined}
              onClick={handleSubmit(forceUpdate)} // Ensure this triggers submission
            >
              Confirm
            </LoadingButton>
          }
          open={isAlert}
          onClose={onCloseCallback}
          title={`Change “${editColumnType?.name ?? "column"}” to ${pendingLabel}?`}
        />
      )}
    </>
  );
};

EditColumnType.propTypes = {};

export default EditColumnType;
