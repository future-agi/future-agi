import React, { useCallback, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Divider,
  FormHelperText,
  IconButton,
  Tab,
  Tabs,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { useFieldArray, useFormState, useWatch } from "react-hook-form";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import _ from "lodash";
import Iconify from "src/components/iconify";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FilterErrorBoundary from "src/components/ComplexFilter/FilterErrorBoundary";
import {
  EvalPickerDrawer,
  serializeEvalConfig,
} from "src/sections/common/EvalPicker";
import { enqueueSnackbar } from "src/components/snackbar";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import TaskSchedulingSection from "./TaskSchedulingSection";
import { getNewTaskFilters } from "src/sections/tasks/schema";
import { useGetProjectDetails } from "src/api/project/project-detail";
import { PROJECT_SOURCE } from "src/utils/constants";
import TaskFilterBar from "./TaskFilterBar";

const ROW_TYPE_OPTIONS = [
  { value: "spans", label: "Spans", icon: "solar:layers-outline" },
  { value: "traces", label: "Traces", icon: "solar:flow-outline" },
  { value: "sessions", label: "Sessions", icon: "solar:chat-line-outline" },
];

// ── Section Header ──
const SectionHeader = ({ title, subtitle, action }) => (
  <Box
    sx={{
      display: "flex",
      justifyContent: "space-between",
      alignItems: "flex-start",
      mb: 1,
    }}
  >
    <Box>
      <Typography
        variant="subtitle2"
        fontWeight={600}
        sx={{ fontSize: "13px" }}
      >
        {title}
      </Typography>
      {subtitle && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontSize: "12px", display: "block", mt: 0.25 }}
        >
          {subtitle}
        </Typography>
      )}
    </Box>
    {action}
  </Box>
);

SectionHeader.propTypes = {
  title: PropTypes.string.isRequired,
  subtitle: PropTypes.string,
  action: PropTypes.node,
};

// ── Output type label ──
const formatOutputType = (ot) => {
  if (!ot) return null;
  const v = String(ot).toLowerCase();
  if (v === "pass_fail" || v === "passfail") return "Pass / Fail";
  if (v === "scoring" || v === "score") return "Score";
  if (v === "choices" || v === "choice") return "Choice";
  return ot;
};

// ── Eval type meta — icon + label per eval type ──
const EVAL_TYPE_META = {
  llm: { icon: "solar:chat-square-code-linear", label: "LLM" },
  agent: { icon: "solar:atom-linear", label: "Agent" },
  code: { icon: "solar:code-square-linear", label: "Code" },
};

