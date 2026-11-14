import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
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
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { evidenceTitle } from '../utils/review-display';
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
// One-step path: "Approve renewed revision" creates the next revision bound to
// the inspected fingerprint and records this reviewer's approval of exactly
// that revision. Both writes are server-checked; if anything moved after the
// inspection, the server refuses and the revision stays pending.
const show = (value, side) =>
  typeof value === 'string' && (/^(sha256:)?[a-f0-9]{64}$/i.test(value) || /^[a-f0-9]{8}-[a-f0-9-]{27}$/i.test(value)) ? (side === 'before' ? 'Earlier configuration' : 'Current configuration') : value === null || value === undefined ? 'None' : typeof value === 'object' ? 'See details' : typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value);
const setting = (path) => {
  if (path.endsWith('expected_security_revision')) return 'Security policy revision';
  if (path.endsWith('expected_release_revision')) return 'Release revision';
  if (path === 'transition.expected_impact_fingerprint') return 'Affected applications';
  if (path === 'target') return 'Application configuration';
  if (path.startsWith('target')) return 'Application configuration';
  if (path.startsWith('evidence')) return `Evidence ${path.split('.').slice(1).join(' ').replaceAll('_', ' ')}`;
  return path.replace(/^transition\./, '').replaceAll('_', ' ');
};
// Why a precondition moved, in the reviewer's words. The raw field path stays in "Changed values".
const cause = (path) => {
  if (path.endsWith('expected_security_revision')) return 'The security policy was edited.';
  if (path.endsWith('expected_release_revision')) return 'A label moved or label protection was edited.';
  if (path === 'transition.expected_impact_fingerprint') return 'The set of affected applications changed.';
  if (path.startsWith('target')) return 'The application configuration changed.';
  if (path.startsWith('evidence')) return 'Attached evidence changed.';
  return 'This setting changed since the review.';
};
const shown = (path, value, side) => show(value, side);

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
  return (
    <ModalWrapper
      open={open}
      onClose={close}
      title="Renew release review"
      subTitle={value?.changes.some((row) => row.item.endsWith('expected_security_revision')) ? "The security policy changed after this request was made, so the approval no longer applies. Review the change and approve again." : "This request changed after it was reviewed, so the approval no longer applies. Review the changes and approve again."}
      actionBtnTitle="Approve renewed revision"
      isValid={acknowledged && !busy && !uncertain && !done}
      isLoading={operation.busy}
      onSubmit={async () => {
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
      }}
      onCancelBtn={close}
      modalWidth="720px"
    >
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
                    <TableCell><Typography variant="s1" component="div">{setting(row.item)}</Typography><Typography variant="s3" display="block" color="text.secondary">{JSON.stringify(row.before) === JSON.stringify(row.after) ? 'Changed and restored' : cause(row.item)}</Typography></TableCell>
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
              {value.packet.evidence.map((ref) => <Stack key={`${ref.source}:${ref.sourceId}`} direction="row" gap={1} alignItems="center" flexWrap="wrap"><Typography variant="s3">{evidenceTitle(ref, value.packet.transition.version, value.packet.transition.expectedVersion)}</Typography><StatusBadge status={ref.outcome || (ref.status !== 'completed' ? ref.status : 'not_tested')} label={ref.outcome ? undefined : ref.status === 'completed' ? 'Result not recorded' : undefined} /><IdentityChip inline kind="id" label="Kept" value={ref.sourceId} /></Stack>)}
            </Stack>
            <TechnicalDetails title="Changed values">
              {value.changes.map((row) => <Typography key={row.item} variant="s3" component="code" display="block" sx={{ fontFamily: 'monospace', overflowWrap: 'anywhere' }}>{row.item}</Typography>)}
              <Typography component="pre" variant="s3" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(value.changes, null, 2)}</Typography>
            </TechnicalDetails>
            <Typography variant="s3">Approving creates revision {value.revision + 1} of this request.</Typography>
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
        <Button variant="outlined" sx={{ alignSelf: 'flex-start' }} disabled={!acknowledged || busy || uncertain || done} onClick={async () => {
          const result = await operation.run(renew.mutateAsync, renewInput);
          if (result) close();
        }}>Renew without approving</Button>
        <Typography variant="s3" color="text.secondary">Creates a pending revision without approval, if you need more time to decide.</Typography>
      </Stack>
    </ModalWrapper>
  );
}
ReleaseRenewalDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  requestId: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
  onApproved: PropTypes.func,
};
