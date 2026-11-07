import { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Stack, TextField, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";

import { getSkillErrorCode } from "src/api/skills";
import {
  useSkillLabelProtection,
  useSkillLabelProtectionHistory,
  useSetSkillLabelProtection,
} from "src/api/skills/governance";
import { useSkillV11Scope } from "src/api/skills/testing";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import ContentSkeleton from "src/components/content-skeleton";
import { DataTablePagination } from "src/components/data-table";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import Section from "src/components/section";
import { LABEL_PATTERN, LIMITS } from "src/sections/skills/constants";
import { usePreviewState } from "src/sections/skills/components/SkillsStatePreview";

function PolicyHistory({ skillId }) {
  const [page, setPage] = useState(1);
  const query = useSkillLabelProtectionHistory(skillId, { page, pageSize: 10 });
  return (
    <Section title="Protection history">
      {query.isPending ? (
        <ContentSkeleton
          variant="rows"
          rows={2}
          label="Loading protection history"
        />
      ) : query.isError ? (
        <ErrorState
          title="Could not load protection history"
          onRetry={query.refetch}
        />
      ) : query.data?.results.length === 0 ? (
        <Typography variant="s3" color="text.secondary">
          No policy changes recorded.
        </Typography>
      ) : (
        <Stack spacing={1}>
          {query.data?.results.map((event) => (
            <Stack key={event.toRevision} spacing={0.5}>
              <Typography variant="s1">
                {event.newNames.length
                  ? event.newNames.join(", ")
                  : "No protected labels"}{" "}
                · r{event.fromRevision} → r{event.toRevision}
              </Typography>
              <Typography variant="s3" color="text.secondary">
                Previously:{" "}
                {event.oldNames.length ? event.oldNames.join(", ") : "None"}.{" "}
                {event.actor?.name || "Deleted actor"} ·{" "}
                {new Date(event.createdAt).toLocaleString()}
              </Typography>
            </Stack>
          ))}
          <DataTablePagination
            page={page - 1}
            pageSize={10}
            pageSizeOptions={[10]}
            total={query.data?.count || 0}
            onPageChange={(value) => setPage(value + 1)}
            disabled={query.isFetching}
          />
        </Stack>
      )}
    </Section>
  );
}
PolicyHistory.propTypes = { skillId: PropTypes.string.isRequired };

function ProtectionForm({ skill, onClose, initial }) {
  const query = useSkillLabelProtection(skill.id);
  const mutation = useSetSkillLabelProtection(skill.id);
  // A background refresh must not silently replace the policy the admin reviewed.
  const [basis, setBasis] = useState(initial);
  const [text, setText] = useState(initial.protectedLabelNames.join(", "));
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const names = text.trim() ? text.split(",").map((name) => name.trim()) : [];
  const valid =
    names.length <= LIMITS.MAX_LABELS &&
    new Set(names).size === names.length &&
    names.every((name) => LABEL_PATTERN.test(name) && name !== "latest");
  const canManage = query.data?.canManage === true && !query.isError;
  const changed =
    JSON.stringify([...names].sort()) !==
    JSON.stringify([...basis.protectedLabelNames].sort());

  const reload = async () => {
    setRefreshing(true);
    const result = await query.refetch();
    if (result.isError || !result.data)
      setError("Could not reload protection. Your chosen names are preserved.");
    else {
      setBasis(result.data);
      setConflict(false);
      setError("");
    }
    setRefreshing(false);
  };
  const save = async () => {
    if (!canManage || !valid || conflict || !changed) return;
    setError("");
    try {
      await mutation.mutateAsync({
        protectedLabelNames: names,
        expectedReleaseRevision: basis.releaseRevision,
      });
      enqueueSnackbar(
        "Label protection updated. Existing version pointers are unchanged.",
        { variant: "success" },
      );
      onClose();
    } catch (failure) {
      const code = getSkillErrorCode(failure);
      if (code === "release_conflict") {
        setConflict(true);
        setError(
          "A label or the protection policy changed. Reload current policy, review it below, then save again. Your chosen names are preserved.",
        );
      } else if (
        [
          "protected_label_forbidden",
          "label_protection_forbidden",
          "principal_not_supported",
          "access_revoked",
          "permission_denied",
        ].includes(code)
      ) {
        setConflict(true);
        setError(
          "Your current access does not allow this policy change. Reload permissions to continue; your chosen names are preserved.",
        );
      } else
        setError(
          "Could not save protection. Your chosen names are preserved. Try again.",
        );
    }
  };
  return (
    <ModalWrapper
      open
      title="Manage label protection"
      subTitle={skill.name}
      onClose={onClose}
      onSubmit={save}
      actionBtnTitle="Save protection"
      isLoading={mutation.isPending || refreshing}
      isValid={valid && canManage && changed && !conflict}
    >
      <Stack spacing={2} sx={{ overflowY: "auto", minWidth: 0 }}>
        <Typography variant="s1">
          Protected labels can be assigned, moved or removed only by a workspace
          admin or organization admin/owner. Members can still publish versions.
          System-key automation cannot move protected labels.
        </Typography>
        {error && (
          <Alert
            severity="error"
            action={
              conflict ? (
                <Button color="inherit" onClick={reload} disabled={refreshing}>
                  Reload current policy
                </Button>
              ) : undefined
            }
          >
            {error}
          </Alert>
        )}
        {!canManage && (
          <Alert severity="info">
            Only a current workspace or organization administrator can change
            protection.
          </Alert>
        )}
        <TextField
          autoFocus
          size="small"
          label="Protected label names"
          value={text}
          onChange={(event) => setText(event.target.value)}
          disabled={!canManage || mutation.isPending || refreshing}
          error={!valid}
          helperText="Comma-separated, up to 20 unique labels. ‘latest’ cannot be protected. Leave empty for no protected labels."
          inputProps={{ maxLength: 840 }}
        />
        <Typography variant="s3" color="text.secondary">
          Current policy r{basis.releaseRevision}:{" "}
          {basis.protectedLabelNames.length
            ? basis.protectedLabelNames.join(", ")
            : "No protected labels"}
          . Production is{" "}
          {basis.protectedLabelNames.includes("production")
            ? "protected"
            : "not protected"}
          .
        </Typography>
        <Typography variant="s3" color="text.secondary">
          Protection survives removal of a label pointer. Saving does not move
          any version.
        </Typography>
        <PolicyHistory skillId={skill.id} />
      </Stack>
    </ModalWrapper>
  );
}
ProtectionForm.propTypes = {
  skill: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
  initial: PropTypes.object.isRequired,
};

function ProtectionLoader({ skill, onClose }) {
  const query = useSkillLabelProtection(skill.id);
  const preview = usePreviewState();
  const [initial, setInitial] = useState(null);
  if (!initial && query.data && !query.isError) setInitial(query.data);
  if (initial && !preview)
    return <ProtectionForm skill={skill} onClose={onClose} initial={initial} />;
  return (
    <ModalWrapper
      open
      title="Manage label protection"
      onClose={onClose}
      isValid={false}
      actionBtnTitle="Save protection"
    >
      {preview === "error" || query.isError ? (
        <ErrorState title="Could not load protection" onRetry={query.refetch} />
      ) : preview === "empty" ? (
        <EmptyState
          title="No protected labels"
          description="Existing labels remain editable by workspace writers until an administrator explicitly enables protection."
        />
      ) : (
        <ContentSkeleton variant="panel" label="Loading label protection" />
      )}
    </ModalWrapper>
  );
}
ProtectionLoader.propTypes = {
  skill: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
};

export default function LabelProtectionDialog({ open, onClose, skill }) {
  const access = useSkillV11Scope();
  return open ? (
    <ProtectionLoader
      key={`${access.identity}:${skill.id}`}
      skill={skill}
      onClose={onClose}
    />
  ) : null;
}
LabelProtectionDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  skill: PropTypes.object.isRequired,
};
