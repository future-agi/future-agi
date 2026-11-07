import { useState } from "react";
import PropTypes from "prop-types";
import { Button } from "@mui/material";
import { enqueueSnackbar } from "notistack";

import { useRestoreSkillVersion } from "src/api/skills";
import { ConfirmDialog } from "src/components/custom-dialog";

export default function RestoreVersionDialog({ skill, number, onClose }) {
  const restore = useRestoreSkillVersion(skill.id);
  const [error, setError] = useState("");

  const handleRestore = async () => {
    setError("");
    try {
      const result = await restore.mutateAsync({ number });
      enqueueSnackbar(
        result.unchanged
          ? "These files already match the latest version. No new version was created."
          : `Restored as v${result.version.number}. Labels have not moved.`,
        { variant: "success" },
      );
      onClose();
    } catch {
      setError(
        "Could not restore this version. Try again. No labels have been moved.",
      );
    }
  };

  return (
    <ConfirmDialog
      open
      title={`Restore v${number} as new version?`}
      content={
        error ||
        `Publish the files from v${number} as a new version of ${skill.name}. Existing versions and labels will not change.`
      }
      onClose={() => {
        if (!restore.isPending) onClose();
      }}
      closeDisabled={restore.isPending}
      action={
        <Button
          variant="contained"
          disabled={restore.isPending}
          onClick={handleRestore}
        >
          {restore.isPending ? "Restoring…" : "Restore as new version"}
        </Button>
      }
    />
  );
}

RestoreVersionDialog.propTypes = {
  skill: PropTypes.shape({
    id: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
  }).isRequired,
  number: PropTypes.number.isRequired,
  onClose: PropTypes.func.isRequired,
};
