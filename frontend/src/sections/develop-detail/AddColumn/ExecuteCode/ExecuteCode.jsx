import { Box, Drawer, IconButton, Typography } from "@mui/material";
import React, { useEffect } from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { FormCodeEditor } from "src/components/form-code-editor";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { enqueueSnackbar } from "src/components/snackbar";
import { zodResolver } from "@hookform/resolvers/zod";
import ExecuteCodeValidation from "./validation";
import { LoadingButton } from "@mui/lab";
import PreviewAddColumn from "../PreviewAddColumn";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useExecuteCodeStore , useRerunDependentColumnsStore } from "../../states";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import DynamicColumnSkeleton from "../DynamicColumnSkeleton";
import { ShowComponent } from "../../../../components/show";

const getDefaultValue = () => {
  return {
    code: `# Function name should be main only. You can access any column of the row using the kwargs.
def main(**kwargs):
    return kwargs.get("column_name")
`,
    newColumnName: "",
    concurrency: "",
  };
};

const editorOptions = {
  selectOnLineNumbers: true,
  roundedSelection: false,
  readOnly: false,
  cursorStyle: "line",
  automaticLayout: true,
  minimap: {
    enabled: false,
  },
  wordWrap: "on",
};

export const ExecuteCodeChild = ({
  initialData,
  onFormSubmit,
  onClose,
  editId,
}) => {
  const { refreshGrid } = useDevelopDetailContext();
  const { setRerunDependentColumns } = useRerunDependentColumnsStore();

  const { control, handleSubmit, reset } = useForm({
    defaultValues: {
      code: `# Function name should be main only. You can access any column of the row using the kwargs.
def main(**kwargs):
    return kwargs.get("column_name")
`,
      newColumnName: "",
      concurrency: "",
    },
    resolver: zodResolver(ExecuteCodeValidation(!!onFormSubmit, !!editId)),
  });

  const { dataset } = useParams();
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
      axios.post(endpoints.develop.addColumns.executeCode(dataset), data),
    onSuccess: () => {
      enqueueSnackbar("Custom code column created successfully", {
        variant: "success",
      });
      refreshGrid(null, true);
      onClose();
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
        endpoints.develop.addColumns.preview(dataset, "execute_code"),
        data,
      ),
    onSuccess: () => {
      enqueueSnackbar("Preview generated successfully", {
        variant: "success",
      });
    },
  });

  const { mutate: updateColumn } = useMutation({
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
    const { newColumnName, ...rest } = formValues;
    return {
      ...rest,
      new_column_name: newColumnName,
    };
  };

  const onSubmit = (formValues) => {
    if (editId) {
      updateColumn({
        config: transformFormToApi(formValues),
        operation_type: "execute_code",
      });
      return;
    }
    if (onFormSubmit) {
      onFormSubmit({ ...formValues, type: "extract_code" });
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
        //@ts-ignore
        onSubmit={handleSubmit(onSubmit)}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Typography fontWeight={700} color="text.secondary">
            {editId ? "Edit Execute Code" : "Execute Custom Code"}
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
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
            <Typography fontWeight={700} fontSize="12px" color="text.secondary">
              Python code to be executed
            </Typography>
            <Box sx={{ borderRadius: "8px", overflow: "hidden" }}>
              <FormCodeEditor
                height="300px"
                defaultLanguage="python"
                control={control}
                fieldName="code"
                options={editorOptions}
              />
            </Box>
          </Box>

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
              variant="outlined"
              fullWidth
              size="small"
              //@ts-ignore
              onClick={handlePreview}
              loading={isPreviewPending}
            >
              Test
            </LoadingButton>
          )}
          <LoadingButton
            variant="contained"
            color="primary"
            fullWidth
            size="small"
            type="submit"
            loading={isSubmitting}
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

ExecuteCodeChild.propTypes = {
  onClose: PropTypes.func,
  initialData: PropTypes.object,
  onFormSubmit: PropTypes.func,
  editId: PropTypes.string,
};

const ExecuteCode = ({ initialData, onFormSubmit }) => {
  // Using individual store
  const { openExecuteCode, setOpenExecuteCode } = useExecuteCodeStore();

  const onClose = () => {
    setOpenExecuteCode(false);
  };

  const editId = openExecuteCode?.editId;

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
      open={openExecuteCode}
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
        <ExecuteCodeChild
          initialData={columnConfig ?? initialData}
          onFormSubmit={onFormSubmit}
          onClose={onClose}
          editId={editId}
        />
      </ShowComponent>
    </Drawer>
  );
};

ExecuteCode.propTypes = {
  initialData: PropTypes.object,
  onFormSubmit: PropTypes.func,
};

export default ExecuteCode;
