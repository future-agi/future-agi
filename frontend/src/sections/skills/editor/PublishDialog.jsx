import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { Alert, Box, Checkbox, FormControlLabel, List, ListItemButton, Stack, TextField, Typography } from "@mui/material";

import { getSkillErrorCode, usePublishSkillDraft, useSkillCompare } from "src/api/skills";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Section from "src/components/section";
import ChecksList from "src/sections/skills/components/ChecksList";
import FileStatusBadge from "src/sections/skills/components/FileStatusBadge";
import TextDiff from "src/sections/skills/components/TextDiff";
import useDraftChecks from "./useDraftChecks";

function PublishReview({ onClose, skill, draft }) {
  const navigate = useNavigate();
  const [note, setNote] = useState(draft.note);
  const [labels, setLabels] = useState([]);
  const [acknowledged, setAcknowledged] = useState(false);
  const [stale, setStale] = useState(false);
  const [failure, setFailure] = useState(null);
  const [serverChecks, setServerChecks] = useState(null);
  const [selectedPath, setSelectedPath] = useState(null);
  const live = useDraftChecks(draft.files, skill.name, 0);
  const checks = serverChecks ?? live.checks;
  const comparison = useSkillCompare(skill.id, draft.baseVersion, "draft");
  const publish = usePublishSkillDraft(skill.id);
  const files = draft.baseVersion === null
    ? draft.files.map((file) => ({ path: file.path, status: "added", fromText: "", toText: file.content }))
    : (comparison.data?.files ?? []);
  const orderedFiles = [...files].sort((a, b) => Number(a.status === "unchanged") - Number(b.status === "unchanged") || a.path.localeCompare(b.path));
  const selected = files.find((file) => file.path === selectedPath) ?? orderedFiles[0];
  const comparisonReady = draft.baseVersion === null || Boolean(comparison.data && !comparison.isError);
  const canPublish = Boolean(checks && !live.pending && !live.error && comparisonReady
    && checks.summary.errors === 0 && (checks.summary.warnings === 0 || acknowledged) && note.length <= 2000);

  const submit = async () => {
    if (!canPublish || publish.isPending) return;
    setFailure(null);
    try {
      const result = await publish.mutateAsync({ note, labels, acknowledgeWarnings: acknowledged, force: stale });
      enqueueSnackbar(result.unchanged ? "This version is already published." : `Published v${result.version.number}.`, { variant: "success" });
      onClose();
      navigate(`/dashboard/skills/${skill.id}`);
    } catch (error) {
      const code = getSkillErrorCode(error);
      if (code === "stale_base") {
        setStale(true);
      } else if (code === "checks_failed" || code === "warnings_unacknowledged") {
        setServerChecks(error.detail);
        setAcknowledged(false);
        setFailure("Checks changed. Review the results before publishing.");
      } else {
        setFailure("Couldn't publish this draft. Your draft is still saved. Try again.");
      }
    }
  };

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
        {failure && <Alert severity="error">{failure}</Alert>}
        <Section title={draft.baseVersion === null ? "Files in the first version" : `Changes from v${draft.baseVersion}`}>
          {draft.baseVersion !== null && comparison.isPending && <ContentSkeleton variant="panel" label="Loading changes" />}
          {draft.baseVersion !== null && comparison.isError && <ErrorState title="Couldn't load file changes" onRetry={() => comparison.refetch()} />}
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
        <TextField label="Version note" value={note} onChange={(event) => setNote(event.target.value)} multiline minRows={2} inputProps={{ maxLength: 2000 }} helperText={`${note.length}/2,000 characters`} fullWidth />
        <Section title="After publishing, move label" description="Agents using a selected label will fetch this version on their next request. Pinned versions do not change.">
          <Stack>
            {["staging", "production"].map((label) => (
              <FormControlLabel key={label} label={label} control={(
                <Checkbox checked={labels.includes(label)} onChange={(_, checked) => setLabels((current) => checked ? [...current, label] : current.filter((value) => value !== label))} />
              )} />
            ))}
          </Stack>
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
  skill: PropTypes.shape({ id: PropTypes.string.isRequired, name: PropTypes.string.isRequired }).isRequired,
  draft: PropTypes.shape({ files: PropTypes.array.isRequired, note: PropTypes.string.isRequired, baseVersion: PropTypes.number }).isRequired,
};
PublishReview.propTypes = reviewPropTypes;

export default function PublishDialog({ open, onClose, skill, draft }) {
  return open ? <PublishReview onClose={onClose} skill={skill} draft={draft} /> : null;
}

PublishDialog.propTypes = { ...reviewPropTypes, open: PropTypes.bool.isRequired };
