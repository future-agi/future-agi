import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Stack, TextField } from "@mui/material";
import CustomDialog from "../Common/CustomDialog/CustomDialog";
import ExperimentTagsField from "src/sections/common/Tags/ExperimentTagsField";
import { useExperimentTagsDraft } from "src/sections/common/Tags/useTags";

/** `Edit…` from the experiment menu: name, description and tags (W5 C-H1). */
const RenameExperimentDialog = ({ target, onClose, onSave, loading }) => {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  useEffect(() => {
    setName(target?.name ?? "");
    setDescription(target?.description ?? "");
  }, [target]);
  const trimmed = name.trim();
  const tags = useExperimentTagsDraft(target?.id, { enabled: !!target });
  return (
    <CustomDialog
      open={!!target}
      onClose={onClose}
      title="Edit experiment"
      actionButton="Save"
      isData={
        !!trimmed &&
        (trimmed !== target?.name ||
          description !== (target?.description ?? "") ||
          tags.dirty)
      }
      loading={loading || tags.saving}
      onClickAction={() =>
        tags
          .save()
          .then(() =>
            // Tags alone: saved, close (no rename toast for an unchanged name).
            trimmed !== target?.name ||
            description !== (target?.description ?? "")
              ? onSave({ id: target.id, name: trimmed, description })
              : onClose(),
          )
          .catch(() => {})
      }
    >
      <Stack spacing={2} sx={{ pt: 1, width: 420 }}>
        <TextField
          label="Name"
          size="small"
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          error={!trimmed}
          helperText={!trimmed ? "Name cannot be empty" : " "}
          inputProps={{ maxLength: 120 }}
        />
        <TextField
          label="Description"
          size="small"
          multiline
          minRows={2}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What changed in this experiment"
        />
        <ExperimentTagsField draft={tags} />
      </Stack>
    </CustomDialog>
  );
};

RenameExperimentDialog.propTypes = {
  target: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  loading: PropTypes.bool,
};

export default RenameExperimentDialog;
