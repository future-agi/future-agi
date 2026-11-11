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
import PageHeader from "src/components/page-header";
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
  draftReview,
  canPublish,
  onAcknowledgeHead,
  onPublish,
  onRefresh,
}) {
  const [note, setNote] = useState(draftReview.note ?? "");
  const [warningFingerprint, setWarningFingerprint] = useState(null);
  const warnings = warningFingerprint === draftReview.reviewFingerprint;
  const [ackFingerprint, setAckFingerprint] = useState(null);
  const operation = useReviewedOperation();
  const reviewed = ackFingerprint === draftReview.reviewFingerprint;
  const locked =
    operation.busy || operation.uncertain || Boolean(operation.result);
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
      <Section title="Immutable candidate">
        <Stack spacing={1}>
          <IdentityValue
            label={`Saved draft r${draftReview.revision}`}
            value={draftReview.candidateDigest}
          />
          <Typography variant="s1">
            Content origin{" "}
            {draftReview.baseVersion === null
              ? "new context"
              : `v${draftReview.baseVersion}`}{" "}
            · reviewed publication head{" "}
            {draftReview.expectedHeadVersion === null
              ? "none"
              : `v${draftReview.expectedHeadVersion}`}
          </Typography>
          <IdentityValue
            label="Reviewed head digest"
            value={draftReview.headDigest ?? "Unpublished"}
          />
          <Alert severity="info">
            Production stays{" "}
            {draftReview.productionVersion
              ? `on v${draftReview.productionVersion}`
              : "unassigned"}
            . Publication creates an immutable candidate; promotion requires a
            separate authorized review.
          </Alert>
          {draftReview.baseVersion !== draftReview.expectedHeadVersion && (
            <Alert severity="warning">
              This repair starts from historical content, not the latest
              release. Publishing makes this repair the new latest without
              incorporating unrelated head changes.
            </Alert>
          )}
        </Stack>
      </Section>
      <Section id="review-files" title="Changes from content origin">
        <Stack spacing={1.5}>
          {draftReview.changes.map((file) => (
            <Box key={file.path}>
              <Typography variant="m3" sx={{ overflowWrap: "anywhere" }}>
                {file.path}
              </Typography>
              <ReviewFileDiff file={file} />
            </Box>
          ))}
        </Stack>
      </Section>
      {draftReview.baseVersion !== draftReview.expectedHeadVersion && (
        <Section title="Candidate versus current head">
          <Stack spacing={1.5}>
            {draftReview.headChanges?.map((file) => (
              <Box key={file.path}>
                <Typography variant="m3" sx={{ overflowWrap: "anywhere" }}>
                  {file.path}
                </Typography>
                <ReviewFileDiff file={file} />
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
      <Section id="review-dependencies" title="Accepted dependency lock">
        <Stack spacing={1}>
          {draftReview.acceptedLock?.links.map((link) => (
            <IdentityValue
              key={link.skillId}
              label={`${link.mount} · included v${link.version}`}
              value={`${link.versionId} · ${link.digest}`}
            />
          ))}
          <Typography variant="s3">
            Upstream changes do not replace this accepted lock. Refreshing
            dependencies is an explicit draft mutation.
          </Typography>
        </Stack>
      </Section>
      <CapabilityChanges changes={draftReview.capabilities} />
      <Section title="Static checks">
        {draftReview.checks ? (
          <ChecksList checks={draftReview.checks} />
        ) : (
          <Alert severity="warning">
            Checks unavailable. Publication is blocked.
          </Alert>
        )}
      </Section>
      <Section id="review-publication" title="Publication note">
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
            label="I reviewed the saved candidate against its content origin and current publication head."
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
          <Typography variant="s3">
            Model evidence is not silently attached or shared by publishing.
            Submit an exact release packet with the selected shared evidence
            after publication.
          </Typography>
          <OperationFeedback operation={operation} />
          {operation.result && (
            <Alert severity="info">
              Operation recorded. Reload the acknowledged draft or immutable
              publication receipt before another action. Production was not
              moved.
            </Alert>
          )}
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
            {draftReview.headAcknowledged ? (
              <Button
                variant="contained"
                disabled={!ready || !onPublish}
                onClick={() =>
                  operation.run(onPublish, {
                    ...fields,
                    baseVersion: draftReview.baseVersion,
                    note,
                    acknowledgeWarnings: warnings,
                  })
                }
              >
                Publish version
              </Button>
            ) : (
              <Button
                variant="contained"
                disabled={
                  !ready ||
                  !onAcknowledgeHead ||
                  (draftReview.baseVersion !==
                    draftReview.expectedHeadVersion &&
                    !draftReview.headChanges)
                }
                onClick={() => operation.run(onAcknowledgeHead, fields)}
              >
                Acknowledge reviewed head
              </Button>
            )}
            {onRefresh && (
              <Button
                variant="outlined"
                disabled={operation.busy || operation.uncertain}
                onClick={onRefresh}
              >
                Inspect current draft
              </Button>
            )}
          </Stack>
        </Stack>
      </Section>
    </Stack>
  );
}
DraftPublication.propTypes = {
  draftReview: PropTypes.object.isRequired,
  canPublish: PropTypes.bool,
  onAcknowledgeHead: PropTypes.func,
  onPublish: PropTypes.func,
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
  return (
    <Box sx={{ px: { xs: 2, sm: 3 }, py: 2, minWidth: 0 }}>
      <PageHeader
        title={name ? `Review ${name}` : "Review agent context"}
        description="What was reviewed, what will change, and what remains unobserved."
      />
      {scopeKey && state === "ready" && (
        <Box
          component="nav"
          aria-label="Review sections"
          sx={{
            position: "sticky",
            top: 0,
            zIndex: 1,
            bgcolor: "background.paper",
            py: 1,
            mb: 2,
            borderBottom: 1,
            borderColor: "divider",
          }}
        >
          <Typography variant="s3" sx={{ overflowWrap: "anywhere" }}>
            {draftReview
              ? `Saved draft r${draftReview.revision}`
              : `Candidate v${props.preview?.packet.transition.version ?? "unknown"}`}{" "}
            ·{" "}
            {draftReview?.candidateDigest ??
              props.preview?.packet.candidateDigest}
          </Typography>
          <Stack direction="row" flexWrap="wrap" gap={0.5}>
            <Button size="small" href="#review-files">
              Files
            </Button>
            <Button size="small" href="#review-dependencies">
              Dependencies
            </Button>
            {draftReview ? (
              <Button size="small" href="#review-publication">
                Publish
              </Button>
            ) : (
              <>
                <Button size="small" href="#review-evidence">
                  Evidence
                </Button>
                <Button size="small" href="#review-impact">
                  Impact
                </Button>
              </>
            )}
          </Stack>
        </Box>
      )}
      {!scopeKey || state !== "ready" ? (
        <ReviewState
          state={!scopeKey ? "forbidden" : state}
          onRetry={onRetry}
        />
      ) : draftReview ? (
        <DraftPublication
          key={`${scopeKey}:${draftReview.subjectId}:${draftReview.headAcknowledged}`}
          {...props}
          draftReview={draftReview}
        />
      ) : (
        <ReleaseRequestPanel {...props} {...{ scopeKey, state, onRetry }} />
      )}
    </Box>
  );
}
ContextReleaseReview.propTypes = {
  scopeKey: PropTypes.string,
  state: PropTypes.string,
  name: PropTypes.string,
  draftReview: PropTypes.object,
  preview: PropTypes.object,
  onRetry: PropTypes.func,
};
