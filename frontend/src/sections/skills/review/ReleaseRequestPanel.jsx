import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { isDefiniteRejection, operationStatus } from "src/api/skills/operation-errors";
import Section from "src/components/section";
import StatusBadge from "src/components/status-badge";
import SharedOperationFeedback from '../components/OperationFeedback';
import TechnicalDetails from '../components/TechnicalDetails';
import { reviewPersonName, outcomeLabel } from '../utils/review-display';
import PeoplePicker from "../components/PeoplePicker";
import { ConfirmDialog } from "src/components/custom-dialog";
import ReleaseReceipt from './ReleaseReceipt';
import ReleaseReviewBody, {
  ReviewState,
  evidenceKey,
  evidenceProblems,
  useReviewClock,
} from "./ReleaseReviewBody";

// No auto-retry. An uncertain response keeps the original operation/body, even
// if current props advance. Scope changes unmount the form and fence completion.
export function useReviewedOperation() {
  const active = useRef(true);
  const pending = useRef(null);
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [failure, setFailure] = useState(null);
  const [uncertain, setUncertain] = useState(false);
  const [result, setResult] = useState(null);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  async function run(send, input) {
    if (!send || inFlight.current) return null;
    const canonical = JSON.stringify(input);
    if (pending.current && pending.current.canonical !== canonical) {
      setError(
        "The previous request has an unknown outcome. Retry its original operation before making another change.",
      );
      return null;
    }
    if (!pending.current)
      pending.current = {
        canonical,
        input: JSON.parse(canonical),
        idempotencyKey: crypto.randomUUID(),
        send,
      };
    const operation = pending.current;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setFailure(null);
    try {
      const response = await operation.send({
        ...operation.input,
        idempotencyKey: operation.idempotencyKey,
      });
      if (!active.current) return null;
      pending.current = null;
      setUncertain(false);
      setResult(response);
      return response;
    } catch (err) {
      if (!active.current) return null;
      const knownRejected = isDefiniteRejection(err);
      // A denial on a recovery attempt cannot prove that the original send
      // failed: current authorization is checked before receipt lookup.
      const confirmedRejected = knownRejected && !operation.uncertain;
      if (confirmedRejected) pending.current = null;
      else operation.uncertain = true;
      setUncertain(!confirmedRejected);
      setFailure({ ...err, message: err.message, uncertain: !confirmedRejected });
      setError(
        confirmedRejected
          ? "Request rejected. Review current permissions and packet state; your inputs are preserved. No preconditions were refreshed."
          : "We couldn't confirm whether this went through. Check before trying again.",
      );
      return null;
    } finally {
      inFlight.current = false;
      if (active.current) setBusy(false);
    }
  }
  return {
    busy,
    error,
    failure,
    stale: failure && !failure.uncertain && [409, 410, 412].includes(operationStatus(failure)),
    clearFailure: () => { setError(""); setFailure(null); },
    uncertain,
    result,
    run,
    retry: () =>
      pending.current ? run(pending.current.send, pending.current.input) : null,
  };
}

// Compatibility adapter for the operation hook; all messaging comes from the
// shared component. Reconciliation always replays the original frozen write.
export function OperationFeedback({ operation, onRefresh }) {
  return <SharedOperationFeedback error={operation.failure || (operation.error ? { message: operation.error, uncertain: operation.uncertain } : null)} onRefresh={onRefresh} onReconcile={operation.busy ? undefined : operation.retry} onRetry={operation.busy ? undefined : operation.retry} />;
}
OperationFeedback.propTypes = { operation: PropTypes.object.isRequired, onRefresh: PropTypes.func };

