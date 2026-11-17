import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  createExternalRunRecord,
  externalRunRecipe,
  loadExternalRunPlan,
} from "src/api/develop/external-run-handoff";
import { isUnknownRequestOutcome } from "./httpResendCopy";
import { hold, usePendingHandoffs } from "./externalRunHandoffStore";

function downloadPlan(plan, recipe) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify({ ...plan, recipe }, null, 2)], {
      type: "application/json",
    }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = `external-run-${plan.body.run_id}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export default function ExternalRunHandoffDialog({
  action,
  onClose,
  disabled,
}) {
  usePendingHandoffs((state) => state.actions);
  const [plan, setPlan] = useState(action.plan ?? null);
  const [readError, setReadError] = useState("");
  const [readAttempt, setReadAttempt] = useState(0);
  useEffect(() => {
    if (action.plan) return undefined;
    const controller = new AbortController();
    setReadError("");
    loadExternalRunPlan({ ...action, signal: controller.signal })
      .then((loaded) => {
        if (controller.signal.aborted) return;
        action.plan = loaded;
        setPlan(loaded);
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setReadError(
          error?.message === "No selected examples belong to this run."
            ? error.message
            : "Could not read the pinned run and inputs. Check access and retry.",
        );
      });
    return () => controller.abort();
  }, [action, readAttempt]);
  const send = async () => {
    if (!plan || disabled || action.busy || action.created) return;
    action.busy = true;
    action.error = "";
    hold(action.datasetId, action);
    try {
      action.created = await createExternalRunRecord(plan);
    } catch (error) {
      // A later refusal cannot resolve an earlier request whose response was lost.
      action.unresolved ||= isUnknownRequestOutcome(error);
      action.error = action.unresolved
        ? "Could not confirm creation. Retry the same UUID and body; no code was executed."
        : "Could not create the run record. Check access and the source configuration before retrying.";
    } finally {
      action.busy = false;
      // Do not restore a detached action after logout or clear another action.
      if (
        usePendingHandoffs.getState().actions.get(action.datasetId) === action
      )
        hold(
          action.datasetId,
          action.unresolved && !action.created ? action : null,
        );
    }
  };
  const recipe = plan ? externalRunRecipe(plan, Boolean(action.created)) : "";
  return (
    <Dialog
      open
      onClose={() => !action.busy && onClose()}
      fullWidth
      maxWidth="md"
      aria-labelledby="external-handoff-title"
    >
      <DialogTitle id="external-handoff-title">
        Run again in your code
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2}>
          <Typography>
            {action.scope === "all" ? "Full run" : "Selected rows"} ·{" "}
            {action.label}
          </Typography>
          <Alert severity="info">
            Execution happens in your code, not in Future AGI. Creating a run
            record does not execute your task or change the original run.
          </Alert>
          <Typography variant="body2">
            A new run resolves current evaluator pins, which may differ from the
            source run. Inspect the returned pins before executing or scoring.
          </Typography>
          {readError && (
            <Alert
              severity="error"
              action={
                <Button onClick={() => setReadAttempt((n) => n + 1)}>
                  Retry read
                </Button>
              }
            >
              {readError}
            </Alert>
          )}
          {!plan && !readError && (
            <Typography>Reading the pinned plan…</Typography>
          )}
          {plan && (
            <>
              <Typography variant="body2">
                {plan.inputs.length} examples ·{" "}
                {plan.body.expected_items.length} trials · Version{" "}
                {plan.body.dataset_version_id} · New run {plan.body.run_id}
              </Typography>
              <Typography variant="body2">
                Download the handoff JSON for the exact plan, immutable inputs
                and current recipe. Keep it private: it contains dataset inputs
                and source configuration. Only full and selected scopes are
                available here.
              </Typography>
              <TextField
                label="REST recipe"
                multiline
                minRows={8}
                maxRows={16}
                value={recipe}
                InputProps={{ readOnly: true }}
              />
            </>
          )}
          {action.error && <Alert severity="error">{action.error}</Alert>}
          {action.created && (
            <>
              <Alert severity="success">
                Run record created. No code was executed.
              </Alert>
              <Typography variant="subtitle2">
                New run evaluator pins
              </Typography>
              <Typography component="div" data-testid="created-evaluator-pins">
                {action.created.evaluator_bindings.length
                  ? action.created.evaluator_bindings.map((binding) => (
                      <div key={binding.metric_id}>
                        {binding.metric_id} → {binding.evaluator_version_id}
                        {` (v${binding.version_number})`}
                      </div>
                    ))
                  : "No evaluators pinned."}
              </Typography>
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button disabled={action.busy} onClick={onClose}>
          Close
        </Button>
        <Button
          disabled={disabled || !plan}
          onClick={() => downloadPlan(plan, recipe)}
        >
          Download handoff JSON
        </Button>
        {!action.created && (
          <Button
            variant="contained"
            disabled={disabled || !plan || action.busy}
            onClick={send}
          >
            {action.busy
              ? "Creating record…"
              : action.error
                ? "Retry same create"
                : "Create run record"}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}

ExternalRunHandoffDialog.propTypes = {
  action: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
