import { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Stack, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";

import { useRestoreSkillVersion } from "src/api/skills";
import { ConfirmDialog } from "src/components/custom-dialog";
import ReleaseRuleNote from "../components/ReleaseRuleNote";
import LabelProtectionDialog from "../governance/LabelProtectionDialog";

export default function RestoreVersionDialog({ skill, number, onClose }) {
  const restore = useRestoreSkillVersion(skill.id);
  const [error, setError] = useState("");
  const [rulesOpen, setRulesOpen] = useState(false);
  // UXF-09: restoring publishes a version and never moves a label; the note says
  // who may move the protected ones afterwards. Built from the skill's current
  // protected labels only; with no list loaded it says nothing.
  const protectedLabels = Array.isArray(skill.protectedLabelNames) ? skill.protectedLabelNames : null;

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
    <>
      <ConfirmDialog
        open
        title={`Restore v${number} as new version?`}
        content={
          <Stack spacing={1.5} sx={{ minWidth: 0 }}>
            {error && <Alert severity="error">{error}</Alert>}
            <Typography variant="s1" color="text.secondary" sx={{ overflowWrap: "anywhere" }}>
              {`Publish the files from v${number} as a new version of ${skill.name}. Existing versions and labels will not change.`}
            </Typography>
            {protectedLabels && (
              <ReleaseRuleNote
                rules={{ protectedLabels }}
                canChangeRules={skill.permissions?.canManageLabelProtection === true}
                onChangeRules={() => setRulesOpen(true)}
              />
            )}
          </Stack>
        }
        onClose={() => {
          if (!restore.isPending) onClose();
        }}
        closeDisabled={restore.isPending}
        action={
          <Button
            variant="contained" color="primary"
            disabled={restore.isPending}
            onClick={handleRestore}
          >
            {restore.isPending ? "Restoring…" : "Restore as new version"}
          </Button>
        }
      />
      {rulesOpen && <LabelProtectionDialog open onClose={() => setRulesOpen(false)} skill={skill} />}
    </>
  );
}

RestoreVersionDialog.propTypes = {
  skill: PropTypes.shape({
    id: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
    protectedLabelNames: PropTypes.arrayOf(PropTypes.string),
    permissions: PropTypes.object,
  }).isRequired,
  number: PropTypes.number.isRequired,
  onClose: PropTypes.func.isRequired,
};
