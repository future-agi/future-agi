import { Box, Drawer, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useRef } from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../../AccordianElements";
import AddFieldInput from "./AddFieldInput";
import RequestBody from "./RequestBody";
import { zodResolver } from "@hookform/resolvers/zod";
import { getAddColumnApiCallValidation } from "./validation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { enqueueSnackbar } from "src/components/snackbar";
import PreviewAddColumn from "../PreviewAddColumn";
import { LoadingButton } from "@mui/lab";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useAddColumnApiCallStore } from "../../states";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import { ShowComponent } from "src/components/show";
import DynamicColumnSkeleton from "../DynamicColumnSkeleton";
import { transformDynamicColumnConfig } from "../common";

const getDefaultValue = () => {
  return {
    columnName: "",
    config: {
      url: "",
      method: "POST",
      params: [],
      headers: [],
      body: "",
      outputType: "string",
    },
    concurrency: "",
  };
};

const OutputTypeOptions = [
  { label: "String", value: "string" },
  { label: "Object", value: "object" },
  { label: "Array", value: "array" },
  { label: "Number", value: "number" },
];

const RequestTypeOptions = [
  { label: "GET", value: "GET" },
  { label: "POST", value: "POST" },
  { label: "PUT", value: "PUT" },
  { label: "DELETE", value: "DELETE" },
  { label: "PATCH", value: "PATCH" },
];

