import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Checkbox,
  FormControlLabel,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Section from "src/components/section";
import { ConfirmDialog } from "src/components/custom-dialog";
import { IdentityValue, ReviewState } from "./ReleaseReviewBody";
import { OperationFeedback, useReviewedOperation } from "./ReleaseRequestPanel";

function PolicyForm({ policy, canManage, onSave, onRefresh }) {
  const [revision, setRevision] = useState(policy.revision);
  const [amount, setAmount] = useState(policy.periodLimitUsd ?? "");
  const [enabled, setEnabled] = useState(policy.enabled);
  const [confirm, setConfirm] = useState(false);
  const operation = useReviewedOperation();
  const stale = revision !== policy.revision;
  // Preserve decimal text all the way to the mapper. Never authorize a default.
  const valid =
    /^(?:0|[1-9]\d{0,9})(?:\.\d{1,8})?$/.test(amount) &&
    (!enabled || /[1-9]/.test(amount));
  const locked =
    operation.busy || operation.uncertain || Boolean(operation.result);
  return (
    <Section title="Workspace spending policy">
      <Stack spacing={1.5}>
        <Typography variant="s3">
          One workspace allowance covers task, judge and approved charged-tool
          calls. Provider estimates are not invoices. Existing Simulation plan
          entitlements remain additional checks, not replacement budgets or a
          second allowance.
        </Typography>
        <IdentityValue
          label="Period (UTC)"
          value={`${policy.startsAt} → ${policy.endsAt}`}
        />
        <IdentityValue
          label="Configured period allowance (USD)"
          value={policy.periodLimitUsd ?? "Not configured"}
        />
        <IdentityValue
          label="Known estimated spend this period (USD)"
          value={policy.knownEstimateUsd}
        />
        <IdentityValue
          label="Committed allowance, including reservations (USD)"
          value={policy.committedUsd}
        />
        <IdentityValue
          label="Remaining allowance (USD)"
          value={policy.remainingUsd ?? "Not configured"}
        />
        <IdentityValue
          label="Uncertain reservations across periods (USD)"
          value={policy.unknownReservedUsd}
        />
        <IdentityValue
          label="Uncertain reservations from prior periods (USD)"
          value={policy.priorPeriodUnknownUsd}
        />
        <Alert severity="info">
          Cancellation stops new calls; it does not erase incurred or uncertain
          spend. Reducing the allowance below committed usage blocks new
          admissions without rewriting history.
        </Alert>
        {!canManage && (
          <Typography variant="s3">
            An administrator must configure the allowance. No default spend is
            authorized.
          </Typography>
        )}
        <TextField
          label="Period allowance (USD)"
          size="small"
          value={amount}
          disabled={!canManage || locked}
          onChange={(event) => setAmount(event.target.value)}
          inputProps={{ inputMode: "decimal" }}
          error={amount !== "" && !valid}
          helperText="Enter an explicit decimal amount with at most eight decimal places. No amount is preselected for a new policy."
        />
        <FormControlLabel
          control={
            <Checkbox
              checked={enabled}
              disabled={!canManage || locked}
              onChange={(_, value) => setEnabled(value)}
            />
          }
          label="Enable paid-work admission under this allowance"
        />
        {stale && (
          <Alert severity="warning">
            Policy changed while you were editing. Review the current allowance
            before applying your preserved input.
          </Alert>
        )}
        {stale && (
          <Button
            disabled={locked}
            onClick={() => setRevision(policy.revision)}
          >
            I reviewed policy revision {policy.revision}
          </Button>
        )}
        <OperationFeedback operation={operation} />
        {operation.result && (
          <Alert severity="info">
            Policy update recorded. Refresh accounting; existing reservations
            are unchanged.
          </Alert>
        )}
        <Stack direction="row" spacing={1}>
          <Button
            variant="outlined"
            disabled={!canManage || !onSave || !valid || stale || locked}
            onClick={() => setConfirm(true)}
          >
            Review policy change
          </Button>
          {onRefresh && (
            <Button disabled={operation.busy} onClick={onRefresh}>
              Refresh accounting
            </Button>
          )}
        </Stack>
      </Stack>
      <ConfirmDialog
        open={confirm}
        onClose={() => {
          if (!operation.busy) setConfirm(false);
        }}
        closeDisabled={operation.busy}
        title="Change workspace allowance?"
        content={`${enabled ? "Enable" : "Disable"} new paid-work admission with a period limit of USD ${amount}. Existing incurred and uncertain reservations remain accounted for. No model call is made by this change.`}
        action={
          <Button
            variant="contained"
            disabled={!valid || stale || locked || !canManage}
            onClick={async () => {
              const result = await operation.run(onSave, {
                revision,
                enabled,
                periodLimitUsd: amount,
              });
              if (result) setConfirm(false);
            }}
          >
            Apply policy change
          </Button>
        }
      />
    </Section>
  );
}
PolicyForm.propTypes = {
  policy: PropTypes.object.isRequired,
  canManage: PropTypes.bool,
  onSave: PropTypes.func,
  onRefresh: PropTypes.func,
};
export default function SpendPolicyPanel({
  scopeKey,
  state = "unavailable",
  policy,
  onRetry,
  ...props
}) {
  if (!scopeKey || state !== "ready" || !policy)
    return (
      <ReviewState state={!scopeKey ? "forbidden" : state} onRetry={onRetry} />
    );
  return <PolicyForm key={scopeKey} {...props} policy={policy} />;
}
SpendPolicyPanel.propTypes = {
  scopeKey: PropTypes.string,
  state: PropTypes.string,
  policy: PropTypes.object,
  onRetry: PropTypes.func,
};
