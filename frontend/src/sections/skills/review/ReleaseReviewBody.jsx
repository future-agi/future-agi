import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Link,
  Stack,
  Typography,
} from "@mui/material";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import Section from "src/components/section";
import StatusBadge from "src/components/status-badge";
import { ConfirmDialog } from "src/components/custom-dialog";
import TextDiff from "../components/TextDiff";
import ChecksList from "../components/ChecksList";
import CapabilityChanges from "./CapabilityChanges";

const TERMINAL = new Set([
  "completed",
  "failed",
  "cancelled",
  "timed_out",
  "interrupted",
]);
export const evidenceKey = (ref) => `${ref.source}:${ref.sourceId}`;
export const evidenceSelection = (ref) => ({
  source: ref.source,
  sourceId: ref.sourceId,
  annotationRevision: ref.annotationRevision,
});

// A time-driven guard, not just a check at render: an open review expires too.
export function useReviewClock() {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

export function evidenceProblems(ref, packet, now = Date.now()) {
  const reasons = [];
  if (
    ref.subjectId !== packet.subjectId ||
    ref.subjectKind !== packet.subjectKind ||
    ref.candidateDigest !== packet.candidateDigest
  )
    reasons.push("For different content");
  if (ref.visibility !== "workspace")
    reasons.push("Private — share explicitly before attaching");
  if (!TERMINAL.has(ref.status)) reasons.push("Run has not settled");
  if (
    !Number.isFinite(Date.parse(ref.payloadExpiresAt)) ||
    Date.parse(ref.payloadExpiresAt) <= now
  )
    reasons.push("Detailed evidence expired");
  if (ref.source !== "skills_text" && ref.source !== "simulation_native")
    reasons.push("Unsupported evidence source");
  if (
    ref.source === "skills_text" &&
    (!ref.configuration?.text || ref.configuration?.native)
  )
    reasons.push("Text configuration unavailable");
  if (ref.source === "simulation_native") {
    if (!ref.configuration?.native || ref.configuration?.text)
      reasons.push("Native configuration unavailable");
    if (
      !packet.target ||
      ref.configuration?.digest !== packet.target.configurationDigest
    )
      reasons.push("Runtime configuration does not match the target");
  }
  return reasons;
}

export function IdentityValue({ label, value }) {
  return (
    <Stack spacing={0.25} sx={{ minWidth: 0 }}>
      <Typography variant="s3" color="text.secondary">
        {label}
      </Typography>
      <Typography
        variant="s1"
        component="div"
        sx={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}
      >
        {value ?? "Unknown"}
      </Typography>
    </Stack>
  );
}
IdentityValue.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node,
};

export function ReviewState({ state, onRetry }) {
  if (state === "loading")
    return <ContentSkeleton label="Loading review" rows={4} />;
  if (state === "error")
    return (
      <ErrorState
        title="Could not load review"
        detail="No release action was taken. Retry to check current access and state."
        onRetry={onRetry}
      />
    );
  if (state === "empty")
    return (
      <EmptyState
        variant="no-results"
        title="Nothing to review"
        description="Publish an immutable candidate before requesting a release decision."
      />
    );
  const messages = {
    forbidden:
      "You no longer have access to this review. Private details and counts are hidden.",
    unavailable:
      "This review capability is not available. No release action can be taken here.",
    expired:
      "Detailed evidence expired. Review a new evidence set before releasing.",
    conflict:
      "The reviewed state changed. Inspect a new review; no preconditions were refreshed automatically.",
    stale:
      "This review is no longer current. Inspect the changed target, evidence or annotations before continuing.",
  };
  return messages[state] ? (
    <Alert severity="warning">{messages[state]}</Alert>
  ) : null;
}
ReviewState.propTypes = { state: PropTypes.string, onRetry: PropTypes.func };

export function ReviewFileDiff({ file }) {
  if (file.fromText === undefined || file.toText === undefined)
    return (
      <Alert severity="info">
        Exact file text has not loaded. Open this file to retrieve the complete
        revision-bound diff.
      </Alert>
    );
  return <TextDiff fromText={file.fromText} toText={file.toText} fromTitle="Before" toTitle="Candidate" />;
}
ReviewFileDiff.propTypes = { file: PropTypes.object.isRequired };

