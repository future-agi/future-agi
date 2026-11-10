import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { MenuItem, Stack, TextField, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import ModalWrapper from "src/components/ModalWrapper";
import { usePromoteSkill } from "src/api/simulate-skills/skills";

export default function PromoteDialog({
  open,
  onClose,
  skill,
  version,
  testedVersions,
}) {
  const [tag, setTag] = useState("production");
  const [reason, setReason] = useState("");
  const promote = usePromoteSkill(skill?.id);
  const tested = testedVersions.has(version);

  useEffect(() => {
    if (open) {
      setTag("production");
      setReason("");
    }
  }, [open, version]);

  const submit = async () => {
    try {
      await promote.mutateAsync({ version, tag, reason });
      enqueueSnackbar(`${tag} now points at v${version}`, {
        variant: "success",
      });
      onClose();
    } catch (error) {
      enqueueSnackbar(error?.message || "Could not promote this version", {
        variant: "error",
      });
    }
  };

  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      onCancelBtn={onClose}
      onSubmit={submit}
      isValid={tested || reason.trim() !== ""}
      isLoading={promote.isPending}
      title={`Promote ${skill?.name} v${version}`}
      subTitle="Agents attached to the tag get this version from their next run. Earlier runs keep theirs."
      actionBtnTitle="Promote"
      modalWidth="520px"
    >
      <Stack spacing={1.5}>
        <TextField
          select
          size="small"
          label="Tag"
          value={tag}
          onChange={(event) => setTag(event.target.value)}
        >
          <MenuItem value="production">production</MenuItem>
          <MenuItem value="staging">staging</MenuItem>
        </TextField>
        {tested ? (
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            This version has a finished test.
          </Typography>
        ) : (
          <TextField
            label="Why is it going out untested?"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            multiline
            minRows={2}
            helperText="Kept with the promotion history."
            fullWidth
          />
        )}
      </Stack>
    </ModalWrapper>
  );
}

PromoteDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  skill: PropTypes.object,
  version: PropTypes.number,
  testedVersions: PropTypes.instanceOf(Set).isRequired,
};