// ── Configured Eval Card ──
const ConfiguredEvalCard = ({ evalItem, onEdit, onRemove }) => {
  const theme = useTheme();
  const invalid = !evalItem?.id;
  const name =
    evalItem?.name ||
    evalItem?.evalTemplate?.name ||
    evalItem?.evalTemplateName ||
    "Evaluation";
  const mappedKeys = evalItem?.mapping ? Object.keys(evalItem.mapping) : [];

  const evalType = (
    evalItem?.evalType ||
    evalItem?.evalTemplate?.evalType ||
    "llm"
  ).toLowerCase();
  const typeMeta = EVAL_TYPE_META[evalType] || EVAL_TYPE_META.llm;
  const isCode = evalType === "code";

  const model = evalItem?.model;
  const outputLabel = formatOutputType(
    evalItem?.outputType || evalItem?.evalTemplate?.outputType,
  );
  const codeLang =
    evalItem?.config?.language ||
    evalItem?.evalTemplate?.config?.language ||
    (isCode ? "Python" : null);

  const hasError = !evalItem?.id;

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        p: 1.5,
        borderRadius: 1,
        border: "1px solid",
        borderColor: hasError ? "error.main" : "divider",
        bgcolor:
          theme.palette.mode === "dark"
            ? "rgba(255,255,255,0.02)"
            : "rgba(0,0,0,0.01)",
        transition: "border-color 0.15s",
        "&:hover": { borderColor: hasError ? "error.main" : "primary.main" },
      }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            flexWrap: "wrap",
          }}
        >
          <Iconify
            icon={typeMeta.icon}
            width={14}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Typography
            variant="body2"
            fontWeight={500}
            noWrap
            sx={{ fontSize: "13px" }}
          >
            {name}
          </Typography>

          {/* Type-specific metadata — code shows language, llm/agent
              show model. Output type is always informative. */}
          {isCode && codeLang && (
            <Chip
              label={
                codeLang.charAt(0).toUpperCase() +
                codeLang.slice(1).toLowerCase()
              }
              size="small"
              sx={{
                height: 18,
                fontSize: "10px",
                bgcolor: "background.neutral",
                color: "text.secondary",
                "& .MuiChip-label": { px: 0.5 },
              }}
            />
          )}
          {!isCode && model && (
            <Chip
              label={model}
              size="small"
              sx={{
                height: 18,
                fontSize: "10px",
                bgcolor: "background.neutral",
                color: "text.secondary",
                "& .MuiChip-label": { px: 0.5 },
              }}
            />
          )}
          {outputLabel && (
            <Chip
              label={outputLabel}
              size="small"
              variant="outlined"
              sx={{
                height: 18,
                fontSize: "10px",
                borderColor: "divider",
                color: "text.secondary",
                "& .MuiChip-label": { px: 0.5 },
              }}
            />
          )}
        </Box>
        {hasError && (
          <Typography
            variant="caption"
            sx={{
              display: "block",
              mt: 0.5,
              fontSize: "11px",
              color: "error.main",
            }}
          >
            Failed to save — remove and re-add this evaluation.
          </Typography>
        )}
        {mappedKeys.length > 0 && (
          <Box sx={{ display: "flex", gap: 0.5, mt: 0.75, flexWrap: "wrap" }}>
            {mappedKeys.slice(0, 4).map((key) => (
              <Chip
                key={key}
                label={`${key} → ${evalItem.mapping[key]}`}
                size="small"
                sx={{
                  fontSize: "10px",
                  height: 18,
                  bgcolor: "background.neutral",
                  color: "text.secondary",
                  fontFamily: "monospace",
                  "& .MuiChip-label": { px: 0.5 },
                }}
              />
            ))}
            {mappedKeys.length > 4 && (
              <Chip
                label={`+${mappedKeys.length - 4}`}
                size="small"
                sx={{
                  fontSize: "10px",
                  height: 18,
                  "& .MuiChip-label": { px: 0.5 },
                }}
              />
            )}
          </Box>
        )}
      </Box>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
        {onEdit && (
          <Tooltip title="Edit evaluation">
            <IconButton
              size="small"
              onClick={onEdit}
              sx={{ p: 0.25, color: "text.secondary" }}
            >
              <Iconify icon="solar:pen-2-linear" width={15} />
            </IconButton>
          </Tooltip>
        )}
        <Tooltip title="Remove evaluation">
          <IconButton
            size="small"
            onClick={onRemove}
            sx={{ p: 0.25, color: "text.secondary" }}
          >
            <Iconify icon="mingcute:close-line" width={16} />
          </IconButton>
        </Tooltip>
      </Box>
    </Box>
  );
};

ConfiguredEvalCard.propTypes = {
  evalItem: PropTypes.object.isRequired,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func.isRequired,
};

