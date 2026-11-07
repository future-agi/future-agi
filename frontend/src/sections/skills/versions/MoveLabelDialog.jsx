import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Stack, TextField, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";

import { getSkillErrorCode, useSetSkillLabel, useSkillCompare, useSkillLabels } from "src/api/skills";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import VersionSelect from "src/sections/skills/components/VersionSelect";
import { LABEL_PATTERN } from "src/sections/skills/constants";
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
  const move = useSetSkillLabel(skill.id);
  const validName = LABEL_PATTERN.test(name) && name !== "latest";
  const validTarget = Number.isInteger(target) && target > 0;

  // Keep the reviewed pointer stable across background refetches. Only an
  // explicit conflict reload may change the CAS precondition for this label.
  useEffect(() => {
    if (labels.data && snapshot?.name !== name) {
      setSnapshot({ name, version: labels.data.find((item) => item.name === name)?.version ?? null });
    }
  }, [labels.data, name, snapshot?.name]);

  const ready = snapshot?.name === name;
  const current = ready ? snapshot.version : null;
  const compare = useSkillCompare(
    ready && current !== null && validTarget ? skill.id : undefined,
    current,
    validTarget ? target : undefined,
  );
  const summaryReady = current === null || (compare.data && !compare.isError && !compare.isFetching);

  const refreshLabel = async () => {
    setRefreshing(true);
    const result = await labels.refetch();
    setRefreshing(false);
    if (result.isError || !result.data) {
      setError("Could not reload the current label. Try reloading again before moving it.");
      return;
    }
    setSnapshot({ name, version: result.data.find((item) => item.name === name)?.version ?? null });
    setConflict(false);
    setError("");
  };

  const handleMove = async () => {
    if (!ready || !validName || !validTarget || conflict || !summaryReady) return;
    setError("");
    try {
      await move.mutateAsync({ label: name, version: target, expectedVersion: current, note });
      enqueueSnackbar(`${name} now points to v${target}.`, { variant: "success" });
      onClose();
    } catch (failure) {
      if (getSkillErrorCode(failure) === "label_conflict") {
        setConflict(true);
        setError("This label changed while you were reviewing it. Reload the current label, review the changes, then try again.");
      } else {
        setError("Could not move this label. Try again.");
      }
    }
  };

  return (
    <ModalWrapper
      open
      title="Move label"
      subTitle="A label controls which version agents fetch. Existing versions stay unchanged."
      onClose={onClose}
      onSubmit={handleMove}
      isLoading={move.isPending || refreshing}
      isValid={Boolean(validName && validTarget && ready && !labels.isError && !conflict && summaryReady && current !== target)}
      actionBtnTitle={`Move ${name || "label"} to ${validTarget ? `v${target}` : "version"}`}
    >
      <Stack spacing={2}>
        {error && <Alert severity="error" action={conflict ? <Button color="inherit" onClick={refreshLabel} disabled={refreshing}>Reload current label</Button> : undefined}>{error}</Alert>}
        <TextField
          autoFocus
          label="Label"
          size="small"
          value={name}
          disabled={Boolean(initialLabel) || move.isPending || refreshing}
          onChange={(event) => { setName(event.target.value); setError(""); setConflict(false); }}
          inputProps={{ maxLength: 40 }}
          error={!validName}
          helperText={!validName ? "Use lowercase letters, numbers or hyphens. Start with a letter or number. ‘latest’ is reserved." : "Use production, staging or a custom label."}
        />
        <Stack spacing={0.5} role="group" aria-label="Target version">
          <Typography variant="s3" color="text.secondary">Target version</Typography>
          <VersionSelect skillId={skill.id} value={target} onChange={setTarget} includeLabels={false} />
        </Stack>
        {labels.isLoading && <ContentSkeleton variant="rows" rows={2} label="Loading current label" />}
        {labels.isError && <ErrorState title="Could not load labels" onRetry={labels.refetch} />}
        {ready && !labels.isError && (
          <Stack spacing={1}>
            <Typography variant="s1" color="text.secondary">
              {current === null ? "This is a new label. It does not point to a version yet." : `Currently points to v${current}.`}
            </Typography>
            {current !== null && compare.isLoading && <ContentSkeleton variant="rows" rows={2} label="Comparing file changes" />}
            {current !== null && compare.isError && <ErrorState title="Could not compare these versions" onRetry={compare.refetch} />}
            {current !== null && compare.data && !compare.isError && <CompareSummary summary={compare.data.summary} />}
          </Stack>
        )}
        <TextField label="Note (optional)" size="small" multiline minRows={2} value={note} onChange={(event) => setNote(event.target.value)} inputProps={{ maxLength: 500 }} disabled={move.isPending} />
      </Stack>
    </ModalWrapper>
  );
}

export default function MoveLabelDialog({ open, ...props }) {
  return open ? <MoveLabelForm {...props} /> : null;
}

const formPropTypes = {
  onClose: PropTypes.func.isRequired,
  skill: PropTypes.shape({ id: PropTypes.string.isRequired, latestVersion: PropTypes.number }).isRequired,
  label: PropTypes.oneOfType([PropTypes.string, PropTypes.shape({ name: PropTypes.string })]),
  version: PropTypes.number,
};
MoveLabelForm.propTypes = formPropTypes;
MoveLabelDialog.propTypes = { open: PropTypes.bool.isRequired, ...formPropTypes };
