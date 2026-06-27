import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import ModalWrapper from "../../../components/ModalWrapper/ModalWrapper";
import { Box, Typography } from "@mui/material";
import FormSearchSelectFieldState from "../../../components/FromSearchSelectField/FormSearchSelectFieldState";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { useParams } from "react-router";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { PROMPT_ITEM_TYPES } from "../common";

const MoveItem = ({ id, open, onClose, name, folderId }) => {
  const [selectedFolder, setSelectedFolder] = useState("");
  const queryClient = useQueryClient();
  const { folder } = useParams();

  const { data: folders } = useQuery({
    queryKey: ["prompt-folders"],
    queryFn: () => axios.get(endpoints.develop.runPrompt.promptFolder),
    select: (d) =>
      d.data?.result?.map((item) => ({
        value: item?.id,
        label: item?.name,
      })),
    enabled: false,
  });

  const { mutate, isPending } = useMutation({
    mutationFn: async (selectedFolder) => {
      return axios.post(endpoints.develop.runPrompt.movePrompt(id), {
        prompt_folder_id: selectedFolder,
      });
    },
    onSuccess: () => {
      enqueueSnackbar("Prompt moved successfully", {
        variant: "success",
      });
      onClose();
      const foldersToRefresh = new Set([folder, selectedFolder, "all"]);

      foldersToRefresh.forEach((folderKey) => {
        if (!folderKey) return;
        queryClient.invalidateQueries({
          queryKey: ["folder-items", folderKey],
        });
      });
    },
  });

  const currentFolder = useMemo(() => {
    if (!folderId) return { label: "All prompts", value: "all" };

    return folders?.find((f) =>
      folderId ? f?.value === folderId : f?.value === folder,
    );
  }, [folder, folders, folderId]);

  const handleMove = () => {
    if (!selectedFolder) return;
    trackEvent(Events.promptMoveConfirmed, {
      [PropertyName.type]: PROMPT_ITEM_TYPES.FILE,
      [PropertyName.currentLocation]: currentFolder?.value,
      [PropertyName.newLocation]: selectedFolder,
    });
    mutate(selectedFolder);
  };

  return (
    <ModalWrapper
      hideCancelBtn
      actionBtnTitle="Move"
      open={open}
      onClose={onClose}
      title={`Move "${name}"`}
      actionBtnSx={{
        width: "100%",
      }}
      isValid={!!selectedFolder}
      isLoading={isPending}
      onSubmit={handleMove}
    >
      <Box
        sx={{
          padding: 2,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
        }}
      >
        <Typography
          typography={"s2"}
          color={"text.disabled"}
          fontWeight={"fontWeightRegular"}
        >
          Current location
        </Typography>
        <Typography
          color="text.primary"
          typography={"m3"}
          fontWeight={"fontWeightMedium"}
        >
          {currentFolder?.label}
        </Typography>
      </Box>
      <FormSearchSelectFieldState
        size="small"
        label="Select folder"
        value={selectedFolder}
        options={folders?.filter((f) => f?.value !== folder)}
        onChange={(e) => setSelectedFolder(e.target.value)}
      />
    </ModalWrapper>
  );
};

export default MoveItem;

MoveItem.propTypes = {
  id: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  name: PropTypes.string,
  folderId: PropTypes.string,
};
