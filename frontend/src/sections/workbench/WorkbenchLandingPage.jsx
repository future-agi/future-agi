import React, { useMemo, useCallback } from "react";
import { Box, Typography, useTheme } from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { useNavigate } from "react-router";
import LandingPageCard from "src/components/LandingPageCard/LandingPageCard";

import { createDraftPayload } from "./constant";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const WorkbenchLandingPage = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const { role } = useAuthContext();
  const { mutate: createDraft, isPending: isLoadingCreate } = useMutation({
    mutationFn: (body) =>
      axios.post(endpoints.develop.runPrompt.createPromptDraft, body),
    onSuccess: (data) => {
      enqueueSnackbar("Prompt created successfully.", {
        variant: "success",
      });
      trackEvent(Events.promptCreateNewClicked, {
        [PropertyName.click]: true,
        [PropertyName.promptId]: data?.data?.result?.root_template,
      });
      navigate(
        `/dashboard/workbench/create/${data?.data?.result?.root_template}`,
      );
    },
  });

  const handleWritePrompt = useCallback(() => {
    createDraft(createDraftPayload);
  }, [createDraft]);

  const prompts = useMemo(() => {
    return [
      {
        image: "/assets/prompt/write-prompt.svg",
        title: "Write a prompt from scratch",
        description:
          "Start from scratch to create a clear, goal-oriented prompt tailored to your needs.",
        onClick: handleWritePrompt,
        availableFeature: true,
        disabled:
          isLoadingCreate || !RolePermission.PROMPTS[PERMISSIONS.CREATE][role],
        loading: isLoadingCreate,
      },
      {
        image: "/assets/prompt/generate-prompt.svg",
        title: "Generate a Prompt",
        description:
          "Start with a ready-made prompt template. Select an option and tailor it to fit your specific needs.",
        onClick: () => {},
        availableFeature: false,
        disabled: true,
        loading: false,
      },
      {
        image: "/assets/prompt/improve-prompt.svg",
        title: "Improve an Existing Prompt",
        description:
          "Refine what you have to make your output clearer, smarter, and more effective.",
        onClick: () => {},
        availableFeature: false,
        disabled: true,
        loading: false,
      },
    ];
  }, [handleWritePrompt, isLoadingCreate, role]);

  return (
    <Box
      padding={theme.spacing(2)}
      height={"100%"}
      sx={{
        backgroundColor: "background.paper",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box
        sx={{
          textAlign: "center",
          height: "100%",
          alignContent: "center",
        }}
      >
        <Box display={"flex"} flexDirection={"column"} gap={0.5}>
          <Typography
            variant="l3"
            fontWeight={"fontWeightMedium"}
            color="text.primary"
            sx={{ marginBottom: "4px" }}
          >
            Prompt Management
          </Typography>
          <Typography
            variant="s1"
            fontWeight={"fontWeightRegular"}
            sx={{ marginBottom: "24px", color: "text.disabled" }}
          >
            Generate a new prompt or improve an existing one to get better, more
            accurate results.
          </Typography>
        </Box>
        <Box
          display="flex"
          justifyContent="center"
          gap={theme.spacing(2)}
          flexWrap={"wrap"}
        >
          {prompts.map((item, index) => (
            <Box
              flex={1}
              key={index}
              onClick={item.onClick}
              sx={{ cursor: item.availableFeature ? "pointer" : "default" }}
            >
              <LandingPageCard
                title={item.title}
                description={item.description}
                image={item.image}
                showAction={true}
                {...item}
              />
            </Box>
          ))}
        </Box>
      </Box>
    </Box>
  );
};

export default WorkbenchLandingPage;
