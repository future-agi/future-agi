import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { Alert, Box, Button, Checkbox, FormControlLabel, List, ListItemButton, Stack, TextField, Typography } from "@mui/material";

import { getSkillErrorCode, useSkill, usePublishSkillDraft, useSaveSkillDraft, useSkillCompare, useSkillDraft } from "src/api/skills";
import { skillFilesDigest, skillManifestDigest } from 'src/api/skills/digest';
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

function PublishReview({ onClose, onDraftChange, skill, draft: initialDraft }) {
  const [draft, setDraft] = useState(initialDraft);
  const [digest, setDigest] = useState(null);
  const [evidenceRunId, setEvidenceRunId] = useState(null);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [release, setRelease] = useState(skill);
  const [releaseConflict, setReleaseConflict] = useState(false);
  const currentSkill = useSkill(skill.id);
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
  const canPublish = Boolean(digest && !releaseConflict && labels.every(canMove) && checks && !live.pending && !live.error && comparisonReady
    && checks.summary.errors === 0 && (checks.summary.warnings === 0 || acknowledged) && note.length <= 2000);

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
        setFailure('The selected content or evidence cannot be released as reviewed. Review the evidence below, run tests again, or explicitly continue without the attachment.');
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
    <ModalWrapper
      open
      onClose={onClose}
      title="Publish skill"
      subTitle="Create an immutable version. Your draft does not change production until you move its label."
      actionBtnTitle={stale ? "Publish anyway" : "Publish"}
      onSubmit={submit}
      isValid={canPublish}
      isLoading={publish.isPending}
      modalWidth="960px"
      paperSx={{ maxWidth: "100%" }}
    >
      <Stack spacing={3} sx={{ overflowY: "auto", minWidth: 0 }}>
        {stale && <Alert severity="warning">A newer version was published after this draft started. Publishing anyway creates a version from your draft, not from the newer files. Selected labels will move to your version.</Alert>}
        {failure && <Alert severity="error" action={releaseConflict ? <Button color="inherit" onClick={reloadRelease}>Reload release state</Button> : undefined}>{failure}</Alert>}
        <Section title={draft.baseVersion === null ? "Files in the first version" : `Changes from v${draft.baseVersion}`}>
          {draft.baseVersion !== null && comparison.isPending && <ContentSkeleton variant="panel" label="Loading changes" />}
          {draft.baseVersion !== null && comparison.isError && <ErrorState title="Couldn't load file changes" onRetry={() => setComparisonAttempt((attempt) => attempt + 1)} />}
          {comparisonMismatch && <Alert severity="warning" action={<Button color="inherit" onClick={() => setConflict(true)}>Review changed draft</Button>}>The server draft changed after this review opened. Reload or overwrite it before reviewing the diff.</Alert>}
          {comparisonReady && (
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
                  <TextDiff fromText={selected.fromText ?? ""} toText={selected.toText ?? ""} fromTitle={draft.baseVersion === null ? "New file" : `v${draft.baseVersion}`} toTitle="Your draft" />
                </Box>
              )}
              {selected?.status === "unchanged" && <Typography variant="s3" color="text.secondary">This file has no changes.</Typography>}
            </Stack>
          )}
        </Section>
        <TestEvidence skillId={skill.id} candidate={{ kind: 'draft', revision: draft.revision, ...(digest ? { digest } : {}) }} onSelectRun={setEvidenceRunId} selectedRunId={evidenceRunId} />
        <TextField label="Version note" value={note} onChange={(event) => setNote(event.target.value)} multiline minRows={2} inputProps={{ maxLength: 2000 }} helperText={`${note.length}/2,000 characters`} fullWidth />
        <Box><Button variant="outlined" size="small" disabled={!skill.permissions?.canRunTests} onClick={() => setSummaryOpen((value) => !value)}>{summaryOpen ? 'Close summary assistance' : 'Summarize changes'}</Button></Box>
        {summaryOpen && <AssistPanel skill={skill} draft={draft} kind="summary" isDirty={Boolean(currentDraft.data && currentDraft.data.revision !== draft.revision)} onUseSummary={(summary) => setNote(summary)} />}
        <Section title="After publishing, move label" description="Agents using a selected label will fetch this version on their next request. Pinned versions do not change.">
          <Stack>
            {["staging", "production"].map((label) => (
              <FormControlLabel key={label} label={label} control={(
                <Checkbox disabled={!canMove(label) && !labels.includes(label)} checked={labels.includes(label)} onChange={(_, checked) => setLabels((current) => checked ? [...current, label] : current.filter((value) => value !== label))} />
              )} />
            ))}
          </Stack>
          {['staging', 'production'].filter((name) => !canMove(name)).map((name) => <Typography key={name} variant="s3" color="text.secondary">You can publish a version. A workspace admin or organization admin must move {name}.</Typography>)}
          {labels.map((name) => <Typography key={name} variant="s3">Move {name} from {release.labels?.find((item) => item.name === name)?.version ? `v${release.labels.find((item) => item.name === name).version}` : 'unassigned'} to the published version.</Typography>)}
        </Section>
        <Section title="Checks">
          {live.pending && <ContentSkeleton variant="rows" rows={2} label="Checking draft before publishing" />}
          {live.error && <ErrorState title="Couldn't run checks" onRetry={live.retry} />}
          {checks && <ChecksList checks={checks} />}
          {checks?.summary.errors > 0 && <Alert severity="error">Fix the errors in the editor before publishing.</Alert>}
          {checks?.summary.warnings > 0 && <FormControlLabel label="I reviewed the warnings and want to publish this version." control={<Checkbox checked={acknowledged} onChange={(_, value) => setAcknowledged(value)} />} />}
        </Section>
      </Stack>
    </ModalWrapper>
  );
}

const reviewPropTypes = {
  onClose: PropTypes.func.isRequired,
  onDraftChange: PropTypes.func,
  skill: PropTypes.shape({ id: PropTypes.string.isRequired, name: PropTypes.string.isRequired, permissions: PropTypes.object, labels: PropTypes.array, protectedLabelNames: PropTypes.array, releaseRevision: PropTypes.number }).isRequired,
  draft: PropTypes.shape({ files: PropTypes.array.isRequired, note: PropTypes.string.isRequired, baseVersion: PropTypes.number, revision: PropTypes.number.isRequired }).isRequired,
};
PublishReview.propTypes = reviewPropTypes;

export default function PublishDialog({ open, onClose, onDraftChange, skill, draft }) {
  return open ? <PublishReview key={`${skill.id}:${draft.baseVersion}:${draft.revision}`} onClose={onClose} onDraftChange={onDraftChange} skill={skill} draft={draft} /> : null;
}

PublishDialog.propTypes = { ...reviewPropTypes, open: PropTypes.bool.isRequired };
