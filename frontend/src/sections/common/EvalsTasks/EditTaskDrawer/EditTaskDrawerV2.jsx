import React, { useCallback, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import _ from "lodash";
import {
  Box,
  Button,
  Chip,
  Drawer,
  FormHelperText,
  IconButton,
  Tab,
  Tabs,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import {
  useController,
  useFieldArray,
  useForm,
  useFormState,
  useWatch,
} from "react-hook-form";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSelectField } from "src/components/FormSelectField";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import FilterErrorBoundary from "src/components/ComplexFilter/FilterErrorBoundary";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useGetProjectById } from "src/api/project/evals-task";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import { objectCamelToSnake } from "src/utils/utils";
import { red } from "src/theme/palette";
import {
  extractAttributeFilters,
  getNewTaskFilters,
  NewTaskValidationSchema,
} from "../NewTaskDrawer/validation";
import NewTaskFilterBox from "../NewTaskDrawer/NewTaskFilterBox";
import ScheduledRuns from "../NewTaskDrawer/ScheduledRuns";
import { getDefaultTaskValues, useGetTaskData } from "../common";
import TaskConfirmDialog from "./TaskConfirmBox";
import TaskLogsView from "../TaskLogsView";
import { EvalPickerDrawer } from "../../EvalPicker";

// ── Configured Eval Card ──

