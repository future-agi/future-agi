import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ObjectHeader from "../components/ObjectHeader";
import ActionReason, { reasonProps } from '../components/ActionReason';
import { useStickyFooterHeight } from '../components/stickyFooter';
import IdentityChip from "../components/IdentityChip";
import TechnicalDetails from "../components/TechnicalDetails";
import { mountName, publishSentence } from "../utils/conclusions";
import Acknowledgment from "../components/Acknowledgment";
import ReleaseRuleNote from "../components/ReleaseRuleNote";
import { PUBLISH_AND_REQUEST_RELEASE } from "../utils/terms";
import { counted } from "../review/reviewPresentation";

// "v2 changes refund-policy v1 → v2; AGENTS.md is unchanged." The section title already says what
// publishing creates, so the sentence leads with the version it creates.
function publishLead(name, review) {
  const next = (review.expectedHeadVersion ?? 0) + 1;
  const sentence = publishSentence(name, review);
  const prefix = `Publishing creates ${name || "this context"} v${next}: `;
  if (!sentence.startsWith(prefix)) return sentence;
  const rest = sentence.slice(prefix.length);
  return `v${next} ${rest.startsWith("no ") ? "has" : "changes"} ${rest}`;
}
import { paths } from "src/routes/paths";
import Section from "src/components/section";
import ChecksList from "../components/ChecksList";
import ReleaseRequestPanel, {
  OperationFeedback,
  useReviewedOperation,
} from "../review/ReleaseRequestPanel";
import {
  ChangedFiles,
  DESCRIPTOR_FILE,
  IdentityValue,
  ReviewFileDiff,
  ReviewState,
  checkedFiles,
} from "../review/ReleaseReviewBody";
import CapabilityChanges from "../review/CapabilityChanges";
import DecisionFooterRow from "../review/DecisionFooterRow";

