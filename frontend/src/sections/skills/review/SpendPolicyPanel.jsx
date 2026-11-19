import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Checkbox,
  FormControlLabel,
  InputAdornment,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Section from "src/components/section";
import { paths } from "src/routes/paths";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import { ConfirmDialog } from "src/components/custom-dialog";
import { IdentityValue } from "./ReleaseReviewBody";
import { OperationFeedback, useReviewedOperation } from "./ReleaseRequestPanel";
import TechnicalDetails from "../components/TechnicalDetails";
import SavedFeedback from "../components/OperationFeedback";
import CapabilityNotice from "../components/CapabilityNotice";
import ActionReason, { reasonProps } from "../components/ActionReason";
import { periodLabel, usd } from "../utils/conclusions";
import { allowanceInput } from "../utils/format";

const DECIMAL = /^(?:0|[1-9]\d{0,9})(?:\.\d{1,8})?$/;
const SAVE_REASON_ID = "allowance-save-reason";

// Allowance periods are UTC calendar months (spend_policy.py); the field says
// "Monthly" only when the period the server reports is one.
function isCalendarMonth(startsAt, endsAt) {
  const start = new Date(startsAt);
  const end = new Date(endsAt);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return false;
  const next = Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 1);
  return start.getTime() === Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1) && end.getTime() === next;
}

// A member can't set the allowance, but can hand a workspace admin a
// ready-to-send request with a link back to this page (the viewer pattern of
// ContextUsePanel's "Copy request for admin"). Nothing is changed or sent.
function AdminRequestNotice({ inForce, onRefresh, busy }) {
  const [copying, setCopying] = useState(false);
  const copy = async () => {
    const url = typeof window === "undefined" ? paths.dashboard.skills.allowance : new URL(paths.dashboard.skills.allowance, window.location.origin).href;
    const request = `Please set a Skills workspace allowance so paid application tests can run: ${url}`;
    setCopying(true);
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Unavailable");
      await navigator.clipboard.writeText(request);
      enqueueSnackbar("Copied the request for an admin.");
    } catch {
      enqueueSnackbar("Couldn't copy the request. Ask a workspace admin to set the allowance.", { variant: "error" });
    } finally {
      setCopying(false);
    }
  };
  return (
    <CapabilityNotice
      title="Only workspace admins can set the allowance"
      description={inForce ? "Copy a request and send it to a workspace admin if paid tests need more." : "Paid application tests can run once an admin sets one. Copy a request and send it to a workspace admin."}
      actions={<>
        <Button variant="outlined" size="small" disabled={copying} onClick={copy}>Copy request for admin</Button>
        {onRefresh && <Button size="small" disabled={busy} onClick={onRefresh}>Refresh</Button>}
      </>}
    />
  );
}
AdminRequestNotice.propTypes = { inForce: PropTypes.bool, onRefresh: PropTypes.func, busy: PropTypes.bool };

