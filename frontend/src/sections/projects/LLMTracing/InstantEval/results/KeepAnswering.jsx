import React, { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
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
      const taskId = await createContinuousTask({
        projectId,
        items: run.items,
        scopeFilters: run.scopeFilters || [],
        unit,
      });
      setState({ step: "done", taskId });
    } catch (error) {
      setState({
        step: "error",
        // Nothing was started when the check couldn't be made; it says why.
        error:
          error instanceof LiveTaskUnchecked
            ? error.message
            : getSafeActionErrorMessage(
                error,
                "The live task could not be created.",
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
            sx={{ textTransform: "none" }}
          >
            View task
          </Button>
        }
      >
        {state.existed
          ? `These questions are already being answered for new ${noun.plural} that match this view.`
          : `Live. New ${noun.plural} that match this view will be answered as they arrive.`}
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
              sx={{ textTransform: "none" }}
            >
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              disabled={state.step === "working"}
              onClick={start}
              sx={{ textTransform: "none" }}
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
        sx={{ textTransform: "none" }}
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
