import React, { useCallback, useEffect, useRef, useState } from "react";
import { Box, Button, CircularProgress, Tab, Tabs } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import Iconify from "src/components/iconify";
import ResizablePanels from "src/components/resizablePanels/ResizablePanels";
import TaskLogsView from "src/sections/common/EvalsTasks/TaskLogsView";
import { useGetTaskData } from "src/sections/common/EvalsTasks/common";
import TaskHeader from "./components/TaskHeader";
import TaskConfigPanel from "./components/TaskConfigPanel";
import TaskLivePreview from "./components/TaskLivePreview";
import TaskUsageTab from "./components/TaskUsageTab";
import {
  NewTaskValidationSchema,
  getDefaultTaskValues,
  extractAttributeFilters,
} from "./schema";
import TaskConfirmDialog from "src/sections/common/EvalsTasks/EditTaskDrawer/TaskConfirmBox";

const TAB_OPTIONS = [
  { label: "Details", value: "details", icon: "solar:settings-linear" },
  { label: "Logs", value: "logs", icon: "solar:notebook-linear" },
  { label: "Usage", value: "usage", icon: "solar:chart-2-linear" },
];

const TaskDetailPage = () => {
  const { taskId } = useParams();
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

  const { data: taskDetails, isLoading } = useGetTaskData(taskId, {
    enabled: !!taskId,
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
      enqueueSnackbar("Task updated successfully", { variant: "success" });
    },
    onError: (err) => {
      enqueueSnackbar(err?.response?.data?.result || "Failed to update task", {
        variant: "error",
      });
    },
  });

  const { mutate: pauseTask } = useMutation({
    mutationFn: () => axios.post(endpoints.project.pauseEvalTask(taskId)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["taskDetails", taskId] });
      enqueueSnackbar("Task paused", { variant: "success" });
    },
  });

  const { mutate: resumeTask } = useMutation({
    mutationFn: () => axios.post(endpoints.project.resumeEvalTask(taskId)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["taskDetails", taskId] });
      enqueueSnackbar("Task resumed", { variant: "success" });
    },
  });

  const { mutate: renameTask } = useMutation({
    mutationFn: (newName) =>
      axios.patch(endpoints.project.patchEvalTask(), {
        eval_task_id: taskId,
        name: newName,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["taskDetails", taskId] });
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
      const attributeFilters = extractAttributeFilters(data?.filters);
      const observationTypes = (data.filters || [])
        .filter((f) => f.property === "observation_type")
        .map((f) => f?.filterConfig?.filterValue);

      const transformedData = {
        evals: data.evalsDetails?.map((item) => item.id || item) || [],
        filters: {
          project_id: data.project,
          date_range: [
            new Date(data.startDate).toISOString(),
            new Date(data.endDate).toISOString(),
          ],
          ...(observationTypes?.length > 0
            ? { observation_type: observationTypes }
            : {}),
          ...(attributeFilters?.length > 0
            ? { span_attributes_filters: attributeFilters }
            : {}),
        },
        project_id: data.project,
        name: data.name,
        project: data.project,
        run_type: data.runType,
        sampling_rate: data.samplingRate,
        spans_limit: data.spansLimit ? String(data.spansLimit) : undefined,
        edit_type: editType,
      };
      updateTask(transformedData);
      setConfirmOpen(false);
    },
    [formValues, updateTask],
  );

  if (isLoading || !taskDetails) {
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

  const status = (taskDetails.status || "").toLowerCase();
  const canPause = status === "running" || status === "pending";
  const canResume = status === "paused";

  // Pause/Resume stay in the header
  const headerActions = (
    <>
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
            <TaskLogsView evalTaskId={taskId} />
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
            disabled={!testState.canTest}
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
