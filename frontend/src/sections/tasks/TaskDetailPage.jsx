import React, { useCallback, useEffect, useRef, useState } from "react";
import { SegmentedTabs } from "src/components/tabs/tabs";
import {
  Alert,
  Box,
  Button,
  MenuItem,
  Stack,
  Tab,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import Iconify from "src/components/iconify";
import ResizablePanels from "src/components/resizablePanels/ResizablePanels";
import TaskLogsView from "src/sections/common/EvalsTasks/TaskLogsView";
import { useGetTaskData } from "src/sections/common/EvalsTasks/common";
import { isResumableTaskStatus } from "src/sections/common/EvalsTasks/task_status";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import CustomPopover, { usePopover } from "src/components/custom-popover";
import TaskHeader from "./components/TaskHeader";
import TaskConfigPanel from "./components/TaskConfigPanel";
import TaskLivePreview from "./components/TaskLivePreview";
import TaskUsageTab from "./components/TaskUsageTab";
import {
  NewTaskValidationSchema,
  getDefaultTaskValues,
  getNewTaskFilters,
} from "./schema";
import TaskConfirmDialog from "src/sections/common/EvalsTasks/EditTaskDrawer/TaskConfirmBox";
import DuplicateTaskDialog from "./components/DuplicateTaskDialog";
import {
  getInlineLoadErrorMessage,
  getSafeActionErrorMessage,
} from "src/utils/errorUtils";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import ContentSkeleton from "src/components/content-skeleton";

// A refusal the workspace recovery explains gets a calm line, not its text.
const getTaskDetailsErrorMessage = (error) =>
  getInlineLoadErrorMessage(error, "Task details could not be loaded.");

const TAB_OPTIONS = [
  { label: "Details", value: "details", icon: "solar:settings-linear" },
  { label: "Logs", value: "logs", icon: "solar:notebook-linear" },
  { label: "Usage", value: "usage", icon: "solar:chart-2-linear" },
];

const firstFilterValue = (value) => {
  if (Array.isArray(value)) return value.find(Boolean) || null;
  return value || null;
};

const getLinkedTraceSource = (taskDetails) => {
  const filters = taskDetails?.filters_applied || taskDetails?.filters || {};
  const projectId =
    taskDetails?.project_id ||
    taskDetails?.projectId ||
    filters.project_id ||
    filters.projectId;
  const traceId = firstFilterValue(filters.trace_id || filters.traceId);
  if (!projectId || !traceId) return null;
  return {
    label: "Open source",
    path: `/dashboard/observe/${projectId}/trace/${traceId}`,
  };
};

// An Instant eval run reads the rows its Ask page sampled, not a filter query.
// Saving or re-running it here would drop that sample, delete its answers and
// evaluate an ordinary window, so it is asked again from its Ask page.
const getInstantRunAskPath = (taskDetails, taskId) => {
  const filters = taskDetails?.filters_applied || {};
  if (!filters.instant_eval_run) return null;
  const projectId = taskDetails?.project_id || filters.project_id;
  return `/dashboard/observe/${projectId}/ask?run=${encodeURIComponent(taskId)}`;
};

const hasDirtyField = (dirty) =>
  dirty === true ||
  (dirty &&
    typeof dirty === "object" &&
    Object.values(dirty).some(hasDirtyField));

const WINDOW_FIELDS = ["startDate", "endDate", "datePreset"];

// Save keeps a historical task's stored window unless the user changed it.
// Rebuilding it moves a rolling preset (Past 7D, 12M) to now, which the server
// reads as changed rows, so adding one evaluator could then only be saved with
// Delete & rerun. Re-run still resolves the preset against now.
const getUntouchedSavedWindow = ({ taskDetails, data, dirty, confirmMode }) => {
  if (confirmMode === "rerun") return null;
  if (data?.runType !== "historical") return null;
  if (taskDetails?.run_type !== "historical") return null;
  if (WINDOW_FIELDS.some((name) => hasDirtyField(dirty?.[name]))) return null;
  const saved = taskDetails?.filters_applied;
  if (!Array.isArray(saved?.date_range) || saved.date_range.length !== 2) {
    return null;
  }
  return {
    date_range: saved.date_range,
    ...(saved.date_preset ? { date_preset: saved.date_preset } : {}),
  };
};

// The task fields getDefaultTaskValues reads. Polls of a running task also
// refresh status and last_run; those alone must not re-hydrate the form,
// because every hydration hands the live preview new evaluation and
// date-window values and it discards its row and pending test results.
const getTaskConfigSignature = (task) =>
  JSON.stringify([
    task?.name,
    task?.project_id,
    task?.filters_applied ?? null,
    task?.spans_limit,
    task?.sampling_rate,
    task?.evals_applied ?? null,
    task?.run_type,
    task?.row_type,
  ]);

const TaskDetailPage = () => {
  const { taskId } = useParams();
  const navigate = useNavigate();
  const { role } = useAuthContext();
  const canEditTask =
    RolePermission.OBSERVABILITY[PERMISSIONS.ADD_TASKS_ALERTS][role];
  const queryClient = useQueryClient();
  const popover = usePopover();
  const [tab, setTab] = useState("details");
  const [confirmMode, setConfirmMode] = useState(null);
  const [duplicateDialogOpen, setDuplicateDialogOpen] = useState(false);

  // Test runner — imperative handle from the live preview
  const previewRef = useRef(null);
  const [testState, setTestState] = useState({
    canTest: false,
    isTesting: false,
  });
  const handleTestStateChange = useCallback((next) => {
    setTestState(next);
  }, []);

  const {
    data: taskDetails,
    isLoading,
    isError,
    error,
    isFetching,
    refetch,
  } = useGetTaskData(taskId, {
    enabled: !!taskId,
    // Poll while non-terminal so the header/badge advance without a refresh.
    // queryFn returns the raw axios response, so status is pre-`select`.
    refetchInterval: (query) => {
      const s = query?.state?.data?.data?.result?.status?.toLowerCase?.();
      return s === "pending" || s === "running" ? 4000 : false;
    },
  });

  const {
    control,
    handleSubmit,
    getValues,
    setValue,
    reset,
    formState: { dirtyFields },
  } = useForm({
    defaultValues: getDefaultTaskValues(null, null),
    resolver: zodResolver(NewTaskValidationSchema()),
  });

  const project = useWatch({ control, name: "project" });
  const formValues = useWatch({ control });

  const hydratedTaskId = useRef(null);
  const hydratedConfigSignature = useRef(null);
  // Read the latest dirty state when a poll arrives, without making each edit
  // trigger the hydration effect itself.
  const dirtyFieldsRef = useRef(dirtyFields);
  dirtyFieldsRef.current = dirtyFields;

  // Polls update operational details without discarding local configuration.
  // Freeze all form values while confirmation is open, including pristine
  // fields, so a background refresh cannot change the payload being confirmed.
  useEffect(() => {
    if (!taskDetails) return;
    const isSameTask = hydratedTaskId.current === taskId;
    if (isSameTask && confirmMode) return;
    const configSignature = getTaskConfigSignature(taskDetails);
    if (isSameTask && configSignature === hydratedConfigSignature.current) {
      return;
    }

    const defaults = getDefaultTaskValues(taskDetails, null);
    if (isSameTask) {
      const currentValues = getValues();
      const edits = Object.fromEntries(
        Object.entries(dirtyFieldsRef.current)
          .filter(([, dirty]) => hasDirtyField(dirty))
          .map(([name]) => [name, currentValues[name]]),
      );
      // keepDirtyValues only preserves registered inputs in our RHF version.
      // Filters set programmatically and field-array removals need whole-value
      // preservation, not a per-index merge that can restore deleted rows.
      // Update the server baseline without broadcasting it to the inputs, then
      // hydrate once with the edits and recompute dirtiness against that baseline.
      reset(defaults, { keepValues: true });
      reset({ ...defaults, ...edits }, { keepDefaultValues: true });
    } else {
      reset(defaults);
    }
    hydratedTaskId.current = taskId;
    hydratedConfigSignature.current = configSignature;
    if (!isSameTask) setConfirmMode(null);
  }, [taskId, taskDetails, reset, confirmMode, getValues]);

  // ── Mutations ──
  const { mutate: updateTask, isPending: isUpdating } = useMutation({
    meta: { errorHandled: true },
    mutationFn: ({ payload }) =>
      axios.patch(endpoints.project.patchEvalTask(), {
        ...payload,
        eval_task_id: taskId,
      }),
    onSuccess: (_data, { mode }) => {
      queryClient.invalidateQueries({ queryKey: ["taskDetails", taskId] });
      queryClient.invalidateQueries({ queryKey: ["eval-tasks"] });
      enqueueSnackbar(
        mode === "rerun" ? "Re-run started" : "Task updated successfully",
        { variant: "success" },
      );
    },
    onError: (err, { mode }) => {
      enqueueSnackbar(
        getSafeActionErrorMessage(
          err,
          mode === "rerun"
            ? "Failed to start the re-run"
            : "Task could not be updated. Review the filters and try again.",
        ),
        { variant: "error" },
      );
    },
  });

  // Optimistically flip the header badge on click. The cache holds the raw
  // axios response, so status lives at data.data.result.
  const setCachedTaskStatus = (status) =>
    queryClient.setQueryData(["taskDetails", taskId], (old) =>
      old?.data?.result
        ? {
            ...old,
            data: { ...old.data, result: { ...old.data.result, status } },
          }
        : old,
    );

  const optimisticStatus = async (status) => {
    await queryClient.cancelQueries({ queryKey: ["taskDetails", taskId] });
    const prev = queryClient.getQueryData(["taskDetails", taskId]);
    setCachedTaskStatus(status);
    return { prev };
  };
  const rollbackStatus = (ctx) => {
    if (ctx?.prev !== undefined)
      queryClient.setQueryData(["taskDetails", taskId], ctx.prev);
  };
  const reconcileStatus = () => {
    queryClient.invalidateQueries({ queryKey: ["taskDetails", taskId] });
    queryClient.invalidateQueries({ queryKey: ["eval-tasks"] });
  };

  const { mutate: pauseTask } = useMutation({
    // {} body required — the request-contract interceptor drops a bodyless POST.
    mutationFn: () => axios.post(endpoints.project.pauseEvalTask(taskId), {}),
    meta: { errorHandled: true },
    onMutate: () => optimisticStatus("paused"),
    onError: (_e, _v, ctx) => {
      rollbackStatus(ctx);
      enqueueSnackbar("Failed to pause task", { variant: "error" });
    },
    onSuccess: () => enqueueSnackbar("Task paused", { variant: "success" }),
    onSettled: reconcileStatus,
  });

  const { mutate: resumeTask } = useMutation({
    mutationFn: () => axios.post(endpoints.project.resumeEvalTask(taskId), {}),
    meta: { errorHandled: true },
    // pending (not running): resume re-queues, so the badge moves forward only.
    onMutate: () => optimisticStatus("pending"),
    onError: (_e, _v, ctx) => {
      rollbackStatus(ctx);
      enqueueSnackbar("Failed to resume task. It may have already finished.", {
        variant: "error",
      });
    },
    onSuccess: () => enqueueSnackbar("Task resumed", { variant: "success" }),
    onSettled: reconcileStatus,
  });

  const { mutate: renameTask } = useMutation({
    mutationFn: (newName) =>
      axios.patch(endpoints.project.updateEvalTask(taskId), {
        name: newName,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["taskDetails", taskId] });
      queryClient.invalidateQueries({ queryKey: ["eval-tasks"] });
      enqueueSnackbar("Task renamed", { variant: "success" });
    },
  });

  // Re-run is reachable from the Logs tab, where an invalid field isn't
  // rendered — a silent validation failure would read as a broken button.
  const openConfirm = useCallback(
    (mode) =>
      handleSubmit(
        () => setConfirmMode(mode),
        () => {
          setTab("details");
          enqueueSnackbar(
            "Fix the highlighted fields before running this task.",
            { variant: "error" },
          );
        },
      )(),
    [handleSubmit],
  );

  const { mutate: duplicateTask, isPending: isDuplicating } = useMutation({
    mutationFn: (payload) =>
      axios.post(endpoints.project.createEvalTask(), payload),
    meta: { errorHandled: true },
    onSuccess: (resp) => {
      queryClient.invalidateQueries({ queryKey: ["eval-tasks"] });
      enqueueSnackbar("Your task has been duplicated", { variant: "success" });
      const newId = resp?.data?.result?.id;
      navigate(newId ? `/dashboard/tasks/${newId}` : "/dashboard/tasks");
    },
    onError: (err) => {
      enqueueSnackbar(
        err?.response?.data?.result ||
          err?.message ||
          "Failed to duplicate task",
        { variant: "error" },
      );
    },
  });

  const handleOpenDuplicateDialog = () => {
    popover.onClose();
    setDuplicateDialogOpen(true);
  };

  const handleConfirmDuplicate = (taskName) => {
    if (!taskDetails || isDuplicating) return;
    const src = formValues?.name
      ? formValues
      : getDefaultTaskValues(taskDetails, null);

    const isDateDirty = Boolean(dirtyFields?.startDate || dirtyFields?.endDate);
    const hasSavedDateRange = Boolean(
      taskDetails?.filters_applied?.date_range?.length ||
        taskDetails?.filters_applied?.start_date ||
        taskDetails?.filters_applied?.end_date ||
        taskDetails?.start_date ||
        taskDetails?.end_date,
    );
    const includeDateRange = hasSavedDateRange || isDateDirty;

    const { filters: wireFilters, attributeFilters } = getNewTaskFilters(
      src,
      src.project,
      !includeDateRange,
    );

    if (!includeDateRange && wireFilters.date_range) {
      delete wireFilters.date_range;
    }

    const payload = {
      name: taskName,
      project: src.project,
      run_type: src.runType,
      row_type: src.rowType,
      ...(src.runType !== "continuous" &&
      Number.isFinite(Number(src.spansLimit)) &&
      Number(src.spansLimit) > 0
        ? { spans_limit: Number(src.spansLimit) }
        : {}),
      sampling_rate: src.samplingRate,
      evals: src.evalsDetails?.map((item) => item.id || item) || [],
      filters: {
        ...wireFilters,
        ...(attributeFilters?.length > 0 ? { filters: attributeFilters } : {}),
      },
    };
    duplicateTask(payload, {
      onSuccess: () => {
        setDuplicateDialogOpen(false);
      },
    });
  };

  const handleConfirm = useCallback(
    (editType) => {
      const data = formValues;
      const savedWindow = getUntouchedSavedWindow({
        taskDetails,
        data,
        dirty: dirtyFieldsRef.current,
        confirmMode,
      });
      const { filters, attributeFilters } = getNewTaskFilters(
        data,
        data.project,
        Boolean(savedWindow),
      );

      const transformedData = {
        evals: data.evalsDetails?.map((item) => item.id || item) || [],
        filters: {
          ...filters,
          ...savedWindow,
          ...(attributeFilters?.length > 0
            ? { filters: attributeFilters }
            : {}),
        },
        project_id: data.project,
        name: data.name,
        project: data.project,
        run_type: data.runType,
        sampling_rate: data.samplingRate,
        spans_limit: data.spansLimit ? Number(data.spansLimit) : undefined,
        edit_type: editType,
      };
      updateTask({ payload: transformedData, mode: confirmMode });
      setConfirmMode(null);
    },
    [formValues, updateTask, confirmMode, taskDetails],
  );

  if (isLoading && !taskDetails) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
        }}
      >
        <ContentSkeleton variant="panel" />
      </Box>
    );
  }

  if (!taskDetails) {
    const message = getTaskDetailsErrorMessage(error);
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100%",
          px: 3,
        }}
      >
        <Stack
          spacing={2}
          alignItems="center"
          sx={{ maxWidth: 420, textAlign: "center" }}
        >
          <Iconify
            icon="solar:clipboard-remove-linear"
            width={42}
            sx={{ color: "text.disabled" }}
          />
          <Box>
            <Typography variant="h6">Task not available</Typography>
            <Typography
              variant="body2"
              sx={{ mt: 0.75, color: "text.secondary" }}
            >
              {message}
            </Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            <Button
              variant="outlined"
              size="small"
              onClick={() => refetch?.()}
              disabled={isFetching}
            >
              Retry
            </Button>
            <Button
              variant="contained"
              size="small"
              onClick={() => navigate("/dashboard/tasks")}
              startIcon={<Iconify icon="solar:arrow-left-linear" width={14} />}
            >
              Back to Tasks
            </Button>
          </Stack>
        </Stack>
      </Box>
    );
  }

  const status = (taskDetails.status || "").toLowerCase();
  const canPause = status === "running";
  const canResume = isResumableTaskStatus(status);
  const linkedTraceSource = getLinkedTraceSource(taskDetails);
  const instantRunAskPath = getInstantRunAskPath(taskDetails, taskId);

  // A re-run mid-flight would race the live run.
  const rerunBlockedReason = !canEditTask
    ? "You don't have permission to run tasks."
    : status === "running" || status === "pending"
      ? "Wait for the current run to finish."
      : "";

  // The server refuses to update a running task, so Save waits for Pause and
  // the header says so before the user starts editing.
  const showPauseToEdit = canPause && canEditTask && !instantRunAskPath;

  // Pause/Resume stay in the header
  const headerActions = (
    <>
      {linkedTraceSource && (
        <Button
          variant="outlined"
          size="small"
          onClick={() => navigate(linkedTraceSource.path)}
          startIcon={<Iconify icon="solar:map-point-wave-linear" width={14} />}
        >
          {linkedTraceSource.label}
        </Button>
      )}
      {showPauseToEdit && (
        <Typography
          variant="caption"
          sx={{ color: "text.secondary", whiteSpace: "nowrap" }}
        >
          Pause the task to edit it
        </Typography>
      )}
      {canPause && (
        <Button
          variant="outlined"
          size="small"
          onClick={() => pauseTask()}
          startIcon={<Iconify icon="solar:pause-circle-linear" width={14} />}
        >
          Pause
        </Button>
      )}
      {canResume && (
        <Button
          variant="outlined"
          size="small"
          onClick={() => resumeTask()}
          startIcon={<Iconify icon="solar:play-circle-linear" width={14} />}
        >
          Resume
        </Button>
      )}
      {instantRunAskPath ? (
        <Button
          variant="outlined"
          size="small"
          onClick={() => navigate(instantRunAskPath)}
          startIcon={<Iconify icon="mdi:creation-outline" width={14} />}
        >
          Open in Ask
        </Button>
      ) : (
        <CustomTooltip
          show={!!rerunBlockedReason}
          title={rerunBlockedReason}
          size="small"
        >
          <span>
            <LoadingButton
              variant="outlined"
              size="small"
              onClick={() => openConfirm("rerun")}
              loading={isUpdating}
              disabled={!!rerunBlockedReason}
              startIcon={<Iconify icon="solar:restart-linear" width={14} />}
            >
              Re-run
            </LoadingButton>
          </span>
        </CustomTooltip>
      )}

      <LoadingButton
        variant="outlined"
        size="small"
        onClick={popover.onOpen}
        loading={isDuplicating}
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={14} />}
        sx={{
          textTransform: "none",
        }}
      >
        Actions
      </LoadingButton>

      <CustomPopover
        open={popover.open}
        onClose={popover.onClose}
        arrow="top-right"
        sx={{ width: 140 }}
      >
        <MenuItem
          onClick={handleOpenDuplicateDialog}
          disabled={!canEditTask || isDuplicating}
        >
          <Iconify icon="solar:copy-linear" width={16} />
          Duplicate
        </MenuItem>
      </CustomPopover>
    </>
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <TaskHeader
        mode="edit"
        name={taskDetails.name}
        projectName={taskDetails.project_name ?? taskDetails.projectName}
        status={taskDetails.status}
        actions={headerActions}
        onNameChange={(newName) => renameTask(newName)}
      />

      {isError && (
        <Alert
          severity="error"
          action={
            <Button
              color="inherit"
              size="small"
              onClick={() => refetch?.()}
              disabled={isFetching}
            >
              Retry
            </Button>
          }
          sx={{ mx: 2, mt: 1, flexShrink: 0 }}
        >
          {getTaskDetailsErrorMessage(error)} Existing task details are still
          shown.
        </Alert>
      )}

      {/* Segmented-pill tabs — matches EvalDetailPage style */}
      <Box
        sx={{
          px: 2,
          pt: 1.5,
          pb: 1,
          flexShrink: 0,
          backgroundColor: "background.paper",
        }}
      >
        <SegmentedTabs
          value={tab}
          onChange={(_, val) => setTab(val)}
          sx={{ width: "fit-content" }}
        >
          {TAB_OPTIONS.map((t) => (
            <Tab
              key={t.value}
              value={t.value}
              label={
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
                  <Iconify icon={t.icon} width={14} />
                  {t.label}
                </Box>
              }
            />
          ))}
        </SegmentedTabs>
      </Box>

      {/* Tab content */}
      <Box sx={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        {tab === "details" && (
          <ResizablePanels
            initialLeftWidth={55}
            minLeftWidth={35}
            maxLeftWidth={75}
            showIcon
            leftPanel={
              <TaskConfigPanel
                mode="edit"
                control={control}
                getValues={getValues}
                setValue={setValue}
                projectLocked
                initialProjectName={
                  taskDetails.project_name ?? taskDetails.projectName
                }
              />
            }
            rightPanel={
              <TaskLivePreview
                ref={previewRef}
                control={control}
                projectId={project}
                onTestStateChange={handleTestStateChange}
              />
            }
          />
        )}

        {tab === "logs" && (
          <Box sx={{ height: "100%", overflow: "auto", p: 2 }}>
            <TaskLogsView
              evalTaskId={taskId}
              taskStatus={taskDetails?.status}
            />
          </Box>
        )}

        {tab === "usage" && (
          <Box sx={{ height: "100%", overflow: "hidden" }}>
            <TaskUsageTab
              taskId={taskId}
              projectId={project}
              onViewLogs={() => setTab("logs")}
            />
          </Box>
        )}
      </Box>

      {/* Footer with Test + Save — only on Details tab */}
      {tab === "details" && (
        <Box
          sx={{
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            gap: 1,
            px: 2,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
            backgroundColor: "background.paper",
            flexShrink: 0,
          }}
        >
          <LoadingButton
            variant="outlined"
            size="small"
            loading={testState.isTesting}
            disabled={!testState.canTest || !canEditTask}
            onClick={() => previewRef.current?.runTest()}
            startIcon={<Iconify icon="solar:play-circle-linear" width={14} />}
            sx={{ minWidth: 120 }}
          >
            Test
          </LoadingButton>
          {instantRunAskPath ? (
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              This is an Instant eval run. Ask it again from its Ask page.
            </Typography>
          ) : (
            <LoadingButton
              variant="contained"
              size="small"
              onClick={() => openConfirm("save")}
              loading={isUpdating}
              disabled={!canEditTask || canPause}
              sx={{ minWidth: 140 }}
            >
              Save
            </LoadingButton>
          )}
        </Box>
      )}

      <TaskConfirmDialog
        title={confirmMode === "rerun" ? "Re-run Task" : "Update Task"}
        content="Select one of the options"
        confirmText={confirmMode === "rerun" ? "Re-run" : "Run task"}
        open={!!confirmMode}
        onClose={() => setConfirmMode(null)}
        onConfirm={handleConfirm}
        isLoading={isUpdating}
      />

      {duplicateDialogOpen && (
        <DuplicateTaskDialog
          open={duplicateDialogOpen}
          onClose={() => setDuplicateDialogOpen(false)}
          defaultName={`${formValues?.name || taskDetails?.name || "Task"}-duplicate`}
          onSubmit={handleConfirmDuplicate}
          isSubmitting={isDuplicating}
        />
      )}
    </Box>
  );
};

export default TaskDetailPage;
