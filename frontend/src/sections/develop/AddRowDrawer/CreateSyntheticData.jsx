import React, { useState, useMemo, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Drawer,
  IconButton,
  Typography,
  Button,
  Divider,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useForm, useFieldArray, FormProvider } from "react-hook-form";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { useMutation } from "@tanstack/react-query";
import CreateDescriptions from "./CreateDescriptions";
import AddColumnField from "./CreateSyntheticData/AddColumnField";
import FormTextLabelField from "src/components/FormTextLabelField/FormTextLabelField";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useNavigate } from "react-router";
import { ConfirmDialog } from "src/components/custom-dialog";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import { FormSelectField } from "src/components/FormSelectField";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { getRequestErrorMessage } from "src/utils/errorUtils";

const createValidationSchema = (datasetId, openDescription) =>
  z.object({
    name: z.string().refine(
      (value) => (!datasetId ? value?.trim().length > 0 : true), // Conditional validation based on `x`
      { message: "Name is required" },
    ), // Use .min(1) instead of .nonempty()
    kb_id: z.string().optional(),
    description: z.string().min(1, "Description is required"), // Optional field
    useCase: z.string().optional(), //.min(1, "Use case is required"), // Use .min(1) instead of .nonempty()
    pattern: z.string().optional(), //.min(1, "Pattern is required"), // Use .min(1) instead of .nonempty()
    rowNumber: z
      .string()
      .refine(
        (value) => (value?.trim().length > 0 ? Number(value) >= 10 : false),
        { message: "Row number must be at least 10" },
      ),
    columns: z
      .array(
        z.object({
          name: z.string().min(1, "Column name is required"), // Use .min(1) instead of .nonempty()
          description: openDescription
            ? z.string().min(1, "Column Description is required")
            : z.string().optional(),
          // z.string().optional(), //.min(1, "Column Description is required"), // Optional description
          data_type: z.string().min(1, "Column Data Type is required"),
          property: z.array(
            z.object({
              type: z.string().min(1, "Property type is required"), // Use .min(1) instead of .nonempty()
              value: z.string().min(1, "Property value is required"), // Use .min(1) instead of .nonempty()
            }),
          ),
          // .min(1, "Property value is required"),
        }),
      )
      .min(1, "At least one column is required"),
  });

