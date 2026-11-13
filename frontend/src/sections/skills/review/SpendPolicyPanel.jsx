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
import TechnicalDetails from "../components/TechnicalDetails";
import { periodLabel, usd } from "../utils/conclusions";

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
  const period = periodLabel(policy.startsAt, policy.endsAt);
  const used = usd(policy.knownEstimateUsd) ?? '$0.00';
  const limit = policy.enabled ? usd(policy.periodLimitUsd) : null;
  const uncertain = Number(policy.unknownReservedUsd) > 0 ? usd(policy.unknownReservedUsd) : null;
  return (
    <Section title="Spending allowance">
      <Stack spacing={1.5}>
        {/* Conclusion first: what is allowed now and what has been used this period. */}
        <Typography variant="m3" role="status">
          {limit ? `${limit} allowance` : 'No spending allowance set'} · {used} used this period{period ? ` (${period})` : ''}
        </Typography>
        {limit && usd(policy.remainingUsd) && <Typography variant="s1">{usd(policy.remainingUsd)} left this period.</Typography>}
        {uncertain && <Typography variant="s1">{uncertain} is held by runs whose final cost isn&apos;t settled yet.</Typography>}
        <Typography variant="s3" color="text.secondary">
          Paid test runs on the application and with a model stay blocked until an admin sets an allowance. Estimates are not invoices.
        </Typography>
        {!canManage && (
          <Typography variant="s3">
            Only a workspace admin can change the allowance. No spending is allowed by default.
          </Typography>
        )}
        <TextField
          label="Period allowance (USD)"
          size="small"
          sx={{ maxWidth: 320 }}
          value={amount}
          disabled={!canManage || locked}
          onChange={(event) => setAmount(event.target.value)}
          inputProps={{ inputMode: "decimal" }}
          error={amount !== "" && !valid}
          helperText={amount !== "" && !valid ? "Enter an amount such as 25 or 25.50." : "No amount is filled in for you."}
        />
        <FormControlLabel
          control={
            <Checkbox
              checked={enabled}
              disabled={!canManage || locked}
              onChange={(_, value) => setEnabled(value)}
            />
          }
          label="Allow paid test runs up to this amount"
        />
        {stale && (
          <Alert severity="warning">
            The allowance changed while you were editing. Review the current
            allowance before saving your preserved input.
          </Alert>
        )}
        {stale && (
          <Button
            disabled={locked}
            onClick={() => setRevision(policy.revision)}
          >
            I reviewed the current allowance
          </Button>
        )}
        <OperationFeedback operation={operation} />
        {operation.result && (
          <Alert severity="success">
            Allowance saved. Spending already recorded is unchanged.
          </Alert>
        )}
        <Stack direction="row" spacing={1}>
          <Button
            variant="contained"
            color="primary"
            disabled={!canManage || !onSave || !valid || stale || locked}
            onClick={() => setConfirm(true)}
          >
            Save
          </Button>
          {onRefresh && (
            <Button disabled={operation.busy} onClick={onRefresh}>
              Refresh
            </Button>
          )}
        </Stack>
        <TechnicalDetails title="Accounting details">
          <Stack spacing={1}>
            <IdentityValue label="Period (UTC)" value={`${policy.startsAt} → ${policy.endsAt}`} />
            <IdentityValue label="Configured allowance (USD)" value={policy.periodLimitUsd ?? "Not configured"} />
            <IdentityValue label="Known estimated spend (USD)" value={policy.knownEstimateUsd} />
            <IdentityValue label="Committed, including reservations (USD)" value={policy.committedUsd} />
            <IdentityValue label="Remaining (USD)" value={policy.remainingUsd ?? "Not configured"} />
            <IdentityValue label="Unsettled reservations, all periods (USD)" value={policy.unknownReservedUsd} />
            <IdentityValue label="Unsettled reservations from earlier periods (USD)" value={policy.priorPeriodUnknownUsd} />
            <Typography variant="s3">
              One allowance covers task, judge and approved charged-tool calls. Existing Simulation plan
              entitlements remain additional checks, not a second allowance. Cancelling a run stops new
              calls; it does not erase incurred or unsettled spend. Lowering the allowance below what is
              committed blocks new runs without rewriting history.
            </Typography>
          </Stack>
        </TechnicalDetails>
      </Stack>
      <ConfirmDialog
        open={confirm}
        onClose={() => {
          if (!operation.busy) setConfirm(false);
        }}
        closeDisabled={operation.busy}
        title="Change workspace allowance?"
        content={enabled ? `Allow paid test runs up to USD ${amount} this period. Spending already recorded stays recorded. Saving makes no model call.` : `Paid test runs stay blocked. The allowance is recorded as USD ${amount}. Spending already recorded stays recorded.`}
        action={
          <Button
            variant="contained"
            color="primary"
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
            Save allowance
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
