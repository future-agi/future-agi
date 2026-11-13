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
  TextField,
  Typography,
} from "@mui/material";
import { ClosableDialog } from "src/components/custom-dialog";
import TechnicalDetails from "../components/TechnicalDetails";
import { expiresIn } from "../utils/conclusions";
import {
  IdentityValue,
  ReviewState,
  useReviewClock,
} from "./ReleaseReviewBody";
import { OperationFeedback, useReviewedOperation } from "./ReleaseRequestPanel";
import ApplicationBehaviorFields from "../tests/ApplicationBehaviorFields";
import { behaviorPartial, withBehavior } from "../tests/applicationBehavior";

const bytes = (text) => new TextEncoder().encode(text).length;
function ReviewedExample({
  preview,
  suiteRevision,
  onSave,
  onClose,
  canSave,
  subjectKind,
  applicationBehavior,
  renderSaved,
  describeSource,
}) {
  const [title, setTitle] = useState("");
  const [behavior, setBehavior] = useState({ skillId: "", action: "" });
  const [expectedSuiteRevision] = useState(suiteRevision);
  const suiteChanged = expectedSuiteRevision !== suiteRevision;
  const [prompt, setPrompt] = useState(() =>
    preview.segments
      .filter((item) => item.kind === "request")
      .map((item) => item.text)
      .join("\n\n"),
  );
  const [fixtures, setFixtures] = useState(() =>
    preview.segments
      .filter((item) => item.kind === "tool_result")
      .map((item, index) => ({
        id: crypto.randomUUID(),
        name: `Supplied tool result ${index + 1}`,
        text: item.text,
      })),
  );
  const [expectation, setExpectation] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [shared, setShared] = useState(false);
  const [ids] = useState(() => ({
    id: crypto.randomUUID(),
    checkId: crypto.randomUUID(),
    sourceGroupId: crypto.randomUUID(),
  }));
  const now = useReviewClock();
  const operation = useReviewedOperation();
  const expired =
    !Number.isFinite(Date.parse(preview.expiresAt)) ||
    Date.parse(preview.expiresAt) <= now;
  const locked =
    operation.busy || operation.uncertain || Boolean(operation.result);
  const valid =
    canSave &&
    !suiteChanged &&
    !expired &&
    !locked &&
    title.trim() &&
    prompt.trim() &&
    expectation.trim() &&
    reviewed &&
    shared &&
    !(applicationBehavior && behaviorPartial(behavior)) &&
    bytes(prompt) <= 16384 &&
    fixtures.reduce((sum, item) => sum + bytes(item.text), 0) <= 49152;
  const edit = (setter) => (event) => {
    setter(event.target.value);
    setReviewed(false);
    setShared(false);
  };
  // After saving, the confirmation and the next step lead; the saved fields are no longer editable.
  const rendered = operation.result ? renderSaved?.({ caseId: ids.id, preview }) : null;
  const saved = rendered && typeof rendered === "object" && ("action" in rendered || "notice" in rendered) ? rendered : rendered ? { notice: rendered } : null;
  if (operation.result) return (
    <>
      <DialogContent>
        <Stack spacing={1.5}>
          <Alert severity="success" role="status">
            Reviewed example saved: {title}. No comparison was run and no content was published.
          </Alert>
          {saved?.action && <Typography variant="s1">Next, reproduce it on the application to confirm the failure and test a fix.</Typography>}
          {saved?.notice}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        {saved?.action}
      </DialogActions>
    </>
  );
  return (
    <>
      <DialogContent>
        <Stack spacing={1.5}>
          <Alert severity="info">
            This replays supplied text, not external actions. The observed
            failed answer is not an answer key. Saving does not run a model.
          </Alert>
          {/* Readable provenance first; fingerprints and exact identities are details. */}
          <Typography variant="s1">{describeSource?.(preview) || (preview.recordedContext ? "From a recorded trace" : "From a trace with no recorded context version")}</Typography>
          {!expired && <Typography variant="s3" color="text.secondary">{expiresIn(preview.expiresAt, now)}</Typography>}
          <TechnicalDetails title="Source details">
            <Stack spacing={1}>
              <IdentityValue label="Source fingerprint" value={preview.sourceFingerprint} />
              <IdentityValue label="Source identity" value={preview.sourceLabel ?? preview.captureId ?? preview.planId} />
              {preview.recordedContext ? <>
                <IdentityValue label="Recorded context version" value={preview.recordedContext.versionId} />
                <IdentityValue label="Recorded context digest" value={preview.recordedContext.digest} />
              </> : <IdentityValue label="Recorded context" value="Unavailable — diagnostic example, not exact historical replay" />}
              <IdentityValue label="Private preview expires (UTC)" value={preview.expiresAt} />
            </Stack>
          </TechnicalDetails>
          {expired && (
            <Alert severity="warning">
              Preview expired. Start a new authorized capture; these edits have
              not been saved.
            </Alert>
          )}
          {suiteChanged && !operation.result && (
            <Alert severity="warning">
              Saved examples changed while this capture was open. Your edits are
              preserved, but the suite precondition will not be refreshed
              silently. Reopen after reviewing the current suite.
            </Alert>
          )}
          {preview.omitted?.length > 0 && (
            <Typography variant="s3">
              Omitted: {preview.omitted.map((item) => item.reason).join("; ")}
            </Typography>
          )}
          {preview.outboundFindings?.map((finding) => (
            <Alert key={finding.findingId} severity="warning">
              {finding.message ?? finding.explanation}
            </Alert>
          ))}
          <TextField
            autoFocus
            label="Example title"
            size="small"
            value={title}
            inputProps={{ maxLength: 120 }}
            onChange={edit(setTitle)}
            disabled={locked}
          />
          <TextField
            label="Redacted request"
            size="small"
            multiline
            minRows={4}
            value={prompt}
            onChange={edit(setPrompt)}
            disabled={locked}
            error={bytes(prompt) > 16384}
            helperText="Remove private or irrelevant text. Maximum 16 KiB UTF-8. Expected answers belong in checks, not here."
          />
          {fixtures.map((fixture, index) => (
            <TextField
              key={fixture.id}
              label={`Redacted tool result ${index + 1}`}
              size="small"
              multiline
              minRows={2}
              value={fixture.text}
              disabled={locked}
              onChange={(event) => {
                setFixtures((items) =>
                  items.map((item) =>
                    item.id === fixture.id
                      ? { ...item, text: event.target.value }
                      : item,
                  ),
                );
                setReviewed(false);
                setShared(false);
              }}
            />
          ))}
          {fixtures.reduce((sum, item) => sum + bytes(item.text), 0) >
            49152 && (
            <Alert severity="error">
              Combined tool results exceed 48 KiB UTF-8.
            </Alert>
          )}
          <TextField
            label="Expected answer must contain"
            size="small"
            multiline
            minRows={2}
            value={expectation}
            inputProps={{ maxLength: 2000 }}
            onChange={edit(setExpectation)}
            disabled={locked}
            helperText="An explicit, case-sensitive literal text check, not an AI judge. Add more checks in saved examples after import."
          />
          {applicationBehavior && (
            <ApplicationBehaviorFields
              skills={applicationBehavior.skills}
              behavior={behavior}
              onChange={(patch) => {
                setBehavior((previous) => ({ ...previous, ...patch }));
                setReviewed(false);
                setShared(false);
              }}
            />
          )}
          {subjectKind !== "agent_context" && <Typography variant="s3">
            This example checks the answer, not skill selection.
          </Typography>}
          <FormControlLabel
            control={
              <Checkbox
                checked={reviewed}
                disabled={locked}
                onChange={(_, value) => setReviewed(value)}
              />
            }
            label="I confirmed the expected behavior independently of the failed answer."
          />
          <FormControlLabel
            control={
              <Checkbox
                checked={shared}
                disabled={locked}
                onChange={(_, value) => setShared(value)}
              />
            }
            label="Share this redacted request, supplied tool results and success check with the workspace."
          />
          <OperationFeedback operation={operation} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button disabled={operation.busy} onClick={onClose}>
          Close
        </Button>
        {!operation.result && <Button
          variant="contained"
          color="primary"
          disabled={!valid || !onSave}
          onClick={() =>
            operation.run(onSave, {
              captureId: preview.captureId,
              planId: preview.planId,
              sourceFingerprint: preview.sourceFingerprint,
              suiteRevision: expectedSuiteRevision,
              case: {
                id: ids.id,
                sourceGroupId: ids.sourceGroupId,
                title,
                prompt,
                fixtures: applicationBehavior
                  ? withBehavior(fixtures, behavior, () => crypto.randomUUID())
                  : fixtures,
                expectedTrigger: null,
                checks: [
                  {
                    id: ids.checkId,
                    kind: "contains",
                    critical: true,
                    config: { text: expectation, caseSensitive: true },
                  },
                ],
                reviewed: true,
              },
              acknowledgeWorkspaceSharing: true,
            })
          }
        >
          Save reviewed example
        </Button>}
      </DialogActions>
    </>
  );
}
ReviewedExample.propTypes = {
  preview: PropTypes.object.isRequired,
  suiteRevision: PropTypes.number.isRequired,
  onSave: PropTypes.func,
  onClose: PropTypes.func.isRequired,
  canSave: PropTypes.bool,
  subjectKind: PropTypes.string.isRequired,
  applicationBehavior: PropTypes.object,
  renderSaved: PropTypes.func,
  describeSource: PropTypes.func,
};