const ConfiguredEvalCard = ({ evalItem, onRemove, isEditing }) => {
  const theme = useTheme();
  const name =
    evalItem?.name ||
    evalItem?.eval_template?.name ||
    evalItem?.eval_template_name ||
    "Evaluation";
  const mappedKeys = evalItem?.mapping ? Object.keys(evalItem.mapping) : [];

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        p: 1.5,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor:
          theme.palette.mode === "dark"
            ? "rgba(255,255,255,0.02)"
            : "rgba(0,0,0,0.01)",
      }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="body2" fontWeight={500} noWrap>
          {name}
        </Typography>
        {mappedKeys.length > 0 && (
          <Box sx={{ display: "flex", gap: 0.5, mt: 0.5, flexWrap: "wrap" }}>
            {mappedKeys.slice(0, 3).map((key) => (
              <Chip
                key={key}
                label={`${key} → ${evalItem.mapping[key]}`}
                size="small"
                sx={{
                  fontSize: "10px",
                  height: 18,
                  bgcolor: "action.hover",
                  "& .MuiChip-label": { px: 0.5 },
                }}
              />
            ))}
            {mappedKeys.length > 3 && (
              <Chip
                label={`+${mappedKeys.length - 3}`}
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
      {isEditing && (
        <IconButton
          size="small"
          onClick={onRemove}
          sx={{ p: 0.25, color: "text.secondary" }}
        >
          <Iconify icon="mingcute:close-line" width={16} />
        </IconButton>
      )}
    </Box>
  );
};

ConfiguredEvalCard.propTypes = {
  evalItem: PropTypes.object.isRequired,
  onRemove: PropTypes.func,
  isEditing: PropTypes.bool,
};

// ── Tab Options ──

const TAB_OPTIONS = [
  { label: "Details", value: "details" },
  { label: "Logs", value: "logs" },
];

// ── Main Component ──

const EditTaskDrawerV2Content = ({
  selectedRow,
  taskDetails,
  onClose,
  refreshGrid,
  isView,
  observeId,
}) => {
  const { role } = useAuthContext();
  const queryClient = useQueryClient();

  const [selectedTab, setSelectedTab] = useState("details");
  const [isEditing, setIsEditing] = useState(!isView);
  const [evalPickerOpen, setEvalPickerOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const { control, handleSubmit, getValues, setValue } = useForm({
    defaultValues: getDefaultTaskValues(taskDetails, observeId),
    resolver: zodResolver(NewTaskValidationSchema()),
  });

  const project = useWatch({ control, name: "project" });
  const rowType = useWatch({ control, name: "rowType" }) || "spans";
  const formValues = useWatch({ control });

  const { field: startDateField } = useController({
    control,
    name: "startDate",
  });
  const { field: endDateField } = useController({ control, name: "endDate" });

  const {
    fields: configuredEvals,
    append: addEval,
    remove: removeEval,
    replace,
  } = useFieldArray({ name: "evalsDetails", control });

  const { errors } = useFormState({ control });
  const evalsDetailsErrorMessage = _.get(errors, "evalsDetails")?.message || "";

  const { data: projectDetails } = useGetProjectById(observeId, {
    enabled: !!observeId,
  });

  // Debounced filters for API calls
  const _filters = useMemo(() => {
    return getNewTaskFilters(formValues, project, true).filters || {};
  }, [formValues, project]);
  const filters = useDebounce(_filters, 500);

  // Fetch configured evals
  const { data: configuredEvalList } = useQuery({
    queryKey: ["configured-evals", project, filters, selectedRow?.id],
    queryFn: () =>
      axios.get(endpoints.project.getEvalTaskConfig(), {
        params: {
          project_id: project,
          filters: JSON.stringify(objectCamelToSnake(filters)),
          task_id: selectedRow?.id,
        },
      }),
    select: (d) => d.data?.result,
    enabled: !!project,
  });

  useEffect(() => {
    if (configuredEvalList) replace(configuredEvalList);
  }, [configuredEvalList, replace]);

  // Fetch eval attributes for variable mapping
  const { data: evalAttributes } = useQuery({
    queryKey: ["eval-attributes", rowType, filters],
    queryFn: () =>
      axios.get(endpoints.project.getEvalAttributeList(), {
        params: {
          row_type: rowType,
          filters: JSON.stringify(objectCamelToSnake(filters)),
        },
      }),
    select: (d) => d.data?.result,
  });

  const sourceColumns = useMemo(() => {
    if (!evalAttributes) return [];
    return evalAttributes.map((attr) => ({
      headerName: attr,
      field: attr,
      name: attr,
    }));
  }, [evalAttributes]);

  // Fetch project list
  const { data: projectsList } = useQuery({
    queryKey: ["project-list"],
    queryFn: () =>
      axios.get(endpoints.project.listProjects(), {
        params: { project_type: "observe" },
      }),
    select: (d) => d.data?.result?.projects,
  });

  // Update mutation
  const { mutate: updateEvalTask, isPending } = useMutation({
    mutationFn: (data) =>
      axios.patch(endpoints.project.patchEvalTask(), {
        ...data,
        eval_task_id: selectedRow?.id,
      }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({
        queryKey: ["taskDetails", taskDetails?.id],
      });
      enqueueSnackbar(data?.data?.result?.message || "Task updated", {
        variant: "success",
      });
      refreshGrid();
      onClose();
    },
  });

  const onUpdateSubmit = useCallback(
    (editType) => {
      const data = formValues;
      const attributeFilters = extractAttributeFilters(data?.filters);

      // Generic system filter aggregation: every non-attribute filter
      // row contributes its value to a BE key named after `f.property`.
      // Mirrors the create-side getNewTaskFilters (validation.js) so
      // span_kind, latency_ms, total_tokens, etc. all round-trip
      // without each one being hard-coded.
      const systemFilters = {};
      (data.filters || []).forEach((f) => {
        if (!f?.property || f.property === "attributes") return;
        const v = f?.filterConfig?.filterValue;
        if (v === undefined || v === null || v === "") return;
        if (systemFilters[f.property]) {
          systemFilters[f.property].push(v);
        } else {
          systemFilters[f.property] = [v];
        }
      });

      const transformedData = {
        evals: data.evalsDetails?.map((item) => item.id) || [],
        filters: {
          project_id: data.project,
          date_range: [
            new Date(startDateField.value).toISOString(),
            new Date(endDateField.value).toISOString(),
          ],
          ...systemFilters,
          ...(attributeFilters?.length > 0
            ? { span_attributes_filters: attributeFilters }
            : {}),
        },
        project_id: data.project,
        name: data.name,
        project: data.project,
        run_type: data.runType,
        // row_type intentionally omitted — immutable after task creation.
        // The BE serializer rejects it on PATCH; the picker is also
        // locked on edit (see TaskConfigPanel rowTypeLocked).
        sampling_rate: data.samplingRate,
        spans_limit: String(data.spansLimit),
        edit_type: editType,
      };
      updateEvalTask(transformedData);
    },
    [formValues, startDateField.value, endDateField.value, updateEvalTask],
  );

  const onSubmit = useCallback(() => {
    setConfirmOpen(true);
  }, []);

  // Handle adding eval from the new picker
  const handleEvalAdded = useCallback(
    async (evalConfig) => {
      const tplId = evalConfig.templateId || evalConfig.template_id;
      const existingId = evalConfig.id;
      try {
        let id;
        if (existingId) {
          const { data: resp } = await axios.patch(
            endpoints.project.updateEvalTaskConfig(existingId),
            {
              eval_template: tplId,
              name: evalConfig.name,
              model: evalConfig.model || null,
              mapping: evalConfig.mapping,
              config: evalConfig.config || {},
              error_localizer: evalConfig.errorLocalizerEnabled || false,
            },
          );
          id = resp?.result?.id ?? existingId;
        } else {
          const { data: resp } = await axios.post(
            endpoints.project.createEvalTaskConfig(),
            {
              project: project,
              name: evalConfig.name,
              eval_template: tplId,
              model: evalConfig.model || null,
              mapping: evalConfig.mapping,
              config: evalConfig.config || {},
              filters: getNewTaskFilters(formValues, observeId, true).filters,
              error_localizer: evalConfig.errorLocalizerEnabled || false,
            },
          );
          id = resp?.result?.id;
        }
        addEval({
          ...evalConfig,
          id,
          template_id: tplId,
          templateId: tplId,
        });
      } catch {
        addEval({ ...evalConfig, template_id: tplId, templateId: tplId });
      }
    },
    [project, formValues, observeId, addEval],
  );

  const canEdit =
    RolePermission.OBSERVABILITY[PERMISSIONS.ADD_TASKS_ALERTS][role];

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        p: 2.5,
        bgcolor: "background.paper",
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          mb: 1,
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            flex: 1,
            minWidth: 0,
          }}
        >
          <Typography
            variant="h6"
            fontWeight={600}
            noWrap
            sx={{ fontSize: "16px" }}
          >
            {selectedRow?.name || "Task Details"}
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          {isView && selectedTab === "details" && canEdit && (
            <Button
              size="small"
              variant={isEditing ? "contained" : "outlined"}
              startIcon={
                <Iconify
                  icon={
                    isEditing
                      ? "solar:check-circle-linear"
                      : "solar:pen-2-linear"
                  }
                  width={16}
                />
              }
              onClick={() => setIsEditing(!isEditing)}
              sx={{ textTransform: "none", fontSize: "12px" }}
            >
              {isEditing ? "Editing" : "Edit"}
            </Button>
          )}
          <IconButton onClick={onClose} size="small" sx={{ p: 0.5 }}>
            <Iconify icon="mingcute:close-line" width={20} />
          </IconButton>
        </Box>
      </Box>

      {/* Tabs */}
      {isView && (
        <Box sx={{ borderBottom: 1, borderColor: "divider", mb: 2 }}>
          <Tabs
            value={selectedTab}
            onChange={(_, val) => setSelectedTab(val)}
            sx={{
              minHeight: 0,
              "& .MuiTab-root": {
                minHeight: 36,
                fontWeight: 600,
                fontSize: "13px",
                textTransform: "none",
                "&:not(.Mui-selected)": {
                  color: "text.disabled",
                  fontWeight: 500,
                },
              },
            }}
          >
            {TAB_OPTIONS.map((tab) => (
              <Tab key={tab.value} label={tab.label} value={tab.value} />
            ))}
          </Tabs>
        </Box>
      )}

      {/* Details Tab */}
      {selectedTab === "details" && (
        <Box sx={{ flex: 1, overflow: "auto" }}>
          <form
            onSubmit={handleSubmit(onSubmit)}
            style={{
              display: "flex",
              flexDirection: "column",
              height: "100%",
              gap: 8,
            }}
          >
            <Box
              sx={{
                overflow: "auto",
                display: "flex",
                flexDirection: "column",
                gap: 1.5,
                flex: 1,
                py: 0.5,
              }}
            >
              {/* Name */}
              <FormTextFieldV2
                control={control}
                fieldName="name"
                size="small"
                label="Name"
                placeholder="Enter name"
                variant="outlined"
                fullWidth
                required
                disabled={!isEditing}
              />

              {/* Project */}
              {observeId ? (
                <TextField
                  size="small"
                  label="Project"
                  variant="outlined"
                  fullWidth
                  value={
                    projectDetails?.name ?? projectDetails?.result?.name ?? ""
                  }
                  disabled
                  required
                  sx={{ "& .MuiFormLabel-asterisk": { color: red[500] } }}
                />
              ) : (
                <FormSelectField
                  control={control}
                  fieldName="project"
                  size="small"
                  label="Project"
                  required
                  disabled={!isEditing}
                  options={
                    projectsList?.map((p) => ({
                      label: p.name,
                      value: p.id,
                    })) || []
                  }
                />
              )}

              {/* Filters */}
              <FilterErrorBoundary>
                <Accordion defaultExpanded>
                  <AccordionSummary>Filters</AccordionSummary>
                  <AccordionDetails>
                    <NewTaskFilterBox
                      getValues={getValues}
                      setValue={setValue}
                      attributes={
                        Array.isArray(evalAttributes)
                          ? evalAttributes.map((attr) => ({
                              label: attr,
                              value: attr,
                            }))
                          : []
                      }
                      control={control}
                    />
                  </AccordionDetails>
                </Accordion>
              </FilterErrorBoundary>

              {/* Scheduled Run */}
              <Accordion defaultExpanded>
                <AccordionSummary>Scheduled Run</AccordionSummary>
                <AccordionDetails>
                  <ScheduledRuns control={control} dayLimit="Custom" isEdit />
                </AccordionDetails>
              </Accordion>

              {/* Evaluations */}
              <Accordion defaultExpanded>
                <AccordionSummary>Evaluations</AccordionSummary>
                <AccordionDetails>
                  <Box
                    sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
                  >
                    {isEditing && (
                      <Button
                        variant="outlined"
                        size="small"
                        disabled={!project}
                        onClick={() => setEvalPickerOpen(true)}
                        startIcon={
                          <Iconify icon="mingcute:add-line" width={16} />
                        }
                        sx={{
                          textTransform: "none",
                          fontWeight: 500,
                          alignSelf: "flex-start",
                        }}
                      >
                        Add Evaluation
                      </Button>
                    )}

                    {configuredEvals.length > 0 ? (
                      <Box
                        sx={{
                          display: "flex",
                          flexDirection: "column",
                          gap: 1,
                        }}
                      >
                        {configuredEvals.map((evalItem, index) => (
                          <ConfiguredEvalCard
                            key={evalItem.id || index}
                            evalItem={evalItem}
                            onRemove={() => removeEval(index)}
                            isEditing={isEditing}
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
                          No evaluations configured
                        </Typography>
                      </Box>
                    )}

                    <FormHelperText
                      sx={{ pl: 1, mt: 0 }}
                      error={!!evalsDetailsErrorMessage}
                    >
                      {evalsDetailsErrorMessage}
                    </FormHelperText>
                  </Box>
                </AccordionDetails>
              </Accordion>
            </Box>

            {/* Submit */}
            {isEditing && (
              <Box sx={{ mt: 1, ml: "auto", pb: 1 }}>
                <Button
                  type="submit"
                  variant="contained"
                  color="primary"
                  disabled={!canEdit}
                  sx={{ width: "200px" }}
                >
                  Update Task
                </Button>
              </Box>
            )}
          </form>
        </Box>
      )}

      {/* Logs Tab */}
      {selectedTab === "logs" && (
        <Box sx={{ flex: 1, overflow: "auto" }}>
          <TaskLogsView
            evalTaskId={selectedRow?.id}
            taskStatus={taskDetails?.status}
          />
        </Box>
      )}

      {/* Task Update Confirm Dialog */}
      <TaskConfirmDialog
        title="Update Task"
        content="Select one of the options"
        onConfirm={onUpdateSubmit}
        open={confirmOpen}
        isLoading={isPending}
        onClose={() => setConfirmOpen(false)}
      />

      {/* Eval Picker Drawer */}
      <EvalPickerDrawer
        open={evalPickerOpen}
        onClose={() => setEvalPickerOpen(false)}
        source="task"
        sourceColumns={sourceColumns}
        onEvalAdded={handleEvalAdded}
        existingEvals={configuredEvals}
      />
    </Box>
  );
};

EditTaskDrawerV2Content.propTypes = {
  selectedRow: PropTypes.object,
  taskDetails: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  refreshGrid: PropTypes.func,
  isView: PropTypes.bool,
  observeId: PropTypes.string,
};

// ── Wrapper ──

const EditTaskDrawerV2 = ({
  open,
  onClose,
  selectedRow,
  refreshGrid,
  observeId,
  isView = false,
}) => {
  const theme = useTheme();
  const taskId = selectedRow?.id;

  const { data: taskDetails } = useGetTaskData(taskId, {
    enabled: !!taskId && open,
  });

  if (!open) return null;

  return (
    <Drawer
      anchor="right"
      open={open}
      variant="temporary"
      onClose={onClose}
      PaperProps={{
        sx: {
          width: { xs: "100%", sm: "100%", md: "640px" },
          height: "100vh",
          position: "fixed",
          zIndex: 10,
          boxShadow: theme.customShadows?.drawer || theme.shadows[16],
          borderRadius: "0px !important",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: { style: { backgroundColor: "rgba(0, 0, 0, 0.3)" } },
      }}
    >
      {taskDetails && (
        <EditTaskDrawerV2Content
          selectedRow={selectedRow}
          taskDetails={taskDetails}
          onClose={onClose}
          refreshGrid={refreshGrid}
          isView={isView}
          observeId={taskDetails?.project_id || observeId}
        />
      )}
    </Drawer>
  );
};

EditTaskDrawerV2.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  selectedRow: PropTypes.object,
  refreshGrid: PropTypes.func,
  observeId: PropTypes.string,
  isView: PropTypes.bool,
};

export default EditTaskDrawerV2;
