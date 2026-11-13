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
const show = (value) =>
  value === null || value === undefined ? "None" : typeof value === 'object' ? 'See details' : typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value);
const setting = (path) => {
  if (path.endsWith('expected_security_revision')) return 'Security policy revision';
  if (path.endsWith('expected_release_revision')) return 'Release revision';
  if (path.startsWith('target')) return `Application configuration: ${path.split('.').slice(1).join(' ').replaceAll('_', ' ')}`;
  if (path.startsWith('evidence')) return `Evidence ${path.split('.').slice(1).join(' ').replaceAll('_', ' ')}`;
  return path.replace(/^transition\./, '').replaceAll('_', ' ');
};

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
  const renewOnly = useReviewedOperation();
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
  const done = renewedThis(operation.result) || renewedThis(renewOnly.result);
  const busy = operation.busy || renewOnly.busy;
  const uncertain = operation.uncertain || renewOnly.uncertain;
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
      subTitle="Inspect every change since the reviewed revision. The previous approval does not carry forward."
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
            <Typography variant="s1">
              Revision {value.revision} becomes revision {value.revision + 1}{" "}
              for the same candidate, transition and target.
            </Typography>
            <Table size="small" aria-label="Changed preconditions">
              <TableHead>
                <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                  <TableCell>Setting</TableCell>
                  <TableCell>Field</TableCell>
                  <TableCell>Before</TableCell>
                  <TableCell>After</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {value.changes.map((row) => (
                  <TableRow
                    key={row.item}
                    hover
                    sx={{ height: ROW_HEIGHT.default }}
                  >
                    <TableCell>{setting(row.item)}{JSON.stringify(row.before) === JSON.stringify(row.after) && <Typography variant="s3" display="block">Changed and restored</Typography>}</TableCell>
                    <TableCell sx={{ typography: "s3", fontFamily: "monospace", overflowWrap: "anywhere" }}>
                      {row.item}
                    </TableCell>
                    <TableCell sx={{ overflowWrap: "anywhere" }}>
                      {show(row.before)}
                    </TableCell>
                    <TableCell sx={{ overflowWrap: "anywhere" }}>
                      {show(row.after)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {value.removedEvidence.map((row) => (
              <Alert severity="warning" key={`${row.source}:${row.sourceId}`}>
                {REASONS[row.reason] || "Evidence unavailable"}: {row.sourceId}{" "}
                is removed from the renewed packet. It is not replaced. <StatusBadge status="expired" label={row.reason === 'evidence_expired' ? 'Removed (expired)' : 'Removed (unavailable)'} />
              </Alert>
            ))}
            <Typography variant="s3">
              The renewed packet keeps{" "}
              {value.packet.evidence.length
                ? value.packet.evidence.map((ref) => ref.sourceId).join(", ")
                : "no evidence"}{" "}
              for the same candidate and target.
            </Typography>
            <TechnicalDetails title="Changed values">
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
        <OperationFeedback operation={renewOnly} />
        <Stack direction="row">
            <Button
              variant="outlined"
              size="small"
              disabled={!acknowledged || busy || uncertain || done}
              onClick={async () => {
                const result = await renewOnly.run(
                  (input) => renew.mutateAsync(input),
                  renewInput,
                );
                if (result) close();
              }}
            >
              Create renewed revision
            </Button>
          </Stack>
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