const SyntheticDataDrawer = ({
  open,
  onClose,
  datasetId,
  refreshGrid,
  knowledgeId,
}) => {
  const [openDescription, setOpenDescription] = useState(false);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const validationSchema = createValidationSchema(datasetId, openDescription);
  const navigate = useNavigate();

  const { data: knowledgeBaseList, refetch: refetchKnowledgeBaseList } =
    useKnowledgeBaseList("", { enabled: !!open }, { status: true });

  const knowledgeBaseOptions = useMemo(
    () =>
      (knowledgeBaseList || []).map(({ id, name }) => ({
        label: name,
        value: id,
      })),
    [knowledgeBaseList],
  );

  const {
    handleSubmit,
    control,
    reset,
    setValue,
    formState: { errors, isDirty },
    ...methods
  } = useForm({
    defaultValues: {
      name: "",
      description: "",
      useCase: "",
      pattern: "",
      rowNumber: "",
      kb_id: "",
      columns: [
        {
          name: "",
          data_type: "",
          property: [],
          description: "",
        },
      ],
    },
    resolver: zodResolver(validationSchema),
  });
  const { fields, append, remove } = useFieldArray({
    name: "columns",
    control,
  });

  useEffect(() => {
    if (knowledgeId) {
      setValue("kb_id", knowledgeId);
    }
  }, [knowledgeId, open]);

  useEffect(() => {
    if (open) {
      refetchKnowledgeBaseList();
    }
  }, [open]);

  const handleAddColumn = () => {
    append({
      name: "",
      data_type: "",
      property: [],
      description: "",
    });
  };

  const handleClose = () => {
    reset();
    onClose();
    setOpenDescription(false);
  };

  const { mutate: createSyntheticData, isPending } = useMutation({
    mutationFn: (data) => {
      if (datasetId) {
        return axios.post(
          endpoints.develop.addSyntheticDataset(datasetId),
          data,
        );
      } else {
        return axios.post(endpoints.develop.createSyntheticDataset, data);
      }
    },
    onSuccess: (res) => {
      if (!datasetId) {
        const data = res?.data?.result?.data;
        setTimeout(() => {
          navigate(`/dashboard/develop/${data?.id}?tab=data`);
        }, 0);
      }
      enqueueSnackbar(
        res?.data?.result?.message || "Dataset uploaded successfully",
        {
          variant: "success",
        },
      );
      onCloseClick(null, true);
      reset();
      refreshGrid(null, true);
    },
    onError: (error) => {
      enqueueSnackbar(
        getRequestErrorMessage(error, "Failed to create synthetic dataset", {
          retryAction: "creating this synthetic dataset",
        }),
        { variant: "error" },
      );
    },
  });

  const onSubmit = (formData) => {
    if (!openDescription) {
      setOpenDescription(true);
      return;
    }
    trackEvent(Events.dataAddSuccessfull, {
      [PropertyName.method]: "add using synthetic data",
    });
    const cols = {};
    const replaceColumn = (data, key) => {
      let incomingText = data || "";
      formData.columns.forEach(({ name }) => {
        const pattern = new RegExp(`{{${name}}}`, "g");
        if (incomingText && incomingText?.length)
          incomingText = incomingText.replace(pattern, cols[name]);
      });
      cols[key] = incomingText;
      return incomingText;
    };

    trackEvent(Events.syntheticDatasetCreationSuccessful, {
      [PropertyName.name]: formData.name,
      [PropertyName.description]: formData.description,
      [PropertyName.pattern]: formData.pattern,
      [PropertyName.useCase]: formData.useCase,
    });

    const formattedColumns = formData.columns.map((col) => ({
      Column_Name: col.name,
      Column_Type: col.data_type,
      Property: col.property,
      Property_Value: col.description,
    }));

    trackEvent(Events.syntheticDatasetCreationSuccessfulColumn, {
      [PropertyName.column]: formattedColumns,
    });

    const payload = {
      dataset: {
        name: formData.name,
        description: formData.description,
        objective: formData.useCase,
        patterns: formData.pattern,
      },
      num_rows: Number(formData.rowNumber),
      kb_id: formData?.kb_id,
      columns: formData.columns.map((item, index) => {
        const property = {};
        item.property.forEach((dummy) => {
          property[dummy.type] = dummy.value;
        });
        if (index === 0) {
          cols[item.name] = item.description;
        }
        const newItem = {
          ...item,
          description: replaceColumn(item.description, item.name),
          property,
          ...(datasetId && { is_new: true, skip: true }),
        };
        return newItem;
      }),
    };

    if (!formData?.kb_id) {
      delete payload.kb_id;
    }
    // console.log("formData", formData, payload);
    createSyntheticData(payload);
  };

  const onCloseClick = (_, skipConfirmation = false) => {
    if (!skipConfirmation && isDirty) {
      setIsDialogOpen(true);
    } else {
      handleClose();
    }
  };
  const handleDialogClose = (confirm) => {
    if (confirm) {
      handleClose();
    }
    setIsDialogOpen(false);
  };

  const knowledgeBaseDropdownDisabled = Boolean(knowledgeId);

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onCloseClick}
      PaperProps={{
        sx: {
          height: "100vh",
          // width: "550px",
          position: "fixed",
          zIndex: 9999,
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
      <FormProvider {...methods}>
        <Box
          sx={{ display: "flex", height: "100vh", flexDirection: "row" }}
          component="form"
          onSubmit={handleSubmit(onSubmit)}
        >
          <CreateDescriptions
            open={openDescription}
            onClose={() => setOpenDescription(false)}
            fields={fields}
            control={control}
            // columns={watchFieldArray}
          />
          <Box
            sx={{
              padding: 2,
              display: "flex",
              flexDirection: "column",
              gap: 2,
              height: "100%",
              width: "550px",
            }}
          >
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <Typography
                fontWeight={"fontWeightMedium"}
                color="text.primary"
                variant="m3"
              >
                Create Synthetic Data
              </Typography>
              <IconButton onClick={onCloseClick} size="small">
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
                paddingTop: "5px",
                marginTop: "-5px",
              }}
            >
              {!datasetId && (
                <FormTextFieldV2
                  label="Name"
                  size="small"
                  placeholder="Enter name"
                  control={control}
                  fieldName="name"
                  fullWidth
                  required
                />
              )}

              <FormTextLabelField
                label="Description*"
                control={control}
                fieldName="description"
                fullWidth
                placeholder=" Enter your dataset description."
                multiline
                rows={3}
              />

              <FormSelectField
                disabled={knowledgeBaseDropdownDisabled}
                label="Knowledge base"
                size="small"
                control={control}
                fieldName={`kb_id`}
                fullWidth
                options={knowledgeBaseOptions}
                isSearchable
                allowClear
                sx={{
                  mt: "8px",
                }}
              />

              <FormTextLabelField
                label="Use Case (Optional)"
                control={control}
                fieldName="useCase"
                fullWidth
                placeholder="What will you be using the dataset for?"
                multiline
                rows={3}
              />
              <FormTextLabelField
                label="Pattern (Optional)"
                control={control}
                fieldName="pattern"
                fullWidth
                placeholder="Do you want the data to be conversational or formal? Let us know any patterns to follow."
                multiline
                rows={3}
              />
              <Divider orientation="horizontal" />
              <Box
                sx={{ display: "flex", flexDirection: "column", gap: "2px" }}
              >
                <Typography
                  fontWeight={"fontWeightMedium"}
                  color="text.primary"
                  variant="m3"
                >
                  Add Column
                </Typography>
                <Box sx={{ display: "flex", justifyContent: "space-between" }}>
                  <HelperText text="Add new column to get synthetic data" />
                </Box>
              </Box>
              {fields?.map((field, index) => {
                return (
                  <AddColumnField
                    key={index}
                    control={control}
                    index={index}
                    remove={remove}
                    error={errors}
                  />
                );
              })}
              <LoadingButton
                sx={{ width: "30px" }}
                size="small"
                variant="contained"
                color="primary"
                loading={false}
                onClick={handleAddColumn}
              >
                <Iconify icon="mdi:plus" color="background.paper" />
              </LoadingButton>
              <Divider orientation="horizontal" />

              <FormTextFieldV2
                fieldType="number"
                label="Enter No. of rows"
                size="small"
                control={control}
                fieldName="rowNumber"
                placeholder="Enter number of rows"
              />
            </Box>
            <Box
              sx={{
                display: "flex",
                gap: 2,
                width: "100%",
              }}
            >
              <Button
                onClick={onCloseClick}
                size="small"
                fullWidth
                variant="outlined"
              >
                Cancel
              </Button>
              <LoadingButton
                fullWidth
                size="small"
                variant="contained"
                color="primary"
                loading={isPending}
                type="submit"
              >
                {openDescription ? "Create" : "Next"}
              </LoadingButton>
            </Box>
          </Box>
        </Box>
      </FormProvider>
      <ConfirmDialog
        content="Are you sure you want to close? Your work will be lost"
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => handleDialogClose(true)}
          >
            Confirm
          </Button>
        }
        open={isDialogOpen}
        onClose={() => setIsDialogOpen(false)}
        title="Confirm Action"
        message="Are you sure you want to close?"
      />
    </Drawer>
  );
};

export default SyntheticDataDrawer;

SyntheticDataDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  datasetId: PropTypes.string,
  refreshGrid: PropTypes.func.isRequired,
  closeDrawer: PropTypes.func,
  knowledgeId: PropTypes.string,
};
