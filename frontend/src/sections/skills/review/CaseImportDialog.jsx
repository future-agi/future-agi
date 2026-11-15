import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
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
import ActionReason, { reasonProps } from "../components/ActionReason";

const bytes = (text) => new TextEncoder().encode(text).length;
const humanizeKey = (key) => { const text = String(key).replace(/[_-]+/g, " ").trim(); return text ? text.charAt(0).toUpperCase() + text.slice(1) : "Value"; };
// A structured tool result reads as named values ("Age days: 10 · Amount: 25"); the raw JSON stays one
// toggle away for redaction. A result shaped { "<tool>": { ... } } is named after its tool. Plain text is
// not structured, so it stays an open text field.
function toolStructure(text) {
  let value;
  try { value = JSON.parse(text); } catch { return null; }
  if (!value || typeof value !== "object") return null;
  const keys = Array.isArray(value) ? [] : Object.keys(value);
  const named = keys.length === 1 && value[keys[0]] && typeof value[keys[0]] === "object";
  const leaves = [];
  const walk = (node, path) => {
    if (node && typeof node === "object") Object.entries(node).forEach(([key, child]) => walk(child, path ? `${path} ${key}` : key));
    else leaves.push(`${humanizeKey(path)}: ${node === null ? "empty" : String(node)}`);
  };
  walk(named ? value[keys[0]] : value, "");
  const shown = leaves.slice(0, 6);
  return { name: named ? keys[0] : null, summary: leaves.length ? `${shown.join(" · ")}${leaves.length > shown.length ? ` · ${leaves.length - shown.length} more` : ""}` : "No values" };
}
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
  targetName,
  onSavedChange,
  onBack,
  initialDraft,
}) {
  // Back keeps every typed field: the same snapshot restores all of them; a new snapshot (a changed
  // selection) keeps only what the reviewer wrote (name, expected answer, behavior) and re-reads the
  // request and tool results from it. Acknowledgments always start unticked.
  const sameSnapshot = initialDraft?.snapshot === (preview.captureId ?? preview.planId);
  const [title, setTitle] = useState(initialDraft?.title ?? "");
  const [behavior, setBehavior] = useState(initialDraft?.behavior ?? { skillId: "", action: "" });
  const [expectedSuiteRevision] = useState(suiteRevision);
  const suiteChanged = expectedSuiteRevision !== suiteRevision;
  const [prompt, setPrompt] = useState(() => sameSnapshot ? initialDraft.prompt :
    preview.segments
      .filter((item) => item.kind === "request")
      .map((item) => item.text)
      .join("\n\n"),
  );
  const [fixtures, setFixtures] = useState(() => sameSnapshot ? initialDraft.fixtures :
    preview.segments
      .filter((item) => item.kind === "tool_result")
      .map((item, index) => ({
        id: crypto.randomUUID(),
        name: `Supplied tool result ${index + 1}`,
        text: item.text,
      })),
  );
  const [expectation, setExpectation] = useState(initialDraft?.expectation ?? "");
  // Which tool results are structured is decided once, from the snapshot, so editing the raw text
  // never moves the field.
  const [structured] = useState(() => Object.fromEntries(preview.segments.filter((item) => item.kind === "tool_result").map((item, index) => [index, Boolean(toolStructure(item.text))])));
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
  const promptBytes = bytes(prompt);
  const fixtureBytes = fixtures.reduce((sum, item) => sum + bytes(item.text), 0);
  // Why Save is unavailable, in the order a reviewer would fix it.
  const saveReason = !canSave || !onSave ? "You can't save test cases in this workspace."
    : expired ? "This preview expired. Start a new capture."
    : suiteChanged ? "Saved test cases changed. Reopen this capture."
    : locked ? null
    : !title.trim() ? "Add a case name."
    : !prompt.trim() ? "Add the request."
    : promptBytes > 16384 ? "Shorten the request to 16 KB."
    : fixtureBytes > 49152 ? "Shorten the tool results to 48 KB."
    : !expectation.trim() ? "Add the expected answer."
    : applicationBehavior && behaviorPartial(behavior) ? "Choose both a skill and an action, or neither."
    : !reviewed || !shared ? "Confirm both statements above."
    : null;
  useEffect(() => { onSavedChange?.(Boolean(operation.result)); }, [operation.result, onSavedChange]);
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
            {targetName ? `Test case saved to ${targetName}` : "Test case saved"}: {title}. Nothing was run or published.
          </Alert>
          {saved?.action && <Typography variant="s1">Next, set up an application test to confirm the failure and test a fix.</Typography>}
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
          {targetName && <Typography variant="s1">Saving to {targetName}</Typography>}
          {/* Readable provenance first; fingerprints and exact identities are details. */}
          <Typography variant="s1">{describeSource?.(preview) || (preview.recordedContext ? "From a recorded trace" : "From a trace with no recorded context version")}</Typography>
          <Typography variant="s3" color="text.secondary">
            {targetName ? "Step 2 of 2 · " : ""}Saving doesn&apos;t run anything. Set the expected answer from your policy, not from the failed answer.
          </Typography>
          <TechnicalDetails title="What are these checks?">
            <Stack spacing={0.5}>
              <Typography variant="s3">
                An answer check replays the selected text{applicationBehavior ? "; an application test checks the skills and business actions you specify below" : ""}.
              </Typography>
              {preview.omitted?.length > 0 && (
                <Typography variant="s3">
                  Left out of this case: {preview.omitted.map((item) => item.reason).join("; ")}
                </Typography>
              )}
            </Stack>
          </TechnicalDetails>
          <TechnicalDetails title="Source details">
            <Stack spacing={1}>
              <IdentityValue label="Source fingerprint" value={preview.sourceFingerprint} />
              <IdentityValue label="Source identity" value={preview.sourceLabel ?? preview.captureId ?? preview.planId} />
              {preview.recordedContext ? <>
                <IdentityValue label="Recorded context version" value={preview.recordedContext.versionId} />
                <IdentityValue label="Recorded context digest" value={preview.recordedContext.digest} />
              </> : <IdentityValue label="Recorded context" value="Unavailable — diagnostic case, not exact historical replay" />}
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
              Test cases changed while this capture was open. Your edits are
              preserved but can&apos;t be saved against the changed list. Reopen
              this capture after reviewing the current test cases.
            </Alert>
          )}
          {preview.outboundFindings?.map((finding) => (
            <Alert key={finding.findingId} severity="warning">
              {finding.message ?? finding.explanation}
            </Alert>
          ))}
          <TextField
            autoFocus
            label="Case name"
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
            helperText="Remove private or irrelevant text. Up to 16 KB of text. Put the expected answer in the check below, not here."
          />
          {fixtures.map((fixture, index) => {
            const field = (
              <TextField
                label={`Redacted tool result ${index + 1}`}
                size="small"
                multiline
                minRows={2}
                fullWidth
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
            );
            if (!structured[index]) return <Box key={fixture.id}>{field}</Box>;
            const shape = toolStructure(fixture.text);
            const name = shape?.name || `Tool result ${index + 1}`;
            return (
              <Stack key={fixture.id} spacing={0.5} role="group" aria-label={name} sx={{ p: 1.5, border: 1, borderColor: "divider", borderRadius: 1, minWidth: 0 }}>
                <Typography variant="s1" sx={{ fontWeight: 500, overflowWrap: "anywhere" }}>{name}</Typography>
                <Typography variant="s3" color="text.secondary" sx={{ overflowWrap: "anywhere" }}>
                  {shape ? shape.summary : "Edited text; see the raw data."}
                </Typography>
                <TechnicalDetails title="Show raw data">{field}</TechnicalDetails>
              </Stack>
            );
          })}
          {fixtures.reduce((sum, item) => sum + bytes(item.text), 0) >
            49152 && (
            <Alert severity="error">
              Tool results are over 48 KB of text. Shorten them to save.
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
            helperText="An explicit, case-sensitive literal text check, not an AI judge. You can add more checks to the saved test case."
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
            This test case checks the answer, not skill selection.
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
      <DialogActions sx={{ flexWrap: "wrap", gap: 1 }}>
        {onBack && !operation.result && (
          <Button
            variant="text"
            disabled={operation.busy || operation.uncertain}
            onClick={() => onBack({ snapshot: preview.captureId ?? preview.planId, title, prompt, fixtures, expectation, behavior })}
          >
            Back
          </Button>
        )}
        {!operation.result && (
          <Stack sx={{ mr: "auto", minWidth: 0 }} spacing={0.25}>
            <ActionReason id="save-case-reason" reason={saveReason} />
            {!expired && expiresIn(preview.expiresAt, now).startsWith("Preview expires in") && (
              <Typography variant="s3" color="text.secondary">
                {expiresIn(preview.expiresAt, now).replace("Preview expires in", "The trace snapshot expires in")}; after that, start again from the trace.
              </Typography>
            )}
          </Stack>
        )}
        <Button disabled={operation.busy} onClick={onClose}>
          Cancel
        </Button>
        {!operation.result && <Button
          variant="contained"
          color="primary"
          {...reasonProps("save-case-reason", saveReason)}
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
          Save test case
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
  targetName: PropTypes.string,
  onSavedChange: PropTypes.func,
  onBack: PropTypes.func,
  initialDraft: PropTypes.object,
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
  targetName,
  onBack,
  initialDraft,
}) {
  const [saved, setSaved] = useState(false);
  return (
    <ClosableDialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="md"
      aria-labelledby="reviewed-example-title"
    >
      <DialogTitle id="reviewed-example-title">
        {saved ? "Test case saved" : "Save test case"}
      </DialogTitle>
      {open && scopeKey && state === "ready" && preview ? (
        <ReviewedExample
          key={`${scopeKey}:${preview.captureId ?? preview.planId}`}
          {...{ preview, suiteRevision, onSave, onClose, canSave, subjectKind, applicationBehavior, renderSaved, describeSource, targetName, onBack, initialDraft }}
          onSavedChange={setSaved}
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
  targetName: PropTypes.string,
  onBack: PropTypes.func,
  initialDraft: PropTypes.object,
};
