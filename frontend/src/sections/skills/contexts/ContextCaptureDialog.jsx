import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
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
import { IdentityValue, ReviewState } from "../review/ReleaseReviewBody";
import {
  OperationFeedback,
  useReviewedOperation,
} from "../review/ReleaseRequestPanel";

function Capture({
  open,
  onClose,
  scopeKey,
  source,
  onPreview,
  onAccept,
  suiteRevision,
  canCapture,
}) {
  const [selected, setSelected] = useState([]);
  const operation = useReviewedOperation();
  const segments = source.segments.filter((item) =>
    ["request", "tool_result"].includes(item.kind),
  );
  const allowed =
    source.authorized === true && source.readOnly !== true && canCapture;
  if (operation.result && allowed)
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
      <DialogTitle id="context-capture-title">
        Create context regression case
      </DialogTitle>
      <DialogContent>
        <Stack spacing={1.5}>
          {!allowed ? (
            <ReviewState state="forbidden" />
          ) : (
            <>
              <Alert severity="info">
                Select only relevant request and tool-result text. The next step
                is private redaction and expectation review. This does not run a
                model or copy hidden reasoning.
              </Alert>
              <IdentityValue
                label="Source project"
                value={source.traceRef.projectId}
              />
              <IdentityValue
                label="Physical span"
                value={`${source.traceRef.traceId} / ${source.traceRef.spanId} / ${source.traceRef.startTime}`}
              />
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
                    label={`${item.kind === "request" ? "Request" : "Tool result"} · ${item.id}`}
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
        <Button disabled={operation.busy} onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="contained"
          disabled={
            !allowed ||
            !onPreview ||
            !selected.length ||
            operation.busy ||
            operation.uncertain ||
            selected.some((id) => !segments.some((item) => item.id === id))
          }
          onClick={() =>
            operation.run(onPreview, {
              traceRef: source.traceRef,
              selectedSegmentIds: selected,
            })
          }
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
        <DialogTitle>Create context regression case</DialogTitle>
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
