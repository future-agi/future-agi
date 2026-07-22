import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  Tab,
  Tabs,
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
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
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

const getTaskDetailsErrorMessage = (error) =>
  error?.result ||
  error?.message ||
  error?.response?.data?.result ||
  error?.response?.data?.message ||
  "Task details could not be loaded.";

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

const TaskDetailPage = () => {
  const { taskId } = useParams();
  const navigate = useNavigate();
  const { role } = useAuthContext();
  const canEditTask =
    RolePermission.OBSERVABILITY[PERMISSIONS.ADD_TASKS_ALERTS][role];
  const queryClient = useQueryClient();
  const [tab, setTab] = useState("details");
  const [confirmOpen, setConfirmOpen] = useState(false);

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
  } = useGetTaskData(taskId, {
    enabled: !!taskId,
    // Poll while non-terminal so the header/badge advance without a refresh.
    // queryFn returns the raw axios response, so status is pre-`select`.
    refetchInterval: (query) => {
      const s = query?.state?.data?.data?.result?.status?.toLowerCase?.();
      return s === "pending" || s === "running" ? 4000 : false;
    },
  });

  const { control, handleSubmit, getValues, setValue, reset } = useForm({
    defaultValues: getDefaultTaskValues(null, null),
    resolver: zodResolver(NewTaskValidationSchema()),
  });

  const project = useWatch({ control, name: "project" });
  const formValues = useWatch({ control });

  // Populate form once task is loaded
  useEffect(() => {
    if (taskDetails) {
      reset(getDefaultTaskValues(taskDetails, null));
    }
  }, [taskDetails, reset]);

  // ── Mutations ──
  const { mutate: updateTask, isPending: isUpdating } = useMutation({
    mutationFn: (data) =>
      axios.patch(endpoints.project.patchEvalTask(), {
        ...data,
        eval_task_id: taskId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["taskDetails", taskId] });
      queryClient.invalidateQueries({ queryKey: ["eval-tasks"] });
      enqueueSnackbar("Task updated successfully", { variant: "success" });
    },
    onError: (err) => {
      enqueueSnackbar(err?.response?.data?.result || "Failed to update task", {
        variant: "error",
      });
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

  // Transform form → update payload (same logic as EditTaskDrawerV2)
  const handleSave = useCallback(() => {
    handleSubmit(() => {
      setConfirmOpen(true);
    })();
  }, [handleSubmit]);

  const handleConfirm = useCallback(
    (editType) => {
      const data = formValues;
      const { filters, attributeFilters } = getNewTaskFilters(
        data,
        data.project,
      );

      const transformedData = {
        evals: data.evalsDetails?.map((item) => item.id || item) || [],
        filters: {
          ...filters,
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
      updateTask(transformedData);
      setConfirmOpen(false);
    },
    [formValues, updateTask],
  );

  if (isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
        }}
      >
        <CircularProgress size={28} />
      </Box>
    );
  }

  if (isError || !taskDetails) {
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
          <Button
            variant="contained"
            size="small"
            onClick={() => navigate("/dashboard/tasks")}
            startIcon={<Iconify icon="solar:arrow-left-linear" width={14} />}
            sx={{ textTransform: "none" }}
          >
            Back to Tasks
          </Button>
        </Stack>
      </Box>
    );
  }

  const status = (taskDetails.status || "").toLowerCase();
  const canPause = status === "running";
  const canResume = status === "paused";
  const linkedTraceSource = getLinkedTraceSource(taskDetails);

  // Pause/Resume stay in the header
  const headerActions = (
    <>
      {linkedTraceSource && (
        <Button
          variant="outlined"
          size="small"
          onClick={() => navigate(linkedTraceSource.path)}
          startIcon={<Iconify icon="solar:map-point-wave-linear" width={14} />}
          sx={{
            textTransform: "none",
            fontWeight: 500,
            fontSize: "12px",
            height: 30,
          }}
        >
          {linkedTraceSource.label}
        </Button>
      )}
      {canPause && (
        <Button
          variant="outlined"
          size="small"
          onClick={() => pauseTask()}
          startIcon={<Iconify icon="solar:pause-circle-linear" width={14} />}
          sx={{
            textTransform: "none",
            fontWeight: 500,
            fontSize: "12px",
            height: 30,
          }}
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
          sx={{
            textTransform: "none",
            fontWeight: 500,
            fontSize: "12px",
            height: 30,
          }}
        >
          Resume
        </Button>
      )}
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
        <Tabs
          value={tab}
          onChange={(_, val) => setTab(val)}
          TabIndicatorProps={{ style: { display: "none" } }}
          sx={{
            minHeight: 32,
            "& .MuiTab-root": {
              minHeight: 32,
              px: 1.5,
              py: 0,
              mr: "0px !important",
              textTransform: "none",
              fontSize: "13px",
              borderRadius: "6px",
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
              sx={{
                bgcolor:
                  tab === t.value
                    ? (theme) =>
                        theme.palette.mode === "dark"
                          ? "rgba(255,255,255,0.12)"
                          : "background.paper"
                    : "transparent",
                boxShadow:
                  tab === t.value
                    ? (theme) =>
                        theme.palette.mode === "dark"
                          ? "none"
                          : "0 1px 3px rgba(0,0,0,0.08)"
                    : "none",
                borderRadius: "6px",
                fontWeight: tab === t.value ? 600 : 400,
                color: tab === t.value ? "text.primary" : "text.disabled",
              }}
            />
          ))}
        </Tabs>
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
            <TaskUsageTab taskId={taskId} />
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
            sx={{ textTransform: "none", fontWeight: 500, minWidth: 120 }}
          >
            Test
          </LoadingButton>
          <LoadingButton
            variant="contained"
            size="small"
            onClick={handleSave}
            loading={isUpdating}
            disabled={!canEditTask}
            sx={{ textTransform: "none", fontWeight: 500, minWidth: 140 }}
          >
            Save
          </LoadingButton>
        </Box>
      )}

      <TaskConfirmDialog
        title="Update Task"
        content="Select one of the options"
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={handleConfirm}
        isLoading={isUpdating}
      />
    </Box>
  );
};

export default TaskDetailPage;
