import { useState } from "react";
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
import ObjectHeader from "../components/ObjectHeader";
import ActionReason, { reasonProps } from '../components/ActionReason';
import IdentityChip from "../components/IdentityChip";
import TechnicalDetails from "../components/TechnicalDetails";
import { mountName, publishSentence } from "../utils/conclusions";

const DESCRIPTOR_FILE = ".futureagi/context.json";
import { paths } from "src/routes/paths";
import Section from "src/components/section";
import ChecksList from "../components/ChecksList";
import ReleaseRequestPanel, {
  OperationFeedback,
  useReviewedOperation,
} from "../review/ReleaseRequestPanel";
import {
  IdentityValue,
  ReviewFileDiff,
  ReviewState,
} from "../review/ReleaseReviewBody";
import CapabilityChanges from "../review/CapabilityChanges";

function DraftPublication({
  name,
  carried,
  onCarry,
  draftReview,
  canPublish,
  onAcknowledgeHead,
  onPublish,
  onPublishAndRequest,
  oneStepOperation,
  onRefresh,
}) {
  const [warningFingerprint, setWarningFingerprint] = useState(null);
  const [ackFingerprint, setAckFingerprint] = useState(null);
  const operation = useReviewedOperation();
  // Acknowledging the head issues a new review fingerprint for the very same candidate bytes.
  // The reviewer's two acknowledgements carry across that one step, and only when the candidate,
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
  const warnings =
    warningFingerprint === draftReview.reviewFingerprint ||
    (carriedHere && carried.warned);
  const reviewed =
    ackFingerprint === draftReview.reviewFingerprint ||
    (carriedHere && carried.reviewed);
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
  const fields = {
    revision: draftReview.revision,
    expectedDigest: draftReview.candidateDigest,
    expectedHeadVersion: draftReview.expectedHeadVersion,
    reviewFingerprint: draftReview.reviewFingerprint,
  };
  return (
    <Stack spacing={3}>
      <Section title="What publishing creates">
        <Stack spacing={1}>
          {/* Conclusion first: the version this creates and what changes in it, in words. */}
          <Typography variant="m3" role="status">{publishSentence(name, draftReview)}</Typography>
          <Typography variant="s1">
            Based on {draftReview.baseVersion === null ? "a new context" : `v${draftReview.baseVersion}`}.
            Production stays {draftReview.productionVersion ? `on v${draftReview.productionVersion}` : "unassigned"} until a separate review releases it.
          </Typography>
          {draftReview.baseVersion !== draftReview.expectedHeadVersion && (
            <Alert severity="warning">
              This repair starts from v{draftReview.baseVersion}, not the latest v{draftReview.expectedHeadVersion}. Publishing makes it the new latest without the other changes in v{draftReview.expectedHeadVersion}.
            </Alert>
          )}
          <TechnicalDetails title="Exact identities">
            <Stack spacing={1}>
              <IdentityValue label="Saved draft" value={draftReview.candidateDigest} />
              <IdentityValue label="Latest published version" value={draftReview.headDigest ?? "Unpublished"} />
            </Stack>
          </TechnicalDetails>
        </Stack>
      </Section>
      <Section id="review-files" title={draftReview.baseVersion == null ? "Changes in your draft" : `Changes since v${draftReview.baseVersion}`}>
        <Stack spacing={1.5}>
          {/* Files people read come first; the machine descriptor's exact diff is one click away. */}
          {draftReview.changes.filter((file) => file.path !== DESCRIPTOR_FILE).map((file) => (
            <Box key={file.path}>
              <Typography variant="m3" sx={{ overflowWrap: "anywhere" }}>
                {file.path}
              </Typography>
              <ReviewFileDiff file={file} fromTitle={draftReview.baseVersion == null ? "New context" : `v${draftReview.baseVersion}`} toTitle="Your draft" />
            </Box>
          ))}
          {draftReview.changes.filter((file) => file.path === DESCRIPTOR_FILE).map((file) => (
            <TechnicalDetails key={file.path} title={`Raw ${file.path} change`}>
              <ReviewFileDiff file={file} fromTitle={draftReview.baseVersion == null ? "New context" : `v${draftReview.baseVersion}`} toTitle="Your draft" />
            </TechnicalDetails>
          ))}
        </Stack>
      </Section>
      {draftReview.baseVersion !== draftReview.expectedHeadVersion && (
        <Section title="Changes against the current published version">
          <Stack spacing={1.5}>
            {draftReview.headChanges?.map((file) => (
              <Box key={file.path}>
                <Typography variant="m3" sx={{ overflowWrap: "anywhere" }}>
                  {file.path}
                </Typography>
                <ReviewFileDiff file={file} fromTitle={`v${draftReview.expectedHeadVersion}`} toTitle="Your draft" />
              </Box>
            ))}
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
          <TechnicalDetails title="Locked versions">
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
      <TechnicalDetails title="Permissions comparison"><CapabilityChanges changes={draftReview.capabilities} summary /></TechnicalDetails>
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
        <Stack spacing={1.5}>
          <TextField
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
          <FormControlLabel
            control={
              <Checkbox
                checked={reviewed}
                disabled={locked}
                onChange={(_, checked) =>
                  setAckFingerprint(
                    checked ? draftReview.reviewFingerprint : null,
                  )
                }
              />
            }
            label="I reviewed these changes against the current published version."
          />
          {draftReview.checks?.summary.warnings > 0 && (
            <FormControlLabel
              control={
                <Checkbox
                  checked={warnings && reviewed}
                  disabled={locked || !reviewed}
                  onChange={(_, checked) =>
                    setWarningFingerprint(
                      checked ? draftReview.reviewFingerprint : null,
                    )
                  }
                />
              }
              label="I acknowledge the static warnings for this exact candidate."
            />
          )}
          <OperationFeedback operation={operation} />
          {oneStep !== operation && <OperationFeedback operation={oneStep} />}
          {operation.result && (
            <Alert severity="info">
              Operation recorded. Reload the acknowledged draft or immutable
              publication receipt before another action. Production was not
              moved.
            </Alert>
          )}
          <Box>
            {/* One reason, above the row, while the publish actions wait for the review confirmation. */}
            <ActionReason id="publish-review-reason" reason={!draftReview.headAcknowledged && 'Confirm the review first'} sx={{ display: 'block', mb: 1 }} />
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: 'stretch', sm: 'center' }}>
              {onRefresh && <Button disabled={operation.busy || operation.uncertain} onClick={onRefresh} sx={{ ml: { sm: -1 }, alignSelf: { xs: 'flex-start', sm: 'center' } }}>Reload draft</Button>}
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: 'stretch', sm: 'center' }} sx={{ ml: { sm: 'auto' } }}>
                {draftReview.headAcknowledged ? <Typography variant="s3" role="status" sx={{ px: { sm: 1 } }}>Review confirmed</Typography> : (
                  <Button
                    variant="outlined"
                    color="primary"
                    disabled={
                      !ready ||
                      !onAcknowledgeHead ||
                      (draftReview.baseVersion !==
                        draftReview.expectedHeadVersion &&
                        !draftReview.headChanges)
                    }
                    onClick={() =>
                      operation.run(async (input) => {
                        const acknowledged = await onAcknowledgeHead(input);
                        // Recorded before the review reloads and remounts for the same candidate.
                        onCarry({
                          reviewed,
                          note,
                          warned: warnings,
                          candidateDigest: draftReview.candidateDigest,
                          baseVersion: draftReview.baseVersion,
                          expectedHeadVersion: draftReview.expectedHeadVersion,
                          headDigest: draftReview.headDigest,
                          warnings: draftReview.checks?.summary.warnings ?? null,
                        });
                        return acknowledged;
                      }, fields)
                    }
                  >
                    Confirm review
                  </Button>
                )}
                <Button variant="outlined" {...reasonProps('publish-review-reason', !draftReview.headAcknowledged && 'Confirm the review first')} disabled={!draftReview.headAcknowledged || !ready || !onPublish}
                  onClick={() => operation.run(onPublish, { ...fields, baseVersion: draftReview.baseVersion, note, acknowledgeWarnings: warnings })}>
                  Publish version
                </Button>
                {/* J4 one-step path: the same reviewed identity acknowledges the
                    head when needed, publishes the immutable candidate and opens
                    the release-review request with it carried forward. Production
                    is not moved; sharing and submission stay explicit. */}
                {onPublishAndRequest && (
                  <Button
                    variant="contained"
                    color="primary"
                    disabled={
                      !ready ||
                      (draftReview.baseVersion !==
                        draftReview.expectedHeadVersion &&
                        !draftReview.headChanges)
                    }
                    onClick={() =>
                      oneStep.run(onPublishAndRequest, {
                        ...fields,
                        headAcknowledged: draftReview.headAcknowledged === true,
                        baseVersion: draftReview.baseVersion,
                        note,
                        acknowledgeWarnings: warnings,
                      })
                    }
                  >
                    Publish and request review
                  </Button>
                )}
              </Stack>
            </Stack>
          </Box>
        </Stack>
      </Box>
    </Stack>
  );
}
DraftPublication.propTypes = {
  name: PropTypes.string,
  carried: PropTypes.object,
  onCarry: PropTypes.func.isRequired,
  draftReview: PropTypes.object.isRequired,
  canPublish: PropTypes.bool,
  onAcknowledgeHead: PropTypes.func,
  onPublish: PropTypes.func,
  onPublishAndRequest: PropTypes.func,
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
        purpose={draftReview ? "Review the saved changes and linked skills before publishing a version. Production stays unchanged until a separate release review." : "Inspect the changes before making a release decision. Production stays unchanged until released."}
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
  scopeKey: PropTypes.string,
  state: PropTypes.string,
  name: PropTypes.string,
  draftReview: PropTypes.object,
  preview: PropTypes.object,
  onRetry: PropTypes.func,
};
