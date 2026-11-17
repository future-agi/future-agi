import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  DialogActions,
  DialogContent,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { budgetRaisePayload } from "./budgetCapTargets";

export default function BudgetCapForm({
  target,
  mutation,
  busy,
  onReload,
  onClose,
}) {
  const [amount, setAmount] = useState(String(target.limit));
  let payload;
  let validation;
  try {
    payload = budgetRaisePayload(target, amount);
  } catch (error) {
    validation = error.message;
  }
  const save = () => {
    if (!payload || busy || mutation.isPending || mutation.error) return;
    mutation.mutate(payload, {
      onSuccess: () => {
        enqueueSnackbar(
          target.mode === "workspace"
            ? "Budget saved. Live agent keys receive it at the next gateway key sync."
            : target.level === "agent_run"
              ? "Run ceiling saved for new runs. Existing run limits are unchanged."
              : "Budget limit saved. Other limits still apply.",
          { variant: "success" },
        );
        onClose();
      },
    });
  };
  return (
    <>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Typography variant="subtitle1">{target.title}</Typography>
          <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>
            {`Budget ID: ${target.id} · Current limit: $${target.limit} · Period: ${target.period}`}
          </Typography>
          <Alert severity="info">{target.notice}</Alert>
          <Typography variant="body2" color="text.secondary">
            Raise-only adjustment; this form does not activate admission caps.
            Use Org Config &gt; Budgets &gt; Admission cap activation to
            activate, edit-and-activate or deactivate agent/key caps and the run
            ceiling.
          </Typography>
          {target.inactive && (
            <Alert severity="warning">
              This policy is not enabled. Raising its limit does not turn
              enforcement on.
            </Alert>
          )}
          <TextField
            autoFocus
            label="New limit (USD)"
            type="number"
            fullWidth
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            inputProps={{
              min: 0,
              step: target.mode === "workspace" ? "0.01" : "any",
            }}
            disabled={mutation.isPending}
            error={Boolean(validation && amount !== String(target.limit))}
            helperText={
              validation ||
              "Only this limit is raised; other policy fields are kept."
            }
          />
          {mutation.error && (
            <Alert severity="error">
              {mutation.error.message || "The cap could not be saved."} Reload
              the cap before retrying. A stale policy is never overwritten
              automatically.
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button onClick={onReload} disabled={busy || mutation.isPending}>
          Reload cap
        </Button>
        <Button
          variant="contained"
          onClick={save}
          disabled={
            !payload || busy || mutation.isPending || Boolean(mutation.error)
          }
        >
          {mutation.isPending ? "Saving…" : "Raise cap"}
        </Button>
      </DialogActions>
    </>
  );
}

BudgetCapForm.propTypes = {
  target: PropTypes.object.isRequired,
  mutation: PropTypes.object.isRequired,
  busy: PropTypes.bool,
  onReload: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};
