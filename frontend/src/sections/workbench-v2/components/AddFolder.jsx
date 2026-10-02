import React, { useState } from "react";
import ModalWrapper from "../../../components/ModalWrapper/ModalWrapper";
import PropTypes from "prop-types";
import { Divider, TextField } from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "../../../utils/axios";
import { useNavigate } from "react-router";
import { Events, PropertyName, trackEvent } from "../../../utils/Mixpanel";
export default function AddFolder({ open, onClose }) {
  const [folderName, setFolderName] = useState("");

  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const { mutate, isPending } = useMutation({
    mutationFn: async (name) => {
      return axios.post(endpoints.develop.runPrompt.promptFolder, {
        name,
      });
    },
    onSuccess: (data) => {
      enqueueSnackbar(`${folderName} folder has been created`, {
        variant: "success",
      });
      onClose(); // close modal on success
      setFolderName(""); // reset field
      queryClient.invalidateQueries({
        queryKey: ["prompt-folders"],
      });
      queryClient.invalidateQueries({
        queryKey: ["folder-items", "all"],
      });
      if (data.data?.result?.id) {
        navigate(`/dashboard/workbench/${data.data?.result?.id}`);
      }
    },
  });

  const handleSubmit = () => {
    trackEvent(Events.promptNewFolderCreateClicked, {
      [PropertyName.name]: folderName,
    });
    mutate(folderName);
  };

  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      title={"Create new folder"}
      subTitle={
        "Save curated prompt templates for writing, coding, research, and more in folders."
      }
      hideCancelBtn
      actionBtnSx={{
        width: "100%",
      }}
      dialogActionSx={{
        mt: 0,
      }}
      isValid={folderName.trim().length > 0}
      onSubmit={handleSubmit}
      isLoading={isPending}
      actionBtnTitle="Create"
      modalWidth="500px"
    >
      <Divider
        sx={{
          borderColor: "divider",
        }}
      />
      <TextField
        autoFocus
        value={folderName}
        onChange={(e) => setFolderName(e.target.value)}
        size="small"
        label="Name"
        fullWidth
        onKeyDown={(e) => {
          if (e.key === "Enter" && folderName.trim().length > 0) {
            e.preventDefault(); // prevent form submit/reload
            handleSubmit();
          }
        }}
      />
    </ModalWrapper>
  );
}

AddFolder.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
};