export default function CaseImportDialog({
  open,
  onClose,
  scopeKey,
  state = "unavailable",
  preview,
  suiteRevision = 0,
  subjectKind = "agent_context",
  canSave = false,
  onSave,
  onRetry,
  applicationBehavior,
  renderSaved,
  describeSource,
}) {
  return (
    <ClosableDialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="md"
      aria-labelledby="reviewed-example-title"
    >
      <DialogTitle id="reviewed-example-title">
        Review captured example
      </DialogTitle>
      {open && scopeKey && state === "ready" && preview ? (
        <ReviewedExample
          key={`${scopeKey}:${preview.captureId ?? preview.planId}`}
          {...{ preview, suiteRevision, onSave, onClose, canSave, subjectKind, applicationBehavior, renderSaved, describeSource }}
        />
      ) : (
        <DialogContent>
          <ReviewState
            state={!scopeKey ? "forbidden" : state}
            onRetry={onRetry}
          />
        </DialogContent>
      )}
    </ClosableDialog>
  );
}
CaseImportDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  scopeKey: PropTypes.string,
  state: PropTypes.string,
  preview: PropTypes.object,
  suiteRevision: PropTypes.number,
  subjectKind: PropTypes.string,
  canSave: PropTypes.bool,
  onSave: PropTypes.func,
  onRetry: PropTypes.func,
  applicationBehavior: PropTypes.shape({ skills: PropTypes.array.isRequired }),
  renderSaved: PropTypes.func,
  describeSource: PropTypes.func,
};