export function TypedEvidenceList({
  packet,
  evidence,
  selected,
  onSelectionChange,
  onShare,
  onInspect,
  summaries = [],
}) {
  const now = useReviewClock();
  const [sharing, setSharing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const choose = (ref, checked) =>
    onSelectionChange(
      checked
        ? [...selected, evidenceSelection(ref)]
        : selected.filter((item) => evidenceKey(item) !== evidenceKey(ref)),
    );
  const missing = selected.filter(
    (item) => !evidence.some((ref) => evidenceKey(ref) === evidenceKey(item)),
  );
  return (
    <Stack spacing={1.5}>
      <Typography variant="s3" color="text.secondary">
        Tests are advisory. Exact content is an identity match, not approval.
        Text and native evidence are separate; neither requires a second paid
        run.
      </Typography>
      {missing.map((item) => (
        <Alert
          key={evidenceKey(item)}
          severity="warning"
          action={
            onSelectionChange && (
              <Button onClick={() => choose(item, false)}>
                Remove explicitly
              </Button>
            )
          }
        >
          Selected evidence {item.sourceId} is unavailable. It will not be
          silently dropped.
        </Alert>
      ))}
      {!evidence.length && (
        <Typography variant="s1">
          No evidence selected. Not tested is not a pass.
        </Typography>
      )}
      {evidence.map((ref) => {
        const choice = selected.find(
          (item) => evidenceKey(item) === evidenceKey(ref),
        );
        const reasons = evidenceProblems(ref, packet, now);
        if (choice && choice.annotationRevision !== ref.annotationRevision)
          reasons.push(
            "Human annotations changed — select the new revision after review",
          );
        const summary = summaries.find(
          (item) => evidenceKey(item) === evidenceKey(ref),
        );
        return (
          <Section
            key={evidenceKey(ref)}
            variant="panel"
            title={
              ref.source === "skills_text"
                ? "Text comparison"
                : "Native application evidence"
            }
          >
            <Stack spacing={1}>
              <Stack direction="row" spacing={1} flexWrap="wrap">
                <StatusBadge status={ref.status} />
                <Typography variant="s3">
                  {ref.visibility} · annotation r{ref.annotationRevision}
                </Typography>
              </Stack>
              <Typography variant="s1">
                {ref.source === "skills_text"
                  ? "Text-only comparison. No scripts or external actions."
                  : "Answer, child selection, file delivery and tool execution are separate outcomes. Completion is not success."}
              </Typography>
              <IdentityValue label="Evidence ID" value={ref.sourceId} />
              <IdentityValue
                label="Execution fingerprint"
                value={ref.executionFingerprint}
              />
              <IdentityValue
                label="Payload expires"
                value={ref.payloadExpiresAt}
              />
              {summary ? (
                <Stack spacing={0.5}>
                  <Typography variant="s1">
                    {summary.graded ?? "Unknown"}/{summary.planned ?? "Unknown"}{" "}
                    attempts graded · {summary.incomplete ?? "Unknown"}{" "}
                    incomplete · {summary.regressions ?? "Unknown"} regressions
                  </Typography>
                  {["answer", "selection", "delivery", "tools"].map((key) => (
                    <IdentityValue
                      key={key}
                      label={`${key[0].toUpperCase()}${key.slice(1)} outcome`}
                      value={summary[key] ?? "Not observed"}
                    />
                  ))}
                  {summary.disagreements > 0 && (
                    <Alert severity="warning">
                      {summary.disagreements} human disagreement(s) require
                      review. Machine grades have not changed.
                    </Alert>
                  )}
                </Stack>
              ) : (
                <Typography variant="s3">
                  Outcome details not loaded. Do not infer success from a
                  terminal status.
                </Typography>
              )}
              <Box component="details">
                <Typography component="summary" variant="s1">
                  Frozen execution configuration
                </Typography>
                <Typography
                  component="pre"
                  variant="s3"
                  sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                >
                  {JSON.stringify(ref.configuration, null, 2)}
                </Typography>
              </Box>
              {reasons.map((reason) => (
                <Alert severity="warning" key={reason}>
                  {reason}
                </Alert>
              ))}
              <Stack direction="row" spacing={1} flexWrap="wrap">
                {onInspect && (
                  <Button onClick={() => onInspect(ref)}>
                    Inspect results
                  </Button>
                )}
                {ref.visibility === "private" && onShare && (
                  <Button
                    variant="outlined"
                    disabled={
                      !TERMINAL.has(ref.status) ||
                      Date.parse(ref.payloadExpiresAt) <= now
                    }
                    onClick={() => {
                      setSharing(ref);
                      setError("");
                    }}
                  >
                    Share evidence
                  </Button>
                )}
              </Stack>
              {onSelectionChange && (
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={Boolean(choice)}
                      disabled={!choice && reasons.length > 0}
                      onChange={(_, checked) => choose(ref, checked)}
                    />
                  }
                  label={`Attach ${ref.source === "skills_text" ? "text" : "native"} evidence ${ref.sourceId}`}
                />
              )}
            </Stack>
          </Section>
        );
      })}
      <ConfirmDialog
        open={Boolean(sharing)}
        onClose={() => {
          if (!busy) setSharing(null);
        }}
        closeDisabled={busy}
        title="Share evidence with workspace?"
        content="Share the frozen draft, example inputs, model answers and file receipts with workspace members. Sharing does not extend expiry, attach evidence or release content."
        action={
          <Button
            variant="contained"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onShare({
                  ...evidenceSelection(sharing),
                  acknowledgeSnapshotSharing: true,
                });
                setSharing(null);
              } catch {
                setError(
                  "Could not confirm sharing. Refresh the evidence state before attaching.",
                );
              } finally {
                setBusy(false);
              }
            }}
          >
            Share with workspace
          </Button>
        }
      />
      {error && <Alert severity="error">{error}</Alert>}
    </Stack>
  );
}
TypedEvidenceList.propTypes = {
  packet: PropTypes.object.isRequired,
  evidence: PropTypes.array.isRequired,
  selected: PropTypes.array.isRequired,
  onSelectionChange: PropTypes.func,
  onShare: PropTypes.func,
  onInspect: PropTypes.func,
  summaries: PropTypes.array,
};

