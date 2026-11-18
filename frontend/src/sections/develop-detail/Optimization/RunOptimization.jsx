import { Box, IconButton, Button, Typography, Stack } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState, useEffect, useMemo } from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { zodResolver } from "@hookform/resolvers/zod";
import { useParams, useNavigate } from "react-router";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import EvaluationDrawer from "src/sections/common/EvaluationDrawer/EvaluationDrawer";
import HelperText from "../Common/HelperText";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { LoadingButton } from "@mui/lab";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { useRunOptimizationStore, useRunPromptStoreShallow } from "../states";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import SvgColor from "src/components/svg-color";
import ConfigureKeysModal from "src/components/ConfigureApiKeysModal/ConfigureKeysModal";
import CustomModelDropdownControl from "src/components/custom-model-dropdown/CustomModelDropdownControl";
import {
  OPTIMIZER_OPTIONS,
  OptimizerConfigurationMapping,
} from "../DatasetOptimization/common";
import AddedEvaluations from "./AddedEvaluations";
import { z } from "zod";
import {
  FieldWrapper,
  OptimizerSelectField,
  OptimizerConfigFields,
} from "../DatasetOptimization/shared";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

// Simplified validation schema for the drawer
const optimizationDrawerSchema = z.object({
  name: z.string().min(1, "Name is required"),
  column_id: z.string().min(1, "Column is required"),
  optimizer_algorithm: z.string().min(1, "Optimizer is required"),
  optimizer_model_id: z.string().min(1, "Model is required"),
  optimizer_config: z.record(z.any()).optional(),
  userEvalTemplateIds: z.array(z.any()).default([]),
});

