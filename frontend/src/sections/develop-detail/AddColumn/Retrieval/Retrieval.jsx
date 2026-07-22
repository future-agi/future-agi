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
import PineConeForm from "./PineConeForm";
import QdrantForm from "./QdrantForm";
import WeivateForm from "./WeivateForm";
import { RetrievalValidationSchema } from "./validation";
import { LoadingButton } from "@mui/lab";
import PreviewAddColumn from "../PreviewAddColumn";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useRetrievalStore } from "../../states";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import DynamicColumnSkeleton from "../DynamicColumnSkeleton";
import { ShowComponent } from "src/components/show";

const getDefaultValue = () => {
  return {
    newColumnName: "",
    subType: "",
  };
};

export const RetrievalChild = ({
  initialData,
  onFormSubmit,
  onClose,
  editId,
}) => {
  const { refreshGrid } = useDevelopDetailContext();
  // Using individual store

  const { control, handleSubmit, watch, reset } = useForm({
    defaultValues: {
      newColumnName: "",
      subType: "",
    },
    resolver: zodResolver(RetrievalValidationSchema(!!onFormSubmit, !!editId)),
  });

  const subType = watch("subType");

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
      axios.post(endpoints.develop.addColumns.addVectorDBColumn(dataset), data),
    onSuccess: () => {
      enqueueSnackbar("Vector DB column created successfully", {
        variant: "success",
      });
      refreshGrid(null, true);
      onClose();
    },
  });

  // const onSubmit = (formValues) => {
  //   addColumn(formValues);
  // };

  const renderForm = () => {
    if (subType === "pinecone") {
      return <PineConeForm control={control} allColumns={allColumns} />;
    } else if (subType === "qdrant") {
      return <QdrantForm control={control} allColumns={allColumns} />;
    } else if (subType === "weaviate") {
      return <WeivateForm control={control} allColumns={allColumns} />;
    }
  };

  const {
    data: previewData,
    isSuccess,
    mutate: preview,
    isPending: isPreviewPending,
  } = useMutation({
    mutationFn: (data) =>
      axios.post(
        endpoints.develop.addColumns.preview(dataset, "vector_db"),
        data,
      ),
    onSuccess: () => {
      enqueueSnackbar("Preview generated successfully", {
        variant: "success",
      });
    },
    onError: (error) => {
      enqueueSnackbar(error.message || "Failed to generate preview", {
        variant: "error",
      });
    },
  });

  const { mutate: updateColumn, isPending: _isUpdating } = useMutation({
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

  const transformFormToApi = (formValues) => {
    const { newColumnName, subType: st, columnId, kbId, ...rest } = formValues;
    return {
      ...rest,
      new_column_name: newColumnName,
      sub_type: st,
      ...(columnId && { column_id: columnId }),
      ...(kbId && { kb_id: kbId }),
    };
  };

  const onSubmit = (formValues) => {
    if (editId) {
      updateColumn({
        config: { ...transformFormToApi(formValues) },
        operation_type: "vector_db",
      });
      return;
    }
    if (onFormSubmit) {
      onFormSubmit({ ...formValues, type: "retrieval" });
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
            {editId ? "Edit Retrieval" : "Retrieval"}
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
              control={control}
              placeholder="Enter column name"
              fieldName="newColumnName"
            />
          )}
          <FormSearchSelectFieldControl
            control={control}
            fieldName="subType"
            size="small"
            label="Vector Database"
            options={[
              { label: "Pinecone", value: "pinecone" },
              { label: "Qdrant", value: "qdrant" },
              { label: "Weaviate", value: "weaviate" },
            ]}
            fullWidth
          />
          {renderForm()}
        </Box>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          {!onFormSubmit && (
            <LoadingButton
              variant="outlined"
              fullWidth
              size="small"
              loading={isPreviewPending}
              onClick={handlePreview}
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
            // onClick={handleSubmit(onSubmit)}
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

RetrievalChild.propTypes = {
  initialData: PropTypes.object,
  onFormSubmit: PropTypes.func,
  onClose: PropTypes.func,
  editId: PropTypes.string,
};

const Retrieval = ({ initialData, onFormSubmit }) => {
  // Using individual store
  const { openRetrieval, setOpenRetrieval } = useRetrievalStore();

  const onClose = () => {
    setOpenRetrieval(false);
  };

  const editId = openRetrieval?.editId;

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
      open={Boolean(openRetrieval)}
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
        <RetrievalChild
          initialData={columnConfig ?? initialData}
          onFormSubmit={onFormSubmit}
          onClose={onClose}
          editId={editId}
        />
      </ShowComponent>
    </Drawer>
  );
};

Retrieval.propTypes = {
  initialData: PropTypes.object,
  onFormSubmit: PropTypes.func,
};

export default Retrieval;
