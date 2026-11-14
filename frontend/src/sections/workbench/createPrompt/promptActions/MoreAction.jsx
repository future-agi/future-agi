import { Box } from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useMemo, useRef, useState } from "react";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";

import { MoreActionOptions } from "./MoreActionOptions";
import SaveAndCommit from "./SaveAndCommit";
import VersionStyle from "./VersionStyle";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

const MoreAction = ({ data, promptName, handleEdit }) => {
  const { id } = useParams();
  const btnRef = useRef(null);
  const [isMenuItems, setIsMenuItems] = useState(false);
  const [saveCommitOpen, setSaveCommitOpen] = useState(false);

  const { mutate: saveDefaultMutate } = useMutation({
    mutationFn: (id) => {
      return axios.post(endpoints.develop.runPrompt.saveDefaultPrompt(id), {
        version_name: data?.version,
      });
    },
    onSuccess: () => {
      setIsMenuItems(false);
      enqueueSnackbar(
        <>
          {promptName}&nbsp;
          <VersionStyle text={data?.version} />
          &nbsp; has been saved as a default
        </>,
        { variant: "info" },
      );
      trackEvent(Events.promptSaveClicked, {
        [PropertyName.promptId]: id,
      });
    },
  });

  const saveDefault = () => {
    saveDefaultMutate(id);
  };

  const handleRename = () => {
    setIsMenuItems(false);
    handleEdit();
  };

  const btnRefId = useMemo(
    () => (isMenuItems ? "action-menu" : undefined),
    [isMenuItems],
  );

  return (
    <Box sx={{ display: "flex", alignItems: "center" }}>
      <Iconify
        icon="solar:menu-dots-vertical-linear"
        width="24px"
        height="24px"
        sx={{
          cursor: "pointer",
          color: "text.primary",
          borderRadius: (theme) => theme.spacing(0.5),
          padding: (theme) => theme.spacing(0.5),
        }}
        ref={btnRef}
        aria-describedby={btnRefId}
        onClick={() => setIsMenuItems(true)}
      />
      <MoreActionOptions
        ref={btnRef}
        open={isMenuItems}
        onClose={() => setIsMenuItems(false)}
        id={btnRefId}
        saveDefault={saveDefault}
        handleRename={handleRename}
        setSaveCommitOpen={setSaveCommitOpen}
        data={data}
      />
      <SaveAndCommit
        open={saveCommitOpen}
        onClose={() => setSaveCommitOpen(false)}
        data={data}
        promptName={promptName}
      />
    </Box>
  );
};

export default MoreAction;

MoreAction.propTypes = {
  data: PropTypes.object,
  handleEdit: PropTypes.func,
  promptName: PropTypes.string,
};