const RunOptimizationForm = ({
  onClose,
  setFormIsDirty,
  columnOptions,
  allColumns,
  onColumnChange,
  onSuccess,
}) => {
  const { role } = useAuthContext();
  const { dataset } = useParams();
  const { refreshGrid } = useDevelopDetailContext();
  const queryClient = useQueryClient();
  const setOpenRunPrompt = useRunPromptStoreShallow((s) => s.setOpenRunPrompt);

  const {
    control,
    handleSubmit,
    formState: { isValid, errors, isDirty },
    watch,
    setValue,
    trigger,
    reset,
  } = useForm({
    defaultValues: {
      name: "",
      optimizer_model_id: "",
      optimizer_algorithm: "gepa",
      column_id: "",
      optimizer_config: OptimizerConfigurationMapping.gepa,
      userEvalTemplateIds: [],
    },
    resolver: zodResolver(optimizationDrawerSchema),
    mode: "onChange",
  });

  const [isApiConfigurationOpen, setIsApiConfigurationOpen] = useState(null);
  const optimizerValue = watch("optimizer_algorithm");
  const columnValue = watch("column_id");
  const nameValue = watch("name");

  // Auto-generate name when column or optimizer changes
  // Format: <column>-<optimizer>-<datetime> or <optimizer>-<datetime> if no column
  // Preserves user's custom name if they've manually edited it
  useEffect(() => {
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
    onColumnChange?.(columnValue);
  }, [
    columnValue,
    optimizerValue,
    columnOptions,
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
      onSuccess: (response) => {
        enqueueSnackbar("Optimization created successfully", {
          variant: "success",
        });
        queryClient.invalidateQueries(["dataset-optimization-runs"]);
        refreshGrid(null, true);
        reset();
        const optimizationId = response?.data?.result?.id || response?.data?.id;
        onClose(null, true);
        if (optimizationId && onSuccess) {
          onSuccess(optimizationId);
        }
      },
      onError: (error) => {
        enqueueSnackbar(
          error?.response?.data?.message || "Failed to create optimization",
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
      [PropertyName.method]: "column",
    });
    if (setFormIsDirty) setFormIsDirty(false);
    const { userEvalTemplateIds, ...restData } = data;
    createOptimization({
      ...restData,
      dataset_id: dataset,
      user_eval_template_ids:
        userEvalTemplateIds?.map((t) => t.id || t.evalId) || [],
    });
  };

  return (
    <Box
      sx={{
        display: "flex",
        width: "100%",
        height: "100%",
        justifyContent: "flex-end",
      }}
    >
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
              <Typography fontWeight={600} color="text.primary">
                Run Optimization
              </Typography>
              <Stack direction={"row"} alignItems={"center"} gap={1.5}>
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => {
                    window.open(
                      "https://docs.futureagi.com/docs/optimization",
                      "_blank",
                    );
                  }}
                  startIcon={
                    <SvgColor width={16} src="/assets/icons/ic_paper.svg" />
                  }
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
                  <Iconify icon="solar:close-linear" />
                </IconButton>
              </Stack>
            </Stack>
            <HelperText text="Optimize your prompt using advanced algorithms" />
          </Box>
        </Box>

        {columnOptions.length === 0 ? (
          <EmptyLayout
            title="Use Run Prompt before using Optimization"
            description={
              <>
                Get a clear baseline by running your prompt first.
                <br />
                It helps you optimize with intent.
              </>
            }
            icon={`/assets/icons/action_buttons/ic_run_prompt.svg`}
            action={
              <Button
                variant="contained"
                color="primary"
                onClick={() => {
                  onClose();
                  setOpenRunPrompt(true);
                  trackEvent(Events.datasetRunPromptClicked, {
                    [PropertyName.id]: dataset,
                  });
                }}
                disabled={!RolePermission.DATASETS[PERMISSIONS.UPDATE][role]}
              >
                <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
                  Run Prompt
                </Typography>
              </Button>
            }
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

              <FieldWrapper helperText="The prompt column chosen will be used for optimization">
                <FormSearchSelectFieldControl
                  control={control}
                  options={columnOptions}
                  fieldName="column_id"
                  label="Choose Column"
                  size="small"
                  placeholder="Select a column"
                  fullWidth
                  required
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

RunOptimizationForm.propTypes = {
  onClose: PropTypes.func,
  setFormIsDirty: PropTypes.any,
  columnOptions: PropTypes.array,
  allColumns: PropTypes.array,
  onColumnChange: PropTypes.func,
  onSuccess: PropTypes.func,
};

const RunOptimization = () => {
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [isFormDirty, setFormIsDirty] = useState(false);
  const [selectedColumnId, setSelectedColumnId] = useState("");

  const { refreshGrid } = useDevelopDetailContext();
  const { dataset: datasetId } = useParams();
  const { openRunOptimization: open, setOpenRunOptimization } =
    useRunOptimizationStore();
  const navigate = useNavigate();

  const allColumns = useDatasetColumnConfig(datasetId);

  // Filter columns to only show RUN_PROMPT columns for optimization
  const columnOptions = useMemo(() => {
    if (!allColumns) return [];
    return allColumns
      .filter(
        (col) => col.originType === "run_prompt" || col.source === "RUN_PROMPT",
      )
      .map((col) => ({
        value: col.field || col.id,
        label: col.headerName || col.name,
      }));
  }, [allColumns]);

  const onClose = () => {
    setOpenRunOptimization(false);
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
        borderRadius: "8px",
        backgroundColor: "background.paper",
      }}
    >
      <EvaluationDrawer
        module="run-optimization"
        id={datasetId}
        open={open}
        onClose={() => {}}
        allColumns={allColumns}
        refreshGrid={refreshGrid}
        type="persistent"
        listComponent={
          open ? (
            <RunOptimizationForm
              setFormIsDirty={setFormIsDirty}
              onClose={onCloseClick}
              columnOptions={columnOptions}
              allColumns={allColumns}
              onColumnChange={setSelectedColumnId}
              onSuccess={(id) =>
                navigate(
                  `/dashboard/develop/${datasetId}?tab=optimization&optimizationId=${id}`,
                )
              }
            />
          ) : null
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

RunOptimization.propTypes = {
  parentIsDirty: PropTypes.bool,
};

export default RunOptimization;