function PolicyForm({ policy, canManage, onSave, onRefresh, savedAction }) {
  const [revision, setRevision] = useState(policy.revision);
  const initialAmount = allowanceInput(policy.periodLimitUsd);
  const [amount, setAmount] = useState(initialAmount);
  // A workspace with no saved policy (revision 0) authorizes nothing: the box starts unticked.
  const initialEnabled = policy.revision > 0 ? policy.enabled : false;
  const [enabled, setEnabled] = useState(initialEnabled);
  const [confirm, setConfirm] = useState(false);
  const operation = useReviewedOperation();
  const stale = revision !== policy.revision;
  // Preserve decimal text all the way to the mapper. Never authorize a default.
  const wellFormed = DECIMAL.test(amount);
  const valid = wellFormed && (!enabled || /[1-9]/.test(amount));
  const unchanged = policy.revision > 0 && amount === initialAmount && enabled === initialEnabled;
  const locked =
    operation.busy || operation.uncertain || Boolean(operation.result);
  const period = periodLabel(policy.startsAt, policy.endsAt);
  const used = usd(policy.knownEstimateUsd) ?? "$0.00";
  const inForce = policy.revision > 0 && policy.enabled;
  const limit = inForce ? usd(policy.periodLimitUsd) : null;
  const uncertain = Number(policy.unknownReservedUsd) > 0 ? usd(policy.unknownReservedUsd) : null;
  const amountError = amount === "" ? null : !wellFormed ? "Enter an amount such as 25 or 25.50." : !valid ? "Enter an amount above $0.00 to allow paid runs." : null;
  // The box below is the only "off" control; the field only takes the amount.
  const monthly = isCalendarMonth(policy.startsAt, policy.endsAt);
  const emptyHint = monthly ? "Monthly limit in USD." : "Limit for each period, in USD.";
  const left = limit ? usd(policy.remainingUsd) : null;
  // One summary line: limit, use, what's left and the period, then the caveat once.
  const facts = limit
    ? [`${limit} limit`, `${used} used`, left ? `${left} left` : null, period ? `${period} (UTC)` : null]
    : [Number(policy.knownEstimateUsd) > 0 ? `${used} used` : null, Number(policy.knownEstimateUsd) > 0 && period ? `${period} (UTC)` : null];
  const summary = facts.filter(Boolean).join(" · ");
  const saveReason = locked || !onSave ? null
    : stale ? "Review the current allowance first."
      : amount === "" ? "Enter an amount."
        : !valid ? "Fix the amount."
          : unchanged ? "Nothing changed yet."
            : null;
  return (
    // The page title names the setting; this block carries no second heading.
    <Section>
      <Stack spacing={1.5}>
        {/* Conclusion first, once: what is allowed now and what has been used this period. */}
        {limit ? (
          <Typography variant="m3" role="status">
            {summary}.{" "}
            <Typography component="span" variant="s1" color="text.secondary">Estimates, not invoices.</Typography>
          </Typography>
        ) : (
          <Stack spacing={0.5}>
            <Typography variant="m3" role="status">Paid test runs are off for this workspace.</Typography>
            {summary && <Typography variant="s3" color="text.secondary">{summary}. Estimates, not invoices.</Typography>}
          </Stack>
        )}
        {uncertain && <Typography variant="s1">{uncertain} is held by runs whose final cost isn&apos;t settled yet.</Typography>}
        {!canManage && <AdminRequestNotice inForce={inForce} onRefresh={onRefresh} busy={operation.busy} />}
        {canManage && <>
          <TextField
            label={monthly ? "Monthly allowance (USD)" : "Allowance per period (USD)"}
            size="small"
            sx={{ maxWidth: 320 }}
            value={amount}
            disabled={locked}
            onChange={(event) => setAmount(event.target.value)}
            InputProps={{ startAdornment: <InputAdornment position="start">$</InputAdornment> }}
            inputProps={{ inputMode: "decimal", step: 0.01 }}
            error={Boolean(amountError)}
            helperText={amountError || (amount === "" ? emptyHint : undefined)}
          />
          <FormControlLabel
            control={
              <Checkbox
                checked={enabled}
                disabled={locked}
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
            <SavedFeedback success="Allowance saved. Spending already recorded is unchanged.">
              {savedAction}
            </SavedFeedback>
          )}
        </>}
        {canManage && <Stack direction="row" spacing={1} useFlexGap alignItems="center" sx={{ flexWrap: "wrap" }}>
          <Button
            variant="contained"
            color="primary"
            disabled={!onSave || !valid || stale || locked || unchanged}
            onClick={() => setConfirm(true)}
            {...reasonProps(SAVE_REASON_ID, saveReason)}
          >
            Save
          </Button>
          {onRefresh && (
            <Button disabled={operation.busy} onClick={onRefresh}>
              Refresh
            </Button>
          )}
          <ActionReason id={SAVE_REASON_ID} reason={saveReason} />
        </Stack>}
        <TechnicalDetails title="How usage is counted">
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
      {canManage && <ConfirmDialog
        open={confirm}
        onClose={() => {
          if (!operation.busy) setConfirm(false);
        }}
        closeDisabled={operation.busy}
        title="Change workspace allowance?"
        content={enabled ? `Allow paid test runs up to $${amount} this period. Spending already recorded stays recorded. Saving makes no model call.` : `Paid test runs stay off. The allowance is recorded as $${amount}. Spending already recorded stays recorded.`}
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
              if (result) {
                // Our own write produced this revision: adopt it, so the refetch
                // that follows is not mistaken for someone else's change. A later
                // change by anyone else still differs and still blocks saving.
                if (Number.isInteger(result.revision)) setRevision(result.revision);
                setConfirm(false);
              }
            }}
          >
            Save allowance
          </Button>
        }
      />}
    </Section>
  );
}
PolicyForm.propTypes = {
  policy: PropTypes.object.isRequired,
  canManage: PropTypes.bool,
  onSave: PropTypes.func,
  onRefresh: PropTypes.func,
  savedAction: PropTypes.node,
};
// The allowance page's own states; never the release-review wording.
export default function SpendPolicyPanel({
  scopeKey,
  state = "unavailable",
  policy,
  onRetry,
  ...props
}) {
  if (!scopeKey || state === "unavailable" || state === "off")
    return <CapabilityNotice title="Workspace allowance isn't available here" description="Paid test runs stay off. Open Skills in the workspace you want to manage, or ask a workspace admin." />;
  if (state === "denied" || state === "forbidden")
    return <ErrorState title="You don't have access to this workspace's allowance." detail="Ask a workspace admin to check your access." />;
  if (state === "loading") return <ContentSkeleton variant="panel" label="Loading workspace allowance" rows={4} />;
  if (state === "error")
    return <ErrorState title="Couldn't load the workspace allowance." detail="Nothing was changed." onRetry={onRetry} />;
  if (state !== "ready" || !policy)
    return <EmptyState variant="no-results" title="No allowance saved yet" description="Paid test runs are off for this workspace." />;
  return <PolicyForm key={scopeKey} {...props} policy={policy} />;
}
SpendPolicyPanel.propTypes = {
  scopeKey: PropTypes.string,
  state: PropTypes.string,
  policy: PropTypes.object,
  onRetry: PropTypes.func,
};