function PacketActions({
  request,
  preview,
  recipients,
  recipientsLoading,
  recipientsError,
  onRetryRecipients,
  permissions,
  onSubmit,
  onDecide,
  onExecute,
  onRefresh,
  onRenew,
  staleCode,
  receipt,
  currentEvidence,
  currentTarget,
  review,
  state,
  reviewedIdentity,
  onReviewedChange,
  initialRecipientId,
  renewalOpen,
}) {
  const operation = useReviewedOperation();
  const now = useReviewClock();
  // J4: one eligible reviewer is preselected; the author still sees the name
  // and can change it before sending.
  const [recipientId, setRecipientId] = useState(
    initialRecipientId || (recipients.length === 1 ? recipients[0].id : ""),
  );
  const [comment, setComment] = useState("");
  const [confirm, setConfirm] = useState(false);
  const packet = preview.packet;
  const fingerprint = preview.fingerprint;
  // Acknowledgment is for this exact server packet revision, never just its
  // content hash. A controller may keep it across the pending -> approved
  // remount so the same reviewer is not asked to re-attest an unchanged
  // revision; any renewal changes the revision and clears it.
  const [localIdentity, setLocalIdentity] = useState(null);
  const ackKey = `${request?.revision ?? 0}:${fingerprint}`;
  const ackValue =
    reviewedIdentity !== undefined ? reviewedIdentity : localIdentity;
  const setAckValue = onReviewedChange || setLocalIdentity;
  const reviewed = ackValue === ackKey;
  const reasons = packet.evidence.flatMap((ref) => {
    const live = currentEvidence?.find(
      (item) => evidenceKey(item) === evidenceKey(ref),
    );
    return [
      ...evidenceProblems(ref, packet, now),
      ...(currentEvidence &&
      (!live ||
        live.annotationRevision !== ref.annotationRevision ||
        live.configuration.digest !== ref.configuration.digest)
        ? ["Evidence or human annotations changed."]
        : []),
      ...(live ? evidenceProblems(live, packet, now) : []),
    ];
  });
  if (
    currentTarget !== undefined &&
    JSON.stringify(currentTarget) !== JSON.stringify(packet.target)
  )
    reasons.push("Target configuration changed.");
  if (review?.complete !== true)
    reasons.push(
      "The complete revision-bound diff and impact review have not loaded.",
    );
  const selectionIdentity = (refs) =>
    JSON.stringify(
      refs
        .map((ref) => [ref.source, ref.sourceId, ref.annotationRevision])
        .sort((a, b) => `${a[0]}:${a[1]}`.localeCompare(`${b[0]}:${b[1]}`)),
    );
  if (
    review?.selected &&
    selectionIdentity(review.selected) !== selectionIdentity(packet.evidence)
  )
    reasons.push(
      "Evidence selection changed. Prepare and inspect a new exact packet.",
    );
  const current =
    state === "ready" && preview.current === true && reasons.length === 0 && !staleCode && !operation.stale;
  const locked =
    operation.busy || operation.uncertain || Boolean(operation.result);
  const canDecide =
    current &&
    permissions.canDecide &&
    request &&
    request.status === "pending";
  // Declining grants nothing, so a reviewer can close a packet that went stale.
  const canRequestChanges =
    permissions.canDecide &&
    request &&
    request.status !== "released" &&
    request.status !== "superseded";
  const canExecute =
    current && permissions.canExecute && request?.status === "approved";
  const actionInput = request
    ? {
        requestId: request.id,
        revision: request.revision,
        expectedFingerprint: fingerprint,
      }
    : null;
  const finalReceipt =
    receipt ??
    (operation.result?.currentnessAtAction ? operation.result : null);
  const closed = Boolean(finalReceipt) || request?.status === "released";
  const refresh = onRefresh && (async () => {
    const result = await onRefresh();
    if (!result?.isError) { operation.clearFailure(); setAckValue(null); }
  });
  return (
    <Stack spacing={3}>
      {request && <Stack spacing={1}>
        <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap" aria-label="Release progress">
          <StatusBadge status={request.status === 'pending' ? 'in_review' : request.status === 'changes_requested' ? 'needs_changes' : request.status} />
          <Typography variant="s3" color="text.secondary">Revision {request.revision} · Author: {reviewPersonName(request.authorName)} · Reviewer: {request.recipients?.map((person) => reviewPersonName(person.name, person.deleted)).join(', ') || 'Not assigned'}</Typography>
        </Stack>
        {request.decision && <Typography variant="s3">Decision: {outcomeLabel(request.decision)}{request.decidedByName ? ` by ${request.decidedByName}` : ''}</Typography>}
      </Stack>}
      {!finalReceipt && <Typography variant="s1">{!current ? 'This review needs to be refreshed.' : request?.status === 'approved' ? 'Approved and ready for an authorized releaser.' : request ? 'Waiting for your approval.' : 'Review the changes before sending.'} {packet.transition.label === 'production' ? 'Production' : packet.transition.label} stays {packet.transition.expectedVersion == null ? 'unassigned' : `v${packet.transition.expectedVersion}`} until released.</Typography>}
      {!closed && (
        <Box sx={{ position: { md: 'sticky' }, top: 0, zIndex: 1, bgcolor: 'background.paper', border: 1, borderColor: 'divider', borderRadius: 1, p: 1.5 }}>
        <Section title={request ? "Review decision" : "Send for review"}>
          <Stack spacing={1.5}>
            {!request && (
              <PeoplePicker label="Assigned reviewer" people={recipients} value={recipientId} loading={recipientsLoading} error={recipientsError} onRetry={onRetryRecipients}
                onChange={setRecipientId} disabled={locked || !permissions.canSubmit}
                helperText="An independent reviewer must approve before production can move." />
            )}
            {request && (
              <TextField
                multiline
                minRows={2}
                size="small"
                label="Review comment"
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                inputProps={{ maxLength: 2000 }}
                disabled={locked || !canRequestChanges}
              />
            )}
            <FormControlLabel
              control={
                <Checkbox
                  checked={reviewed}
                  disabled={!current || locked}
                  onChange={(_, value) => setAckValue(value ? ackKey : null)}
                />
              }
              label="I reviewed this exact candidate, target, evidence and release consequence."
            />
            <OperationFeedback operation={operation} onRefresh={refresh} />
            {!current && !operation.error && <SharedOperationFeedback error={{ statusCode: 409, code: staleCode || 'review_stale' }} onRefresh={refresh} />}
            {!current && <Typography variant="s3" color="text.secondary">Approval and release are paused until you refresh and inspect the changes.</Typography>}
            {reasons.length > 0 && <TechnicalDetails title="Why this review is paused">{Array.from(new Set(reasons)).map((reason) => <Typography key={reason} variant="s3">{reason}</Typography>)}</TechnicalDetails>}
            {onRenew && request && permissions.canDecide && !current && <Button variant="outlined" disabled={locked} onClick={() => { setConfirm(false); onRenew(); }}>Renew review</Button>}
            {operation.result && !finalReceipt && (
              <Alert severity="info">
                Request recorded. Refresh the server-backed review to see the
                current decision and unread state. Nothing was released by this
                acknowledgment.
              </Alert>
            )}
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
              {!request && (
                <Button
                  variant="contained" color="primary"
                  disabled={
                    !onSubmit ||
                    !permissions.canSubmit || recipientsLoading || Boolean(recipientsError) ||
                    !current ||
                    !reviewed ||
                    locked ||
                    !recipients.some((item) => item.id === recipientId)
                  }
                  onClick={() =>
                    operation.run(onSubmit, {
                      packet,
                      expectedFingerprint: fingerprint,
                      recipientIds: [recipientId],
                      revision: 0,
                    })
                  }
                >
                  Send for review
                </Button>
              )}
              {request && (
                <>
                  {request.status === "pending" && <Button
                    variant="contained" color="primary"
                    disabled={!onDecide || !canDecide || !reviewed || locked}
                    onClick={() =>
                      operation.run(onDecide, {
                        ...actionInput,
                        decision: "approve",
                        comment,
                      })
                    }
                  >
                    Approve release
                  </Button>}
                  <Button
                    variant="outlined"
                    disabled={
                      !onDecide ||
                      !canRequestChanges ||
                      !comment.trim() ||
                      locked
                    }
                    onClick={() =>
                      operation.run(onDecide, {
                        ...actionInput,
                        decision: "request_changes",
                        comment,
                      })
                    }
                  >
                    Request changes
                  </Button>
                  {request.status === "approved" && <Button
                    variant="contained" color="primary"
                    disabled={!onExecute || !canExecute || !reviewed || locked}
                    onClick={() => setConfirm(true)}
                  >
                    Release to production
                  </Button>}
                </>
              )}
            </Stack>
            {!reviewed && current && <Typography variant="s3" color="text.secondary">Confirm that you reviewed the candidate and release consequence to continue.</Typography>}
            {request && request.status !== 'pending' && !canExecute && <Typography variant="s3" color="text.secondary">Only a pending request can be approved. Releasing requires a current approval and release access.</Typography>}
            {request && !permissions.canExecute && (
              <Typography variant="s3">
                A currently authorized releaser must execute the approved
                transition. Passing tests or approval do not grant that role.
              </Typography>
            )}
          </Stack>
        </Section>
        </Box>
      )}
      {!request && (!recipientId || !permissions.canSubmit || !current) && <Typography variant="s3" color="text.secondary">{!permissions.canSubmit ? 'You need permission to request a review.' : !recipientId ? 'Choose an assigned reviewer to continue.' : 'Refresh and inspect the complete review before sending.'}</Typography>}
      {!finalReceipt && <>
        <Typography variant="s3" color="text.secondary">{review?.changes ? `${review.changes.length} ${review.changes.length === 1 ? 'file' : 'files'} changed` : 'File changes not loaded'}</Typography>
        <ReleaseReviewBody {...review} packet={packet} onRetryDependencies={refresh} />
        <TechnicalDetails title="How to read this review"><Typography variant="s3">Tests are advisory, not approval. Text and application evidence are separate and do not require a second paid run. Declared permissions are not a sandbox. Opening this review executes nothing. Decisions bind to this revision, target and annotations; approval does not grant release authority. Existing threads and locked versions may keep using earlier content.</Typography></TechnicalDetails>
      </>}
      {finalReceipt && <ReleaseReceipt receipt={finalReceipt} request={request} />}
      <ConfirmDialog
        open={confirm && !closed && !renewalOpen}
        onClose={() => {
          if (!operation.busy) setConfirm(false);
        }}
        closeDisabled={operation.busy}
        title={`Move ${packet.transition.label} to v${packet.transition.version}?`}
        content={`${request?.name || "This resource"}: ${packet.transition.label} ${packet.transition.expectedVersion == null ? "unassigned" : `v${packet.transition.expectedVersion}`} → v${packet.transition.version}. ${packet.target ? `Application target: ${review?.targetConfiguration?.name || packet.target.bindingId || packet.target.environmentId}.` : "No application target."} Execute only approved revision ${request?.revision ?? ""}. Existing threads and checked-in locks may still use another version. Current authority and all packet preconditions are rechecked by the server.`}
        action={
          <Button
            variant="contained" color="primary"
            disabled={!canExecute || !reviewed || locked}
            onClick={async () => {
              await operation.run(onExecute, actionInput);
              // Close on every outcome: a refusal or unknown result is shown
              // beside the decision, where renewal or retry is offered.
              setConfirm(false);
            }}
          >
            Move {packet.transition.label}
          </Button>
        }
      />
    </Stack>
  );
}
PacketActions.propTypes = {
  request: PropTypes.object,
  preview: PropTypes.object.isRequired,
  recipients: PropTypes.array.isRequired,
  recipientsLoading: PropTypes.bool,
  recipientsError: PropTypes.any,
  onRetryRecipients: PropTypes.func,
  permissions: PropTypes.object.isRequired,
  onSubmit: PropTypes.func,
  onDecide: PropTypes.func,
  onExecute: PropTypes.func,
  onRefresh: PropTypes.func,
  onRenew: PropTypes.func,
  staleCode: PropTypes.string,
  receipt: PropTypes.object,
  currentEvidence: PropTypes.array,
  currentTarget: PropTypes.object,
  review: PropTypes.object,
  state: PropTypes.string.isRequired,
  reviewedIdentity: PropTypes.string,
  onReviewedChange: PropTypes.func,
  initialRecipientId: PropTypes.string,
  renewalOpen: PropTypes.bool,
};

