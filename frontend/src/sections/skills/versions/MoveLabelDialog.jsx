import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Stack, TextField, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import { enqueueSnackbar } from "notistack";

import {
  getSkillErrorCode,
  useSetSkillLabel,
  useSkill,
  useSkillVersion,
  useSkillCompare,
  useSkillLabels,
  useSkillVersions,
} from "src/api/skills";
import { useSkillTestEvidence } from "src/api/skills/testing";
import { paths } from "src/routes/paths";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import VersionSelect from "src/sections/skills/components/VersionSelect";
import { LABEL_PATTERN, LIMITS } from "src/sections/skills/constants";
import TestEvidence from '../tests/TestEvidence';
import ActionReason from "../components/ActionReason";
import { canAttachRun } from "../utils/evidence";
import CompareSummary from "../compare/CompareSummary";

function MoveLabelForm({ onClose, skill, label, version }) {
  const initialLabel = typeof label === "string" ? label : label?.name;
  const [name, setName] = useState(initialLabel || "production");
  const [target, setTarget] = useState(version ?? skill.latestVersion ?? "");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [snapshot, setSnapshot] = useState(null);
  const labels = useSkillLabels(skill.id);
  const versions = useSkillVersions(skill.id, { page: 1, pageSize: 100 });
  const [targetChosen, setTargetChosen] = useState(version != null);
  const detail = useSkill(skill.id);
  const targetVersion = useSkillVersion(skill.id, target);
  const [evidenceRunId, setEvidenceRunId] = useState(null);
  const authoritative = detail.data ?? skill;
  const pointer = authoritative.labels?.find((item) => item.name === name);
  const canMove = pointer ? pointer.canMove === true : !authoritative.protectedLabelNames?.includes(name) || authoritative.permissions?.canManageLabelProtection === true;
  const move = useSetSkillLabel(skill.id);
  const validName = LABEL_PATTERN.test(name) && name !== "latest";
  const validTarget = Number.isInteger(target) && target > 0;

  // Keep the reviewed pointer stable across background refetches. Only an
  // explicit conflict reload may change the CAS precondition for this label.
  useEffect(() => {
    if (detail.data && labels.data && snapshot?.name !== name) {
      setSnapshot({
        name,
        version:
          detail.data.labels.find((item) => item.name === name)?.version ?? null,
        releaseRevision: detail.data.releaseRevision,
      });
    }
  }, [labels.data, detail.data, name, snapshot?.name]);

  const ready = snapshot?.name === name;
  const current = ready ? snapshot.version : null;
  useEffect(() => {
    if (!ready || targetChosen || !versions.data) return;
    const alternative = versions.data.results.filter((item) => item.number !== current).sort((a, b) => b.number - a.number)[0];
    setTarget(alternative?.number ?? current ?? skill.latestVersion ?? '');
    setTargetChosen(true);
  }, [ready, targetChosen, versions.data, current, skill.latestVersion]);
  const labelLimitReached =
    ready && current === null && labels.data?.length >= LIMITS.MAX_LABELS;
  const compare = useSkillCompare(
    ready && current !== null && validTarget ? skill.id : undefined,
    current,
    validTarget ? target : undefined,
  );
  // The same query TestEvidence reads, so a version with no test runs can say so beside the action.
  const evidenceCandidate = validTarget ? { kind: 'version', version: target, digest: targetVersion.data?.digest } : undefined;
  const evidenceQuery = useSkillTestEvidence(validTarget ? skill.id : undefined, evidenceCandidate);
  // A previously chosen run keeps TestEvidence mounted so its mismatch warning still shows.
  const noEvidence = validTarget && !evidenceRunId && Boolean(evidenceQuery.data) && !(evidenceQuery.data.runs?.length);
  // The footer promises the attachment only while TestEvidence shows it as attached.
  const evidenceAttached = Boolean(evidenceRunId) && canAttachRun(evidenceQuery.data?.runs?.find((run) => run.runId === evidenceRunId));
  const movesProduction = name === "production" && validTarget && current !== target;
  const summaryReady =
    current === null ||
    (compare.data && !compare.isError && !compare.isFetching);

  const refreshLabel = async () => {
    setRefreshing(true);
    try {
      const result = await detail.refetch();
      await labels.refetch();
      if (result.isError || !result.data)
        throw new Error("Label reload failed");
      setSnapshot({
        name,
        version:
          result.data.labels.find((item) => item.name === name)?.version ?? null,
        releaseRevision: result.data.releaseRevision,
      });
      setConflict(false);
      setError("");
    } catch {
      setError(
        "Could not reload the current label. Try reloading again before moving it.",
      );
    } finally {
      setRefreshing(false);
    }
  };

  const handleMove = async () => {
    if (
      !canMove || detail.isError || targetVersion.isError || !targetVersion.data?.digest ||
      !ready ||
      !validName ||
      !validTarget || current === target ||
      conflict ||
      labelLimitReached ||
      !summaryReady
    )
      return;
    setError("");
    try {
      await move.mutateAsync({
        label: name,
        version: target,
        expectedVersion: current,
        expectedReleaseRevision: snapshot.releaseRevision,
        expectedDigest: targetVersion.data.digest,
        evidenceRunId,
        note,
      });
      enqueueSnackbar(`${name.charAt(0).toUpperCase()}${name.slice(1)} now points to v${target}.`, {
        variant: "success",
      });
      onClose();
    } catch (failure) {
      if (["label_conflict", "release_conflict", "release_precondition_required", "protected_label_forbidden"].includes(getSkillErrorCode(failure))) {
        setConflict(true);
        setError(
          "This label changed while you were reviewing it. Reload the current label, review the changes, then try again.",
        );
      } else if (['candidate_changed', 'evidence_mismatch', 'evidence_expired', 'evidence_share_required'].includes(getSkillErrorCode(failure))) {
        setError('The selected evidence cannot be attached. Review it, run tests again, or remove the attachment and continue without it. Your selections are preserved.');
      } else {
        setError("Could not move this label. Try again.");
      }
    }
  };

  return (
    <ModalWrapper
      open
      title="Move label"
      modalWidth="600px"
      paperSx={{ maxWidth: "sm" }}
      subTitle="A label controls which version agents fetch. Existing versions stay unchanged."
      onClose={onClose}
      onSubmit={handleMove}
      isLoading={move.isPending || refreshing}
      isValid={Boolean(
        canMove && !detail.isError && !targetVersion.isError && targetVersion.data?.digest &&
        validName &&
          validTarget &&
          ready &&
          !labels.isError &&
          !conflict &&
          !labelLimitReached &&
          summaryReady &&
          current !== target,
      )}
      actionBtnTitle={ready && current === target ? `Move ${name || "label"}` : `Move ${name || "label"} from ${current === null ? "unassigned" : `v${current}`} to ${validTarget ? `v${target}` : "version"}`}
    >
      <Stack spacing={2} sx={{ minWidth: 0, overflowY: "auto" }}>
        {error && (
          <Alert
            severity="error"
            action={
              conflict ? (
                <Button
                  color="inherit"
                  onClick={refreshLabel}
                  disabled={refreshing}
                >
                  Reload current label
                </Button>
              ) : undefined
            }
          >
            {error}
          </Alert>
        )}
        {labelLimitReached && (
          <Alert severity="warning">
            This skill already has {LIMITS.MAX_LABELS} labels. Choose an
            existing label to move.
          </Alert>
        )}
        <TextField
          autoFocus
          label="Label"
          size="small"
          value={name}
          disabled={Boolean(initialLabel) || move.isPending || refreshing}
          onChange={(event) => {
            setName(event.target.value);
            setError("");
            setConflict(false);
          }}
          inputProps={{ maxLength: 40 }}
          error={!validName}
          helperText={
            !validName
              ? "Use lowercase letters, numbers or hyphens. Start with a letter or number. ‘latest’ is reserved."
              : "Use production, staging or a custom label."
          }
        />
        <Stack spacing={0.5} role="group" aria-label="Target version">
          <VersionSelect
            skillId={skill.id}
            value={target}
            onChange={(value) => { setTargetChosen(true); setTarget(value); }}
            includeLabels={false}
          />
        </Stack>
        {labels.isLoading && (
          <ContentSkeleton
            variant="rows"
            rows={2}
            label="Loading current label"
          />
        )}
        {detail.isError && <ErrorState title="Could not load release permissions" onRetry={detail.refetch} />}
        {detail.isLoading && <ContentSkeleton variant="rows" rows={1} label="Loading release permissions" />}
        {labels.isError && (
          <ErrorState title="Could not load labels" onRetry={labels.refetch} />
        )}
        {ready && !labels.isError && (
          <Stack spacing={1}>
            <Typography variant="s1" color="text.secondary">
              {current === null
                ? "This is a new label. It does not point to a version yet."
                : current === target ? `${name.charAt(0).toUpperCase()}${name.slice(1)} already points to v${current}.` : `Currently points to v${current}.`}
            </Typography>
            {current !== null && compare.isLoading && (
              <ContentSkeleton
                variant="rows"
                rows={2}
                label="Comparing file changes"
              />
            )}
            {current !== null && compare.isError && (
              <ErrorState
                title="Could not compare these versions"
                onRetry={compare.refetch}
              />
            )}
            {current !== null && compare.data && !compare.isError && (
              <CompareSummary summary={compare.data.summary} />
            )}
          </Stack>
        )}
        {ready && current === target && <ActionReason id="move-action-reason-1" reason="Choose a different version" />}
        {!canMove && <Alert severity="info">A workspace admin or organization admin must move this protected label. Publishing an ordinary version remains available.</Alert>}
        {targetVersion.isError && <ErrorState title="Could not load the target version" onRetry={targetVersion.refetch} />}
        {validTarget && !noEvidence && <TestEvidence skillId={skill.id} candidate={{ kind: 'version', version: target, digest: targetVersion.data?.digest }} onSelectRun={setEvidenceRunId} selectedRunId={evidenceRunId} recordedWith="" />}
        <TextField
          label="Note (optional)"
          size="small"
          multiline
          minRows={2}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          inputProps={{ maxLength: 500 }}
          disabled={move.isPending}
        />
      </Stack>
      {/* The consequence and the missing-evidence warning sit right above the action, not up in the form. */}
      {(noEvidence || movesProduction || evidenceAttached) && <Stack spacing={1} sx={{ flexShrink: 0 }}>
        {noEvidence && <Alert severity="warning" action={<Button color="inherit" size="small" component={RouterLink} to={`${paths.dashboard.skills.edit(skill.id)}?tab=tests`}>Run tests</Button>}>No tests run for these files. Not tested is not a pass.</Alert>}
        {movesProduction && <Typography variant="s1">Apps that follow production pick up v{target} the next time they fetch. Pinned versions don&apos;t change.</Typography>}
        {evidenceAttached && <Typography variant="s1">Moving saves the attached test evidence with this label change.</Typography>}
      </Stack>}
    </ModalWrapper>
  );
}

export default function MoveLabelDialog({ open, ...props }) {
  return open ? <MoveLabelForm {...props} /> : null;
}

const formPropTypes = {
  onClose: PropTypes.func.isRequired,
  skill: PropTypes.shape({
    id: PropTypes.string.isRequired,
    latestVersion: PropTypes.number,
    labels: PropTypes.array, protectedLabelNames: PropTypes.array, permissions: PropTypes.object,
  }).isRequired,
  label: PropTypes.oneOfType([
    PropTypes.string,
    PropTypes.shape({ name: PropTypes.string }),
  ]),
  version: PropTypes.number,
};
MoveLabelForm.propTypes = formPropTypes;
MoveLabelDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  ...formPropTypes,
};
