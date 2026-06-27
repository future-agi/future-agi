import {
  Box,
  IconButton,
  Button,
  useTheme,
  Typography,
  Stack,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useEffect, useMemo } from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { zodResolver } from "@hookform/resolvers/zod";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import EvaluationDrawer from "src/sections/common/EvaluationDrawer/EvaluationDrawer";
import HelperText from "../Common/HelperText";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { LoadingButton } from "@mui/lab";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { useDatasetOptimizationStoreShallow } from "./states";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import SvgColor from "src/components/svg-color";
import ConfigureKeysModal from "src/components/ConfigureApiKeysModal/ConfigureKeysModal";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import { OPTIMIZER_OPTIONS, OptimizerConfigurationMapping } from "./common";
import AddedEvaluations from "../Optimization/AddedEvaluations";
import { z } from "zod";
import {
  FieldWrapper,
  OptimizerSelectField,
  OptimizerConfigFields,
} from "./shared";
import { useNavigate } from "react-router";

// Validation schema for the drawer
const optimizationDrawerSchema = z.object({
  name: z.string().min(1, "Name is required"),
  column_id: z.string().min(1, "Column is required"),
  optimizer_algorithm: z.string().min(1, "Optimizer is required"),
  optimizer_model_id: z.string().min(1, "Model is required"),
  optimizer_config: z.record(z.any()).optional(),
  userEvalTemplateIds: z.array(z.any()).optional(),
});
const getFormDefaults = (defaults) =>
  defaults
    ? {
        name: defaults.name || "",
        optimizer_model_id: defaults.optimizer_model_id || "",
        optimizer_algorithm: defaults.optimizer_algorithm || "",
        column_id: defaults.column_id || "",
        // remove model_name from optimizer_config to avoid validation error
        optimizer_config: (({ model_name: _mn, ...rest }) => rest)(
          defaults.optimizer_config || {},
        ),
        userEvalTemplateIds: defaults.userEvalTemplateIds || [],
      }
    : {
        name: "",
        optimizer_model_id: "",
        optimizer_algorithm: "gepa",
        column_id: "",
        optimizer_config: OptimizerConfigurationMapping.gepa,
        userEvalTemplateIds: [],
      };

