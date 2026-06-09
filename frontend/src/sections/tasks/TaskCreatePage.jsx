import React, { useCallback, useEffect, useRef, useState } from "react";
import { Box, Button } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useForm, useWatch } from "react-hook-form";
import { useMutation } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { endOfToday, sub } from "date-fns";
import { useNavigate } from "react-router";
import { useSearchParams } from "react-router-dom";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { formatDate } from "src/utils/report-utils";
import ResizablePanels from "src/components/resizablePanels/ResizablePanels";
import Iconify from "src/components/iconify";
import TaskHeader from "./components/TaskHeader";
import TaskConfigPanel from "./components/TaskConfigPanel";
import TaskLivePreview from "./components/TaskLivePreview";
import { NewTaskValidationSchema } from "./schema";
import { useTaskDraft } from "./hooks/useTaskDraft";

const TaskCreatePage = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const preselectedProject = searchParams.get("project") || "";
  const preselectedFilters = (() => {
    try {
      const raw = searchParams.get("filters");
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  })();
  const preselectedStartDate = searchParams.get("startDate") || null;
  const preselectedEndDate = searchParams.get("endDate") || null;
  // `returnTo` — path to go back to when the user cancels or clicks the
  // back arrow (set by entrypoints like the tracing "Add Evals" button).
  // Restricted to same-origin paths to avoid open-redirect risks.
  const returnTo = (() => {
    const raw = searchParams.get("returnTo");
    if (!raw) return null;
    return raw.startsWith("/") && !raw.startsWith("//") ? raw : null;
  })();
  const cancelDestination = returnTo || "/dashboard/tasks";

  // Draft persistence — `useTaskDraft` ensures the URL has a stable
  // `?draft=<uuid>` param and mirrors the form state to localStorage so
  // a reload (or accidental tab close) doesn't lose in-progress work.
  // `initialValues` is read synchronously on mount so useForm's
  // `defaultValues` can hydrate from it directly — no extra reset().
  const {
    initialValues: draftValues,
    save: saveDraft,
    clear: clearDraft,
  } = useTaskDraft();

  // Test runner — imperative handle from the live preview
  const previewRef = useRef(null);
  const [testState, setTestState] = useState({
    canTest: false,
    isTesting: false,
  });
  const handleTestStateChange = useCallback((next) => {
    setTestState(next);
  }, []);

  const baseDefaults = {
    name: "",
    project: preselectedProject,
    rowType: "spans",
    filters: Array.isArray(preselectedFilters) ? preselectedFilters : [],
    spansLimit: 100000,
    samplingRate: 50,
    evalsDetails: [],
    startDate:
      preselectedStartDate || formatDate(sub(new Date(), { months: 12 })),
    endDate: preselectedEndDate || formatDate(endOfToday()),
    runType: "historical",
  };

  const { control, handleSubmit, getValues, setValue, watch } = useForm({
    // Spread saved draft values OVER the defaults so any new fields
    // we add later still get their defaults when an old draft loads.
    defaultValues: { ...baseDefaults, ...(draftValues || {}) },
    resolver: zodResolver(NewTaskValidationSchema()),
  });

  // Persist every form change to localStorage (debounced inside the
  // hook so we don't write on every keystroke).
  useEffect(() => {
    const subscription = watch((values) => {
      saveDraft(values);
    });
    return () => subscription.unsubscribe();
  }, [watch, saveDraft]);

  const project = useWatch({ control, name: "project" });
  const name = useWatch({ control, name: "name" });

  const { mutate: createTask, isPending } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.project.createEvalTask(), { ...data }),
    onSuccess: (resp) => {
      // Drop the localStorage draft now that the task is persisted
      // server-side — otherwise it'd reappear next time the user opens
      // the same `?draft=<id>` URL.
      clearDraft();
      enqueueSnackbar("Task created successfully", { variant: "success" });
      const newTaskId = resp?.data?.result?.id;
      if (newTaskId) {
        navigate(`/dashboard/tasks/${newTaskId}`);
      } else {
        navigate("/dashboard/tasks");
      }
    },
    onError: (err) => {
      enqueueSnackbar(
        err?.response?.data?.result || err?.message || "Failed to create task",
        { variant: "error" },
      );
    },
  });

  const onSubmit = (data) => {
    const {
      runType,
      rowType,
      spansLimit,
      samplingRate,
      evalsDetails,
      startDate,
      endDate,
      ...restData
    } = data;
    const payload = {
      ...restData,
      run_type: runType,
      row_type: rowType,
      ...(runType !== "continuous" && spansLimit
        ? { spans_limit: spansLimit }
        : {}),
      sampling_rate: samplingRate,
      evals_details: evalsDetails,
      start_date: startDate,
      end_date: endDate,
    };
    createTask(payload);
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <TaskHeader
        mode="create"
        name={name || "Create Task"}
        backTo={cancelDestination}
      />

      <Box sx={{ flex: 1, minHeight: 0 }}>
        <ResizablePanels
          initialLeftWidth={55}
          minLeftWidth={35}
          maxLeftWidth={75}
          showIcon
          leftPanel={
            <TaskConfigPanel
              mode="create"
              control={control}
              getValues={getValues}
              setValue={setValue}
              projectLocked={false}
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
      </Box>

      {/* Footer actions */}
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
        <Button
          variant="outlined"
          size="small"
          onClick={() => navigate(cancelDestination)}
          sx={{ textTransform: "none", fontWeight: 500 }}
        >
          Cancel
        </Button>
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
          onClick={handleSubmit(onSubmit)}
          loading={isPending}
          sx={{ textTransform: "none", fontWeight: 500, minWidth: 140 }}
        >
          Create Task
        </LoadingButton>
      </Box>
    </Box>
  );
};

export default TaskCreatePage;
