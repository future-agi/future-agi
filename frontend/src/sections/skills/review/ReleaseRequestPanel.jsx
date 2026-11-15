import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { isDefiniteRejection, operationStatus } from "src/api/skills/operation-errors";
import SharedOperationFeedback from '../components/OperationFeedback';
import { useStickyFooterHeight } from '../components/stickyFooter';
import TechnicalDetails from '../components/TechnicalDetails';
import { reviewPersonName, relativeReviewTime } from '../utils/review-display';
import PeoplePicker from "../components/PeoplePicker";
import ActionReason, { reasonProps } from '../components/ActionReason';
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
  renewalReason,
  onEdit,
  onCancel,
  availableEvidence,
}) {
  const operation = useReviewedOperation();
  const now = useReviewClock();
  // J4: one eligible reviewer is preselected; the author still sees the name
  // and can change it before sending.
  const [recipientId, setRecipientId] = useState(
    initialRecipientId || (recipients.length === 1 ? recipients[0].id : ""),
  );
  const [comment, setComment] = useState("");
  const [commentFocused, setCommentFocused] = useState(false);
  const commentInput = useRef(null);
  const decisionPanel = useRef(null);
  useStickyFooterHeight(decisionPanel);
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
  // One quiet reason beside the disabled primary; the paused notice above carries the full explanation.
  const decisionReason = !current ? (request ? 'Renew the review first' : '')
    : !reviewed && (!request || request.status === 'pending') ? (request ? 'Tick the acknowledgment to approve' : 'Tick the acknowledgment to send')
      : !reviewed && request?.status === 'approved' ? 'Tick the acknowledgment to release' : '';
  return (
    <Stack spacing={3}>
      {request && <Stack spacing={1}>
        <Stack direction={{ xs: 'column', sm: 'row' }} gap={1.5} alignItems={{ sm: 'center' }} aria-label="Release progress">
          <Typography variant="s3">Submitted by {reviewPersonName(request.authorName)}{request.createdAt && ` · ${relativeReviewTime(request.createdAt)}`}</Typography>
          <Typography variant="s3" color="text.secondary" aria-hidden="true" sx={{ display: { xs: 'none', sm: 'block' } }}>→</Typography>
          <Typography variant="s3" color={request.status === 'approved' ? 'text.primary' : 'text.secondary'}>{request.status === 'approved' || closed ? `Approved by ${reviewPersonName(request.decidedByName)}${request.decidedAt ? ` · ${relativeReviewTime(request.decidedAt)}` : ''}` : `Waiting for ${request.recipients?.length ? request.recipients.map((person) => reviewPersonName(person.name)).join(', ') : 'the assigned reviewer'}'s approval`}</Typography>
          <Typography variant="s3" color="text.secondary" aria-hidden="true" sx={{ display: { xs: 'none', sm: 'block' } }}>→</Typography>
          <Typography variant="s3" color="text.secondary">{closed ? 'Released' : 'Not released'}</Typography>
        </Stack>
      </Stack>}
      {!request && onEdit && <Stack direction="row" spacing={1} alignItems="center"><Typography variant="s3">Reviewer: {reviewPersonName(recipients.find((person) => person.id === recipientId)?.name)}</Typography><Button size="small" disabled={locked} onClick={onEdit}>Change</Button></Stack>}
      {/* The paused notice lives in the page, under the status line; the sticky bar below stays one row. */}
      {!closed && !current && <Alert severity="warning">
        <AlertTitle>{renewalReason || (staleCode === 'evidence_expired' ? 'Attached evidence expired after this request was made.' : 'This request changed after it was reviewed.')}</AlertTitle>
        <Typography variant="s1" component="p">{request ? 'Approval no longer applies. See what changed and renew the request.' : 'Refresh the review before sending the request.'}</Typography>
        {onRenew && request && permissions.canDecide && <Button variant="contained" color="primary" disabled={locked} sx={{ mt: 1 }} onClick={() => { setConfirm(false); onRenew(); }}>Renew review</Button>}
        {(reasons.length > 0 || refresh) && <TechnicalDetails title="Why this review is paused">
          <Stack spacing={0.5} alignItems="flex-start">
            {Array.from(new Set(reasons)).map((reason) => <Typography key={reason} variant="s3">{reason}</Typography>)}
            {refresh && <Button size="small" disabled={locked} onClick={refresh}>Refresh</Button>}
          </Stack>
        </TechnicalDetails>}
      </Alert>}
      {!finalReceipt && <Box sx={{ pb: { xs: 2, sm: 'calc(var(--skills-sticky-footer-height, 0px) + 16px)' } }}>
        <ReleaseReviewBody {...review} packet={packet} availableEvidence={availableEvidence} onRetryDependencies={refresh} />
      </Box>}
      {!closed && (
        <>
        <Button sx={{ display: { xs: 'block', sm: 'none' }, position: 'sticky', bottom: 0, bgcolor: 'background.paper', borderTop: 1, borderColor: 'divider', zIndex: 2 }} onClick={() => decisionPanel.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })}>Review decision ↓</Button>
        <Box ref={decisionPanel} data-sticky-footer sx={{ position: { xs: 'static', sm: 'sticky' }, bottom: 0, zIndex: 2, bgcolor: 'background.paper', borderTop: 1, borderColor: 'divider', minHeight: 64, py: 1, px: 1.5 }}>
          <Stack spacing={1.5}>
            {!request && !onEdit && <PeoplePicker label="Assigned reviewer" people={recipients} value={recipientId} loading={recipientsLoading} error={recipientsError} onRetry={onRetryRecipients}
                onChange={setRecipientId} disabled={locked || !permissions.canSubmit}
                helperText="An independent reviewer must approve before production can move." />}
            {/* One footer layout: the acknowledgment on the left; secondary actions, then the reason, then the primary on the right, all centred. */}
            <Stack direction={{ xs: 'column', sm: 'row' }} useFlexGap gap={1.5} flexWrap={{ sm: 'wrap' }} alignItems={{ xs: 'stretch', sm: 'center' }}>
            <FormControlLabel componentsProps={{ typography: { variant: 's3' } }} sx={{ flex: { sm: '1 1 280px' }, minWidth: 0, mr: 0 }}
              control={
                <Checkbox
                  checked={reviewed}
                  disabled={!current || locked}
                  onChange={(_, value) => setAckValue(value ? ackKey : null)}
                />
              }
              label="I've checked this version, where it's going, the evidence and what the release will change."
            />
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems={{ xs: 'stretch', sm: 'center' }} sx={{ minWidth: 0, ml: { sm: 'auto' } }}>
              {!request && onEdit && <><Button variant="outlined" disabled={locked} onClick={onEdit}>Edit request</Button><Button disabled={locked} onClick={onCancel}>Cancel</Button></>}
              {request && <>
                {request.status !== 'approved' && !commentFocused && !comment && <Button size="small" sx={{ ml: { sm: -1 } }} disabled={locked || !canRequestChanges} onClick={() => setCommentFocused(true)}>Add comment</Button>}
                {request.status !== "approved" && <Button
                  variant="outlined"
                  disabled={
                    !onDecide ||
                    !canRequestChanges ||
                    locked
                  }
                  onClick={() => {
                    if (!comment.trim()) { setCommentFocused(true); return; }
                    operation.run(onDecide, { ...actionInput, decision: "request_changes", comment });
                  }}
                >
                  Request changes
                </Button>}
              </>}
              <ActionReason id="decision-reason" reason={decisionReason} sx={{ textAlign: { sm: 'right' } }} />
              {!request && (
                <Button
                  variant="contained" color="primary" {...reasonProps('decision-reason', decisionReason)}
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
                  Send review request
                </Button>
              )}
              {request && <>
                {request.status === "pending" && <Button {...reasonProps('decision-reason', decisionReason)}
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
                {(request.status === "approved" || !current) && <Button {...reasonProps('decision-reason', decisionReason)}
                  variant="contained" color="primary"
                  disabled={!onExecute || !canExecute || !reviewed || locked}
                  onClick={() => setConfirm(true)}
                >
                  Release to production
                </Button>}
              </>}
            </Stack>
            </Stack>
            {request && request.status !== 'approved' && (commentFocused || comment) && <TextField inputRef={commentInput} autoFocus multiline minRows={3} size="small" label="Review comment" value={comment} onChange={(event) => setComment(event.target.value)} inputProps={{ maxLength: 2000 }} disabled={locked || !canRequestChanges} helperText="A comment is required to request changes." />}
            {(!operation.stale || current) && <OperationFeedback operation={operation} onRefresh={refresh} />}
            {operation.result && !finalReceipt && <Alert severity="info">Request recorded. Refresh the server-backed review to see the current decision and unread state. Nothing was released by this acknowledgment.</Alert>}
            {request?.status === 'approved' && current && !permissions.canExecute && <Typography variant="s3" color="text.secondary">A currently authorized releaser must execute the approved transition. Passing tests or approval do not grant that role.</Typography>}
          </Stack>
        </Box>
        </>
      )}
      {!request && (!recipientId || !permissions.canSubmit) && <Typography variant="s3" color="text.secondary">{!permissions.canSubmit ? 'You need permission to request a review.' : 'Choose an assigned reviewer to continue.'}</Typography>}
      {finalReceipt && <ReleaseReceipt receipt={finalReceipt} request={request} />}
      {confirm && !closed && !renewalOpen && <ConfirmDialog
        open
        onClose={() => {
          if (!operation.busy) setConfirm(false);
        }}
        closeDisabled={operation.busy}
        title={`Move ${packet.transition.label} to v${packet.transition.version}?`}
        content={`${packet.transition.label[0].toUpperCase()}${packet.transition.label.slice(1)} for ${request?.name || "this resource"} will point to v${packet.transition.version}. Apps that follow ${packet.transition.label} pick it up the next time they fetch it; check Consumers to confirm delivery. Running conversations aren't changed. Approval is checked again before the move.`}
        action={
          <Button
            variant="contained" color="primary"
            disabled={!canExecute || !reviewed || locked}
            onClick={async () => {
              setConfirm(false);
              await operation.run(onExecute, actionInput);
              // Close on every outcome: a refusal or unknown result is shown
              // beside the decision, where renewal or retry is offered.
              setConfirm(false);
            }}
          >
            Move {packet.transition.label}
          </Button>
        }
      />}
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
  renewalReason: PropTypes.string,
  onEdit: PropTypes.func,
  onCancel: PropTypes.func,
  availableEvidence: PropTypes.number,
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
