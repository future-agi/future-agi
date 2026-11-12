import PropTypes from "prop-types";
import React, { useState } from "react";
import { useSnackbar } from "notistack";
import { Stack, Typography } from "@mui/material";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { useChangeVersion } from "src/api/agents/agentDetail";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import FolderUploadField from "src/sections/agent-playground/connect/FolderUploadField";

/**
 * A new version of an uploaded agent from a folder: the folder is uploaded as
 * one archive, then the version is made from it and read. The same archive
 * again makes no version.
 */
export default function UploadVersionDialog({ agent, open, onClose }) {
  const { enqueueSnackbar } = useSnackbar();
  const change = useChangeVersion(agent.id);
  const [folder, setFolder] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [archiveId, setArchiveId] = useState(null);
  const next = `v${(agent.versionCount || 0) + 1}`;

  const submit = () =>
    change.mutate(
      { kind: "upload", archive_artifact_id: archiveId },
      {
        onSuccess: (data) => {
          onClose();
          const label = data.version.version_name_display;
          enqueueSnackbar(
            data.up_to_date
              ? `${label} already has this code`
              : `${label} created from the upload; it is being read`,
            { variant: data.up_to_date ? "info" : "success" },
          );
        },
        onError: (e) => enqueueSnackbar(errorMessage(e), { variant: "error" }),
      },
    );

  const busy = uploading || change.isPending;
  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      isLoading={busy}
      title={`Add version ${next}`}
      actionBtnTitle={`Save as ${next}`}
      isValid={!!archiveId}
      onSubmit={submit}
      modalWidth="560px"
    >
      <Stack spacing={1.5}>
        <Typography sx={{ typography: "s2_1", color: "text.secondary" }}>
          Choose the folder with the new code. It is read once, like the first,
          and saves an agent version only; environments following production
          test it on their next run.
        </Typography>
        <FolderUploadField
          folder={folder}
          onUploaded={(uploaded) => {
            setFolder(uploaded);
            setArchiveId(uploaded.sourceId);
          }}
          onUploading={(on) => {
            setUploading(on);
            if (on) setArchiveId(null);
          }}
          disabled={change.isPending}
          testId="upload-version-dropzone"
        />
      </Stack>
    </ModalWrapper>
  );
}

UploadVersionDialog.propTypes = {
  agent: PropTypes.object.isRequired,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};
