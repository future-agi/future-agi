import PropTypes from 'prop-types';
import { Alert, Button, Stack, Typography } from '@mui/material';
import { getSkillErrorCode, isDefiniteRejection, operationStatus } from 'src/api/skills/operation-errors';
import { paths } from 'src/routes/paths';
import TechnicalDetails from './TechnicalDetails';

const staleCodes = new Set([
  'stale', 'stale_base', 'draft_conflict', 'candidate_changed', 'label_conflict',
  'release_conflict', 'release_precondition_required', 'impact_changed',
  'target_configuration_changed', 'target_retired', 'annotation_conflict',
  'evidence_mismatch', 'evidence_expired', 'evidence_share_required',
  'review_stale', 'review_conflict', 'revision_changed', 'revision_conflict',
]);

export default function OperationFeedback({ error, onRefresh, onRetry, onReconcile, action = 'complete this action', success, children }) {
  if (!error && success) return <Alert severity="success" role="status"><Stack spacing={1}><Typography variant="s1">{success}</Typography>{children}</Stack></Alert>;
  if (!error) return null;
  const code = getSkillErrorCode(error);
  const status = operationStatus(error);
  let message;
  let label;
  let handler;
  let link;
  // A denial during reconciliation cannot disprove an earlier successful send.
  const uncertain = error.uncertain === true || ['uncertain_outcome', 'outcome_unknown'].includes(code);
  if (uncertain) {
    message = "We couldn't confirm whether this went through. Check before trying again.";
    label = 'Check status';
    handler = onReconcile;
  } else if (code === 'spend_policy_required') {
    // A missing workspace allowance is a prerequisite, not a lost permission: say what to set and where.
    message = 'No spending allowance is set for this workspace. An admin can set one in Workspace allowance.';
    link = { label: 'Workspace allowance', href: paths.dashboard.skills.allowance };
  } else if (staleCodes.has(code) || status === 409 || status === 412) {
    message = 'This changed since you opened it. Refresh and review again.';
    label = 'Refresh';
    handler = onRefresh;
  } else if (['permission', 'permission_denied', 'forbidden', 'access_denied', 'protected_label_forbidden'].includes(code) || status === 401 || status === 403) {
    message = `You no longer have permission to ${action}.`;
  } else if (code === 'retryable_transport' || error.requestSent === false) {
    // Only a known pre-send failure is safe to describe as unchanged. A lost
    // response / timeout / 5xx might have committed and must be reconciled.
    message = "Couldn't reach the server. Nothing was changed.";
    label = 'Retry';
    handler = onRetry;
  } else if (isDefiniteRejection(error)) {
    message = "This couldn't be completed. Review the details before trying again.";
  } else {
    message = "We couldn't confirm whether this went through. Check before trying again.";
    label = 'Check status';
    handler = onReconcile;
  }
  const run = () => {
    // When the error doesn't carry a key, the caller's reconciliation callback
    // must close over the original operation. Never create a fresh key here.
    if (label === 'Check status' && error.idempotencyKey) handler(error.idempotencyKey);
    else handler();
  };
  const technicalMessage = error.response?.data?.message || error.message;
  return <Alert severity="error" role="alert" action={handler ? <Button size="small" color="inherit" onClick={run}>{label}</Button> : link && <Button size="small" color="inherit" href={link.href}>{link.label}</Button>}>
    <Stack spacing={1}>
      <Typography variant="s1">{message}</Typography>
      {(code || technicalMessage) && <TechnicalDetails>
        {code && <Typography component="code" variant="s3" sx={{ fontFamily: 'monospace' }}>{code}</Typography>}
        {technicalMessage && <Typography variant="s3">{technicalMessage}</Typography>}
      </TechnicalDetails>}
    </Stack>
  </Alert>;
}

OperationFeedback.propTypes = {
  success: PropTypes.string,
  children: PropTypes.node,
  error: PropTypes.any,
  onRefresh: PropTypes.func,
  onRetry: PropTypes.func,
  onReconcile: PropTypes.func,
  action: PropTypes.string,
};
