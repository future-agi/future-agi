import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Checkbox,
  FormControlLabel,
  Link,
  Stack,
  Typography,
  Tooltip,
  Table, TableHead, TableBody, TableRow, TableCell, TableContainer,
} from "@mui/material";
import EvidenceSummary from '../components/EvidenceSummary';
import { evidenceTitle, relativeReviewTime, outcomeLabel } from '../utils/review-display';
import { impactSentence, mountName, nativeCaseLines, nativeSummaryConclusion } from '../utils/conclusions';
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import CapabilityNotice from "../components/CapabilityNotice";
import IdentityChip from "../components/IdentityChip";
import TechnicalDetails from "../components/TechnicalDetails";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import Section from "src/components/section";
import StatusBadge from "src/components/status-badge";
import { ConfirmDialog } from "src/components/custom-dialog";
import TextDiff from "../components/TextDiff";
import ChecksList from "../components/ChecksList";
import CapabilityChanges from "./CapabilityChanges";
import SummaryTable from "./SummaryTable";
import VersionLabel from "../components/VersionLabel";
import { counted, responsiveEvidenceSx } from "./reviewPresentation";

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
  if (typeof value === 'string' && (/^(?:sha256:)?[a-f0-9]{64}$/i.test(value) || /^[a-f0-9]{8}-[a-f0-9-]{27}$/i.test(value))) return <IdentityChip label={label} value={value} kind={value.includes('-') ? 'id' : 'digest'} />;
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
  if (state === "off" || state === "unavailable") return <CapabilityNotice title="Release reviews are not available" description="You can still inspect published versions. Ask an admin about review access for this workspace." />;
  if (state === "denied" || state === "forbidden") return <ErrorState title="You no longer have access to this review." detail="Private details are hidden. Ask an admin to check your access." />;
  if (state === "loading")
    return <ContentSkeleton variant="panel" label="Loading review" rows={4} />;
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

