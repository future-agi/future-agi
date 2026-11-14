import {
  Button,
  CircularProgress,
  Stack,
  useTheme,
  IconButton,
} from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import { useNavigate } from "react-router";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import CustomTooltip from "src/components/tooltip";
import { ShowComponent } from "src/components/show";

import { createDraftPayload } from "../constant";

import { usePromptWorkbenchContext } from "./WorkbenchContext";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

export default function NavSection() {
  const theme = useTheme();
  const navigate = useNavigate();

  const { reset, setCurrentTab } = usePromptWorkbenchContext();

  const handleBackClick = () => {
    navigate(`/dashboard/workbench`, { replace: true });
  };

  const handleWritePrompt = () => {
    setCurrentTab("Playground");
    createDraft(createDraftPayload);
  };

  const { mutate: createDraft, isPending: isLoadingCreate } = useMutation({
    /**
     *
     * @param {Object} body
     * @returns
     */
    mutationFn: (body) =>
      axios.post(endpoints.develop.runPrompt.createPromptDraft, body),
    onSuccess: (data) => {
      enqueueSnackbar("Prompt created successfully.", {
        variant: "success",
      });
      reset();
      trackEvent(Events.promptTemplateCreated, {
        [PropertyName.click]: true,
        [PropertyName.promptId]: data?.data?.result?.root_template,
      });
      navigate(
        `/dashboard/workbench/create/${data?.data?.result?.root_template}`,
        { replace: true },
      );
    },
  });

  return (
    <Stack
      sx={{
        alignItems: "center",
      }}
      direction={"row"}
      justifyContent={"space-between"}
    >
      <Stack direction={"row"} gap={theme.spacing(1)} alignItems={"center"}>
        <Button
          onClick={handleBackClick}
          size="small"
          sx={{
            border: "1px solid",
            borderColor: "action.hover",
            color: "text.primary",
            display: "flex",
            alignItems: "center",
            flexDirection: "row",
            "& .MuiButton-startIcon": {
              marginRight: "4px",
            },
          }}
          startIcon={
            <Iconify
              icon="solar:alt-arrow-left-linear"
              width="16px"
              height="16px"
              sx={{ color: "text.primary" }}
            />
          }
        >
          Back
        </Button>
        <CustomTooltip show title="Add New Prompt" arrow size="small">
          <IconButton
            size="small"
            sx={{
              border: "1px solid",
              borderColor: "divider",
              color: "text.disabled",
            }}
            onClick={handleWritePrompt}
          >
            <ShowComponent condition={!isLoadingCreate}>
              <Iconify icon="solar:add-linear" width="16px" height="16px" />
            </ShowComponent>
            <ShowComponent condition={isLoadingCreate}>
              <CircularProgress size={16} sx={{ color: "text.primary" }} />
            </ShowComponent>
          </IconButton>
        </CustomTooltip>
      </Stack>
    </Stack>
  );
}