export function ExternalPipeline({ reference }) {
  if (!reference)
    return (
      <Typography variant="s3">
        No external pipeline reference recorded. Retain your existing CI and
        release gates.
      </Typography>
    );
  let href = null;
  try {
    const url = new URL(reference.url);
    if (url.protocol === "https:" && !url.username && !url.password)
      href = url.href;
  } catch {
    /* Invalid references are displayed as text, never active URLs. */
  }
  return (
    <Stack spacing={1}>
      <Alert severity="info">
        Operator-recorded, unimported result. This is not a verified Hub verdict
        and does not bypass your external release gates.
      </Alert>
      <IdentityValue
        label="Configuration identity"
        value={reference.configurationIdentity}
      />
      <IdentityValue label="Operator decision" value={reference.decision} />
      <IdentityValue label="Recorded by" value={reference.recordedBy} />
      {href ? (
        <Link href={href} target="_blank" rel="noopener noreferrer">
          Open retained pipeline result
        </Link>
      ) : (
        <Typography variant="s3">External link unavailable.</Typography>
      )}
    </Stack>
  );
}
ExternalPipeline.propTypes = { reference: PropTypes.object };

// Controlled presentation only. API mapping/caching and source authorization stay
// in L7; this component never resolves a label or manufactures an evidence ref.
export default function ReleaseReviewBody({
  packet,
  changes = [],
  dependencyChanges = [],
  checks,
  capabilities,
  impact,
  evidence = packet.evidence,
  selected = packet.evidence.map(evidenceSelection),
  onSelectionChange,
  onShare,
  onInspect,
  onOpenFile,
  summaries,
  moreChanges = false,
  onLoadMore,
}) {
  const ordered = [...changes].sort((a, b) =>
    a.path === "AGENTS.md" || a.path === "SKILL.md"
      ? -1
      : b.path === "AGENTS.md" || b.path === "SKILL.md"
        ? 1
        : (a.status === "unchanged") - (b.status === "unchanged") ||
          a.path.localeCompare(b.path),
  );
  return (
    <Stack spacing={3} sx={{ minWidth: 0 }}>
      <Section title="Exact release">
        <Stack spacing={1}>
          <Typography variant="m3">
            Move {packet.transition.label} from{" "}
            {packet.transition.expectedVersion === null
              ? "unassigned"
              : `v${packet.transition.expectedVersion}`}{" "}
            to v{packet.transition.version}
          </Typography>
          <IdentityValue
            label="Candidate version ID"
            value={packet.candidateVersionId}
          />
          <IdentityValue
            label="Candidate digest"
            value={packet.candidateDigest}
          />
          <Typography variant="s3">
            Release r{packet.transition.expectedReleaseRevision} · security r
            {packet.transition.expectedSecurityRevision}
          </Typography>
          {packet.target ? (
            <>
              <IdentityValue
                label="Target binding or environment"
                value={packet.target.bindingId ?? packet.target.environmentId}
              />
              <IdentityValue
                label={`Target configuration r${packet.target.revision}`}
                value={packet.target.configurationDigest}
              />
            </>
          ) : (
            <Typography variant="s3">
              No application target selected. Native applicability cannot be
              established.
            </Typography>
          )}
          <Alert severity="info">
            Publishing and approving do not move production. Existing threads
            and checked-in locks may still use another version after a label
            moves.
          </Alert>
        </Stack>
      </Section>
      <Section id="review-files" title="File changes">
        <Stack spacing={1.5}>
          {!ordered.length && (
            <Typography variant="s3">No file changes loaded.</Typography>
          )}
          {ordered.map((file) => (
            <Box
              component="details"
              key={file.path}
              open={file.path === "AGENTS.md" || file.path === "SKILL.md"}
            >
              <Typography
                component="summary"
                variant="m3"
                sx={{ overflowWrap: "anywhere" }}
              >
                {file.path} · {file.status}
              </Typography>
              {onOpenFile && (
                <Button onClick={() => onOpenFile(file.path)}>
                  Load complete diff
                </Button>
              )}
              <ReviewFileDiff file={file} />
            </Box>
          ))}
          {moreChanges && (
            <Button
              variant="outlined"
              disabled={!onLoadMore}
              onClick={onLoadMore}
            >
              Load more changed files
            </Button>
          )}
          {moreChanges && (
            <Typography variant="s3">
              More changes exist. This is not the complete file inventory.
            </Typography>
          )}
        </Stack>
      </Section>
      <Section id="review-dependencies" title="Accepted dependencies">
        <Stack spacing={1}>
          {!dependencyChanges.length && (
            <Typography variant="s3">No dependency changes loaded.</Typography>
          )}
          {dependencyChanges.map((item) => (
            <Box key={item.skillId}>
              <IdentityValue
                label={`${item.mount} · ${item.change}`}
                value={`Included ${item.before ? `v${item.before.version}` : "none"} → ${item.after ? `v${item.after.version}` : "none"}`}
              />
              <Typography variant="s3">
                Upstream{" "}
                {item.upstreamVersion
                  ? `v${item.upstreamVersion}`
                  : "unavailable"}
                ; the accepted lock does not follow new bytes automatically.
              </Typography>
            </Box>
          ))}
        </Stack>
      </Section>
      <CapabilityChanges changes={capabilities} onOpenFile={onOpenFile} />
      <Section title="Static checks">
        {checks ? (
          <ChecksList checks={checks} />
        ) : (
          <Typography variant="s3">
            Checks unavailable. This is not a successful check.
          </Typography>
        )}
      </Section>
      <Section id="review-evidence" title="Evidence">
        <TypedEvidenceList
          {...{
            packet,
            evidence,
            selected,
            onSelectionChange,
            onShare,
            onInspect,
            summaries,
          }}
        />
      </Section>
      <Section title="Retained regression pipeline">
        <ExternalPipeline reference={packet.externalPipeline} />
      </Section>
      <Section id="review-impact" title="Known impact">
        {impact ? (
          <Stack spacing={1}>
            <Typography variant="s1">
              {impact.following} followers · {impact.pinned} intentional pins ·{" "}
              {impact.unobserved} unobserved bindings
            </Typography>
            <IdentityValue
              label="Reviewed impact fingerprint"
              value={packet.transition.expectedImpactFingerprint}
            />
            <Typography variant="s3">
              Only authorized declared bindings are counted.{" "}
              {impact.moreAvailable
                ? "Additional impact rows require inspection."
                : "Offline and unregistered copies remain unknown."}
            </Typography>
          </Stack>
        ) : (
          <Typography variant="s3">
            Impact not loaded. No fleet coverage claim.
          </Typography>
        )}
      </Section>
    </Stack>
  );
}
ReleaseReviewBody.propTypes = {
  packet: PropTypes.object.isRequired,
  changes: PropTypes.array,
  dependencyChanges: PropTypes.array,
  checks: PropTypes.object,
  capabilities: PropTypes.object,
  impact: PropTypes.object,
  evidence: PropTypes.array,
  selected: PropTypes.array,
  onSelectionChange: PropTypes.func,
  onShare: PropTypes.func,
  onInspect: PropTypes.func,
  onOpenFile: PropTypes.func,
  summaries: PropTypes.array,
  moreChanges: PropTypes.bool,
  onLoadMore: PropTypes.func,
};