export function ReviewFileDiff({ file, fromTitle = "Production", toTitle = "This release" }) {
  if (file.fromText === undefined || file.toText === undefined)
    return (
      <Alert severity="info">
        Exact file text has not loaded. Open this file to retrieve the complete
        revision-bound diff.
      </Alert>
    );
  return <TextDiff fromText={file.fromText} toText={file.toText} fromTitle={fromTitle} toTitle={toTitle} />;
}
ReviewFileDiff.propTypes = { file: PropTypes.object.isRequired, fromTitle: PropTypes.string, toTitle: PropTypes.string };

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
                ? "Answer check"
                : "Application test"
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
              <Tooltip title={ref.payloadExpiresAt || ''}><Typography variant="s3" color="text.secondary">Expires {relativeReviewTime(ref.payloadExpiresAt) || 'at an unknown time'}</Typography></Tooltip>
              {summary ? (
                <Stack spacing={0.5}>
                  <NativeOutcomes summary={summary} />
                  <Typography variant="s1">
                    {summary.graded ?? "Unknown"}/{summary.planned ?? "Unknown"}{" "}
                    attempts graded · {summary.incomplete ?? "Unknown"}{" "}
                    incomplete · {summary.regressions ?? "Unknown"} regressions
                  </Typography>
                  {["answer", "selection", "delivery", "tools"].map((key) => (
                    <IdentityValue
                      key={key}
                      label={`${key[0].toUpperCase()}${key.slice(1)} outcome`}
                      value={outcomeLabel(summary[key]) ?? "Not observed"}
                    />
                  ))}
                  {summary.availability === 'unavailable' && <Alert severity="warning">Verified outcome details are unavailable. A completed run is not a passing result.</Alert>}
                  {summary.cannotAssess > 0 && <Alert severity="warning">{summary.cannotAssess} human assessment(s) remain inconclusive.</Alert>}
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
              <TechnicalDetails title="Frozen execution configuration">
                <Typography
                  component="pre"
                  variant="s3"
                  sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                >
                  {JSON.stringify(ref.configuration, null, 2)}
                </Typography>
              </TechnicalDetails>
              {reasons.map((reason) => (
                reason.startsWith('Private') ? <Typography variant="s3" color="text.secondary" key={reason}>Only you can see this result. Share it with the workspace to attach it.</Typography> : <Alert severity="warning" key={reason}>{reason}</Alert>
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
          <Button color="primary"
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

// A completed application run carried into the packet: its own headline and per-case outcomes.
function NativeOutcomes({ summary }) {
  const conclusion = nativeSummaryConclusion(summary);
  if (!conclusion) return null;
  return <Stack spacing={0.25} sx={{ minWidth: 0 }}>
    <Typography variant="s2">{conclusion}</Typography>
    {nativeCaseLines(summary.cases).map((line) => <Typography key={line.caseId} variant="s3" color="text.secondary">{`${line.title}: ${line.text}`}</Typography>)}
  </Stack>;
}
NativeOutcomes.propTypes = { summary: PropTypes.object };

// Controlled presentation only. API mapping/caching and source authorization stay
// in L7; this component never resolves a label or manufactures an evidence ref.
export default function ReleaseReviewBody({
  packet,
  changes,
  dependencyChanges,
  dependencyError = false,
  onRetryDependencies,
  checks,
  capabilities,
  targetConfiguration,
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
  availableEvidence,
}) {
  const [inspecting, setInspecting] = useState(null);
  const ordered = [...(changes || [])].sort((a, b) =>
    a.path === "AGENTS.md" || a.path === "SKILL.md"
      ? -1
      : b.path === "AGENTS.md" || b.path === "SKILL.md"
        ? 1
        : (a.status === "unchanged") - (b.status === "unchanged") ||
          a.path.localeCompare(b.path),
  );
  const checksEmpty = checks && checks.items?.length === 0 && checks.summary?.errors === 0 && checks.summary?.warnings === 0 && capabilities?.changes.length === 0 && !packet.externalPipeline;
  const isContext = packet.subjectKind === 'agent_context';
  const changedFiles = changes ? changes.filter((file) => file.status !== 'unchanged').length : null;
  // The summary table states each fact once; everything below it is detail the table cannot hold.
  const evidenceFact = evidence.length ? `${counted(evidence.length, 'test result')} attached`
    : availableEvidence > 0 ? `No test results attached (${availableEvidence} available).`
      : availableEvidence === 0 ? 'No matching test evidence. Not tested is not a pass.'
        : 'No test results attached. Not tested is not a pass.';
  const filesFact = changedFiles == null ? 'File changes not loaded'
    : `${counted(changedFiles, 'file')} changed${isContext ? ` · ${dependencyChanges ? `${counted(dependencyChanges.length, 'linked skill')} changed` : 'Linked skills not loaded'}` : ''}`;
  return (
    <Stack spacing={3} sx={{ minWidth: 0, ...responsiveEvidenceSx }}>
      <Stack spacing={1.5} sx={{ minWidth: 0 }}>
        <Stack spacing={0.5}>
          <Typography variant="s1" sx={{ fontWeight: 600 }}>{packet.transition.label.charAt(0).toUpperCase() + packet.transition.label.slice(1)} moves {packet.transition.expectedVersion == null ? 'from no release' : `v${packet.transition.expectedVersion}`} → v{packet.transition.version}</Typography>
          <Typography variant="s3" color="text.secondary">Production stays unchanged until approval and release. Check Consumers to confirm delivery.</Typography>
        </Stack>
        <SummaryTable label="Release summary" rows={[
          { label: 'Evidence', value: evidenceFact },
          { label: 'Who is affected', value: impact ? impactSentence(impact) : 'Application impact has not been measured.' },
          { label: 'Permissions', value: capabilities ? (capabilities.changes.length ? counted(capabilities.changes.length, 'change') : 'No permission changes found.') : 'Comparison unavailable. Review the file changes before deciding.' },
          { label: 'Files', value: filesFact },
          // The full configuration digest is one hover or focus away, never primary text; Technical details holds the copyable value and the revision.
          packet.target && { label: 'Application target', value: <Tooltip title={packet.target.configurationDigest || ''} describeChild><span tabIndex={0}>App configuration</span></Tooltip> },
        ]} />
      </Stack>
      {(evidence.length > 0 || selected.some((ref) => !evidence.some((item) => evidenceKey(item) === evidenceKey(ref)))) && <Section id="review-evidence" title="Test results">
        <EvidenceSummary items={evidence.map((ref) => {
          const summary = summaries?.find((item) => evidenceKey(item) === evidenceKey(ref));
          return { id: ref.sourceId, source: ref.source, resultState: ref.resultState, title: evidenceTitle(ref, packet.transition.version, packet.transition.expectedVersion), outcome: ref.outcome || (ref.source === 'skills_text' ? summary?.answer : undefined), results: ref.source === 'simulation_native' && summary ? ['answer', 'selection', 'delivery', 'tools'].map((key) => ({ label: key.charAt(0).toUpperCase() + key.slice(1), status: summary[key] })) : undefined, status: ref.status, visibility: ref.visibility, expiresAt: ref.payloadExpiresAt, counts: ref.counts || (summary && { graded: summary.graded, planned: summary.planned, regressed: summary.regressions, disagreements: summary.disagreements }) };
        })} renderAction={(item) => {
          // Each row states its outcome once (the Coverage cell carries the counts). The evidence ID and the
          // exact grading counts are one Inspect away, where the reviewer reads this result in full.
          const summary = summaries?.find((row) => evidenceKey(row) === `${item.source}:${item.id}`);
          return <NativeOutcomes summary={summary} />;
        }} onInspect={(item) => { setInspecting(`${item.source}:${item.id}`); onInspect?.(evidence.find((ref) => evidenceKey(ref) === `${item.source}:${item.id}`)); }} />
        {selected.filter((ref) => !evidence.some((item) => evidenceKey(item) === evidenceKey(ref))).map((ref) => <Alert key={evidenceKey(ref)} severity="warning">Selected evidence {ref.sourceId} is unavailable. It will not be silently dropped.</Alert>)}
        {inspecting && <TechnicalDetails title="Inspected result" defaultOpen><TypedEvidenceList {...{ packet, onShare, summaries }} selected={selected.filter((ref) => evidenceKey(ref) === inspecting)} onSelectionChange={onSelectionChange ? (refs) => onSelectionChange([...selected.filter((ref) => evidenceKey(ref) !== inspecting), ...refs]) : undefined} evidence={evidence.filter((ref) => evidenceKey(ref) === inspecting)} /></TechnicalDetails>}
      </Section>}
      <Section id="review-files" title="What changes">
        <Stack spacing={1.5}>
          {!ordered.length && (
            <Typography variant="s3">No file changes loaded.</Typography>
          )}
          {ordered.filter((file) => file.path !== ".futureagi/context.json").map((file) => (
            <TechnicalDetails key={file.path} title={`${file.path} · ${file.status}`} defaultOpen={file.path === 'AGENTS.md' || file.path === 'SKILL.md'}>
              {onOpenFile && (
                <Button onClick={() => onOpenFile(file.path)}>
                  Load complete diff
                </Button>
              )}
              <ReviewFileDiff file={file} />
            </TechnicalDetails>
          ))}
          {ordered.some((file) => file.path === '.futureagi/context.json') && <TechnicalDetails title="Other files">
            {ordered.filter((file) => file.path === '.futureagi/context.json').map((file) => <Stack key={file.path} spacing={1}><Typography variant="s3">{file.path} · {file.status}</Typography><ReviewFileDiff file={file} /></Stack>)}
          </TechnicalDetails>}
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
      <Section id="review-dependencies" title="Linked skills">
        {!isContext ? <Typography variant="s3">Not available for skills</Typography>
          : dependencyError || !dependencyChanges ? <ErrorState title="Couldn't load the linked-skill comparison" onRetry={onRetryDependencies} />
            : !dependencyChanges.length ? <Typography variant="s3">No linked-skill changes</Typography>
              : <TableContainer><Table size="small" aria-label="Linked-skill changes" sx={{ '& thead': { display: { xs: 'none', sm: 'table-header-group' } }, '& tbody': { display: { xs: 'block', sm: 'table-row-group' } }, '& tbody tr': { display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: 'minmax(0, 1fr)' }, '& td, & th': { minWidth: 0, whiteSpace: { xs: 'normal', sm: 'nowrap' }, overflowWrap: 'normal', wordBreak: 'normal' } }}>
                <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell>Skill</TableCell><TableCell>Policy</TableCell><TableCell>Version</TableCell></TableRow></TableHead>
                <TableBody>{dependencyChanges.map((item) => <TableRow key={item.mount} hover sx={{ height: { xs: 'auto', sm: ROW_HEIGHT.default } }}>
                  <TableCell><Typography variant="s1">{mountName(item.mount)}</Typography><TechnicalDetails title="Technical details"><Typography variant="s3" component="code">{item.mount}</Typography></TechnicalDetails></TableCell>
                  <TableCell>{(item.after || item.before)?.requested?.label ? `Follows ${(item.after || item.before).requested.label}` : `Pinned to v${(item.after || item.before)?.requested?.version}`}</TableCell>
                  <TableCell>{item.before ? `v${item.before.version}` : 'Not linked'} → {item.after ? <VersionLabel number={item.after.version} digest={item.after.digest} label="locked skill version" /> : 'Removed'}</TableCell>
                </TableRow>)}</TableBody>
              </Table></TableContainer>}
      </Section>
      {/* What the summary sentences cannot hold stays one click away: changed permission declarations and the
          compared fingerprints, the per-app counts with their caveat, and the version identities. */}
      <Stack spacing={1} sx={{ minWidth: 0 }}>
        <CapabilityChanges changes={capabilities} onOpenFile={onOpenFile} />
        {impact && <TechnicalDetails title="Apps affected">
          <Stack spacing={1}>
            <Typography variant="s3">
              {counted(impact.following, 'follower')} · {counted(impact.pinned, 'intentional pin')} ·{" "}
              {impact.unobserved} applications without observations
            </Typography>
            <Typography variant="s3">
              Only registered applications you can access are counted.{" "}
              {impact.moreAvailable
                ? "Additional impact rows require inspection."
                : "Offline and unregistered copies remain unknown."}
            </Typography>
            <IdentityValue
              label="Reviewed impact fingerprint"
              value={packet.transition.expectedImpactFingerprint}
            />
          </Stack>
        </TechnicalDetails>}
        <TechnicalDetails title="Technical details">
          <Stack spacing={1}>
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
                {targetConfiguration ? <TechnicalDetails title="Reviewed application configuration">
                  <Typography component="pre" variant="s3" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(targetConfiguration, null, 2)}</Typography>
                </TechnicalDetails> : <Alert severity="warning">The application configuration has not loaded. A digest alone does not establish applicability.</Alert>}
              </>
            ) : (
              <Typography variant="s3">
                No application target selected. Native applicability cannot be
                established.
              </Typography>
            )}
          </Stack>
        </TechnicalDetails>
        {/* Checks and pipeline results appear only when there is something to review: no empty heading. */}
        {!checks && <Section title="Checks"><ErrorState title="Couldn't load checks." onRetry={onRetryDependencies} /></Section>}
        {!checksEmpty && <>
          {checks?.items?.length > 0 && <Section title="Checks"><ChecksList checks={checks} /></Section>}
          <Section title="CI results">{packet.externalPipeline ? <ExternalPipeline reference={packet.externalPipeline} /> : <Typography variant="s3" color="text.secondary">None attached</Typography>}</Section>
        </>}
      </Stack>
    </Stack>
  );
}
ReleaseReviewBody.propTypes = {
  packet: PropTypes.object.isRequired,
  changes: PropTypes.array,
  dependencyChanges: PropTypes.array,
  dependencyError: PropTypes.bool,
  onRetryDependencies: PropTypes.func,
  checks: PropTypes.object,
  capabilities: PropTypes.object,
  targetConfiguration: PropTypes.object,
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
  availableEvidence: PropTypes.number,
};