export const AddColumnApiCallChild = ({
  initialData,
  onFormSubmit,
  onClose,
  editId,
}) => {
  const { dataset } = useParams();

  const { refreshGrid } = useDevelopDetailContext();
  const queryClient = useQueryClient();

  const allColumns = useDatasetColumnConfig(dataset);

  const { control, handleSubmit, reset } = useForm({
    defaultValues: getDefaultValue(),
    resolver: zodResolver(
      getAddColumnApiCallValidation(allColumns, !!onFormSubmit, !!editId),
    ),
  });

  // Track which editId we've already loaded data for so that background
  // re-renders (e.g. React Query refetches on window-focus) don't silently
  // overwrite the user's in-progress edits via reset().
  const loadedEditIdRef = useRef(null);

  useEffect(() => {
    if (initialData && loadedEditIdRef.current !== editId) {
      reset(initialData);
      loadedEditIdRef.current = editId;
    } else if (!editId) {
      // Reset to default values when opening for new column (no editId)
      reset(getDefaultValue());
      loadedEditIdRef.current = null;
    }
  }, [initialData, reset, editId]);

  const { mutate: addColumn, isPending: isSubmitting } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.addColumns.apiCall(dataset), data),
    onSuccess: () => {
      enqueueSnackbar("API Call column created successfully", {
        variant: "success",
      });
      refreshGrid();
      onClose();
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
      queryClient.invalidateQueries({
        queryKey: ["dynamic-column-config", editId],
      });
      loadedEditIdRef.current = null;
      refreshGrid();
      onClose();
    },
    onError: () => {
      enqueueSnackbar("Failed to update API Call column", {
        variant: "error",
      });
    },
  });

  const {
    data: previewData,
    isSuccess,
    mutate: preview,
    isPending: isPreviewPending,
  } = useMutation({
    mutationFn: (data) =>
      axios.post(
        endpoints.develop.addColumns.preview(dataset, "api_call"),
        data,
      ),
    onSuccess: () => {
      enqueueSnackbar("Preview generated successfully", {
        variant: "success",
      });
    },
  });

  const transformFormToApi = (formValues) => {
    const { columnName, ...rest } = formValues;
    const { outputType, ...configRest } = rest.config || {};
    return {
      ...rest,
      config: {
        ...configRest,
        output_type: outputType,
      },
      column_name: columnName,
    };
  };

  const onSubmit = (formValues) => {
    if (editId) {
      updateColumn({
        config: { ...transformFormToApi(formValues) },
        operation_type: "api_call",
      });
      return;
    }
    if (onFormSubmit) {
      onFormSubmit({ ...formValues, type: "api_call" });
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
        // @ts-ignore
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
            {editId ? "Edit API Call" : "Add API Call"}
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
          <ShowComponent condition={!editId && !onFormSubmit}>
            <FormTextFieldV2
              label="Name"
              size="small"
              placeholder="Enter name"
              control={control}
              fieldName="columnName"
            />
          </ShowComponent>
          <FormSearchSelectFieldControl
            fullWidth
            label="Output Type"
            size="small"
            control={control}
            fieldName="config.outputType"
            options={OutputTypeOptions}
          />
          <RequestBody
            control={control}
            contentFieldName="config.url"
            allColumns={allColumns}
            placeholder="Enter api endpoint"
            multiline={false}
            label="Add API Endpoint"
            showHelper={false}
          />

          <FormSearchSelectFieldControl
            fullWidth
            label="Request Type"
            size="small"
            control={control}
            fieldName="config.method"
            options={RequestTypeOptions}
          />
          <FormTextFieldV2
            label="Concurrency"
            size="small"
            control={control}
            placeholder="Enter concurrency"
            fieldName="concurrency"
            fieldType="number"
          />
          <Accordion defaultExpanded>
            <AccordionSummary>
              <Typography fontWeight={700} fontSize="12px">
                Params
              </Typography>
            </AccordionSummary>
            <AccordionDetails sx={{ padding: 0 }}>
              <AddFieldInput
                control={control}
                fieldName="config.params"
                allColumns={allColumns}
              />
            </AccordionDetails>
          </Accordion>
          <Accordion defaultExpanded>
            <AccordionSummary>
              <Typography fontWeight={700} fontSize="12px">
                Headers
              </Typography>
            </AccordionSummary>
            <AccordionDetails sx={{ padding: 0 }}>
              <AddFieldInput
                control={control}
                fieldName="config.headers"
                allColumns={allColumns}
              />
            </AccordionDetails>
          </Accordion>
          <Accordion defaultExpanded>
            <AccordionSummary>
              <Typography fontWeight={700} fontSize="12px">
                Request Body
              </Typography>
            </AccordionSummary>
            <AccordionDetails sx={{ padding: 0 }}>
              <RequestBody
                allColumns={allColumns}
                contentFieldName="config.body"
                control={control}
              />
            </AccordionDetails>
          </Accordion>
        </Box>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          {!onFormSubmit && (
            <LoadingButton
              loading={isPreviewPending}
              onClick={handlePreview}
              variant="outlined"
              fullWidth
              size="small"
            >
              Test
            </LoadingButton>
          )}
          <LoadingButton
            type="submit"
            loading={isSubmitting || isUpdating}
            variant="contained"
            color="primary"
            fullWidth
            size="small"
            // onClick={handleSubmit(addColumn)}
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

AddColumnApiCallChild.propTypes = {
  initialData: PropTypes.object,
  onFormSubmit: PropTypes.func,
  onClose: PropTypes.func,
  editId: PropTypes.string,
};

const AddColumnApiCall = ({ initialData, onFormSubmit }) => {
  const { dataset } = useParams();
  const { openAddColumnApiCall, setOpenAddColumnApiCall } =
    useAddColumnApiCallStore();

  const onClose = () => {
    setOpenAddColumnApiCall(false);
  };

  const editId = openAddColumnApiCall?.editId;

  const { data: columnConfig, isLoading: isLoadingColumnConfig } = useQuery({
    queryKey: ["dynamic-column-config", editId],
    queryFn: () =>
      axios.get(endpoints.develop.addColumns.getColumnConfig(editId)),
    enabled: Boolean(editId),
    select: (data) => data?.data?.result?.metadata,
  });

  const allColumns = useDatasetColumnConfig(dataset, true);

  // Memoize so the child receives a stable reference and its useEffect
  // does not fire on every parent re-render.
  const memoizedInitialData = useMemo(() => {
    return columnConfig
      ? transformDynamicColumnConfig("api_call", columnConfig, allColumns)
      : initialData;
  }, [columnConfig, allColumns, initialData]);

  return (
    <Drawer
      anchor="right"
      open={openAddColumnApiCall}
      onClose={onClose}
      variant="persistent"
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 1300,
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
        <AddColumnApiCallChild
          initialData={memoizedInitialData}
          onFormSubmit={onFormSubmit}
          onClose={onClose}
          editId={editId}
        />
      </ShowComponent>
    </Drawer>
  );
};

AddColumnApiCall.propTypes = {
  initialData: PropTypes.object,
  onFormSubmit: PropTypes.func,
};

export default AddColumnApiCall;
