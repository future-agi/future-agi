import { Box, Drawer, IconButton, Typography } from "@mui/material";
import React, { useEffect } from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { zodResolver } from "@hookform/resolvers/zod";
import { useParams } from "react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { ExtractJsonKeyValidationSchema } from "./validation";
import PreviewAddColumn from "../PreviewAddColumn";
import { LoadingButton } from "@mui/lab";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { useExtractJsonKeyStore , useRerunDependentColumnsStore } from "../../states";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import DynamicColumnSkeleton from "../DynamicColumnSkeleton";
import { ShowComponent } from "../../../../components/show";

const getDefaultValue = () => {
  return {
    columnId: "",
    jsonKey: "",
    newColumnName: "",
    concurrency: "",
  };
};

export const ExtractJsonKeyChild = ({
  initialData,
  onFormSubmit,
  onClose,
  editId,
}) => {
  const { refreshGrid } = useDevelopDetailContext();
  const { setRerunDependentColumns } = useRerunDependentColumnsStore();

  const { control, handleSubmit, reset } = useForm({
    defaultValues: {
      columnId: "",
      jsonKey: "",
      newColumnName: "",
      concurrency: "",
    },
    resolver: zodResolver(
      ExtractJsonKeyValidationSchema(!!onFormSubmit, !!editId),
    ),
  });

  const { dataset } = useParams();
  const allColumns = useDatasetColumnConfig(dataset);
  useEffect(() => {
    if (initialData) {
      reset(initialData);
    } else if (!editId) {
      // Reset to default values when opening for new column (no editId)
      reset(getDefaultValue());
    }
  }, [initialData, reset, editId]);

  const { mutate: addColumn, isPending: isSubmitting } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.addColumns.extractJsonKey(dataset), data),
    onSuccess: () => {
      enqueueSnackbar("Extract JSON column created successfully", {
        variant: "success",
      });
      refreshGrid(null, true);
      onClose();
    },
  });

  // const onSubmit = (formValues) => addColumn(formValues);

  const {
    data: previewData,
    isSuccess,
    mutate: preview,
    isPending: isPreviewPending,
  } = useMutation({
    mutationFn: (data) =>
      axios.post(
        endpoints.develop.addColumns.preview(dataset, "extract_json"),
        data,
      ),
    onSuccess: () => {
      enqueueSnackbar("Preview generated successfully", {
        variant: "success",
      });
    },
  });

  const { mutate: updateColumn, isPending: isUpdating } = useMutation({
    mutationFn: (data) =>
      axios.post(
        endpoints.develop.addColumns.updateDynamicColumn(editId),
        data,
      ),
    onSuccess: () => {
      enqueueSnackbar("API Call column updated successfully", {
        variant: "success",
      });
      refreshGrid();
      onClose();
    
      try {
        axios.get(endpoints.develop.addColumns.getDependentColumns(editId)).then((res) => {
          if (res.data?.result?.dependents?.length > 0) {
            setRerunDependentColumns({
              sourceColumnId: editId,
              dependents: res.data.result.dependents,
            });
          }
        });
      } catch (err) {
        console.error("Failed to check dependent columns", err);
      }},
  });

  const transformFormToApi = (formValues) => {
    const { columnId, newColumnName, jsonKey, ...rest } = formValues;
    return {
      ...rest,
      column_id: columnId,
      new_column_name: newColumnName,
      json_key: jsonKey,
    };
  };

  const onSubmit = (formValues) => {
    if (editId) {
      updateColumn({
        config: transformFormToApi(formValues),
        operation_type: "extract_json",
      });
      return;
    }
    if (onFormSubmit) {
      onFormSubmit({ ...formValues, type: "extract_json" });
    } else {
      addColumn(transformFormToApi(formValues));
    }
  };

  const handlePreview = handleSubmit((formValues) => {
    if (!onFormSubmit) {
      preview(transformFormToApi(formValues));
    }
  });

  return (
    <Box sx={{ display: "flex", height: "100vh" }}>
      {!onFormSubmit && (
        <PreviewAddColumn open={isSuccess} previewData={previewData} />
      )}
      <Box
        sx={{
          padding: "20px",
          display: "flex",
          flexDirection: "column",
          gap: 2,
          height: "100%",
          width: "550px",
        }}
        component="form"
        onSubmit={handleSubmit(onSubmit)}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            mb: 2,
          }}
        >
          <Typography fontWeight={700} color="text.secondary">
            {editId ? "Edit Extract JSON Key" : "Extract JSON Key"}
          </Typography>
          <IconButton
            onClick={() => {
              reset();
              onClose();
            }}
            size="small"
          >
            <Iconify icon="mingcute:close-line" />
          </IconButton>
        </Box>
        <Box
          sx={{
            gap: 2,
            display: "flex",
            flexDirection: "column",
            flex: 1,
            overflow: "auto",
            pt: onFormSubmit ? 2 : 1,
          }}
        >
          {!onFormSubmit && !editId && (
            <FormTextFieldV2
              label="Name"
              size="small"
              placeholder="Enter column name"
              control={control}
              fieldName="newColumnName"
            />
          )}
          <FormSearchSelectFieldControl
            fullWidth
            label="Column"
            size="small"
            control={control}
            fieldName="columnId"
            options={allColumns
              ?.filter((column) => column.dataType === "json")
              ?.map((column) => ({
                label: column.headerName,
                value: column.field,
              }))}
            noOptions={"No suitable column"}
          />
          <FormTextFieldV2
            label="JSON Key"
            size="small"
            control={control}
            fieldName="jsonKey"
            placeholder="Enter JSON Path of the key (to be extracted)"
          />

          <FormTextFieldV2
            label="Concurrency"
            size="small"
            control={control}
            placeholder="Enter concurrency"
            fieldName="concurrency"
            fieldType="number"
          />
        </Box>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          {!onFormSubmit && (
            <LoadingButton
              loading={isPreviewPending}
              variant="outlined"
              fullWidth
              size="small"
              onClick={handlePreview}
            >
              Test
            </LoadingButton>
          )}
          <LoadingButton
            loading={isSubmitting || isUpdating}
            type="submit"
            variant="contained"
            color="primary"
            fullWidth
            size="small"
          >
            {editId
              ? "Update Column"
              : onFormSubmit
                ? "Save"
                : "Create New Column"}
          </LoadingButton>
        </Box>
      </Box>
    </Box>
  );
};

