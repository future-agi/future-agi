import {
  Box,
  Button,
  Chip,
  IconButton,
  Paper,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import React, { useEffect, useRef, useState } from "react";
import StepsHeaderComponent from "./StepsHeaderComponent";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { useFieldArray, useWatch } from "react-hook-form";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router";
import { useSearchParams } from "react-router-dom";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { EvalPickerDrawer } from "src/sections/common/EvalPicker";
import { getVersionedEvalName } from "src/components/run-tests/common";
import { ShowComponent } from "src/components/show";
import { isUUID } from "src/utils/utils";

const EvaluationStepExperimentCreation = ({
  control,
  allColumns,
  errors,
  isEditingExperiment = false,
  experimentId = null,
  snapshotDatasetId = null,
}) => {
  const selectedColumn = useWatch({ control, name: "columnId" });
  const { dataset: datasetParam } = useParams();
  const [searchParam] = useSearchParams();
  const datasetId = datasetParam || searchParam.get("datasetId") || "";
  const theme = useTheme();
  const userChangedColumnRef = useRef(false);
  const experimentVirtualColumns = [
    { field: "output", headerName: "Output", dataType: "text" },
    { field: "prompt_chain", headerName: "Prompt Chain", dataType: "text" },
  ];
  const updatedEvalColumns = [
    ...experimentVirtualColumns,
    ...(allColumns || []),
  ];

  const [openEvaluationDialog, setOpenEvaluationDialog] = useState(false);
  const [editingEval, setEditingEval] = useState(null);

  const { data: userEvalList } = useQuery({
    queryFn: () =>
      axios.get(endpoints.develop.optimizeDevelop.columnInfo, {
        params: { column_id: selectedColumn },
      }),
    queryKey: ["optimize-develop-column-info", "eval-step", selectedColumn],
    enabled:
      Boolean(selectedColumn?.length) &&
      (!isEditingExperiment || userChangedColumnRef.current),
    select: (data) => data?.data?.result,
  });
  const {
    fields: evalFields,
    replace: replaceEvals,
    append,
    remove,
    update,
  } = useFieldArray({ control, name: "userEvalMetrics" });
  useEffect(() => {
    if (
      userEvalList &&
      (!isEditingExperiment || userChangedColumnRef.current)
    ) {
      const manualEvals = evalFields.filter((f) => !f?.showInSidebar);
      const apiEvals = userEvalList.map((item) => ({
        ...item,
        evalId: item.id,
        config: item.config || {
          ...item.params,
          mapping: item.mapping || {},
        },
      }));
      replaceEvals([...apiEvals, ...manualEvals]);
      userChangedColumnRef.current = false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userEvalList, replaceEvals]);
  // Only an eval that already has a real backend UserEvalMetric id (i.e. it
  // was persisted on a previous save of this experiment) can be pinned to a
  // new version via the scoped edit-eval endpoint. Brand new evals added
  // mid-session don't exist on the server yet — they stay fully local and
  // get created (with any picked pinned_version_id) at Run Experiment.
  // Experiment evals live on the experiment's snapshot dataset (not the
  // original dataset the experiment was created from) — the scoped
  // edit-eval endpoint's dataset_id path param must match that to find
  // the UserEvalMetric row.
  const canPinInline =
    isEditingExperiment && Boolean(experimentId) && Boolean(snapshotDatasetId);
  const queryClient = useQueryClient();

  const { mutate: pinEvalVersionInline } = useMutation({
    mutationFn: ({ userEvalId, payload }) =>
      axios.post(
        endpoints.develop.eval.editEval(snapshotDatasetId, userEvalId),
        payload,
      ),
    onError: (error) => {
      enqueueSnackbar(
        error?.response?.data?.result ||
          error?.message ||
          "Failed to save the new evaluation version",
        { variant: "error" },
      );
    },
  });

  const handleAddEvaluation = (evalConfig) => {
    // Build mapping: DatasetTestMode returns { variable: "column_name" }.
    // The backend expects { variable: "column_uuid" }.
    // Translate using updatedEvalColumns.
    const rawMapping = evalConfig.mapping || {};
    const translatedMapping = {};
    for (const [variable, colName] of Object.entries(rawMapping)) {
      const col = updatedEvalColumns.find(
        (c) =>
          c.headerName === colName || c.field === colName || c.name === colName,
      );
      translatedMapping[variable] = col?.field || colName;
    }

    // Merge full template config with the mapping so the backend knows
    // how to execute the eval (eval_type_id, rule_prompt, output, etc.)
    const templateConfig =
      evalConfig.config || evalConfig.evalTemplate?.config || {};
    const fullConfig = {
      ...templateConfig,
      mapping: translatedMapping,
    };
    const isComposite = evalConfig.templateType === "composite";

    const evalEntry = {
      evalId: evalConfig.templateId,
      evalTemplateName: evalConfig.name,
      templateId: evalConfig.templateId,
      mapping: translatedMapping,
      model: evalConfig.model,
      config: fullConfig,
      templateDetails: evalConfig.evalTemplate,
      templateType: evalConfig.templateType,
      requiredKeys:
        evalConfig.evalTemplate?.requiredKeys ||
        templateConfig.requiredKeys ||
        [],
      // The version the user picked from the dropdown (or, for a dirty
      // edit, whatever version was pinned before this save — gets
      // overwritten below once the scoped save resolves).
      pinnedVersionId: evalConfig.versionId ?? null,
      ...(isComposite && evalConfig.compositeWeightOverrides
        ? { compositeWeightOverrides: evalConfig.compositeWeightOverrides }
        : {}),
    };

    if (editingEval) {
      // Edit mode: replace the existing field in place, keep the same name.
      const idx = evalFields.findIndex((f) => {
        const fid = f.actualEvalCreatedId || f.evalId || f.id;
        return fid === editingEval.userEvalId;
      });
      if (idx !== -1) {
        const merged = {
          ...evalFields[idx],
          ...evalEntry,
          name: evalConfig.name,
        };
        update(idx, merged);

        const userEvalId = editingEval.userEvalId;
        const hasBackendMetric = userEvalId && isUUID(userEvalId);

        // Only a real, dirty config edit needs a new version created right
        // now. A plain version-dropdown pick (isDirty === false) or an
        // eval that doesn't exist on the server yet just stays local —
        // it's picked up by the full save on "Run Experiment".
        if (canPinInline && hasBackendMetric && evalConfig.isDirty) {
          const runConfig = {};
          if (!isComposite) {
            if (evalConfig.model) runConfig.model = evalConfig.model;
            if (evalConfig.agent_mode) runConfig.agent_mode = evalConfig.agent_mode;
            if (evalConfig.check_internet !== undefined)
              runConfig.check_internet = !!evalConfig.check_internet;
            if (evalConfig.summary) runConfig.summary = evalConfig.summary;
            if (evalConfig.knowledge_bases)
              runConfig.knowledge_bases = evalConfig.knowledge_bases;
            if (evalConfig.tools) runConfig.tools = evalConfig.tools;
            if (evalConfig.pass_threshold !== undefined)
              runConfig.pass_threshold = evalConfig.pass_threshold;
            if (
              evalConfig.choice_scores &&
              Object.keys(evalConfig.choice_scores).length
            )
              runConfig.choice_scores = evalConfig.choice_scores;
            if (evalConfig.multi_choice !== undefined)
              runConfig.multi_choice = !!evalConfig.multi_choice;
          }
          if (evalConfig.data_injection)
            runConfig.data_injection = evalConfig.data_injection;
          if (evalConfig.error_localizer_enabled !== undefined)
            runConfig.error_localizer_enabled = !!evalConfig.error_localizer_enabled;
          const evalParams =
            evalConfig.params && typeof evalConfig.params === "object"
              ? evalConfig.params
              : {};

          pinEvalVersionInline(
            {
              userEvalId,
              payload: {
                name: evalConfig.name,
                template_id: evalConfig.templateId,
                model: isComposite ? undefined : evalConfig.model,
                run: false,
                experiment_id: experimentId,
                error_localizer: runConfig.error_localizer_enabled ?? false,
                pinned_version_id: evalConfig.versionId || undefined,
                config: {
                  mapping: translatedMapping,
                  config: isComposite ? {} : templateConfig,
                  ...(Object.keys(evalParams).length
                    ? { params: evalParams }
                    : {}),
                  ...(Object.keys(runConfig).length
                    ? { run_config: runConfig }
                    : {}),
                },
                ...(isComposite && evalConfig.compositeWeightOverrides
                  ? {
                      composite_weight_overrides:
                        evalConfig.compositeWeightOverrides,
                    }
                  : {}),
              },
            },
            {
              onSuccess: (resp) => {
                const resolvedPinnedVersionId =
                  resp?.data?.result?.pinned_version_id;
                if (resolvedPinnedVersionId) {
                  update(idx, {
                    ...merged,
                    pinnedVersionId: resolvedPinnedVersionId,
                  });
                }
                // The scoped save may have created a new version directly on
                // the backend (bypassing useCreateEvalVersion's own cache
                // invalidation) — refresh so the dropdown shows it next time
                // this eval is reopened for editing.
                queryClient.invalidateQueries({
                  queryKey: ["evals", "versions", evalConfig.templateId],
                });
              },
            },
          );
        }
      }
    } else {
      // Add mode: append with versioned name to avoid duplicates.
      const versionedName = getVersionedEvalName(
        evalConfig.name,
        evalFields,
        evalConfig.templateId,
      );
      append({ ...evalEntry, name: versionedName });
    }
    setEditingEval(null);
    setOpenEvaluationDialog(false);
  };

  const handleRemoveEval = (evalId) => {
    const idx = evalFields.findIndex((f) => (f.evalId || f.id) === evalId);
    if (idx !== -1) remove(idx);
  };

  const handleEditEval = (evalItem) => {
    const tplId =
      evalItem.templateId ||
      evalItem.template_id ||
      evalItem.evalTemplateId ||
      evalItem.eval_template_id ||
      evalItem.evalId ||
      evalItem.id;
    setEditingEval({
      id: tplId,
      // During creation the eval only has a local field id; during editing
      // it may carry a backend-assigned id (actualEvalCreatedId).
      userEvalId:
        evalItem.actualEvalCreatedId || evalItem.evalId || evalItem.id,
      name: evalItem.name || evalItem.evalTemplateName,
      templateType: evalItem.templateType || evalItem.template_type,
      mapping: evalItem.config?.mapping || evalItem.mapping,
      model: evalItem.model || evalItem.selected_model,
      run_config: evalItem.config,
      compositeWeightOverrides:
        evalItem.compositeWeightOverrides ||
        evalItem.composite_weight_overrides,
      // Lets EvalPickerConfigFull preselect whatever version is currently
      // pinned for this eval (either from the backend, or from a version
      // picked/created earlier in this same editing session).
      pinned_version_id:
        evalItem.pinnedVersionId || evalItem.pinned_version_id || null,
    });
    setOpenEvaluationDialog(true);
  };
  const hasError = errors?.userEvalMetrics?.message;
  return (
    <Stack spacing={3}>
      <StepsHeaderComponent
        title={"Configure Evaluations"}
        subtitle={
          "Select a column from your dataset to compare model outputs against"
        }
      />

      {/* Baseline column selector */}
      <Box
        sx={{
          border: "1px solid",
          borderColor: "blue.o20",
          backgroundColor: "blue.o5",
          padding: 2,
          borderRadius: 0.5,
          display: "flex",
          flexDirection: "row",
          alignItems: "flex-start",
          gap: 2,
        }}
      >
        <Box
          sx={{
            width: 36,
            height: 36,
            borderRadius: 0.5,
            backgroundColor: "blue.o10",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <SvgColor
            sx={{ width: 24, height: 24, color: "blue.600" }}
            src="/assets/icons/ic_two_arrows_reverse.svg"
          />
        </Box>
        <Stack spacing={2} flex={1}>
          <Box>
            <Typography typography={"s1_2"} fontWeight={"fontWeightMedium"}>
              Compare against baseline (optional)
            </Typography>
            <Typography typography={"s2_1"} fontWeight={"fontWeightRegular"}>
              Select a column from your dataset to compare model outputs against
            </Typography>
          </Box>
          <FormSearchSelectFieldControl
            required
            fullWidth
            placeholder="Select column"
            control={control}
            fieldName="columnId"
            size="small"
            onChange={() => {
              userChangedColumnRef.current = true;
            }}
            options={(allColumns || []).map((column) => ({
              value: column.field,
              label: column.headerName,
            }))}
            MenuProps={{ sx: { maxHeight: "400px" } }}
            sx={{
              "& .MuiFormHelperText-root": { margin: 0 },
              width: "100%",
              backgroundColor: "background.default",
            }}
          />
        </Stack>
      </Box>

      {/* Evaluations section */}
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          border: "1px solid",
          borderColor: hasError ? "error.main" : "divider",
          backgroundColor: "background.neutral",
          padding: 2,
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "row",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 2,
          }}
        >
          <Stack>
            <Typography typography="m3" fontWeight={"fontWeightMedium"}>
              Add Evaluations
            </Typography>
            <Typography
              typography={"s2_1"}
              fontWeight={"fontWeightRegular"}
              color="text.secondary"
            >
              Select and configure evals to run
            </Typography>
          </Stack>
          <Button
            variant="outlined"
            color="primary"
            size="small"
            onClick={() => {
              setEditingEval(null);
              setOpenEvaluationDialog(true);
            }}
            startIcon={
              <SvgColor
                src="/assets/icons/action_buttons/ic_add.svg"
                sx={{ width: 16, height: 16 }}
              />
            }
            sx={{
              px: theme.spacing(1.5),
              mt: -0.8,
              fontWeight: 500,
              height: 34,
              bgcolor: "background.paper",
            }}
          >
            Add Evaluations
          </Button>
        </Box>
        <ShowComponent condition={hasError}>
          <Typography typography={"s2"} color="error.main">
            {errors?.userEvalMetrics?.message}
          </Typography>
        </ShowComponent>
        <ShowComponent condition={Boolean(evalFields.length)}>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
            {evalFields.map((evalItem) => {
              const itemAny = evalItem;
              const evalId = itemAny.evalId || itemAny.id;
              const mapping = itemAny.config?.mapping || itemAny.mapping || {};
              return (
                <Paper
                  key={evalId}
                  sx={{
                    p: 2,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    backgroundColor: "background.paper",
                  }}
                >
                  <Box
                    sx={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "flex-start",
                    }}
                  >
                    <Box sx={{ flex: 1 }}>
                      <Typography variant="subtitle2">
                        {itemAny.name}
                      </Typography>
                      {itemAny.description && (
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={{ mt: 0.5 }}
                        >
                          {itemAny.description}
                        </Typography>
                      )}
                      <Box
                        sx={{
                          mt: 1,
                          display: "flex",
                          gap: 1,
                          flexWrap: "wrap",
                        }}
                      >
                        <ShowComponent
                          condition={!!itemAny.evalGroup && !!itemAny.groupName}
                        >
                          <Chip
                            label={`Group name - ${itemAny.groupName}.`}
                            size="small"
                            sx={{
                              height: "24px",
                              backgroundColor: "background.neutral",
                              borderColor: "divider",
                              fontSize: "11px",
                              borderRadius: "2px",
                              paddingX: "12px",
                              lineHeight: "16px",
                              fontWeight: 400,
                              color: "text.primary",
                              "& .MuiChip-label": { padding: 0 },
                              ".MuiChip-icon ": { marginRight: "6px" },
                              "&:hover": {
                                backgroundColor: "background.neutral",
                                borderColor: "divider",
                              },
                            }}
                            icon={
                              <SvgColor
                                src="/assets/icons/ic_dashed_square.svg"
                                sx={{ width: 16, height: 16, mr: 1 }}
                                style={{ color: theme.palette.text.primary }}
                              />
                            }
                          />
                        </ShowComponent>
                        {Object.entries(mapping).map(([key, value]) => {
                          let label = value;
                          if (isUUID(String(value))) {
                            const match = (allColumns || []).find(
                              (col) => col.field === value,
                            );
                            if (match) label = match.headerName;
                          }
                          return (
                            <Chip
                              key={key}
                              label={`${key}: ${label}`}
                              size="small"
                              variant="outlined"
                            />
                          );
                        })}
                      </Box>
                    </Box>

                    <IconButton
                      size="small"
                      onClick={() => handleEditEval(itemAny)}
                      sx={{
                        ml: 1,
                        border: "1px solid",
                        borderColor: "divider",
                        borderRadius: "2px",
                        color: "text.action",
                      }}
                    >
                      <SvgColor
                        src="/assets/icons/ic_edit.svg"
                        sx={{ width: 16, height: 16 }}
                      />
                    </IconButton>
                    <IconButton
                      size="small"
                      onClick={() => handleRemoveEval(evalId)}
                      sx={{
                        ml: 1,
                        border: "1px solid",
                        borderColor: "divider",
                        borderRadius: "2px",
                        color: "text.action",
                      }}
                    >
                      <SvgColor
                        src="/assets/icons/ic_delete.svg"
                        sx={{ height: 16, width: 16 }}
                      />
                    </IconButton>
                  </Box>
                </Paper>
              );
            })}
          </Box>
        </ShowComponent>
      </Box>

      <EvalPickerDrawer
        open={openEvaluationDialog}
        onClose={() => {
          setOpenEvaluationDialog(false);
          setEditingEval(null);
        }}
        source="experiment"
        sourceId={datasetId}
        sourceColumns={updatedEvalColumns}
        extraColumns={experimentVirtualColumns}
        existingEvals={evalFields}
        onEvalAdded={handleAddEvaluation}
        initialEval={editingEval}
      />
    </Stack>
  );
};

export default EvaluationStepExperimentCreation;

EvaluationStepExperimentCreation.propTypes = {
  control: PropTypes.object,
  allColumns: PropTypes.array,
  errors: PropTypes.object,
  isEditingExperiment: PropTypes.bool,
  experimentId: PropTypes.string,
  snapshotDatasetId: PropTypes.string,
};
