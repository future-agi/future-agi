import { Box, Drawer, IconButton, Typography } from "@mui/material";
import React, { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../../AccordianElements";
import PropTypes from "prop-types";
import ConfigureKeys from "../../Common/ConfigureKeys/ConfigureKeys";
import AddLabelInput from "./AddLabelInput";
import { zodResolver } from "@hookform/resolvers/zod";
import { ClassificationValidationSchema } from "./validation";
import { useParams } from "react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import PreviewAddColumn from "../PreviewAddColumn";
import { LoadingButton } from "@mui/lab";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useClassificationStore , useRerunDependentColumnsStore } from "../../states";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import { transformDynamicColumnConfig } from "../common";
import DynamicColumnSkeleton from "../DynamicColumnSkeleton";
import { ShowComponent } from "../../../../components/show";

const getDefaultValue = () => {
  return {
    columnId: "",
    labels: [],
    languageModelId: "",
    concurrency: "",
    newColumnName: "",
  };
};

export const ClassificationChild = ({
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
      labels: [],
      languageModelId: "",
      concurrency: "",
      newColumnName: "",
    },
    resolver: zodResolver(
      ClassificationValidationSchema(!!onFormSubmit, !!editId),
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
      axios.post(endpoints.develop.addColumns.classifyColumn(dataset), data),
    onSuccess: () => {
      enqueueSnackbar("Classification column created successfully", {
        variant: "success",
      });
      reset();
      // null for gridRef option and true for set column.
      refreshGrid(null, true);
      onClose();
    },
  });

  const [isApiConfigurationOpen, setApiConfigurationOpen] = useState(false);

  const {
    data: previewData,
    isSuccess,
    mutate: preview,
    isPending: isPreviewPending,
  } = useMutation({
    mutationFn: (data) =>
      axios.post(
        endpoints.develop.addColumns.preview(dataset, "classify"),
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
      enqueueSnackbar("Classification column updated successfully", {
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
    const { columnId, newColumnName, languageModelId, ...rest } = formValues;
    return {
      ...rest,
      column_id: columnId,
      new_column_name: newColumnName,
      language_model_id: languageModelId,
    };
  };

  const onSubmit = (formValues) => {
    if (editId) {
      updateColumn({
        config: transformFormToApi(formValues),
        operation_type: "classify",
      });
      return;
    }
    if (onFormSubmit) {
      onFormSubmit({ ...formValues, type: "classification" });
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
            {editId ? "Edit Classification" : "Classification"}
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
              required={!onFormSubmit}
              fieldName="newColumnName"
            />
          )}
          <FormSearchSelectFieldControl
            fullWidth
            required
            label="Column"
            size="small"
            control={control}
            fieldName="columnId"
            options={allColumns.map((column) => ({
              label: column.headerName,
              value: column.field,
            }))}
          />
          <Accordion defaultExpanded>
            <AccordionSummary>
              <Typography fontSize="12px" fontWeight={700}>
                Labels
              </Typography>
            </AccordionSummary>
            <AccordionDetails sx={{ padding: 0 }}>
              <AddLabelInput
                control={control}
                field="labels"
                required={!onFormSubmit}
              />
            </AccordionDetails>
          </Accordion>
          <CustomModelDropdownControl
            control={control}
            fieldName="languageModelId"
            label="Model"
            searchDropdown
            size="small"
            fullWidth
            required
            excludeCustomProviders
            inputSx={{
              "&.MuiInputLabel-root, .MuiInputLabel-shrink": {
                fontWeight: "fontWeightMedium",
                color: "text.disabled",
              },
              "&.Mui-focused.MuiInputLabel-shrink": {
                color: "text.disabled",
              },
              "& .MuiInputLabel-root.Mui-focused": {
                color: "text.secondary",
              },
            }}
            onModelConfigOpen={(model) => setApiConfigurationOpen(model)}
          />
          <ConfigureKeys
            open={Boolean(isApiConfigurationOpen)}
            onClose={() => setApiConfigurationOpen(false)}
          />
          <FormTextFieldV2
            label="Concurrency"
            size="small"
            required
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
              onClick={handlePreview}
              variant="outlined"
              fullWidth
              size="small"
            >
              Test
            </LoadingButton>
          )}
          <LoadingButton
            loading={isSubmitting || isUpdating}
            variant="contained"
            color="primary"
            fullWidth
            size="small"
            type="submit"
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

ClassificationChild.propTypes = {
  initialData: PropTypes.object,
  onFormSubmit: PropTypes.func,
  onClose: PropTypes.func,
  editId: PropTypes.string,
};

const Classification = ({ initialData, onFormSubmit }) => {
  // Using individual store
  const { openClassification, setOpenClassification } =
    useClassificationStore();

  const onClose = () => {
    setOpenClassification(false);
  };

  const editId = openClassification?.editId;

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
      open={openClassification}
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
        <ClassificationChild
          initialData={
            columnConfig
              ? transformDynamicColumnConfig("classification", columnConfig, [])
              : initialData
          }
          onFormSubmit={onFormSubmit}
          onClose={onClose}
          editId={editId}
        />
      </ShowComponent>
    </Drawer>
  );
};

Classification.propTypes = {
  initialData: PropTypes.object,
  onFormSubmit: PropTypes.func,
};

export default Classification;