ExtractJsonKeyChild.propTypes = {
  initialData: PropTypes.object,
  onFormSubmit: PropTypes.func,
  onClose: PropTypes.func,
  editId: PropTypes.string,
};

const ExtractJsonKey = ({ initialData, onFormSubmit }) => {
  // Using individual store
  const { openExtractJsonKey, setOpenExtractJsonKey } =
    useExtractJsonKeyStore();

  const onClose = () => {
    setOpenExtractJsonKey(false);
  };

  const editId = openExtractJsonKey?.editId;

  const { data: columnConfig, isLoading: isLoadingColumnConfig } = useQuery({
    queryKey: ["dynamic-column-config", editId],
    queryFn: () =>
      axios.get(endpoints.develop.addColumns.getColumnConfig(editId)),
    enabled: Boolean(editId),
    select: (data) => data?.data?.result?.metadata,
  });

  return (
    <Drawer
      anchor="right"
      open={openExtractJsonKey}
      onClose={onClose}
      variant="persistent"
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 2,
          boxShadow: "-10px 0px 100px #00000035",
          borderRadius: "10px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      {isLoadingColumnConfig && (
        <Box sx={{ minWidth: "510px", height: "100%" }}>
          <DynamicColumnSkeleton />
        </Box>
      )}
      <ShowComponent condition={!isLoadingColumnConfig}>
        <ExtractJsonKeyChild
          initialData={columnConfig ?? initialData}
          onFormSubmit={onFormSubmit}
          onClose={onClose}
          editId={editId}
        />
      </ShowComponent>
    </Drawer>
  );
};

ExtractJsonKey.propTypes = {
  initialData: PropTypes.object,
  onFormSubmit: PropTypes.func,
};

export default ExtractJsonKey;
