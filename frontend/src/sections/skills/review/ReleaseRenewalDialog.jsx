import PropTypes from "prop-types";
import {
  Alert,
  Button,
  DialogTitle,
  DialogContent,
  DialogActions,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { ClosableDialog } from "src/components/custom-dialog";
import { evidenceTitle, relativeReviewTime } from '../utils/review-display';
import Acknowledgment, { useAcknowledgment } from '../components/Acknowledgment';
import TechnicalDetails from "../components/TechnicalDetails";
import { RENEW_REQUEST, labelDisplayName } from '../utils/terms';
import { counted } from './reviewPresentation';
import StatusBadge from "src/components/status-badge";
import ContentSkeleton from "src/components/content-skeleton";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import {
  useDecideReleaseRequest,
  useRenewalPreview,
  useRenewReleaseRequest,
} from "src/api/skills/review-production";
import { OperationFeedback, useReviewedOperation } from "./ReleaseRequestPanel";

// Intent: an assigned reviewer sees exactly what changed after a race and
// creates the next immutable packet revision. Nothing is refreshed silently;
// the renewed revision starts pending and needs a new decision.
// One-step path: "Approve again" creates the next revision bound to
// the inspected fingerprint and records this reviewer's approval of exactly
// that revision. Both writes are server-checked; if anything moved after the
// inspection, the server refuses and the revision stays pending.
const show = (value, side) =>
  typeof value === 'string' && (/^(sha256:)?[a-f0-9]{64}$/i.test(value) || /^[a-f0-9]{8}-[a-f0-9-]{27}$/i.test(value)) ? (side === 'before' ? 'Earlier configuration' : 'Current configuration') : value === null || value === undefined ? 'None' : typeof value === 'object' ? 'See details' : typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value);
const setting = (path) => {
  if (path.endsWith('expected_security_revision')) return 'Security policy revision';
  if (path.endsWith('expected_release_revision')) return 'Release revision';
  if (path === 'transition.expected_impact_fingerprint') return 'Affected applications';
  if (path === 'target') return 'App configuration';
  if (path.startsWith('target')) return 'App configuration';
  if (path.startsWith('evidence')) return `Evidence ${path.split('.').slice(1).join(' ').replaceAll('_', ' ')}`;
  return path.replace(/^transition\./, '').replaceAll('_', ' ');
};
// Why a precondition moved, in the reviewer's words. The raw field path stays in "Changed values".
const cause = (path) => {
  if (path.endsWith('expected_security_revision')) return 'Any policy edit after approval requires a new approval.';
  if (path.endsWith('expected_release_revision')) return 'A label moved or label protection was edited.';
  if (path === 'transition.expected_impact_fingerprint') return 'The set of affected applications changed.';
  if (path.startsWith('target')) return 'The application configuration changed.';
  if (path.startsWith('evidence')) return 'Attached evidence changed.';
  return 'This setting changed since the review.';
};
const shown = (path, value, side) => path.endsWith('_revision') ? (side === 'before' ? 'At approval' : 'Changed since approval') : show(value, side);
const restoredPolicy = (changes) => changes.some((row) => row.item.endsWith('expected_security_revision') && row.before !== row.after)
  && changes.some((row) => /security|policy/.test(row.item) && !row.item.endsWith('_revision') && JSON.stringify(row.before) === JSON.stringify(row.after));
const restoredPolicyReason = "The security policy changed and was restored after approval. The earlier approval can't be reused.";

// UXF-10: the rule values behind a changed rule counter, from the server's
// audit rows. A row whose values weren't recorded says so; nothing is guessed.
export const NOT_RECORDED = "Values at approval weren't recorded";
const ruleName = (row) => {
  if (row.field === 'protected_labels') return 'Protected labels';
  if (row.field === 'quarantine') {
    const version = (row.after || row.before)?.version;
    return version != null ? `Quarantine of v${version}` : 'Quarantine';
  }
  if (row.field === 'release_rules') return 'Label rules';
  if (row.field === 'security_rules') return 'Security rules';
  return String(row.field || 'Rule').replaceAll('_', ' ');
};
const ruleValue = (field, value) => {
  if (field === 'protected_labels' && Array.isArray(value)) return value.length ? value.map(labelDisplayName).join(', ') : 'No protected labels';
  if (field === 'quarantine' && value && typeof value === 'object') return value.quarantined ? 'Quarantined' : 'Not quarantined';
  return show(value);
};
// The rule rows to show. An older server sends no list; a changed security
// counter then still gets a row that says its values weren't recorded.
export function ruleChanges(value) {
  if (!value) return [];
  if (Array.isArray(value.policyChanges)) return value.policyChanges;
  return value.changes.some((row) => row.item.endsWith('expected_security_revision'))
    ? [{ field: 'security_rules', before: null, after: null, changedBy: null, changedAt: null, recorded: false }]
    : [];
}
export const RULES_CHANGED_LEAD = "The release review rules changed after this request was approved, so the approval no longer applies. Review what changed, then approve again.";
// The lead names what happened to this request: an approved one lost its
// approval; a waiting one only needs the changes checked before approving.
export const renewalLead = (status, rulesChanged) => {
  if (status === 'approved') return rulesChanged ? RULES_CHANGED_LEAD : 'This request changed after it was approved, so the approval no longer applies. Review what changed, then approve again.';
  return rulesChanged
    ? 'The release review rules changed after this request was sent. Review what changed, then approve.'
    : 'This request changed after it was sent. Review what changed, then approve.';
};
/** "2 test results kept; they'll be rechecked when you renew." */
export const keptEvidenceLine = (count) => (count
  ? `${counted(count, 'test result')} kept; ${count === 1 ? "it'll" : "they'll"} be rechecked when you renew.`
  : 'No test results kept.');

