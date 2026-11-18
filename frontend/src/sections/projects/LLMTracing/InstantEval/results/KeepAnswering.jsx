import React, { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import { isUUID } from "src/utils/utils";
import {
  LiveTaskUnchecked,
  createContinuousTask,
  findContinuousTask,
} from "../useInstantEval";
import { unitMeta } from "../instantEvalUnits";

// Turns a trace (or span) run's questions into a live task: every new trace
// that matches the run's filters is answered as it arrives. `billed` says
// whether each answer costs credits here (on Cloud).
export default function KeepAnswering({
  projectId,
  run,
  navigate,
  unit = "traces",
  billed = false,
  disabled = false,
}) {
  const noun = unitMeta(unit);
  const { user } = useSafeAuthContext();
  const [state, setState] = useState({ step: "idle" });
  const start = async () => {
    setState({ step: "working" });
    let createAttempted = false;
    const creationUnconfirmed =
      "Creation unconfirmed. Check Tasks before creating another.";
    try {
      const existing = await findContinuousTask({
        projectId,
        items: run.items,
        scopeFilters: run.scopeFilters || [],
        unit,
        userId: user?.id,
      });
      if (existing) {
        setState({ step: "done", taskId: existing.id, existed: true });
        return;
      }
      createAttempted = true;
      const taskId = await createContinuousTask({
        projectId,
        items: run.items,
        scopeFilters: run.scopeFilters || [],
        unit,
      });
      if (typeof taskId !== "string" || !isUUID(taskId)) {
        setState({ step: "error", error: creationUnconfirmed });
        return;
      }
      setState({ step: "done", taskId });
    } catch (error) {
      setState({
        step: "error",
        // Lookup failures precede create; after a create attempt an unreadable
        // acknowledgement cannot tell us whether a task was saved.
        error:
          error instanceof LiveTaskUnchecked
            ? error.message
            : getSafeActionErrorMessage(
                error,
                createAttempted
                  ? creationUnconfirmed
                  : "Could not check for an existing task. No new task was created.",
              ),
      });
    }
  };
  if (state.step === "done")
    return (
      <Alert
        severity="success"
        action={
          <Button
            size="small"
            onClick={() => navigate(`/dashboard/tasks/${state.taskId}`)}
          >
            View task
          </Button>
        }
      >
        {state.existed
          ? "A matching task is already saved. Check its status on the task page."
          : "Task saved. Check its status on the task page."}
      </Alert>
    );
  if (state.step === "confirm" || state.step === "working")
    return (
      <Alert
        severity="info"
        action={
          <Stack direction="row" spacing={0.5}>
            <Button
              size="small"
              disabled={state.step === "working"}
              onClick={() => setState({ step: "idle" })}
            >
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              disabled={state.step === "working"}
              onClick={start}
            >
              Start
            </Button>
          </Stack>
        }
      >
        Jev will answer{" "}
        {run.items.length > 1
          ? `these ${run.items.length} questions`
          : "this question"}{" "}
        for every new {noun.noun} that matches this view&apos;s filters.
        {billed ? " Every answer is billed as one eval run;" : ""} TypeSafe
        bills its tokens to your own key.
      </Alert>
    );
  return (
    <>
      {state.step === "error" && (
        <Alert
          severity="error"
          onClose={() => setState({ step: "idle" })}
          sx={{ mb: 1 }}
        >
          {state.error}
        </Alert>
      )}
      <Button
        variant="outlined"
        size="small"
        startIcon={<Iconify icon="mdi:sync" width={16} />}
        disabled={disabled}
        onClick={() => setState({ step: "confirm" })}
      >
        Keep answering new {noun.plural}
      </Button>
    </>
  );
}

KeepAnswering.propTypes = {
  projectId: PropTypes.string.isRequired,
  run: PropTypes.object.isRequired,
  navigate: PropTypes.func.isRequired,
  unit: PropTypes.oneOf(["traces", "spans"]),
  billed: PropTypes.bool,
  // A Viewer reads the run; the server refuses them a live task.
  disabled: PropTypes.bool,
};
