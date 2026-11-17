import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { Alert, Box, Button, Checkbox, DialogActions, FormControlLabel, List, ListItemButton, Stack, TextField, Tooltip, Typography } from "@mui/material";

import { getSkillErrorCode, useSkill, usePublishSkillDraft, useSaveSkillDraft, useSkillCompare, useSkillDraft } from "src/api/skills";
import { useSkillTestingOptions } from "src/api/skills/testing";
import { skillFilesDigest, skillManifestDigest } from 'src/api/skills/digest';
import ActionReason from "../components/ActionReason";
import ReleaseRuleNote from "../components/ReleaseRuleNote";
import LabelProtectionDialog from "../governance/LabelProtectionDialog";
import TestEvidence from '../tests/TestEvidence';
import AssistPanel from '../assist/AssistPanel';
import { paths } from "src/routes/paths";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Section from "src/components/section";
import ChecksList from "src/sections/skills/components/ChecksList";
import FileStatusBadge from "src/sections/skills/components/FileStatusBadge";
import TextDiff from "src/sections/skills/components/TextDiff";
import useDraftChecks from "./useDraftChecks";
import DraftConflictDialog from "./DraftConflictDialog";

function ReviewSection({ title, ...props }) {
  return <Section {...props} title={<Typography component="span" variant="m3">{title}</Typography>} />;
}
ReviewSection.propTypes = { title: PropTypes.string.isRequired };

