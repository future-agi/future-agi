import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { isDefiniteRejection } from "src/api/skills/operation-errors";
import Section from "src/components/section";
import { ConfirmDialog } from "src/components/custom-dialog";
import ReleaseReviewBody, {
  IdentityValue,
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
      setError(
        confirmedRejected
          ? "Request rejected. Review current permissions and packet state; your inputs are preserved. No preconditions were refreshed."
          : "The response was not confirmed. Do not assume failure or submit a new operation. Retry the original request to recover its receipt.",
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
    uncertain,
    result,
    run,
    retry: () =>
      pending.current ? run(pending.current.send, pending.current.input) : null,
  };
}

export function OperationFeedback({ operation }) {
  return (
    <>
      {operation.error && <Alert severity="error">{operation.error}</Alert>}
      {operation.uncertain && (
        <Button
          variant="outlined"
          disabled={operation.busy}
          onClick={operation.retry}
        >
          Retry original request
        </Button>
      )}
    </>
  );
}
OperationFeedback.propTypes = { operation: PropTypes.object.isRequired };

function PacketActions({
  request,
  preview,
  recipients,
  permissions,
  onSubmit,
  onDecide,
  onExecute,
  onRefresh,
  receipt,
  currentEvidence,
  currentTarget,
  review,
  state,
}) {
  const operation = useReviewedOperation();
  const now = useReviewClock();
  const [recipientId, setRecipientId] = useState("");
  const [comment, setComment] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const packet = preview.packet;
  const fingerprint = preview.fingerprint;
  // Acknowledgment is for this exact server packet, never just its content hash.
  const [ackIdentity, setAckIdentity] = useState(null);
  const reviewed = acknowledged && ackIdentity === fingerprint;
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
    state === "ready" && preview.current === true && reasons.length === 0;
  const locked =
    operation.busy || operation.uncertain || Boolean(operation.result);
  const canDecide =
    current &&
    permissions.canDecide &&
    request &&
    request.status !== "released";
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
  return (
    <Stack spacing={3}>
      <ReleaseReviewBody {...review} packet={packet} />
      {!current && (
        <Alert severity="warning">
          Review is stale or unavailable.{" "}
          {Array.from(new Set(reasons)).join(" ")} Request a new packet after
          inspecting the changes.
        </Alert>
      )}
      {onRefresh && (
        <Button variant="outlined" disabled={locked} onClick={onRefresh}>
          Inspect a new review
        </Button>
      )}
      {!closed && (
        <Section title={request ? "Review decision" : "Request review"}>
          <Stack spacing={1.5}>
            <Typography variant="s3">
              Reading is not approval. Decisions bind to this packet, its target
              configuration and the reviewed annotation revisions. Approval does
              not grant release authority.
            </Typography>
            {!request && (
              <TextField
                select
                size="small"
                label="Assigned reviewer"
                value={
                  recipients.some((item) => item.id === recipientId)
                    ? recipientId
                    : ""
                }
                onChange={(event) => setRecipientId(event.target.value)}
                disabled={locked || !permissions.canSubmit}
              >
                <MenuItem value="">Choose an authorized reviewer</MenuItem>
                {recipients.map((item) => (
                  <MenuItem key={item.id} value={item.id}>
                    {item.name}
                  </MenuItem>
                ))}
              </TextField>
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
                disabled={locked || !canDecide}
              />
            )}
            <FormControlLabel
              control={
                <Checkbox
                  checked={reviewed}
                  disabled={!current || locked}
                  onChange={(_, value) => {
                    setAcknowledged(value);
                    setAckIdentity(fingerprint);
                  }}
                />
              }
              label="I reviewed this exact candidate, target, evidence and release consequence."
            />
            <OperationFeedback operation={operation} />
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
                  variant="contained"
                  disabled={
                    !onSubmit ||
                    !permissions.canSubmit ||
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
              {request && (
                <>
                  <Button
                    variant={canExecute ? "outlined" : "contained"}
                    disabled={!onDecide || !canDecide || !reviewed || locked}
                    onClick={() =>
                      operation.run(onDecide, {
                        ...actionInput,
                        decision: "approve",
                        comment,
                      })
                    }
                  >
                    Approve exact packet
                  </Button>
                  <Button
                    variant="outlined"
                    disabled={
                      !onDecide || !canDecide || !comment.trim() || locked
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
                  <Button
                    variant={canExecute ? "contained" : "outlined"}
                    disabled={!onExecute || !canExecute || !reviewed || locked}
                    onClick={() => setConfirm(true)}
                  >
                    Release approved packet
                  </Button>
                </>
              )}
            </Stack>
            {request && !permissions.canExecute && (
              <Typography variant="s3">
                A currently authorized releaser must execute the approved
                transition. Passing tests or approval do not grant that role.
              </Typography>
            )}
          </Stack>
        </Section>
      )}
      {finalReceipt && (
        <Section title="Durable release receipt">
          <Stack spacing={1}>
            <IdentityValue label="Request" value={finalReceipt.requestId} />
            <IdentityValue label="Decision" value={finalReceipt.decisionId} />
            <IdentityValue
              label="Executed by"
              value={finalReceipt.executedBy}
            />
            <Box component="details" open>
              <Typography component="summary" variant="m3">
                Currentness before the move
              </Typography>
              <Typography
                component="pre"
                variant="s3"
                sx={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}
              >
                {JSON.stringify(finalReceipt.currentnessAtAction, null, 2)}
              </Typography>
            </Box>
            <Box component="details" open>
              <Typography component="summary" variant="m3">
                Pointer state after the move
              </Typography>
              <Typography
                component="pre"
                variant="s3"
                sx={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}
              >
                {JSON.stringify(finalReceipt.postMoveState, null, 2)}
              </Typography>
            </Box>
            <Alert severity="info">
              The pointer changed or was already at its target. Installation and
              model delivery require their own observations.
            </Alert>
          </Stack>
        </Section>
      )}
      <ConfirmDialog
        open={confirm && !closed}
        onClose={() => {
          if (!operation.busy) setConfirm(false);
        }}
        closeDisabled={operation.busy}
        title={`Move ${packet.transition.label} to v${packet.transition.version}?`}
        content={`Execute only approved packet revision ${request?.revision ?? ""}. Existing threads and checked-in locks may still use another version. Current authority and all packet preconditions are rechecked by the server.`}
        action={
          <Button
            variant="contained"
            disabled={!canExecute || !reviewed || locked}
            onClick={async () => {
              const result = await operation.run(onExecute, actionInput);
              if (result) setConfirm(false);
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
  permissions: PropTypes.object.isRequired,
  onSubmit: PropTypes.func,
  onDecide: PropTypes.func,
  onExecute: PropTypes.func,
  onRefresh: PropTypes.func,
  receipt: PropTypes.object,
  currentEvidence: PropTypes.array,
  currentTarget: PropTypes.object,
  review: PropTypes.object,
  state: PropTypes.string.isRequired,
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
      key={`${scopeKey}:${request?.id ?? preview.packet.candidateVersionId}:${request?.status ?? "new"}`}
      {...props}
      {...{ state, preview, request, recipients, permissions }}
    />
  );
}
ReleaseRequestPanel.propTypes = {
  scopeKey: PropTypes.string,
  state: PropTypes.string,
  preview: PropTypes.object,
  request: PropTypes.object,
  recipients: PropTypes.array,
  permissions: PropTypes.object,
  onRetry: PropTypes.func,
};
