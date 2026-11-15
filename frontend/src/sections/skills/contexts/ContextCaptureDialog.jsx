import { useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Checkbox,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Stack,
  Typography,
} from "@mui/material";
import { ClosableDialog } from "src/components/custom-dialog";
import CaseImportDialog from "../review/CaseImportDialog";
import TechnicalDetails from '../components/TechnicalDetails';
import IdentityChip from '../components/IdentityChip';

import { IdentityValue, ReviewState } from "../review/ReleaseReviewBody";
import {
  OperationFeedback,
  useReviewedOperation,
} from "../review/ReleaseRequestPanel";

const segmentLabel = (item) => item.kind === 'request' ? 'Customer request' : `Tool result${item.name ? ` · ${item.name}` : ''}`;

function Capture({
  open,
  onClose,
  scopeKey,
  source,
  onPreview,
  onAccept,
  suiteRevision,
  canCapture,
  applicationBehavior,
  renderSaved,
  describeSource,
  onChangeContext,
  contextName,
}) {
  const operation = useReviewedOperation();
  const segments = source.segments.filter((item) =>
    ["request", "tool_result"].includes(item.kind),
  );
  // Every supported request and tool-result segment starts selected so the reviewer
  // deselects exceptions instead of ticking each one; nothing is shared before the
  // separate redaction review and explicit workspace-sharing acknowledgement.
  const [selected, setSelected] = useState(() => segments.map((item) => item.id));
  // Step 2 opens only for the snapshot of the selection it was made from, and never while a new
  // preview is still in flight. Back returns here with every step-2 field kept (draft); the same
  // selection reopens the same snapshot, a changed one makes a fresh snapshot.
  const [step, setStep] = useState("select");
  const [previewedFor, setPreviewedFor] = useState(null);
  const [draft, setDraft] = useState(null);
  const selectionKey = JSON.stringify([...selected].sort());
  const allowed =
    source.authorized === true && source.readOnly !== true && canCapture;
  const next = async () => {
    if (operation.result && previewedFor === selectionKey) {
      setStep("review");
      return;
    }
    const result = await operation.run(onPreview, {
      traceRef: source.traceRef,
      selectedSegmentIds: selected,
    });
    if (result) {
      setPreviewedFor(selectionKey);
      setStep("review");
    }
  };
  if (operation.result && allowed && step === "review" && !operation.busy)
    return (
      <CaseImportDialog
        open={open}
        onClose={onClose}
        scopeKey={scopeKey}
        state="ready"
        preview={operation.result}
        suiteRevision={suiteRevision}
        canSave={canCapture}
        onSave={onAccept}
        applicationBehavior={applicationBehavior}
        renderSaved={renderSaved}
        describeSource={describeSource}
        targetName={contextName}
        initialDraft={draft}
        onBack={(fields) => {
          setDraft(fields);
          setStep("select");
        }}
      />
    );
  return (
    <ClosableDialog
      open={open}
      onClose={() => {
        if (!operation.busy) onClose();
      }}
      fullWidth
      maxWidth="md"
      aria-labelledby="context-capture-title"
    >
      <DialogTitle id="context-capture-title">Save test case</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5}>
          {contextName && (
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
              <Typography variant="s1">Saving to {contextName}</Typography>
              {onChangeContext && (
                <Button size="small" variant="text" aria-label="Change context" disabled={operation.busy} onClick={onChangeContext}>
                  Change
                </Button>
              )}
            </Stack>
          )}
          {!allowed ? (
            <ReviewState state="forbidden" />
          ) : (
            <>
              <Typography variant="s3" color="text.secondary">
                Step 1 of 2 · All request and tool-result text is selected. Untick anything you
                don&apos;t need. Next you&apos;ll remove private details and set the expected
                answer. Nothing runs yet.
              </Typography>
              <TechnicalDetails title="Trace details">
              <IdentityValue
                label="Source project"
                value={source.traceRef.projectId}
              />
              <IdentityValue
                label="Physical span"
                value={`${source.traceRef.traceId} / ${source.traceRef.spanId} / ${source.traceRef.startTime}`}
              />
              {segments.map((item) => <IdentityChip key={item.id} value={item.id} label={segmentLabel(item)} />)}
              </TechnicalDetails>
              {!segments.length && (
                <Typography variant="s1">
                  No supported request or tool-result segments. Assistant
                  answers are not imported as expected truth.
                </Typography>
              )}
              {segments.map((item) => (
                <Stack key={item.id} spacing={0.5}>
                  <FormControlLabel
                    control={
                      <Checkbox
                        disabled={operation.busy || operation.uncertain}
                        checked={selected.includes(item.id)}
                        onChange={(_, value) =>
                          setSelected((ids) =>
                            value
                              ? [...ids, item.id]
                              : ids.filter((id) => id !== item.id),
                          )
                        }
                      />
                    }
                    label={segmentLabel(item)}
                  />
                  <Typography
                    variant="s1"
                    sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                  >
                    {item.text}
                  </Typography>
                </Stack>
              ))}
              <OperationFeedback operation={operation} />
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        {onChangeContext && !contextName && (
          <Button disabled={operation.busy} onClick={onChangeContext}>
            Change context
          </Button>
        )}
        <Button disabled={operation.busy} onClick={onClose}>
          Cancel
        </Button>
        <Button color="primary"
          variant="contained"
          disabled={
            !allowed ||
            !onPreview ||
            !selected.length ||
            operation.busy ||
            operation.uncertain ||
            selected.some((id) => !segments.some((item) => item.id === id))
          }
          onClick={next}
        >
          Preview selected text
        </Button>
      </DialogActions>
    </ClosableDialog>
  );
}
Capture.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  scopeKey: PropTypes.string.isRequired,
  source: PropTypes.object.isRequired,
  onPreview: PropTypes.func,
  onAccept: PropTypes.func,
  suiteRevision: PropTypes.number.isRequired,
  canCapture: PropTypes.bool,
  applicationBehavior: PropTypes.object,
  renderSaved: PropTypes.func,
  describeSource: PropTypes.func,
  onChangeContext: PropTypes.func,
  contextName: PropTypes.string,
};
export default function ContextCaptureDialog({
  open,
  onClose,
  scopeKey,
  state = "unavailable",
  source,
  suiteRevision = 0,
  ...props
}) {
  if (!open) return null;
  if (!scopeKey || state !== "ready" || !source)
    return (
      <ClosableDialog open onClose={onClose} fullWidth maxWidth="sm">
        <DialogTitle>Save test case</DialogTitle>
        <DialogContent>
          <ReviewState state={!scopeKey ? "forbidden" : state} />
        </DialogContent>
      </ClosableDialog>
    );
  return (
    <Capture
      key={`${scopeKey}:${source.traceRef.projectId}:${source.traceRef.traceId}:${source.traceRef.spanId}:${source.traceRef.startTime}`}
      {...props}
      {...{ open, onClose, scopeKey, source, suiteRevision }}
    />
  );
}
ContextCaptureDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  scopeKey: PropTypes.string,
  state: PropTypes.string,
  source: PropTypes.object,
  suiteRevision: PropTypes.number,
};
