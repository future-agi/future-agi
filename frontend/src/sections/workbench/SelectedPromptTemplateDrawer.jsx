import {
  Box,
  Button,
  Divider,
  Drawer,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useEffect, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import { ReadOnlyPrompt } from "../develop-detail/RunPrompt/Modals/ImportPrompt";
import { usePromptStoreShallow } from "../workbench-v2/store/usePromptStore";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useNavigate, useParams } from "react-router";
import { createDraftPayload } from "src/sections/workbench/constant";
import { DefaultMessages } from "./constant";
import { LoadingButton } from "@mui/lab";
import { usePromptWorkbenchContext } from "./createPrompt/WorkbenchContext";
import { getRandomId } from "src/utils/utils";
import { ConfirmDialog } from "src/components/custom-dialog";

export const SelectedPromptTemplateDrawer = ({
  open,
  onClose,
  data = {},
  importMode,
}) => {
  const { folder } = useParams();
  const navigate = useNavigate();
  const {
    setSelectTemplateDrawerOpen,
    selectedPromptIndex,
    setSelectedPromptIndex,
  } = usePromptStoreShallow((state) => ({
    setSelectTemplateDrawerOpen: state.setSelectTemplateDrawerOpen,
    selectedPromptIndex: state.selectedPromptIndex,
    setSelectedPromptIndex: state.setSelectedPromptIndex,
  }));

  const timeoutRef = useRef(null);
  const { prompts, setPrompts, setModelConfig } = usePromptWorkbenchContext();
  const [isConfirmationModalOpen, setIsConfirmationModalOpen] = useState(false);
  const { mutate: createDraft, isPending: isCreatingDraft } = useMutation({
    mutationFn: (body) =>
      axios.post(endpoints.develop.runPrompt.createPromptDraft, body),
    onSuccess: (data) => {
      onClose();
      setSelectTemplateDrawerOpen(false);
      enqueueSnackbar("Prompt created successfully.", {
        variant: "success",
      });
      trackEvent(Events.promptCreateClicked, {
        [PropertyName.click]: true,
      });
      timeoutRef.current = setTimeout(() => {
        navigate(
          `/dashboard/workbench/create/${data?.data?.result?.root_template}`,
          {
            state: { fromOption: "use-template" },
          },
        );
      }, 0);
    },
  });

  const handleImportPrompt = useCallback(() => {
    const newPrompts =
      data?.promptConfig?.prompt_config_snapshot?.messages?.map((rest) => ({
        ...rest,
        id: getRandomId(),
      })) || [];
    const newModelConfig =
      data?.promptConfig?.prompt_config_snapshot?.configuration || {};

    // Create a copy of the existing prompts
    const updatedPrompts = [...prompts];

    // Replace the prompt at the specific index
    updatedPrompts[selectedPromptIndex] = {
      prompts: newPrompts,
      id: getRandomId(),
    };

    // Update the state
    setPrompts(updatedPrompts);
    setModelConfig((prev) => {
      const prevConfigs = [...prev];
      prevConfigs[selectedPromptIndex] = newModelConfig;
      return prevConfigs;
    });

    onClose();
    setSelectTemplateDrawerOpen(false);
    setSelectedPromptIndex(0);
    return;
  }, [
    data?.promptConfig?.prompt_config_snapshot?.messages,
    data?.promptConfig?.prompt_config_snapshot?.configuration,
    prompts,
    selectedPromptIndex,
    setPrompts,
    setModelConfig,
    onClose,
    setSelectTemplateDrawerOpen,
    setSelectedPromptIndex,
  ]);

  const handleWritePrompt = useCallback(() => {
    if (!createDraft) return;
    if (!data?.id) return;
    if (importMode) {
      if (prompts?.[selectedPromptIndex]?.prompts?.length > 0) {
        setIsConfirmationModalOpen(true);
      } else {
        handleImportPrompt();
      }
    }
    if (!folder) return;

    trackEvent(Events.promptUseThisTemplateClicked, {
      [PropertyName.click]: true,
      [PropertyName.id]: data?.id,
    });

    const messages =
      data?.promptConfig?.prompt_config_snapshot?.messages ?? DefaultMessages;
    const configuration = {
      ...data?.promptConfig?.prompt_config_snapshot?.configuration,
      model_detail: {
        ...data?.promptConfig?.prompt_config_snapshot?.configuration
          ?.model_detail,
        type:
          data?.promptConfig?.prompt_config_snapshot?.configuration
            ?.model_detail?.type ?? "llm",
      },
      output_format:
        data?.promptConfig?.prompt_config_snapshot?.configuration
          ?.output_format ?? "string",
    };
    // const placeholders = data?.promptConfig?.prompt_config_snapshot?.placeholders;

    if (!messages || messages.length === 0) return;

    // @ts-ignore
    createDraft({
      ...createDraftPayload,
      prompt_base_template: data.id,
      ...(folder !== "all" && folder !== "my-templates"
        ? { prompt_folder: folder }
        : {}),
      prompt_config: [
        {
          messages,
          ...(configuration &&
          typeof configuration === "object" &&
          !Array.isArray(configuration)
            ? { configuration }
            : {}),
          // ...(Array.isArray(placeholders) && placeholders.length > 0
          //   ? { placeholders }
          //   : {}),
        },
      ],
    });
  }, [
    createDraft,
    data.id,
    data?.promptConfig?.prompt_config_snapshot?.messages,
    data?.promptConfig?.prompt_config_snapshot?.configuration,
    importMode,
    folder,
    prompts,
    selectedPromptIndex,
    handleImportPrompt,
  ]);

  useEffect(() => {
    return () => {
      clearTimeout(timeoutRef);
    };
  }, []);

  return (
    <>
      <Drawer
        anchor="right"
        PaperProps={{
          sx: {
            height: "100vh",
            position: "fixed",
            overflowY: "hidden",
            zIndex: 9999,
            borderRadius: "0 !important",
            backgroundColor: "background.paper",
            width: "80%",
          },
        }}
        ModalProps={{
          BackdropProps: {
            style: { backgroundColor: "transparent" },
          },
        }}
        open={open}
      >
        <Stack
          sx={{
            padding: "16px",
            paddingBottom: "12px",
            alignItems: "center",
          }}
          display={"flex"}
          flexDirection={"row"}
          justifyContent={"space-between"}
        >
          <Button
            size="small"
            startIcon={
              <Iconify
                icon="formkit:left"
                width={24}
                height={24}
                color={"text.primary"}
              />
            }
            onClick={onClose}
            disabled={isCreatingDraft}
            sx={{
              color: "text.disabled",
              padding: "4px 12px",
              height: "30px",
              typography: "s1",
              fontWeight: "fontWeightMedium",
              "& .MuiButton-startIcon": {
                marginRight: "10px",
              },
            }}
          >
            Back
          </Button>

          <IconButton disabled={isCreatingDraft} onClick={onClose}>
            <Iconify
              color={isCreatingDraft ? "text.disabled" : "text.primary"}
              icon="mingcute:close-line"
            />
          </IconButton>
        </Stack>
        <Divider orientation="horizontal" />
        <Stack sx={{ padding: "16px", gap: "12px", pb: "20px" }}>
          <Box
            sx={{
              display: "flex",
              flexDirection: "row",
              justifyContent: "space-between",
            }}
          >
            <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
              <Typography
                fontSize={"fontWeightMedium"}
                color="text.primary"
                typography="m3"
                fontWeight={"fontWeightSemiBold"}
              >
                {data?.promptConfig?.name}
              </Typography>
              <Typography
                typography="s2"
                color="text.secondary"
                fontWeight={"fontWeightRegular"}
              >
                Generate engaging blog posts on any topic with SEO optimization
                and clear structure.
              </Typography>
            </Box>

            <LoadingButton
              loading={isCreatingDraft}
              disabled={isCreatingDraft}
              variant="contained"
              color="primary"
              sx={{
                px: "24px",
                borderRadius: "6px",
                height: "38px",
              }}
              onClick={handleWritePrompt}
            >
              <Typography typography="s1" fontWeight={"fontWeightMedium"}>
                Use this template
              </Typography>
            </LoadingButton>
          </Box>
        </Stack>
        <Stack
          sx={{
            px: "16px",
            gap: 1.5,
            overflowY: "auto",
            pb: "4rem",
          }}
        >
          {data?.promptConfig?.prompt_config_snapshot?.messages?.map(
            (message) => (
              <ReadOnlyPrompt
                key={getRandomId()}
                title={message.role}
                prompt={message.content}
                allColumns={[]}
              />
            ),
          )}
          {/* {data?.promptConfig?.prompt_config_snapshot?.placeholders?.map(
        (placeholder, index) => (
          <Box
            key={index}
            sx={{
                   backgroundColor: theme.palette.mode === "light" ? "whiteScale.100" : "background.default",
              borderRadius: "4px",
              padding: "12px 16px",
              display: "flex",
              flexDirection: "column",
              gap: "8px",
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              variant="s1"
              fontWeight={"fontWeightMedium"}
              color={"text.primary"}
            >
              Placeholder
            </Typography>

            <Typography
              variant="s1"
              fontWeight={"fontWeightRegular"}
              color={"text.primary"}
              sx={{ wordWrap: "break-word" }}
            >
              {placeholder}
            </Typography>
          </Box>
        ),
      )} */}
        </Stack>
      </Drawer>
      <ConfirmDialog
        content="Are you sure you want to close? Your work will be lost"
        action={
          <Button
            variant="contained"
            color="error"
            size="small"
            onClick={handleImportPrompt}
          >
            Continue
          </Button>
        }
        open={isConfirmationModalOpen}
        onClose={() => {
          setIsConfirmationModalOpen(false);
        }}
        title="Confirm Action"
        message="Are you sure you want to proceed?"
      />
    </>
  );
};

SelectedPromptTemplateDrawer.propTypes = {
  onClose: PropTypes.func,
  open: PropTypes.bool,
  data: PropTypes.object,
  importMode: PropTypes.bool,
  promptIndex: PropTypes.number,
};