// ── Main Panel ──
const TaskConfigPanel = ({
  mode,
  control,
  setValue,
  getValues: _getValues,
  projectLocked = false,
  initialProjectName = null,
}) => {
  const [evalPickerOpen, setEvalPickerOpen] = useState(false);
  // Index of the eval currently being edited. null means "add mode".
  const [editingIndex, setEditingIndex] = useState(null);
  const { errors } = useFormState({ control });

  const project = useWatch({ control, name: "project" });
  const rowType = useWatch({ control, name: "rowType" }) || "spans";
  const taskFilters = useWatch({ control, name: "filters" });
  const isProjectSelected = !!project;
  // row_type is immutable after task creation — the dispatcher, the
  // target_type on every EvalLogger row, and the dedup index are all
  // wired off it. The BE rejects row_type on PATCH; the FE matches by
  // locking the picker in edit mode so the user can't try.
  const rowTypeLocked = mode === "edit";

  // Fetch project details to detect voice projects (simulator source)
  const { data: projectDetails } = useGetProjectDetails(
    project,
    isProjectSelected,
  );
  const isVoiceProject = projectDetails?.source === PROJECT_SOURCE.SIMULATOR;

  // Auto-set rowType to voiceCalls when voice project is detected,
  // default to "spans" otherwise.
  useEffect(() => {
    if (!isProjectSelected) return;
    if (isVoiceProject && rowType !== "voiceCalls") {
      setValue("rowType", "voiceCalls");
    } else if (!isVoiceProject && rowType === "voiceCalls") {
      setValue("rowType", "spans");
    }
  }, [isProjectSelected, isVoiceProject, rowType, setValue]);

  const {
    fields: configuredEvals,
    append: addEval,
    remove: removeEval,
    replace: replaceEvals,
    update: updateEval,
  } = useFieldArray({
    name: "evalsDetails",
    control,
    // Use a non-conflicting keyName so react-hook-form does not overwrite
    // the `id` field (which holds the real CustomEvalConfig UUID from the API).
    keyName: "_fieldId",
  });

  const [pendingProject, setPendingProject] = useState(null);

  const handleProjectFieldChange = useCallback(
    (newVal) => {
      if (!newVal || newVal === project) return;
      if (configuredEvals.length === 0) return;
      setPendingProject(project);
    },
    [project, configuredEvals.length],
  );

  const handleConfirmProjectChange = useCallback(() => {
    replaceEvals([]);
    setPendingProject(null);
  }, [replaceEvals]);

  const handleCancelProjectChange = useCallback(() => {
    setValue("project", pendingProject, {
      shouldDirty: false,
      shouldValidate: false,
    });
    setPendingProject(null);
  }, [pendingProject, setValue]);

  const evalsDetailsErrorMessage = _.get(errors, "evalsDetails")?.message || "";

  const formValues = useWatch({ control });

  const filtersWithoutDate = useMemo(
    () => getNewTaskFilters(formValues, project, true).filters || [],
    [formValues, project],
  );

  // Projects list — only fetch in create mode (not locked)
  const { data: projectsList } = useQuery({
    queryKey: ["project-list"],
    queryFn: () =>
      axios.get(endpoints.project.listProjects(), {
        params: { project_type: "observe" },
      }),
    select: (data) => data.data?.result?.projects,
    enabled: !projectLocked,
  });

  // Eval attributes for variable mapping. Includes rowType so the picker
  // shows the right paths per target type — span attribute keys for spans,
  // trace fields + spans.first/last.<key> for traces, session fields +
  // traces.{first,last}.spans.{first,last}.<key> for sessions.
  const { data: evalAttributes } = useQuery({
    queryKey: ["eval-attributes", project, rowType, filtersWithoutDate],
    queryFn: () =>
      axios.get(endpoints.project.getEvalAttributeList(), {
        params: {
          project_id: project,
          row_type: rowType,
          filters: JSON.stringify(filtersWithoutDate),
        },
      }),
    select: (data) => data.data?.result,
    enabled: isProjectSelected,
  });

  const sourceColumns = useMemo(() => {
    if (!evalAttributes) return [];
    return evalAttributes.map((attr) => ({
      headerName: attr,
      field: attr,
      name: attr,
    }));
  }, [evalAttributes]);

  const handleEvalAdded = useCallback(
    async (evalConfig) => {
      const tplId = evalConfig.templateId || evalConfig.template_id;

      // When editing, configuredEvals[editingIndex].id is always the CustomEvalConfig id
      // (set from POST response on first add, or from the API on task load)
      const customEvalConfigId =
        editingIndex !== null ? configuredEvals[editingIndex]?.id : undefined;

      const serialized = serializeEvalConfig(evalConfig);

      const corePayload = {
        eval_template: tplId,
        name: evalConfig.name,
        model: evalConfig.model || null,
        mapping: evalConfig.mapping || {},
        config: {
          ...serialized.config,
          mapping: evalConfig.mapping || {},
        },
        error_localizer: !!evalConfig.errorLocalizerEnabled,
      };

      let finalEval = { ...evalConfig, template_id: tplId };
      try {
        if (customEvalConfigId) {
          const { data: resp } = await axios.patch(
            endpoints.project.updateEvalTaskConfig(customEvalConfigId),
            corePayload,
          );
          finalEval = {
            ...evalConfig,
            id: resp?.result?.id ?? customEvalConfigId,
            template_id: tplId,
            templateId: tplId,
            config: {
              ...(evalConfig.config || {}),
              ...corePayload.config,
            },
            mapping: evalConfig.mapping || {},
          };
        } else {
          const { data: resp } = await axios.post(
            endpoints.project.createEvalTaskConfig(),
            { project, ...corePayload },
          );
          finalEval = {
            ...evalConfig,
            id: resp?.result?.id,
            template_id: tplId,
            templateId: tplId,
            config: {
              ...(evalConfig.config || {}),
              ...corePayload.config,
            },
            mapping: evalConfig.mapping || {},
          };
        }
      } catch (error) {
        enqueueSnackbar(
          error?.response?.data?.result ||
            error?.response?.data?.error ||
            "Failed to save evaluation",
          { variant: "error" },
        );
        throw error;
      }

      if (editingIndex !== null) {
        updateEval(editingIndex, finalEval);
      } else {
        addEval(finalEval);
      }
      setEditingIndex(null);
    },
    [project, addEval, updateEval, editingIndex, configuredEvals],
  );

  const handleEditEval = useCallback((index) => {
    setEditingIndex(index);
    setEvalPickerOpen(true);
  }, []);

  const handleClosePicker = useCallback(() => {
    setEvalPickerOpen(false);
    setEditingIndex(null);
  }, []);

  const handleAddEval = useCallback(() => {
    setEditingIndex(null);
    setEvalPickerOpen(true);
  }, []);

  // The eval currently being edited (passed to picker as initialEval so
  // it jumps directly to the config step).
  //
  // IMPORTANT: EvalPickerConfigFull reads `evalData?.id` as the eval
  // *template* id (it passes it into useEvalDetail). When the eval was
  // originally added, we overwrote `.id` with the custom_eval_config id
  // returned by POST /custom-eval-config/. We need to alias
  // `.templateId` back into `.id` here so the picker loads the correct
  // template. We keep the custom_eval_config id on a separate field in
  // case we need to reference it later.
  const editingEval = useMemo(() => {
    if (editingIndex === null) return null;
    const stored = configuredEvals[editingIndex];
    if (!stored) return null;
    // API response uses `eval_template` for the template FK;
    // locally-added evals use `templateId` / `template_id`.
    const tplId =
      stored.templateId || stored.template_id || stored.eval_template;

    const savedErrorLocalizer =
      stored.error_localizer_enabled ?? stored.error_localizer;
    const existingRunConfig =
      stored.run_config || stored.config?.run_config || {};
    return {
      ...stored,
      id: tplId,
      template_id: tplId,
      templateId: tplId,
      // `stored.id` is always the CustomEvalConfig id (from POST response or API load)
      customEvalConfigId: stored.customEvalConfigId || stored.id,
      run_config: {
        ...existingRunConfig,
        ...(stored.model && { model: stored.model }),
        ...(savedErrorLocalizer !== undefined && {
          error_localizer_enabled: savedErrorLocalizer,
        }),
      },
    };
  }, [editingIndex, configuredEvals]);
  const resolvedProjectName =
    initialProjectName ||
    projectsList?.find((p) => p.id === project)?.name ||
    project;

  return (
    <>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2.5,
          p: 2.5,
          overflow: "auto",
          height: "100%",
        }}
      >
        {/* ── Basic Info ── */}
        <Box>
          <SectionHeader
            title="Basic Info"
            subtitle="Name and target project for this task"
          />
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
            <FormTextFieldV2
              control={control}
              fieldName="name"
              size="small"
              label="Task Name"
              variant="outlined"
              fullWidth
              placeholder="Enter task name"
              required
              disabled={mode === "edit"}
            />

            {projectLocked ? (
              <TextField
                size="small"
                label="Project"
                variant="outlined"
                fullWidth
                value={resolvedProjectName || ""}
                disabled
                InputLabelProps={{ shrink: true }}
                helperText="Project cannot be changed after creation"
              />
            ) : (
              <FormSearchSelectFieldControl
                control={control}
                fieldName="project"
                size="small"
                label="Project"
                required
                options={
                  projectsList?.map((p) => ({
                    label: p.name,
                    value: p.id,
                  })) || []
                }
                onChange={handleProjectFieldChange}
                style={{ width: "100%" }}
                noOptions="No projects available"
              />
            )}

            {/* Target type tabs — hidden for voice projects */}
            {isProjectSelected && !isVoiceProject && (
              <Box>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ fontSize: "11px", display: "block", mb: 0.75 }}
                >
                  Run evaluations on
                </Typography>
                <Tabs
                  value={rowType}
                  onChange={(_, v) => {
                    if (rowTypeLocked) return;
                    setValue("rowType", v);
                  }}
                  variant="standard"
                  scrollButtons={false}
                  TabIndicatorProps={{ style: { display: "none" } }}
                  sx={{
                    minHeight: 28,
                    "& .MuiTabs-scroller": { overflow: "visible !important" },
                    "& .MuiTab-root": {
                      minHeight: 28,
                      px: 1.25,
                      py: 0,
                      mr: "0px !important",
                      textTransform: "none",
                      fontSize: "12px",
                      borderRadius: "6px",
                      minWidth: "auto",
                    },
                    border: "1px solid",
                    borderColor: "divider",
                    p: "2px",
                    borderRadius: "8px",
                    width: "fit-content",
                    bgcolor: (theme) =>
                      theme.palette.mode === "dark"
                        ? "rgba(255,255,255,0.04)"
                        : "background.neutral",
                  }}
                >
                  {ROW_TYPE_OPTIONS.map((t) => (
                    <Tab
                      key={t.value}
                      value={t.value}
                      disabled={rowTypeLocked && rowType !== t.value}
                      label={
                        <Box
                          sx={{
                            display: "flex",
                            alignItems: "center",
                            gap: 0.5,
                          }}
                        >
                          <Iconify icon={t.icon} width={13} />
                          {t.label}
                        </Box>
                      }
                      sx={{
                        bgcolor:
                          rowType === t.value
                            ? (theme) =>
                                theme.palette.mode === "dark"
                                  ? "rgba(255,255,255,0.12)"
                                  : "background.paper"
                            : "transparent",
                        boxShadow:
                          rowType === t.value
                            ? (theme) =>
                                theme.palette.mode === "dark"
                                  ? "none"
                                  : "0 1px 3px rgba(0,0,0,0.08)"
                            : "none",
                        borderRadius: "6px",
                        fontWeight: rowType === t.value ? 600 : 400,
                        color:
                          rowType === t.value
                            ? "text.primary"
                            : "text.disabled",
                      }}
                    />
                  ))}
                </Tabs>
              </Box>
            )}
          </Box>
        </Box>

        <Divider />

        {/* Everything below Basic Info is disabled until a project is selected */}
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 2.5,
            opacity: isProjectSelected ? 1 : 0.45,
            pointerEvents: isProjectSelected ? "auto" : "none",
            transition: "opacity 0.15s",
          }}
        >
          {!isProjectSelected && (
            <Typography
              variant="caption"
              color="text.disabled"
              sx={{ fontSize: "11px", display: "block", fontStyle: "italic" }}
            >
              Select a project to configure filters, scheduling, and
              evaluations.
            </Typography>
          )}

          {/* ── Evaluations ── */}
          <Box>
            <SectionHeader
              title="Evaluations"
              subtitle="Configure which evals run against matching rows"
              action={
                <Button
                  variant="outlined"
                  size="small"
                  disabled={!isProjectSelected}
                  onClick={handleAddEval}
                  startIcon={<Iconify icon="mingcute:add-line" width={14} />}
                  sx={{
                    textTransform: "none",
                    fontWeight: 500,
                    fontSize: "12px",
                    height: 28,
                  }}
                >
                  Add Evaluation
                </Button>
              }
            />

            {configuredEvals.length > 0 ? (
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
                {configuredEvals.map((evalItem, index) => (
                  <ConfiguredEvalCard
                    key={evalItem.id || index}
                    evalItem={evalItem}
                    onEdit={() => handleEditEval(index)}
                    onRemove={() => removeEval(index)}
                  />
                ))}
              </Box>
            ) : (
              <Box
                sx={{
                  p: 3,
                  textAlign: "center",
                  borderRadius: 1,
                  border: "1px dashed",
                  borderColor: "divider",
                }}
              >
                <Typography
                  variant="body2"
                  color="text.disabled"
                  sx={{ fontSize: "13px" }}
                >
                  No evaluations added yet
                </Typography>
              </Box>
            )}

            {evalsDetailsErrorMessage && (
              <FormHelperText error sx={{ pl: 1, mt: 0.5 }}>
                {evalsDetailsErrorMessage}
              </FormHelperText>
            )}
          </Box>

          <Divider />

          {/* ── Filters ── */}
          <Box>
            <SectionHeader
              title="Filters"
              subtitle={`Narrow down which ${rowType === "sessions" ? "sessions" : rowType === "traces" ? "traces" : rowType === "voiceCalls" ? "voice calls" : "spans"} this task runs on`}
            />
            <FilterErrorBoundary>
              <TaskFilterBar
                control={control}
                setValue={setValue}
                projectId={project}
                isSimulator={isVoiceProject}
                rowType={rowType}
              />
            </FilterErrorBoundary>
          </Box>

          <Divider />

          {/* ── Scheduling ── */}
          <Box>
            <SectionHeader
              title="Scheduling"
              subtitle="Choose when and how much data to evaluate"
            />
            <TaskSchedulingSection control={control} isEdit={mode === "edit"} />
          </Box>
        </Box>
      </Box>

      <EvalPickerDrawer
        open={evalPickerOpen}
        onClose={handleClosePicker}
        source="task"
        sourceId={project}
        sourceRowType={rowType}
        sourceColumns={sourceColumns}
        onEvalAdded={handleEvalAdded}
        existingEvals={configuredEvals}
        initialEval={editingEval}
        sourceFilters={taskFilters}
        onFiltersChange={(f) =>
          setValue("filters", f || [], { shouldDirty: true })
        }
      />

      <ModalWrapper
        open={!!pendingProject}
        onClose={handleCancelProjectChange}
        onCancelBtn={handleCancelProjectChange}
        onSubmit={handleConfirmProjectChange}
        title="Switch project?"
        subTitle="Switching the project will remove the evaluations you've already added to this task."
        actionBtnTitle="Confirm"
        cancelBtnTitle="Cancel"
        isValid
      />
    </>
  );
};

TaskConfigPanel.propTypes = {
  mode: PropTypes.oneOf(["create", "edit"]).isRequired,
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
  getValues: PropTypes.func.isRequired,
  projectLocked: PropTypes.bool,
  initialProjectName: PropTypes.string,
};

export default TaskConfigPanel;