export default function ReleaseRequestPanel({
  scopeKey,
  state = "unavailable",
  preview,
  request,
  recipients = [],
  permissions = {},
  onRetry,
  ...props
}) {
  if (scopeKey && state === 'ready' && props.receipt) return <ReleaseReceipt receipt={props.receipt} request={request} />;
  if (
    !scopeKey ||
    ["loading", "error", "forbidden", "unavailable", "empty"].includes(state) ||
    !preview
  )
    return (
      <ReviewState state={!scopeKey ? "forbidden" : state} onRetry={onRetry} />
    );
  return (
    <PacketActions
      // Revision is part of the identity: a renewed-and-approved packet keeps
      // the same status, but a refusal shown for the previous revision must
      // not carry over to it.
      key={`${scopeKey}:${request?.id ?? preview.packet.candidateVersionId}:${request?.revision ?? 0}:${request?.status ?? "new"}`}
      {...props}
      {...{ state, preview, request, recipients, permissions }}
    />
  );
}
ReleaseRequestPanel.propTypes = {
  receipt: PropTypes.object,
  scopeKey: PropTypes.string,
  state: PropTypes.string,
  preview: PropTypes.object,
  request: PropTypes.object,
  recipients: PropTypes.array,
  permissions: PropTypes.object,
  onRetry: PropTypes.func,
};