const DatasetOptimizationForm = ({
  onClose,
  setFormIsDirty,
  columnOptions,
  allColumns,
  datasetId,
  defaultValues,
  onColumnChange,
}) => {
  const queryClient = useQueryClient();
  const { optimizationGridApi } = useDatasetOptimizationStoreShallow(
    (state) => ({
      optimizationGridApi: state.optimizationGridApi,
    }),
  );
  const navigate = useNavigate();

  const {
    control,
    handleSubmit,
    formState: { isValid, errors, isDirty },
    watch,
    setValue,
    trigger,
  } = useForm({
    defaultValues: getFormDefaults(defaultValues),
    resolver: zodResolver(optimizationDrawerSchema),
    mode: "onChange",
  });

  const [isApiConfigurationOpen, setIsApiConfigurationOpen] = useState(null);
  const optimizerValue = watch("optimizer_algorithm");
  const columnValue = watch("column_id");
  const nameValue = watch("name");

  // Merge column options with rerun column if not already present
  const mergedColumnOptions = useMemo(() => {
    if (
      defaultValues?.column_id &&
      defaultValues?.column_name &&
      !columnOptions.find((opt) => opt.value === defaultValues.column_id)
    ) {
      return [
        {
          value: defaultValues.column_id,
          label: defaultValues.column_name,
        },
        ...columnOptions,
      ];
    }
    return columnOptions;
  }, [columnOptions, defaultValues]);

  // Auto-generate name when column or optimizer changes
  // Format: <column>-<optimizer>-<datetime> or <optimizer>-<datetime> if no column
  // Preserves user's custom name if they've manually edited it
  useEffect(() => {
    onColumnChange?.(columnValue);

    // Skip if this is a rerun with default values
    if (defaultValues?.name) return;

    // Need at least optimizer to generate name
    if (!optimizerValue) return;

    // Find the optimizer label
    const selectedOptimizer = OPTIMIZER_OPTIONS.find(
      (opt) => opt.value === optimizerValue,
    );
    const optimizerLabel = selectedOptimizer?.label || optimizerValue;

    // Generate timestamp (e.g., "Jan31-1430" for Jan 31st at 2:30 PM)
    const now = new Date();
    const months = [
      "Jan",
      "Feb",
      "Mar",
      "Apr",
      "May",
      "Jun",
      "Jul",
      "Aug",
      "Sep",
      "Oct",
      "Nov",
      "Dec",
    ];
    const timestamp = `${months[now.getMonth()]}${now.getDate()}-${String(now.getHours()).padStart(2, "0")}${String(now.getMinutes()).padStart(2, "0")}`;

    // Build name based on whether column is selected
    let generatedName;
    if (columnValue) {
      // Find the column label
      const selectedColumn = columnOptions.find(
        (col) => col.value === columnValue,
      );
      const columnLabel = selectedColumn?.label || "Prompt";
      // Truncate column label if too long
      const truncatedColumn =
        columnLabel.length > 20 ? columnLabel.substring(0, 20) : columnLabel;
      generatedName = `${truncatedColumn}-${optimizerLabel}-${timestamp}`;
    } else {
      // No column selected yet, just optimizer and timestamp
      generatedName = `${optimizerLabel}-${timestamp}`;
    }

    // Only set if name is empty or looks like it was auto-generated (ends with timestamp pattern)
    const isAutoGenerated =
      !nameValue ||
      /-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\d+-\d{4}$/.test(
        nameValue,
      );
    if (isAutoGenerated) {
      setValue("name", generatedName, {
        shouldValidate: true,
        shouldDirty: false,
      });
    }
  }, [
    columnValue,
    optimizerValue,
    columnOptions,
    defaultValues,
    nameValue,
    setValue,
    onColumnChange,
  ]);

  useEffect(() => {
    if (setFormIsDirty) setFormIsDirty(isDirty);
  }, [isDirty, setFormIsDirty]);

  const handleOnChange = () => {
    trigger("optimizer_config.min_examples");
    trigger("optimizer_config.max_examples");
  };

  const handleOnChangeOptimizer = (e) => {
    const value = e.target.value;
    if (value !== optimizerValue) {
      setValue(
        "optimizer_config",
        { ...OptimizerConfigurationMapping[value] },
        {
          shouldDirty: false,
          shouldValidate: false,
        },
      );
    }
    setValue("optimizer_algorithm", value, {
      shouldDirty: true,
      shouldValidate: true,
    });
  };

  const { mutate: createOptimization, isPending: isCreatingOptimization } =
    useMutation({
      mutationFn: (data) =>
        axios.post(endpoints.develop.datasetOptimization.create, data),
      onSuccess: (data) => {
        enqueueSnackbar("Optimization created successfully", {
          variant: "success",
        });
        navigate(
          `/dashboard/develop/${datasetId}?tab=optimization&optimizationId=${data?.data?.id}`,
        );
        queryClient.invalidateQueries(["dataset-optimization-runs"]);
        // Refresh the grid if available
        if (optimizationGridApi) {
          optimizationGridApi.refreshServerSide({ purge: true });
        }
        onClose(null, true);
      },
      onError: (error) => {
        enqueueSnackbar(
          error?.response?.data?.result ||
            error?.response?.data?.message ||
            "Failed to create optimization",
          { variant: "error" },
        );
      },
    });

  const handleSubmitForm = (data) => {
    if (!data?.userEvalTemplateIds?.length) {
      enqueueSnackbar("Add evaluations before starting your optimization run", {
        variant: "error",
      });
      return;
    }
    trackEvent(Events.optimizeSuccessful, {
      [PropertyName.method]: "dataset",
    });
    if (setFormIsDirty) setFormIsDirty(false);
    const { userEvalTemplateIds, ...restData } = data;
    createOptimization({
      ...restData,
      dataset_id: datasetId,
      user_eval_template_ids:
        userEvalTemplateIds?.map((t) => t.id || t.evalId) || [],
    });
  };

  const handleViewDocs = () => {
    window.open("https://docs.futureagi.com/docs/optimization", "_blank");
  };

  return (
    <Box sx={{ display: "flex", height: "100%", justifyContent: "flex-end" }}>
      <Box
        sx={{
          gap: "15px",
          display: "flex",
          flexDirection: "column",
          height: "100%",
          width: "100%",
        }}
      >
        <Box>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 0.5,
            }}
          >
            <Stack
              direction={"row"}
              alignItems={"center"}
              justifyContent={"space-between"}
            >
              <Typography fontWeight={700} color="text.primary">
                {defaultValues ? "Re-run Optimization" : "Run Optimization"}
              </Typography>
              <Stack direction={"row"} alignItems={"center"} gap={1.5}>
                <Button
                  size="small"
                  variant="outlined"
                  onClick={handleViewDocs}
                  startIcon={
                    <SvgColor
                      sx={{
                        height: "16px",
                        width: "16px",
                      }}
                      src="/assets/icons/ic_paper.svg"
                    />
                  }
                  sx={{
                    px: 1.5,
                  }}
                >
                  View Docs
                </Button>
                <IconButton
                  size="small"
                  onClick={onClose}
                  sx={{
                    color: "text.primary",
                  }}
                >
                  <Iconify icon="mingcute:close-line" />
                </IconButton>
              </Stack>
            </Stack>
            <HelperText text="Optimize your prompt using advanced algorithms" />
          </Box>
        </Box>

        {columnOptions.length === 0 && !defaultValues?.column_id ? (
          <EmptyLayout
            title="No columns available for optimization"
            description={
              <>
                Run your prompt first to create columns that can be optimized.
              </>
            }
            icon={`/assets/icons/action_buttons/ic_run_prompt.svg`}
          />
        ) : (
          <Box
            sx={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
            }}
          >
            <Box
              sx={{
                flex: 1,
                display: "flex",
                gap: 2,
                flexDirection: "column",
                overflowY: "auto",
                paddingY: 1,
                paddingRight: 1,
              }}
            >
              <FormTextFieldV2
                control={control}
                fieldName="name"
                label="Name"
                required
                placeholder="Optimization Name"
                size="small"
                fullWidth
              />

              <FieldWrapper
                helperText={
                  defaultValues?.column_id
                    ? "Column cannot be changed on rerun"
                    : "The prompt column chosen will be used for optimization"
                }
              >
                <FormSearchSelectFieldControl
                  control={control}
                  options={mergedColumnOptions}
                  fieldName="column_id"
                  label="Choose Column"
                  size="small"
                  placeholder="Select a column"
                  fullWidth
                  required
                  disabled={!!defaultValues?.column_id}
                />
              </FieldWrapper>

              <OptimizerSelectField
                control={control}
                optimizerValue={optimizerValue}
                onChange={handleOnChangeOptimizer}
                errors={errors}
              />

              <FieldWrapper helperText="Model used for optimization.">
                <ConfigureKeysModal
                  open={Boolean(isApiConfigurationOpen)}
                  selectedModel={isApiConfigurationOpen}
                  onClose={() => setIsApiConfigurationOpen(null)}
                />
                <CustomModelDropdownControl
                  control={control}
                  fieldName="optimizer_model_id"
                  hoverPlacement="bottom"
                  label="Language Model"
                  searchDropdown
                  modelObjectKey={null}
                  size="small"
                  fullWidth
                  extraParams={{ model_type: "llm" }}
                  onModelConfigOpen={(selectedModel) => {
                    setIsApiConfigurationOpen(selectedModel);
                  }}
                  required
                  showIcon
                  hideCreateLabel={true}
                  placeholder="Choose a Model (eg: gpt-4o)"
                />
              </FieldWrapper>

              {/* Dynamic Parameters based on optimizer type */}
              <OptimizerConfigFields
                control={control}
                optimizerValue={optimizerValue}
                onMinMaxChange={handleOnChange}
              />

              {/* Evaluations Section */}
              <AddedEvaluations
                control={control}
                allColumns={allColumns}
                columnFieldName="column_id"
              />
            </Box>

            {/* Actions */}
            <Box
              sx={{
                display: "flex",
                gap: 2,
                width: "100%",
                pt: 2,
              }}
            >
              <Button
                onClick={onClose}
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
                loading={isCreatingOptimization}
                onClick={handleSubmit(handleSubmitForm)}
                disabled={!isValid}
                startIcon={
                  <SvgColor src="/assets/icons/navbar/ic_get_started.svg" />
                }
              >
                Start Optimization
              </LoadingButton>
            </Box>
          </Box>
        )}
      </Box>
    </Box>
  );
};