function PublishReview({ onClose, onDraftChange, skill, draft: initialDraft }) {
  const footerRef = useRef(null);
  const [footerHeight, setFooterHeight] = useState(0);
  useEffect(() => {
    const footer = footerRef.current;
    if (!footer || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(() => setFooterHeight(footer.getBoundingClientRect().height));
    observer.observe(footer);
    return () => observer.disconnect();
  }, []);
  const [draft, setDraft] = useState(initialDraft);
  const [digest, setDigest] = useState(null);
  const [evidenceRunId, setEvidenceRunId] = useState(null);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [release, setRelease] = useState(skill);
  const [releaseConflict, setReleaseConflict] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);
  const currentSkill = useSkill(skill.id);
  // UXF-09: the label rule beside the label choice, from the skill's current
  // protected labels. It only explains; canMove and the server's CAS decide.
  const ruleSource = currentSkill.data ?? skill;
  const protectedLabels = Array.isArray(ruleSource.protectedLabelNames) ? ruleSource.protectedLabelNames : null;
  const options = useSkillTestingOptions();
  useEffect(() => {
    let active = true;
    setDigest(null);
    skillFilesDigest(draft.files).then((value) => { if (active) setDigest(value); }).catch(() => { if (active) setFailure('Could not identify these files. Close and reopen the review.'); });
    return () => { active = false; };
  }, [draft.files]);
  const canMove = (name) => {
    const latest = currentSkill.data ?? skill;
    const pointer = latest.labels?.find((item) => item.name === name);
    if (pointer) return pointer.canMove === true;
    return !latest.protectedLabelNames?.includes(name) || latest.permissions?.canManageLabelProtection === true;
  };
  const reloadRelease = async () => {
    const response = await currentSkill.refetch();
    if (response.isError || !response.data) { setFailure('Could not reload release state. Your selections are preserved.'); return; }
    setRelease(response.data);
    setReleaseConflict(false);
    setFailure(null);
  };
  const navigate = useNavigate();
  const [note, setNote] = useState(draft.note);
  const [labels, setLabels] = useState([]);
  const [acknowledged, setAcknowledged] = useState(false);
  const [stale, setStale] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [failure, setFailure] = useState(null);
  const [serverChecks, setServerChecks] = useState(null);
  const [selectedPath, setSelectedPath] = useState(null);
  const live = useDraftChecks(draft.files, skill.name, 0);
  const checks = serverChecks ?? live.checks;
  const credentialWarning = checks?.items?.some((item) => item.code === "secret_suspected");
  const liveComparison = useSkillCompare(skill.id, draft.baseVersion, "draft");
  const { refetch: refetchComparison } = liveComparison;
  const [comparison, setComparison] = useState({ isPending: true });
  const [comparisonAttempt, setComparisonAttempt] = useState(0);
  useEffect(() => {
    if (draft.baseVersion === null) return undefined;
    let active = true;
    setComparison({ isPending: true });
    // The shared draft comparison cache has no revision. Review only a fresh
    // response for this mounted, saved revision, never the previous review's data.
    refetchComparison().then((result) => {
      if (active) setComparison(result);
    }).catch(() => {
      if (active) setComparison({ isError: true });
    });
    return () => { active = false; };
  }, [refetchComparison, draft.baseVersion, draft.revision, comparisonAttempt]);
  const [compared, setCompared] = useState(null);
  useEffect(() => {
    let active = true;
    const data = comparison.data;
    if (draft.baseVersion !== null && data) {
      // The compare endpoint resolves the mutable server draft. Bind its entire
      // target manifest to the acknowledged snapshot, including unchanged files.
      skillManifestDigest(data.files.filter((file) => file.status !== 'removed').map((file) => ({ path: file.path, sha256: file.toSha256 })))
        .then((value) => { if (active) setCompared({ data, digest: value }); })
        .catch(() => { if (active) setCompared({ data, digest: null }); });
    }
    return () => { active = false; };
  }, [comparison.data, draft.baseVersion]);
  const publish = usePublishSkillDraft(skill.id);
  const currentDraft = useSkillDraft(skill.id);
  const save = useSaveSkillDraft(skill.id);
  const files = draft.baseVersion === null
    ? draft.files.map((file) => ({ path: file.path, status: "added", fromText: "", toText: file.content }))
    : (comparison.data?.files ?? []);
  const orderedFiles = [...files].sort((a, b) => Number(a.status === "unchanged") - Number(b.status === "unchanged") || a.path.localeCompare(b.path));
  const selected = files.find((file) => file.path === selectedPath) ?? orderedFiles[0];
  const comparisonVerified = Boolean(compared && compared.data === comparison.data && digest);
  const comparisonMismatch = draft.baseVersion !== null && comparisonVerified && compared.digest !== digest;
  const comparisonReady = draft.baseVersion === null || Boolean(comparisonVerified && compared.digest === digest && !comparison.isError && !comparison.isPending && !liveComparison.isFetching);
  const noFileChanges = comparisonReady && draft.baseVersion !== null && files.length > 0 && files.every((file) => file.status === 'unchanged');
  const canPublish = Boolean(digest && !releaseConflict && !(credentialWarning && labels.includes("production")) && labels.every(canMove) && checks && live.current && !live.pending && !live.error && comparisonReady
    && checks.summary.errors === 0 && (checks.summary.warnings === 0 || acknowledged) && note.length <= 2000);

  const nextVersion = release.latestVersion === null ? 1 : release.latestVersion != null ? release.latestVersion + 1 : null;
  const sameAsLatest = noFileChanges && draft.baseVersion === release.latestVersion;
  const versionText = sameAsLatest ? `v${release.latestVersion}` : nextVersion ? `v${nextVersion}` : 'a new version';
  const publishEffect = sameAsLatest ? 'Reuses' : 'Creates';
  const productionVersion = release.labels?.find((item) => item.name === 'production')?.version;
  const consequence = labels.length
    ? `${publishEffect} ${versionText} and moves ${labels.join(' and ')} to ${versionText}.`
    : `${publishEffect} ${versionText}. Production ${productionVersion ? `stays on v${productionVersion}` : 'stays unassigned'}.`;
  const disabledReason = publish.isPending ? 'Publishing…' : !digest ? 'Identifying reviewed files…' : releaseConflict ? 'Reload and review release state' : credentialWarning && labels.includes("production") ? "Clear the production selection while a possible credential warning is open" : !labels.every(canMove) ? 'An admin must move the selected protected label' : live.error ? 'Retry checks to publish' : !live.current || live.pending || !checks ? 'Wait for current checks' : !comparisonReady ? 'Review current file changes' : checks.summary.errors > 0 ? 'Fix the check errors' : checks.summary.warnings > 0 && !acknowledged ? 'Tick the warning acknowledgment' : note.length > 2000 ? 'Shorten the version note' : '';
  const publishTitle = labels.length > 1 ? 'Publish and move labels' : labels.length ? `Publish and move ${labels[0]}` : 'Publish';

  const submit = async () => {
    if (!canPublish || publish.isPending) return;
    setFailure(null);
    try {
      const result = await publish.mutateAsync({ note, labels, acknowledgeWarnings: acknowledged, force: stale, revision: draft.revision, expectedDigest: digest, expectedReleaseRevision: release.releaseRevision, evidenceRunId });
      enqueueSnackbar(result.unchanged ? "This version is already published." : `Published v${result.version.number}.`, { variant: "success" });
      onClose();
      navigate(paths.dashboard.skills.detail(skill.id));
    } catch (error) {
      const code = getSkillErrorCode(error);
      if (code === "draft_conflict" || code === "draft_not_found") {
        setConflict(true);
      } else if (['release_conflict', 'release_precondition_required', 'protected_label_forbidden'].includes(code)) {
        setReleaseConflict(true);
        setFailure('Release policy or labels changed. Reload and review the current targets. Your note and evidence selection are preserved.');
        currentSkill.refetch();
      } else if (['candidate_changed', 'evidence_mismatch', 'evidence_expired', 'evidence_share_required'].includes(code)) {
        setFailure('The selected content or evidence cannot be released as reviewed. Review the evidence below, run tests again, or remove the attachment and continue without it.');
        currentDraft.refetch();
      } else if (code === "stale_base") {
        setStale(true);
      } else if (code === "checks_failed" || code === "warnings_unacknowledged") {
        setServerChecks(error.response?.data?.detail ?? error.detail);
        setAcknowledged(false);
        setFailure("Checks changed. Review the results before publishing.");
      } else {
        setFailure("Couldn't publish this draft. Your draft is still saved. Try again.");
      }
    }
  };

  if (conflict) return (
    <DraftConflictDialog
      open
      onClose={() => setConflict(false)}
      description="Nothing was published. Reload the changed draft, or overwrite it with your reviewed files. Review again before publishing."
      refetch={currentDraft.refetch}
      onReload={(latest) => {
        if (latest) { setDraft(latest); setServerChecks(null); setAcknowledged(false); onDraftChange?.(latest); }
        else navigate(paths.dashboard.skills.detail(skill.id));
      }}
      onOverwrite={async (revision) => {
        try {
          const saved = await save.mutateAsync({ files: draft.files, note, baseVersion: draft.baseVersion, revision });
          setDraft(saved);
          setServerChecks(null);
          setAcknowledged(false);
          onDraftChange?.(saved);
          return saved;
        } catch {
          // Keep the reviewed files available if a second tab wins the next CAS.
          return null;
        }
      }}
    />
  );

  return (
    <>
    <ModalWrapper
      open
      onClose={onClose}
      title="Publish skill"
      subTitle="Review what changes, then publish."
      dialogActionSx={{ display: "none" }}
      actionBtnTitle={stale ? "Publish anyway" : publishTitle}
      onSubmit={submit}
      isValid={canPublish}
      isLoading={publish.isPending}
      modalWidth="960px"
      paperSx={{ maxWidth: "100%" }}
    >
      <Stack spacing={2} sx={{ overflowY: "auto", minHeight: 0, paddingBottom: footerHeight ? `${footerHeight}px` : 2, minWidth: 0, "& .MuiFormControlLabel-root": { mx: 0 } }}>
        {stale && <Alert severity="warning">A newer version was published after this draft started. Publishing anyway creates a version from your draft, not from the newer files. Selected labels will move to your version.</Alert>}
        {failure && <Alert severity="error" action={releaseConflict ? <Button color="inherit" onClick={reloadRelease}>Reload release state</Button> : undefined}>{failure}</Alert>}
        <ReviewSection title={noFileChanges ? `No file changes since v${draft.baseVersion}` : draft.baseVersion === null ? "Files in the first version" : `Changes from v${draft.baseVersion}`}>
          {draft.baseVersion !== null && !comparison.isError && (comparison.isPending || !comparisonVerified || liveComparison.isFetching) && <ContentSkeleton variant="panel" label="Verifying reviewed file changes" />}
          {draft.baseVersion !== null && comparison.isError && <ErrorState title="Couldn't load file changes" onRetry={() => setComparisonAttempt((attempt) => attempt + 1)} />}
          {comparisonMismatch && <Alert severity="warning" action={<Button color="inherit" onClick={() => setConflict(true)}>Review changed draft</Button>}>The server draft changed after this review opened. Reload or overwrite it before reviewing the diff.</Alert>}
          {comparisonReady && files.some((file) => file.status !== "unchanged") && (
            <Stack spacing={1.5}>
              <List dense aria-label="File changes" disablePadding>
                {orderedFiles.map((file) => (
                  <ListItemButton key={file.path} selected={selected?.path === file.path} onClick={() => setSelectedPath(file.path)} sx={{ gap: 1 }}>
                    <Typography variant="s1" sx={{ fontFamily: "monospace", overflowWrap: "anywhere", flex: 1 }}>{file.path}</Typography>
                    <FileStatusBadge status={file.status} />
                  </ListItemButton>
                ))}
              </List>
              {selected && selected.status !== "unchanged" && (
                <Box sx={{ minWidth: 0, overflowX: "auto" }}>
                  <TextDiff fromText={selected.fromText ?? ""} toText={selected.toText ?? ""} fromTitle={draft.baseVersion === null ? "New file" : `v${draft.baseVersion}`} toTitle="Your draft" fromMissing={selected.status === "added"} toMissing={selected.status === "removed"} />
                </Box>
              )}
              {selected?.status === "unchanged" && <Typography variant="s3" color="text.secondary">{selected.path} has no changes.</Typography>}
            </Stack>
          )}
        </ReviewSection>
        <ReviewSection title="Checks">
          {live.pending && <ContentSkeleton variant="rows" rows={2} label="Checking draft before publishing" />}
          {live.error && <ErrorState title="Couldn't run checks" onRetry={live.retry} />}
          {checks && <ChecksList checks={checks} />}
          {checks?.summary.errors > 0 && <Alert severity="error">Fix the errors in the editor before publishing.</Alert>}
        </ReviewSection>
        <TestEvidence skillId={skill.id} candidate={{ kind: 'draft', revision: draft.revision, ...(digest ? { digest } : {}) }} onSelectRun={setEvidenceRunId} selectedRunId={evidenceRunId} recordedWith="It is saved with the new version when you publish." />
        {/* With AI drafting off for the workspace nothing about it is shown; a missing permission keeps the disabled button and its reason. */}
        {options.data?.assistEnabled && <>
          <ActionReason id="publish-action-reason-1" reason={noFileChanges ? "Make a change to summarise" : !skill.permissions?.canRunTests ? "You need test permissions to use AI assistance." : ""} />
          {!noFileChanges && <Box><Tooltip title={!skill.permissions?.canRunTests ? "You need test permissions to use AI assistance." : ""}><span><Button variant="outlined" size="small" disabled={!skill.permissions?.canRunTests} onClick={() => setSummaryOpen((value) => !value)}>{summaryOpen ? 'Close summary assistance' : 'Summarize changes'}</Button></span></Tooltip></Box>}
        </>}
        {summaryOpen && !noFileChanges && <AssistPanel skill={skill} draft={draft} kind="summary" isDirty={Boolean(currentDraft.data && currentDraft.data.revision !== draft.revision)} onUseSummary={(summary) => setNote(summary)} />}
        <ReviewSection title="Release labels (optional)" description="Apps that follow a selected label get this version the next time they fetch it. Pinned versions do not change.">
          <Stack>
            {["staging", "production"].map((label) => (
              <FormControlLabel key={label} label={label} control={(
                <Checkbox disabled={(label === "production" && credentialWarning && !labels.includes(label)) || (!canMove(label) && !labels.includes(label))} checked={labels.includes(label)} onChange={(_, checked) => setLabels((current) => checked ? [...current, label] : current.filter((value) => value !== label))} />
              )} />
            ))}
          </Stack>
          {credentialWarning && <Typography variant="s3" color="text.secondary">Production can&apos;t be set while a possible credential warning is open.</Typography>}
          {['staging', 'production'].filter((name) => !canMove(name)).map((name) => <Typography key={name} variant="s3" color="text.secondary">You can publish a version. A workspace admin or organization admin must move {name}.</Typography>)}
          {protectedLabels && <ReleaseRuleNote rules={{ protectedLabels }} canChangeRules={ruleSource.permissions?.canManageLabelProtection === true} onChangeRules={() => setRulesOpen(true)} sx={{ mt: 1 }} />}
        </ReviewSection>
        <TextField label="Version note" value={note} onChange={(event) => setNote(event.target.value)} multiline minRows={2} inputProps={{ maxLength: 2000 }} helperText={`${note.length}/2,000 characters`} fullWidth />
      </Stack>
      <DialogActions ref={footerRef} data-sticky-footer sx={{ flexDirection: "column", alignItems: "stretch", flexShrink: 0, bgcolor: "background.paper", borderTop: 1, borderColor: "divider", p: 1.5, position: "sticky", bottom: 0 }}>
        <Typography variant="s1" role="status">{consequence}</Typography>
          {checks?.summary.warnings > 0 && <FormControlLabel label="I reviewed the warnings and want to publish this version." control={<Checkbox checked={acknowledged} onChange={(_, value) => setAcknowledged(value)} />} />}
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} justifyContent="flex-end" alignItems={{ sm: "center" }}>
          <ActionReason id="publish-reason" reason={disabledReason} />
          <Button variant="outlined" disabled={publish.isPending} onClick={onClose}>Cancel</Button>
          <Button variant="contained" color="primary" aria-describedby={disabledReason ? "publish-reason" : undefined} disabled={!canPublish || publish.isPending} onClick={submit}>{stale ? "Publish anyway" : publishTitle}</Button>
        </Stack>
      </DialogActions>
    </ModalWrapper>
    {/* Mounted only while open, outside the publish dialog's tree so nothing in it reaches publish.
        New rules change the release revision, so publishing then fails its CAS and asks for
        "Reload release state"; the reviewed release snapshot is never swapped here. */}
    {rulesOpen && <LabelProtectionDialog open onClose={() => setRulesOpen(false)} skill={ruleSource} />}
    </>
  );
}

const reviewPropTypes = {
  onClose: PropTypes.func.isRequired,
  onDraftChange: PropTypes.func,
  skill: PropTypes.shape({ id: PropTypes.string.isRequired, name: PropTypes.string.isRequired, permissions: PropTypes.object, labels: PropTypes.array, protectedLabelNames: PropTypes.array, releaseRevision: PropTypes.number, latestVersion: PropTypes.number }).isRequired,
  draft: PropTypes.shape({ files: PropTypes.array.isRequired, note: PropTypes.string.isRequired, baseVersion: PropTypes.number, revision: PropTypes.number.isRequired }).isRequired,
};
PublishReview.propTypes = reviewPropTypes;

export default function PublishDialog({ open, onClose, onDraftChange, skill, draft }) {
  return open ? <PublishReview key={`${skill.id}:${draft.baseVersion}:${draft.revision}`} onClose={onClose} onDraftChange={onDraftChange} skill={skill} draft={draft} /> : null;
}

PublishDialog.propTypes = { ...reviewPropTypes, open: PropTypes.bool.isRequired };
