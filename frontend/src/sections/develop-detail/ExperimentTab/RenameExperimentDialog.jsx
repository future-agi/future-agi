import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Stack, TextField } from "@mui/material";
import CustomDialog from "../Common/CustomDialog/CustomDialog";
import ExperimentTagsField from "src/sections/common/Tags/ExperimentTagsField";
import { useExperimentTagsDraft } from "src/sections/common/Tags/useTags";
import { useExperimentNameCheck } from "src/api/develop/experiment-create";
import { useDebounce } from "src/hooks/use-debounce";
import {
  RENAME_COPY,
  descriptionProblem,
  nameProblem,
  renameErrorOf,
} from "src/api/develop/experiment-metadata";

/**
 * `Edit…` from the experiment menu: name, description and tags (W5 C-H1).
 * exp.rename: Save sends only what changed; the runs and their results stay.
 * A refusal keeps the dialog open with dev's words next to its field.
 * `onSave` returns the save's promise (it rejects with the request error).
 */
const RenameExperimentDialog = ({
  target,
  datasetId,
  onClose,
  onSave,
  loading,
}) => {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [failure, setFailure] = useState(null);
  useEffect(() => {
    setName(target?.name ?? "");
    setDescription(target?.description ?? "");
    setFailure(null);
  }, [target]);
  const trimmed = name.trim();
  const nameChanged = Boolean(target) && trimmed !== (target?.name ?? "");
  const descriptionChanged =
    Boolean(target) && description !== (target?.description ?? "");
  // The name check reads dev's validate-name (same rule as the save: one
  // live experiment per name in a dataset, case-sensitive). After a save
  // that could not be confirmed the new name may already be this
  // experiment's own, so the check waits until the user edits again
  // (an edit clears `failure`); a retry of the same rename is safe.
  const unconfirmed = Boolean(failure?.unknown);
  const checkedName = useDebounce(trimmed, 400);
  const nameCheck = useExperimentNameCheck(datasetId, checkedName, {
    enabled:
      nameChanged &&
      !unconfirmed &&
      checkedName === trimmed &&
      !nameProblem(trimmed),
  });
  const taken =
    nameChanged &&
    !unconfirmed &&
    checkedName === trimmed &&
    nameCheck?.data === false;
  const nameError =
    nameProblem(trimmed) ??
    (taken ? RENAME_COPY.duplicateName : null) ??
    failure?.fields?.name ??
    null;
  const descriptionError =
    descriptionProblem(description) ?? failure?.fields?.description ?? null;
  const tags = useExperimentTagsDraft(target?.id, { enabled: !!target });
  const busy = Boolean(loading || tags.saving);

  const save = async () => {
    setFailure(null);
    try {
      await tags.save();
    } catch {
      // The tags field shows its own error.
      return;
    }
    // Tags alone: saved, close (no rename toast for an unchanged name).
    if (!nameChanged && !descriptionChanged) {
      onClose();
      return;
    }
    try {
      await onSave({
        id: target.id,
        ...(nameChanged && { name: trimmed }),
        ...(descriptionChanged && { description }),
      });
    } catch (error) {
      setFailure(renameErrorOf(error));
    }
  };

  return (
    <CustomDialog
      open={!!target}
      onClose={onClose}
      title="Edit experiment"
      actionButton="Save"
      isData={
        !nameError &&
        !descriptionError &&
        (nameChanged || descriptionChanged || tags.dirty)
      }
      loading={busy}
      onClickAction={save}
    >
      <Stack spacing={2} sx={{ pt: 1, width: { xs: "100%", sm: 420 } }}>
        {failure?.message ? (
          <Alert severity={failure.unknown ? "warning" : "error"}>
            {failure.message}
          </Alert>
        ) : null}
        <TextField
          id="rename-experiment-name"
          label="Name"
          size="small"
          required
          autoFocus
          value={name}
          disabled={busy}
          onChange={(e) => {
            setName(e.target.value);
            setFailure(null);
          }}
          error={Boolean(nameError)}
          helperText={nameError ?? " "}
        />
        <TextField
          id="rename-experiment-description"
          label="Description"
          size="small"
          multiline
          minRows={2}
          value={description}
          disabled={busy}
          onChange={(e) => {
            setDescription(e.target.value);
            setFailure(null);
          }}
          error={Boolean(descriptionError)}
          helperText={descriptionError ?? " "}
          placeholder="What changed in this experiment"
        />
        <ExperimentTagsField draft={tags} />
      </Stack>
    </CustomDialog>
  );
};

RenameExperimentDialog.propTypes = {
  target: PropTypes.object,
  datasetId: PropTypes.string,
  onClose: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  loading: PropTypes.bool,
};

export default RenameExperimentDialog;
