import { Box, Drawer, IconButton, Typography } from "@mui/material";
import React, { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import ConfigureKeys from "../../Common/ConfigureKeys/ConfigureKeys";
import { ExtractEntitiesValidationSchema } from "./validation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useParams } from "react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import PreviewAddColumn from "../PreviewAddColumn";
import { LoadingButton } from "@mui/lab";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useExtractEntitiesStore } from "../../states";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import DynamicColumnSkeleton from "../DynamicColumnSkeleton";
import { ShowComponent } from "../../../../components/show";

const getDefaultValue = () => {
  return {
    column_id: "",
    instruction: "",
    language_model_id: "",
    concurrency: "",
    new_column_name: "",
  };
};

export const ExtractEntitiesChild = ({
  initialData,
  onFormSubmit,
  onClose,
  editId,
}) => {
  const { refreshGrid } = useDevelopDetailContext();

  const { control, handleSubmit, reset } = useForm({
    defaultValues: getDefaultValue(),
    resolver: zodResolver(
      ExtractEntitiesValidationSchema(!!onFormSubmit, !!editId),
    ),
  });
  const [isApiConfigurationOpen, setApiConfigurationOpen] = useState(false);

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
      axios.post(endpoints.develop.addColumns.extractEntities(dataset), data),
    onSuccess: () => {
      enqueueSnackbar("Extract Entities column created successfully", {
        variant: "success",
      });
      reset();
      // null for gridRef option and true for set column.
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
        endpoints.develop.addColumns.preview(dataset, "extract_entities"),
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
    },
  });

  const onSubmit = (formValues) => {
    if (editId) {
      updateColumn({
        config: formValues,
        operation_type: "extract_entities",
      });
      return;
    }
    if (onFormSubmit) {
      onFormSubmit({ ...formValues, type: "extract_entities" });
    } else {
      addColumn(formValues);
    }
  };

  const handlePreview = handleSubmit((formValues) => {
    if (!onFormSubmit) {
      preview(formValues);
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
            {editId ? "Edit Extract Entities" : "Extract Entities"}
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
              fieldName="new_column_name"
              required={!onFormSubmit}
            />
          )}
          <FormSearchSelectFieldControl
            control={control}
            fieldName="column_id"
            size="small"
            label="Column"
            required
            options={allColumns.map((column) => ({
              label: column.headerName,
              value: column.field,
            }))}
            fullWidth
          />
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
            <Typography fontWeight={700} fontSize="12px" color="text.secondary">
              Instructions (To extract entities)
            </Typography>
            <FormTextFieldV2
              control={control}
              fieldName="instruction"
              label=""
              multiline
              required
              minRows={4}
              maxRows={4}
              sx={{
                backgroundColor: "background.neutral",
                borderRadius: "10px",
              }}
              placeholder="Enter instructions here"
            />
          </Box>
          <CustomModelDropdownControl
            control={control}
            fieldName="language_model_id"
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
            control={control}
            required
            fieldName="concurrency"
            fieldType="number"
            placeholder="Enter concurrency"
          />
        </Box>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          {!onFormSubmit && (
            <LoadingButton
              onClick={handlePreview}
              variant="outlined"
              fullWidth
              size="small"
              loading={isPreviewPending}
            >
              Test
            </LoadingButton>
          )}
          <LoadingButton
            type="submit"
            variant="contained"
            color="primary"
            fullWidth
            size="small"
            loading={isSubmitting || isUpdating}
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

ExtractEntitiesChild.propTypes = {
  initialData: PropTypes.object,
  onFormSubmit: PropTypes.func,
  onClose: PropTypes.func,
  editId: PropTypes.string,
};

const ExtractEntities = ({ initialData, onFormSubmit }) => {
  // Using individual store
  const { openExtractEntities, setOpenExtractEntities } =
    useExtractEntitiesStore();

  const onClose = () => {
    setOpenExtractEntities(false);
  };

  const editId = openExtractEntities?.editId;

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
      open={openExtractEntities}
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
        <ExtractEntitiesChild
          initialData={columnConfig ?? initialData}
          onFormSubmit={onFormSubmit}
          onClose={onClose}
          editId={editId}
        />
      </ShowComponent>
    </Drawer>
  );
};

ExtractEntities.propTypes = {
  initialData: PropTypes.object,
  onFormSubmit: PropTypes.func,
};

export default ExtractEntities;
