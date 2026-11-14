import PropTypes from "prop-types";
import React, { useState } from "react";
import ModalWrapper from "../../../components/ModalWrapper/ModalWrapper";
import {
  Box,
  Divider,
  Stack,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { PROMPT_ICON_MAPPER } from "../common";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import logger from "src/utils/logger";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useParams } from "react-router";
export default function SavePromptTemplate({ open, onClose, data }) {
  const [templateName, setTemplateName] = useState("");
  const theme = useTheme();
  const { id } = useParams();

  const { mutate, isPending } = useMutation({
    mutationFn: async ({ name, versionId }) => {
      return axios.post(endpoints.develop.runPrompt.promptTemplate, {
        name,
        prompt_version: versionId,
      });
    },
    onSuccess: () => {
      enqueueSnackbar("Template saved successfully", {
        variant: "success",
      });
      onClose();
      setTemplateName("");
    },
  });

  const handleSaveTemplate = () => {
    const versionId = data?.versionId;

    // validate
    if (!templateName?.trim()) {
      logger.error("Template name is required");
      return;
    }
    if (!versionId) {
      logger.error("Version ID is missing");
      return;
    }

    trackEvent(Events.promptSaveAsTemplateConfirmed, {
      [PropertyName.name]: templateName,
      [PropertyName.id]: id,
    });

    // safe to call mutate
    mutate({
      name: templateName.trim(),
      versionId,
    });
  };
  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      title={"Save as template"}
      subTitle={
        "Save curated prompt templates for writing, coding, research, and more in folders"
      }
      hideCancelBtn
      actionBtnSx={{
        width: "100%",
      }}
      dialogActionSx={{
        mt: 3,
      }}
      isValid={templateName?.trim()?.length > 0}
      onSubmit={handleSaveTemplate}
      isLoading={isPending}
      modalWidth="560px"
    >
      <Divider />
      <Stack
        direction={"row"}
        alignItems={"center"}
        gap={1.5}
        sx={{
          padding: 2,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
        }}
      >
        <Box
          sx={{
            height: 44,
            width: 44,
            bgcolor: "background.paper",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
          }}
        >
          <Box
            component="img"
            src={PROMPT_ICON_MAPPER["PROMPT"]}
            sx={{
              height: 20,
              width: 20,
            }}
          />
        </Box>
        <Stack>
          <Typography
            typography={"m3"}
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            {data?.prompt_name}
            <Typography
              component={"span"}
              typography={"s2"}
              fontWeight={"fontWeightRegular"}
              sx={{
                ml: 1.5,
                padding: theme.spacing(0.25, 0.75),
                bgcolor: "action.hover",
                color: "text.primary",
              }}
            >
              {data?.version}
            </Typography>
          </Typography>
          {data?.created_by && (
            <Typography
              color={"text.disabled"}
              typography={"s2"}
              fontWeight={"fontWeightRegular"}
            >
              Created by {data?.created_by}
            </Typography>
          )}
        </Stack>
      </Stack>
      <Typography
        variant="s1"
        fontWeight={"fontWeightRegular"}
        color={"text.primary"}
      >
        Folder: My templates
      </Typography>
      <TextField
        autoFocus
        size="small"
        label="Template name"
        value={templateName}
        onChange={(e) => setTemplateName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && templateName.trim().length > 0) {
            e.preventDefault(); // prevent form submit/reload
            handleSaveTemplate();
          }
        }}
      />
    </ModalWrapper>
  );
}

SavePromptTemplate.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  data: PropTypes.object,
};
