import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  DialogTitle,
  DialogContent,
  DialogActions,
  Checkbox,
  FormControlLabel,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { ClosableDialog } from "src/components/custom-dialog";
import { evidenceTitle } from '../utils/review-display';
import EvidenceSummary from '../components/EvidenceSummary';
import IdentityChip from '../components/IdentityChip';
import TechnicalDetails from "../components/TechnicalDetails";
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
const shown = (path, value, side) => path.endsWith('_revision') ? (side === 'before' ? 'At approval' : 'Edited since approval') : show(value, side);
const restoredPolicy = (changes) => changes.some((row) => row.item.endsWith('expected_security_revision') && row.before !== row.after)
  && changes.some((row) => /security|policy/.test(row.item) && !row.item.endsWith('_revision') && JSON.stringify(row.before) === JSON.stringify(row.after));
const restoredPolicyReason = "The security policy changed and was restored after approval. The earlier approval can't be reused.";

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
  const [inspected, setInspected] = useState(null);
  const value = preview.data;
  const current =
    preview.isError && preview.error?.code === "review_current";
  const acknowledged = Boolean(value) && inspected === value.fingerprint;
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
    setInspected(null);
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
      <DialogTitle component="div"><Typography variant="m3" component="h2">Renew release review</Typography></DialogTitle>
      <DialogContent>
      <Typography variant="s1" component="p" color="text.secondary" sx={{ mb: 2 }}>{value?.changes.some((row) => row.item.endsWith('expected_security_revision')) ? "The security policy changed after this request was made, so the approval no longer applies. See what changed and approve again." : "This request changed after it was reviewed, so the approval no longer applies. See what changed and approve again."}</Typography>
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
            <Stack spacing={1}>
              <Typography variant="m3">Evidence</Typography>
              {value.removedEvidence.map((row) => <Stack spacing={0.5} key={`${row.source}:${row.sourceId}`}>
                <Stack direction="row" gap={1} alignItems="center"><Typography variant="s1">{evidenceTitle(row, value.packet.transition.version, value.packet.transition.expectedVersion)}</Typography><StatusBadge status="expired" label={row.reason === 'evidence_expired' ? 'Removed (expired)' : 'Removed (unavailable)'} /></Stack>
                <Typography variant="s3" color="text.secondary">{REASONS[row.reason] || 'Evidence unavailable'}. It is not replaced.</Typography>
                <Typography variant="s3" component="code" sx={{ fontFamily: 'monospace', overflowWrap: 'anywhere' }}>{row.sourceId} is removed from the renewed request.</Typography>
              </Stack>)}
              <Typography variant="s3">{value.packet.evidence.length ? `The renewed request keeps ${value.packet.evidence.length} ${value.packet.evidence.length === 1 ? 'result' : 'results'} for the same candidate and target.` : 'The renewed request keeps no evidence.'}</Typography>
              <EvidenceSummary items={value.packet.evidence.map((ref) => ({ ...ref, id: ref.sourceId, title: evidenceTitle(ref, value.packet.transition.version, value.packet.transition.expectedVersion), expiresAt: ref.payloadExpiresAt }))} renderAction={() => <Typography variant="s3" color="text.secondary">Kept the same result</Typography>} emptyText="" />
              {(value.packet.evidence.length > 0 || value.packet.target) && <TechnicalDetails title="Technical details">
                <Stack spacing={1}>
                  {value.packet.target && <Typography variant="s3">App configuration r{value.packet.target.revision}</Typography>}
                  {value.packet.evidence.map((ref) => <IdentityChip key={`${ref.source}:${ref.sourceId}`} kind="id" label={`Kept ${evidenceTitle(ref, value.packet.transition.version, value.packet.transition.expectedVersion)}`} value={ref.sourceId} />)}
                </Stack>
              </TechnicalDetails>}
            </Stack>
            <TechnicalDetails title="Changed values">
              {value.changes.map((row) => <Typography key={row.item} variant="s3" component="code" display="block" sx={{ fontFamily: 'monospace', overflowWrap: 'anywhere' }}>{row.item}</Typography>)}
              <Typography component="pre" variant="s3" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(value.changes, null, 2)}</Typography>
            </TechnicalDetails>
            <FormControlLabel
              control={
                <Checkbox
                  checked={acknowledged}
                  disabled={busy}
                  onChange={(_, checked) =>
                    setInspected(checked ? value.fingerprint : null)
                  }
                />
              }
              label="I inspected every change. Approval is required again."
            />
          </>
        )}
        <OperationFeedback operation={operation} />
        <Typography variant="s3" color="text.secondary">Approving again records a new approval. Renew and decide later keeps the request open.</Typography>
      </Stack>
      </DialogContent>
      <DialogActions sx={{ p: 2, gap: 1, flexWrap: 'wrap' }}>
        <Button disabled={busy} onClick={close} sx={{ mr: 'auto' }}>Cancel</Button>
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