// UXF-02: the publisher's one tick names what it covers: the changed files
// against the published version, and the check warnings when there are any.
//   "I've checked the 2 changed files against v1 and the 2 check warnings"
export function publishAcknowledgmentLabel(review) {
  const files = checkedFiles(review.changes).length;
  const what = files ? `the ${counted(files, "changed file")}` : "the changes";
  const against = review.expectedHeadVersion ? ` against v${review.expectedHeadVersion}` : "";
  const warnings = review.checks?.summary?.warnings || 0;
  return `I've checked ${what}${against}${warnings ? ` and the ${counted(warnings, "check warning")}` : ""}`;
}
function DraftPublication({
  name,
  releaseRules,
  carried,
  onCarry,
  draftReview,
  canPublish,
  onPublish,
  onPublishAndRequest,
  publishOperation,
  oneStepOperation,
  onRefresh,
}) {
  // UXF-02: one tick, bound to this review fingerprint, sets both publish
  // acknowledgments (the review and, when there are warnings, the warnings).
  const [ackFingerprint, setAckFingerprint] = useState(null);
  // The publish operation lives in the controller: the head acknowledgment inside
  // the one publish click advances the draft revision and remounts this form, and
  // an unknown outcome must stay retryable with its original idempotency key.
  const localOperation = useReviewedOperation();
  const operation = publishOperation || localOperation;
  const footer = useRef(null);
  useStickyFooterHeight(footer);
  // Acknowledging the head issues a new review fingerprint for the very same candidate bytes.
  // The publisher's acknowledgment carries across that one step, and only when the candidate,
  // its origin, the reviewed head and its static warnings are all unchanged; any other change
  // to the review (a new fingerprint for any other reason) starts the review over.
  const carriedHere =
    Boolean(carried) &&
    draftReview.headAcknowledged === true &&
    carried.candidateDigest === draftReview.candidateDigest &&
    carried.baseVersion === draftReview.baseVersion &&
    carried.expectedHeadVersion === draftReview.expectedHeadVersion &&
    carried.headDigest === draftReview.headDigest &&
    carried.warnings === (draftReview.checks?.summary.warnings ?? null);
  // The note typed for this candidate is not lost when the review reloads after the head step.
  const [note, setNote] = useState(
    (carriedHere ? carried.note : undefined) ?? draftReview.note ?? "",
  );
  const reviewed =
    ackFingerprint === draftReview.reviewFingerprint ||
    (carriedHere && carried.reviewed);
  const warningCount = draftReview.checks?.summary.warnings ?? 0;
  // The warnings are listed open above the tick, so the same tick covers them.
  const warnings = reviewed && warningCount > 0;
  const oneStep = oneStepOperation || operation;
  const locked =
    operation.busy ||
    operation.uncertain ||
    Boolean(operation.result) ||
    oneStep.busy ||
    oneStep.uncertain ||
    Boolean(oneStep.result);
  const fullDiff =
    draftReview.complete === true &&
    draftReview.changes.every(
      (file) =>
        typeof file.fromText === "string" && typeof file.toText === "string",
    ) &&
    (draftReview.baseVersion === draftReview.expectedHeadVersion ||
      (Array.isArray(draftReview.headChanges) &&
        draftReview.headChanges.every(
          (file) =>
            typeof file.fromText === "string" &&
            typeof file.toText === "string",
        )));
  const ready =
    canPublish &&
    fullDiff &&
    draftReview.current === true &&
    draftReview.checks?.summary.errors === 0 &&
    reviewed &&
    (!draftReview.checks.summary.warnings || warnings) &&
    !locked;
  // One quiet reason beside the actions while they wait for the tick.
  const acknowledgmentReason = !reviewed ? "Tick the box to publish." : null;
  // A conflict means the draft or the published head moved after the reviewer ticked: the ticks clear and the
  // review reloads, so the next publish is for what the reviewer sees now.
  const stalled = operation.stale ? operation.failure : oneStep !== operation && oneStep.stale ? oneStep.failure : null;
  const reset = useRef(null);
  useEffect(() => { reset.current = () => { setAckFingerprint(null); onCarry(null); onRefresh?.(); }; });
  useEffect(() => { if (stalled) reset.current(); }, [stalled]);
  const clearStale = () => { if (!stalled) return; operation.clearFailure(); if (oneStep !== operation) oneStep.clearFailure(); };
  const blocked = (send) => !send || (draftReview.baseVersion !== draftReview.expectedHeadVersion && !draftReview.headChanges);
  const carryAcknowledgments = () => onCarry({
    reviewed,
    note,
    candidateDigest: draftReview.candidateDigest,
    baseVersion: draftReview.baseVersion,
    expectedHeadVersion: draftReview.expectedHeadVersion,
    headDigest: draftReview.headDigest,
    warnings: draftReview.checks?.summary.warnings ?? null,
  });
  const publishInput = {
    revision: draftReview.revision,
    expectedDigest: draftReview.candidateDigest,
    expectedHeadVersion: draftReview.expectedHeadVersion,
    reviewFingerprint: draftReview.reviewFingerprint,
    headAcknowledged: draftReview.headAcknowledged === true,
    baseVersion: draftReview.baseVersion,
    note,
    acknowledgeWarnings: warnings,
  };
  return (
    <Stack spacing={3} sx={{ mt: 3, pb: "calc(var(--skills-sticky-footer-height, 0px) + 16px)" }}>
      <Section title="What publishing creates">
        <Stack spacing={1}>
          {/* Conclusion first, in one line: the version this creates, what changes in it, and where production stays. Reload sits beside it. */}
          <Stack direction={{ xs: "column", sm: "row" }} gap={1} alignItems={{ sm: "flex-start" }} justifyContent="space-between">
            <Typography variant="m3" role="status" sx={{ minWidth: 0 }}>
              {publishLead(name, draftReview)} Production stays {draftReview.productionVersion ? `on v${draftReview.productionVersion}` : "unassigned"} until it is reviewed and released.
            </Typography>
            {onRefresh && <Button variant="text" size="small" disabled={locked} onClick={() => { clearStale(); onRefresh(); }} sx={{ flexShrink: 0, alignSelf: "flex-start" }}>Reload draft</Button>}
          </Stack>
          {/* UXF-09: why production needs a release request here, from the rules this page was given. */}
          <ReleaseRuleNote rules={releaseRules || {
            label: "production",
            ...(Array.isArray(draftReview.protectedLabelNames) ? { protectedLabels: draftReview.protectedLabelNames } : {}),
            reviewRequired: true,
            subjectKind: "agent_context",
          }} />
          {draftReview.baseVersion !== draftReview.expectedHeadVersion && (
            <Alert severity="warning">
              This repair starts from v{draftReview.baseVersion}, not the latest v{draftReview.expectedHeadVersion}. Publishing makes it the new latest without the other changes in v{draftReview.expectedHeadVersion}.
            </Alert>
          )}
          <TechnicalDetails title="Technical details">
            <Stack spacing={1}>
              <IdentityValue label="Saved draft" value={draftReview.candidateDigest} />
              <IdentityValue label="Latest published version" value={draftReview.headDigest ?? "Unpublished"} />
            </Stack>
          </TechnicalDetails>
        </Stack>
      </Section>
      <Section id="review-files" title={draftReview.baseVersion == null ? "Changes in your draft" : `Changes since v${draftReview.baseVersion}`}>
        <Stack spacing={1.5}>
          {/* UXF-05: files people read come first and changed ones are open; unchanged ones are collapsed.
              The machine descriptor's exact diff is one click away. */}
          <ChangedFiles files={draftReview.changes.filter((file) => file.path !== DESCRIPTOR_FILE)} renderFile={(file, { open }) => open ? (
            <Box>
              <Typography variant="m3" sx={{ overflowWrap: "anywhere" }}>
                {file.path}
              </Typography>
              <ReviewFileDiff file={file} fromTitle={draftReview.baseVersion == null ? "New context" : `v${draftReview.baseVersion}`} toTitle="Your draft" />
            </Box>
          ) : (
            <TechnicalDetails title={`${file.path} · unchanged`}>
              <ReviewFileDiff file={file} fromTitle={draftReview.baseVersion == null ? "New context" : `v${draftReview.baseVersion}`} toTitle="Your draft" />
            </TechnicalDetails>
          )} />
          {draftReview.changes.filter((file) => file.path === DESCRIPTOR_FILE).map((file) => (
            <TechnicalDetails key={file.path} title="Settings file change">
              <ReviewFileDiff file={file} fromTitle={draftReview.baseVersion == null ? "New context" : `v${draftReview.baseVersion}`} toTitle="Your draft" />
            </TechnicalDetails>
          ))}
        </Stack>
      </Section>
      {draftReview.baseVersion !== draftReview.expectedHeadVersion && (
        <Section title="Changes against the current published version">
          <Stack spacing={1.5}>
            {draftReview.headChanges && <ChangedFiles files={draftReview.headChanges} renderFile={(file, { open }) => open ? (
              <Box>
                <Typography variant="m3" sx={{ overflowWrap: "anywhere" }}>
                  {file.path}
                </Typography>
                <ReviewFileDiff file={file} fromTitle={`v${draftReview.expectedHeadVersion}`} toTitle="Your draft" />
              </Box>
            ) : (
              <TechnicalDetails title={`${file.path} · unchanged`}>
                <ReviewFileDiff file={file} fromTitle={`v${draftReview.expectedHeadVersion}`} toTitle="Your draft" />
              </TechnicalDetails>
            )} />}
            {!draftReview.headChanges && (
              <Alert severity="warning">
                Head comparison not loaded. Review it before acknowledging
                publication.
              </Alert>
            )}
          </Stack>
        </Section>
      )}
      <Section id="review-dependencies" title="Linked skills">
        <Stack spacing={1}>
          {draftReview.acceptedLock?.links.length ? (
            <Typography variant="s1">{draftReview.acceptedLock.links.map((link) => `${mountName(link.mount)} v${link.version}`).join(" · ")}</Typography>
          ) : (
            <Typography variant="s1">No linked skills.</Typography>
          )}
          <Typography variant="s3" color="text.secondary">
            Each linked skill is locked to the version shown. A newer upstream version never replaces it without your review.
          </Typography>
          <TechnicalDetails title="Linked skill versions">
            <Stack spacing={1}>
              {draftReview.acceptedLock?.links.map((link) => (
                <Stack key={link.skillId} spacing={0.5}>
                  <Typography variant="s3">{link.mount} · v{link.version}</Typography>
                  <IdentityChip label="Version" kind="id" value={link.versionId} />
                  <IdentityChip label="Digest" value={link.digest} />
                </Stack>
              ))}
            </Stack>
          </TechnicalDetails>
        </Stack>
      </Section>
      {/* A permission change is something to check, so it is open; "none found" stays one click away. */}
      <TechnicalDetails title="Permission changes" defaultOpen={draftReview.capabilities?.changes?.length > 0}><CapabilityChanges changes={draftReview.capabilities} summary /></TechnicalDetails>
      <Section id="review-checks" title="Checks">
        {draftReview.checks ? (
          <ChecksList checks={draftReview.checks} />
        ) : (
          <Alert severity="warning">
            Checks unavailable. Publication is blocked.
          </Alert>
        )}
      </Section>
      <Box id="review-publication">
        <TextField
          fullWidth
          size="small"
          multiline
          minRows={2}
          label="Version note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          disabled={locked}
          inputProps={{ maxLength: 2000 }}
          helperText="Optional"
        />
      </Box>
      {/* One sticky decision bar: the acknowledgments on the left; on the right the reason while a button waits,
          then Publish version, then Publish and request release. One click on either records the reviewed head and publishes. */}
      <Box ref={footer} data-sticky-footer sx={{ position: "sticky", bottom: 0, zIndex: 2, bgcolor: "background.paper", borderTop: 1, borderColor: "divider", minHeight: 64, py: 1, px: 1.5 }}>
        <Stack spacing={1.5}>
          {stalled ? (
            <Alert severity="warning" role="status">The draft changed since you reviewed it. Review the changes again.</Alert>
          ) : (
            <>
              <OperationFeedback operation={operation} />
              {oneStep !== operation && <OperationFeedback operation={oneStep} />}
            </>
          )}
          <DecisionFooterRow
            left={
              <Stack spacing={0.5} role="group" aria-label="Review acknowledgments" sx={{ flex: "1 1 100%", minWidth: 0 }}>
                <Acknowledgment
                  label={publishAcknowledgmentLabel(draftReview)}
                  disabled={locked}
                  acknowledgment={{
                    identity: draftReview.reviewFingerprint,
                    acknowledged: reviewed,
                    // Ticked for an earlier review of this draft: say why it is asked again.
                    changed: !reviewed && ackFingerprint !== null && ackFingerprint !== draftReview.reviewFingerprint,
                    setAcknowledged: (checked) => {
                      clearStale();
                      setAckFingerprint(checked ? draftReview.reviewFingerprint : null);
                      if (!checked && carriedHere) onCarry(null);
                    },
                  }}
                />
              </Stack>
            }
            reason={<ActionReason id="publish-review-reason" reason={acknowledgmentReason} sx={{ textAlign: { sm: "right" } }} />}
            secondary={
              <Button
                variant="outlined"
                {...reasonProps("publish-review-reason", acknowledgmentReason)}
                disabled={!ready || blocked(onPublish)}
                onClick={() => {
                  // Recorded before the review reloads and remounts for the same candidate.
                  carryAcknowledgments();
                  operation.run(onPublish, publishInput);
                }}
              >
                Publish version
              </Button>
            }
            primary={
              onPublishAndRequest && (
                // J4 one-step path: the same reviewed identity acknowledges the head when needed, publishes the
                // immutable candidate and opens the release-review request with it carried forward. Production
                // is not moved; sharing and submission stay explicit.
                <Button
                  variant="contained"
                  color="primary"
                  {...reasonProps("publish-review-reason", acknowledgmentReason)}
                  disabled={!ready || blocked(onPublishAndRequest)}
                  onClick={() => {
                    carryAcknowledgments();
                    oneStep.run(onPublishAndRequest, publishInput);
                  }}
                >
                  {PUBLISH_AND_REQUEST_RELEASE}
                </Button>
              )
            }
          />
        </Stack>
      </Box>
    </Stack>
  );
}
DraftPublication.propTypes = {
  name: PropTypes.string,
  releaseRules: PropTypes.object,
  carried: PropTypes.object,
  onCarry: PropTypes.func.isRequired,
  draftReview: PropTypes.object.isRequired,
  canPublish: PropTypes.bool,
  onPublish: PropTypes.func,
  onPublishAndRequest: PropTypes.func,
  publishOperation: PropTypes.object,
  oneStepOperation: PropTypes.object,
  onRefresh: PropTypes.func,
};