DatasetOptimizationForm.propTypes = {
  onClose: PropTypes.func,
  setFormIsDirty: PropTypes.func,
  columnOptions: PropTypes.array,
  allColumns: PropTypes.array,
  datasetId: PropTypes.string,
  defaultValues: PropTypes.object,
  onColumnChange: PropTypes.func,
};

const DatasetOptimizationDrawer = ({
  datasetId,
  columnOptions,
  allColumns,
}) => {
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [isFormDirty, setFormIsDirty] = useState(false);
  const [selectedColumnId, setSelectedColumnId] = useState("");
  const theme = useTheme();

  const {
    isCreateDrawerOpen: open,
    setIsCreateDrawerOpen,
    rerunDefaultValues,
    setRerunDefaultValues,
  } = useDatasetOptimizationStoreShallow((state) => ({
    isCreateDrawerOpen: state.isCreateDrawerOpen,
    setIsCreateDrawerOpen: state.setIsCreateDrawerOpen,
    rerunDefaultValues: state.rerunDefaultValues,
    setRerunDefaultValues: state.setRerunDefaultValues,
  }));

  const onClose = () => {
    setIsCreateDrawerOpen(false);
    setRerunDefaultValues(null);
  };

  const onCloseClick = () => {
    if (isFormDirty) {
      setIsDialogOpen(true);
    } else {
      onClose();
    }
  };

  return (
    <Box
      sx={{
        height: "100vh",
        position: "fixed",
        zIndex: 2,
        borderRadius: "10px",
        backgroundColor: "background.paper",
      }}
    >
      <EvaluationDrawer
        module="run-optimization"
        id={datasetId}
        open={open}
        onClose={onCloseClick}
        allColumns={allColumns}
        type="persistent"
        listComponent={
          <DatasetOptimizationForm
            key={
              rerunDefaultValues ? JSON.stringify(rerunDefaultValues) : "new"
            }
            setFormIsDirty={setFormIsDirty}
            onClose={onCloseClick}
            columnOptions={columnOptions}
            allColumns={allColumns}
            datasetId={datasetId}
            defaultValues={rerunDefaultValues}
            onColumnChange={setSelectedColumnId}
          />
        }
        showAdd={false}
        showTest={false}
        runLabel="Save"
        requiredColumnIds={selectedColumnId}
      />
      <ConfirmDialog
        content="Are you sure you want to close? Your work will be lost"
        action={
          <Button
            variant="contained"
            color="error"
            onClick={() => {
              onClose();
              setIsDialogOpen(false);
            }}
            size="small"
            sx={{ paddingX: theme.spacing(2) }}
          >
            Confirm
          </Button>
        }
        open={isDialogOpen}
        onClose={() => setIsDialogOpen(false)}
        title="Confirm Action"
        message="Are you sure you want to close?"
      />
    </Box>
  );
};

DatasetOptimizationDrawer.propTypes = {
  datasetId: PropTypes.string,
  columnOptions: PropTypes.array,
  allColumns: PropTypes.array,
};

export default DatasetOptimizationDrawer;