function RuleChange({ row }) {
  const who = [row.changedBy && `Changed by ${row.changedBy}`, row.changedAt && relativeReviewTime(row.changedAt)].filter(Boolean).join(' · ');
  return <Stack spacing={0.25} data-rule-field={row.field} sx={{ minWidth: 0 }}>
    <Typography variant="s1">{ruleName(row)}</Typography>
    {row.recorded
      ? <Typography variant="s3" sx={{ overflowWrap: 'anywhere' }}>{ruleValue(row.field, row.before)} → {ruleValue(row.field, row.after)}</Typography>
      : <Typography variant="s3" color="text.secondary">{NOT_RECORDED}</Typography>}
    {who && <Typography variant="s3" color="text.secondary">{who}</Typography>}
  </Stack>;
}
RuleChange.propTypes = { row: PropTypes.object.isRequired };

const REASONS = {
  evidence_expired: "Detailed evidence expired",
  resource_not_found: "Evidence is no longer available",
};

export default function ReleaseRenewalDialog({
  open,
  requestId,
  onClose,
  onApproved,
}) {
  const preview = useRenewalPreview(requestId, open);
  const renew = useRenewReleaseRequest();
  const decide = useDecideReleaseRequest();
  const operation = useReviewedOperation();
  const value = preview.data;
  // One tick bound to the inspected renewal fingerprint; it is asked again
  // whenever the renewal the server shows changes.
  const inspection = useAcknowledgment(value?.fingerprint ?? null);
  const current =
    preview.isError && preview.error?.code === "review_current";
  const acknowledged = Boolean(value) && inspection.acknowledgedIdentity === value.fingerprint;
  const rules = ruleChanges(value);
  // The dialog stays mounted across renewals (one stable parent), so an
  // earlier session's result must not block renewing a later revision. A
  // result blocks only the revision it renewed, preventing a double submit.
  const renewedThis = (result) =>
    Boolean(result) && Boolean(value) && result.revision === value.revision + 1;
  const done = renewedThis(operation.result);
  const busy = operation.busy;
  const uncertain = operation.uncertain;
  const close = () => {
    if (busy) return;
    inspection.reset();
    onClose();
  };
  const renewInput = value && {
    requestId,
    revision: value.revision,
    expectedFingerprint: value.fingerprint,
  };
  const approve = async () => {
        // The approval key is fixed at click time so a retry of an unknown
        // outcome replays both writes instead of creating new ones.
        const result = await operation.run(
          async ({ decisionKey, idempotencyKey, ...input }) => {
            const renewed = await renew.mutateAsync({
              ...input,
              idempotencyKey,
            });
            if (renewed.revision !== input.revision + 1)
              throw new Error("Renewal created an unexpected revision.");
            const decided = await decide.mutateAsync({
              requestId,
              revision: renewed.revision,
              expectedFingerprint: input.expectedFingerprint,
              decision: "approve",
              comment: "",
              idempotencyKey: decisionKey,
            });
            return { ...decided, fingerprint: input.expectedFingerprint };
          },
          { ...renewInput, decisionKey: crypto.randomUUID() },
        );
        if (result) {
          onApproved?.({
            revision: result.revision,
            fingerprint: result.fingerprint,
          });
          close();
        }

  };
  return (
    <ClosableDialog open={open} onClose={busy ? undefined : close} disableEscapeKeyDown={busy} fullWidth maxWidth="md" PaperProps={{ sx: { maxWidth: 720 } }}>
      <DialogTitle component="div"><Typography variant="m3" component="h2">{RENEW_REQUEST}</Typography></DialogTitle>
      <DialogContent>
      {value && <Typography variant="s1" component="p" color="text.secondary" sx={{ mb: 2 }}>{renewalLead(value.status, rules.length > 0)}</Typography>}
      <Stack spacing={1.5} sx={{ minWidth: 0 }}>
        {preview.isLoading && (
          <ContentSkeleton label="Loading changes" rows={3} />
        )}
        {current && (
          <Alert severity="info">
            This revision is still current. Nothing needs renewal.
          </Alert>
        )}
        {preview.isError && !current && (
          <Alert severity="error">
            Could not load the changes. No revision was created. Close and
            check current access.
          </Alert>
        )}
        {value && (
          <>
            <Table size="small" aria-label="Changed preconditions" sx={{ '& thead': { display: { xs: 'none', sm: 'table-header-group' } }, '& tbody': { display: { xs: 'block', sm: 'table-row-group' } }, '& tbody tr': { display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: '1fr 1fr' }, '& td': { minWidth: 0 }, '& td:first-of-type': { gridColumn: '1 / -1' } }}>
              <TableHead>
                <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                  <TableCell>What changed</TableCell>
                  <TableCell>Before</TableCell>
                  <TableCell>After</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {value.changes.map((row) => (
                  <TableRow
                    key={row.item}
                    data-field={row.item}
                    hover
                    sx={{ minHeight: ROW_HEIGHT.default }}
                  >
                    <TableCell><Typography variant="s1" component="div">{setting(row.item)}</Typography><Typography variant="s3" display="block" color="text.secondary">{row.item.endsWith('expected_security_revision') && restoredPolicy(value.changes) ? restoredPolicyReason : JSON.stringify(row.before) === JSON.stringify(row.after) ? 'Changed and restored' : cause(row.item)}</Typography></TableCell>
                    <TableCell sx={{ overflowWrap: "break-word" }}>
                      {shown(row.item, row.before, 'before')}
                    </TableCell>
                    <TableCell sx={{ overflowWrap: "break-word" }}>
                      {shown(row.item, row.after, 'after')}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {/* The evidence in one line; only results that are removed get their own row. */}
            <Stack spacing={1}>
              <Typography variant="s1" data-testid="renewal-evidence">{keptEvidenceLine(value.packet.evidence.length)}</Typography>
              {value.removedEvidence.map((row) => <Stack spacing={0.5} key={`${row.source}:${row.sourceId}`}>
                <Stack direction="row" gap={1} alignItems="center"><Typography variant="s1">{evidenceTitle(row, value.packet.transition.version, value.packet.transition.expectedVersion)}</Typography><StatusBadge status="expired" label={row.reason === 'evidence_expired' ? 'Removed (expired)' : 'Removed (unavailable)'} /></Stack>
                <Typography variant="s3" color="text.secondary">{REASONS[row.reason] || 'Evidence unavailable'}. It is not replaced.</Typography>
              </Stack>)}
            </Stack>
            {/* Each changed rule with its values at approval and now, open while there is something to read. */}
            <TechnicalDetails title="Changed values" defaultOpen={rules.length > 0}>
              <Stack spacing={1.5}>
                {rules.map((row, index) => <RuleChange key={`${row.field}:${index}`} row={row} />)}
                <TechnicalDetails title="Technical details">
                  {value.packet.target && <Typography variant="s3">App configuration r{value.packet.target.revision}</Typography>}
                  {value.removedEvidence.map((row) => <Typography key={row.sourceId} variant="s3" component="code" display="block" sx={{ fontFamily: 'monospace', overflowWrap: 'anywhere' }}>{row.sourceId} is removed from the renewed request.</Typography>)}
                  {value.changes.map((row) => <Typography key={row.item} variant="s3" component="code" display="block" sx={{ fontFamily: 'monospace', overflowWrap: 'anywhere' }}>{row.item}</Typography>)}
                  <Typography component="pre" variant="s3" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(value.changes, null, 2)}</Typography>
                </TechnicalDetails>
              </Stack>
            </TechnicalDetails>
            <Acknowledgment acknowledgment={inspection} disabled={busy}
              label="I've checked every change since the last approval, and it needs approving again." />
          </>
        )}
        <OperationFeedback operation={operation} />
        <Typography variant="s3" color="text.secondary">Approving again records a new approval. Renew and decide later keeps the request open.</Typography>
      </Stack>
      </DialogContent>
      {/* Right-aligned: Cancel, then the secondary, then the primary at the far right. */}
      <DialogActions sx={{ p: 2, gap: 1, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        <Button disabled={busy} onClick={close}>Cancel</Button>
        <Button variant="outlined" disabled={!acknowledged || busy || uncertain || done} onClick={async () => {
          const result = await operation.run(renew.mutateAsync, renewInput);
          if (result) close();
        }}>Renew and decide later</Button>
        <Button variant="contained" color="primary" disabled={!acknowledged || busy || uncertain || done} onClick={approve}>Approve again</Button>
      </DialogActions>
    </ClosableDialog>
  );
}
ReleaseRenewalDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  requestId: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
  onApproved: PropTypes.func,
};