export default function ContextReleaseReview({
  scopeKey,
  state = "unavailable",
  name,
  draftReview,
  onRetry,
  ...props
}) {
  // Held above the publication review so the acknowledgements survive the head acknowledgment,
  // which reloads the review (and remounts it) for the same candidate bytes.
  const [carried, setCarried] = useState(null);
  return (
    <Box sx={{ px: { xs: 2, sm: 3 }, py: 2, minWidth: 0 }}>
      <ObjectHeader
        crumbs={draftReview?.subjectId
          ? [{ label: "Agent contexts", to: paths.dashboard.skills.contexts }, { label: name || "Agent context", to: paths.dashboard.skills.contextDetail(draftReview.subjectId) }, { label: "Publish" }]
          : [{ label: "Reviews", to: paths.dashboard.skills.reviews }, { label: name || "Agent context" }]}
        title={draftReview ? `Publish ${name || "agent context"} v${(draftReview.expectedHeadVersion ?? 0) + 1}` : name ? `Review ${name}` : "Review agent context"}
        purpose={draftReview ? "Check the saved changes and linked skills, then publish." : "Inspect the changes before making a release decision. Production stays unchanged until released."}
      />
      {!scopeKey || state !== "ready" ? (
        <ReviewState
          state={!scopeKey ? "forbidden" : state}
          onRetry={onRetry}
        />
      ) : draftReview ? (
        <DraftPublication
          key={`${scopeKey}:${draftReview.subjectId}:${draftReview.headAcknowledged}`}
          {...props}
          name={name}
          carried={carried}
          onCarry={setCarried}
          draftReview={draftReview}
        />
      ) : (
        <ReleaseRequestPanel {...props} {...{ scopeKey, state, onRetry }} />
      )}
    </Box>
  );
}
ContextReleaseReview.propTypes = {
  receipt: PropTypes.object,
  releaseRules: PropTypes.object,
  scopeKey: PropTypes.string,
  state: PropTypes.string,
  name: PropTypes.string,
  draftReview: PropTypes.object,
  preview: PropTypes.object,
  onRetry: PropTypes.func,
};
