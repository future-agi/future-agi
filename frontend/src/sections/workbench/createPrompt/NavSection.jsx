import { Box, Button, CircularProgress, Stack, useTheme } from "@mui/material";
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
            py: theme.spacing(0.5),
            px: theme.spacing(1.5),
            borderRadius: "4px",
            "& .MuiButton-startIcon": {
              marginRight: "4px",
            },
          }}
          startIcon={
            <Iconify
              icon="octicon:chevron-left-24"
              width="16px"
              height="16px"
              sx={{ color: "text.primary" }}
            />
          }
        >
          Back
        </Button>
        <CustomTooltip show title="Add New Prompt" arrow size="small">
          <Box
            sx={{
              cursor: "pointer",
              border: "1px solid",
              borderColor: "divider",
              color: "text.disabled",
              borderRadius: theme.spacing(0.5),
              padding: theme.spacing(0.5),
              lineHeight: 0,
              height: "30px",
              width: "30px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
            onClick={handleWritePrompt}
          >
            <ShowComponent condition={!isLoadingCreate}>
              <Iconify icon="eva:plus-fill" width="16px" height="16px" />
            </ShowComponent>
            <ShowComponent condition={isLoadingCreate}>
              <CircularProgress size={16} sx={{ color: "text.primary" }} />
            </ShowComponent>
          </Box>
        </CustomTooltip>
      </Stack>
    </Stack>
  );
}
