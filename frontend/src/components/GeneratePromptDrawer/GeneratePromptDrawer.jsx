import {
  Drawer,
  IconButton,
  Stack,
  Typography,
  useTheme,
  Box,
  Button,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import SvgColor from "../svg-color";
import { Events, trackEvent } from "src/utils/Mixpanel";
import { LoadingButton } from "@mui/lab";
import { useMutation } from "@tanstack/react-query";
import { z } from "zod";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import GeneratePromptForm from "./GeneratePromptForm";
import GeneratedPromptField from "./GeneratedPromptField";
import { ConfirmDialog } from "../custom-dialog";
import { enqueueSnackbar } from "notistack";
import { GENERATE_PROMPT_BUTTONS } from "src/utils/constants";
import { usePromptStreamUrl } from "src/sections/workbench/createPrompt/hooks/usePromptStreamUrl";
import {
  handleAuthFailClose,
  handleWsErrorFrame,
  runPromptOverSocket,
} from "src/sections/workbench/createPrompt/common";
import { useBeforeUnload } from "src/hooks/useBeforeUnload";
import { useActiveSocket } from "src/hooks/use-active-socket";

const schema = z.object({
  prompt: z.string().min(1, "Prompt is required"),
});

export default function GeneratePromptDrawer({
  open,
  onClose,
  promptFor,
  onApplyPrompt,
  allColumns,
}) {
  const theme = useTheme();
  const [isConfirmDialogOpen, setIsConfirmDialogOpen] = useState(false);
  const promptStreamUrl = usePromptStreamUrl();
  const [activeSocketRef, closeActiveSocket] = useActiveSocket();
  const [streamedText, setStreamedText] = useState("");

  const {
    control,
    handleSubmit,
    reset,
    formState: { isValid, isDirty },
    setValue,
  } = useForm({
    resolver: zodResolver(schema),
    defaultValues: {
      prompt: "",
    },
  });
  const [generatedPrompt, setGeneratedPrompt] = useState("");
  const [loadingStage, setLoadingStage] = useState("");
  const [isGeneratingPrompt, setIsGeneratingPrompt] = useState(false);

  const handleButtonClick = (label) => {
    trackEvent(Events.samplePromptSelected);
    setValue("prompt", label, {
      shouldValidate: true,
      shouldDirty: true,
    });
  };

  const { mutate: generatePrompt } = useMutation({
    mutationFn: (prompt) => {
      return new Promise((resolve, reject) => {
        // We can receive two different "completed" messages:
        // one from llm streaming activity completion and one canonical final completion.
        // settled ensures we resolve/reject only once.
        let settled = false;
        const isSettled = () => settled;
        const markSettled = () => {
          settled = true;
        };
        const resetGenerationState = () => {
          setIsGeneratingPrompt(false);
          setLoadingStage("");
          setGeneratedPrompt("");
        };
        const completeGeneration = (message, promptText) => {
          if (settled || !promptText) return;
          settled = true;
          setStreamedText("");
          setIsGeneratingPrompt(false);
          setGeneratedPrompt(promptText);
          resolve(message);
        };

        // @ts-ignore
        const socket = runPromptOverSocket({
          url: promptStreamUrl,
          payload: {
            statement: prompt,
            type: "generate_prompt",
          },
          onMessage: (data) => {
            const wsData = data;
            // Surface top-level BE error frames (permission, workspace, etc.)
            // before the type filter so the spinner doesn't hang on 4003/4004.
            const handled = handleWsErrorFrame({
              wsData,
              isSettled,
              markSettled,
              cleanup: resetGenerationState,
              reject,
              defaultMessage: "Failed to generate prompt",
            });
            if (handled) return;
            if (wsData?.type !== "generate_prompt") return;
            const generatePromptData = wsData;
            const current_activity = generatePromptData?.current_activity;
            if (current_activity) {
              setLoadingStage(current_activity);
            }
            const status = generatePromptData?.status;
            if (status === "error") {
              enqueueSnackbar(
                generatePromptData?.error || "Failed to generate prompt",
                {
                  variant: "error",
                },
              );
              setIsGeneratingPrompt(false);
              setLoadingStage("");
              setGeneratedPrompt("");
              return;
            }
            if (
              status === "running" &&
              current_activity === "generate_initial_prompt"
            ) {
              setStreamedText((prev) => prev + generatePromptData?.chunk);
            } else if (status === "completed") {
              // Complete only on canonical final prompt, or final-stage chunk completion
              const promptText =
                generatePromptData?.prompt ||
                (current_activity === "generate_initial_prompt"
                  ? generatePromptData?.chunk
                  : "");
              completeGeneration(data, promptText);
            }
          },
          onError: (err) => {
            enqueueSnackbar(
              typeof err === "string"
                ? err
                : "Failed to connect. Please try again.",
              {
                variant: "error",
              },
            );
            reject(err);
          },
          onClose: (event) => {
            const handled = handleAuthFailClose({
              event,
              isSettled,
              markSettled,
              cleanup: () => setIsGeneratingPrompt(false),
              reject,
            });
            if (handled) return;
            if (settled) return;
            settled = true;
            setIsGeneratingPrompt(false);
            reject(
              new Error("WebSocket closed before prompt generation completed"),
            );
          },
        });
        activeSocketRef.current = socket;
      });
    },
    onSuccess: () => {},
    onError: () => {
      setIsGeneratingPrompt(false);
    },
  });

  const promptGeneratingMode = isGeneratingPrompt || generatedPrompt;

  const handleGeneratePrompt = (data) => {
    setLoadingStage("UNDERSTANDING");
    setIsGeneratingPrompt(true);
    setGeneratedPrompt("");
    generatePrompt(data?.prompt);
  };

  const handleClose = (force = false) => {
    const forceOrEvent = typeof force === "boolean" ? force : false;
    if (!forceOrEvent && (isDirty || promptGeneratingMode)) {
      setIsConfirmDialogOpen(true);
    } else {
      closeActiveSocket();
      reset();
      onClose();
      setGeneratedPrompt("");
      setIsConfirmDialogOpen(false);
      setIsGeneratingPrompt(false);
      setLoadingStage("");
    }
  };

  const handleApplyPrompt = () => {
    if (typeof promptFor !== "number" && !generatedPrompt) return;
    onApplyPrompt(promptFor, generatedPrompt);
    handleClose(true);
  };

  // show leave site warning when prompt is being generated
  useBeforeUnload(isGeneratingPrompt || !!generatedPrompt);

  return (
    <>
      <Drawer
        anchor="right"
        open={open}
        onClose={handleClose}
        PaperProps={{
          sx: {
            height: "100vh",
            width: "590px",
            position: "fixed",
            zIndex: 2,
            boxShadow: "-10px 0px 100px #00000035",
            backgroundColor: "background.paper",
            overflow: "visible",
          },
        }}
        ModalProps={{
          BackdropProps: {
            style: { backgroundColor: "transparent" },
          },
        }}
      >
        <Box
          sx={{
            padding: theme.spacing(2),
            display: "flex",
            flexDirection: "column",
            rowGap: theme.spacing(2),
            height: "80%",
            flex: 1,
          }}
        >
          <Stack
            direction={"row"}
            gap={theme.spacing(2)}
            alignItems={"flex-start"}
            justifyContent={"space-between"}
          >
            <Stack direction={"column"} gap={0}>
              <Typography
                variant="m3"
                fontWeight={"fontWeightSemiBold"}
                color={"text.primary"}
              >
                {promptGeneratingMode ? "Your prompt" : "Generate a prompt"}
              </Typography>
              <Typography
                variant="s1"
                color="text.primary"
                fontWeight={"fontWeightRegular"}
              >
                {promptGeneratingMode
                  ? `You'll be able to make further changes and improvements later too.`
                  : "You can generate a prompt template by sharing basic details about your task."}
              </Typography>
            </Stack>
            <IconButton
              onClick={handleClose}
              sx={{
                padding: theme.spacing(0.5),
                margin: 0,
              }}
            >
              <SvgColor
                sx={{
                  color: "text.primary",
                  height: theme.spacing(3),
                  width: theme.spacing(3),
                }}
                src="/assets/icons/ic_close.svg"
              />
            </IconButton>
          </Stack>
          {promptGeneratingMode ? (
            <GeneratedPromptField
              loadingStage={loadingStage}
              allColumns={allColumns}
              generatedPrompt={generatedPrompt}
              streamedText={streamedText}
            />
          ) : (
            <GeneratePromptForm
              onSubmit={handleSubmit(handleGeneratePrompt)}
              control={control}
            />
          )}
          {!promptGeneratingMode && (
            <Stack
              direction={"row"}
              flexWrap={"wrap"}
              gap={theme.spacing(2, 3)}
            >
              {GENERATE_PROMPT_BUTTONS?.map(({ name, src }, index) => (
                <Button
                  type="button"
                  key={index}
                  sx={{
                    display: "flex",
                    flexDirection: "row",
                    gap: theme.spacing(1),
                    backgroundColor: "background.neutral",
                    borderRadius: theme.spacing(0.25),
                    padding: theme.spacing(0.5, 1),
                    border: "1px solid",
                    borderColor: "divider",
                    transition: "border-color 200ms ease-in-out",
                    "&:hover": {
                      borderColor: "divider",
                      backgroundColor: "background.neutral",
                    },
                    "&:focus": {
                      borderColor: "primary.main",
                      outline: "none",
                    },
                    "&:active": {
                      borderColor: "primary.light",
                      backgroundColor: "background.neutral",
                    },
                  }}
                  onClick={(e) => {
                    e.currentTarget.blur();
                    handleButtonClick(name);
                  }}
                >
                  <SvgColor
                    sx={{
                      height: theme.spacing(20 / 8),
                      width: theme.spacing(20 / 8),
                      color: "text.disabled",
                    }}
                    src={src}
                  />
                  <Typography
                    variant="s1"
                    color={"text.primary"}
                    fontWeight={"fontWeightRegular"}
                  >
                    {name}
                  </Typography>
                </Button>
              ))}
            </Stack>
          )}
        </Box>
        <Stack
          direction={"row"}
          sx={{
            gap: theme.spacing(2),
            margin: theme.spacing(2),
            backgroundColor: "background.paper",
          }}
        >
          <Button
            variant="outlined"
            fullWidth
            onClick={handleClose}
            sx={{
              borderRadius: theme.spacing(1),
              minHeight: theme.spacing(38 / 8),
              "&:hover": {
                borderColor: "transparent !important",
              },
            }}
          >
            <Typography
              variant="s1"
              color={"text.disabled"}
              fontWeight={"fontWeightMedium"}
            >
              Cancel
            </Typography>
          </Button>
          <LoadingButton
            type="button"
            onClick={
              generatedPrompt
                ? handleApplyPrompt
                : handleSubmit(handleGeneratePrompt)
            }
            fullWidth
            disabled={isGeneratingPrompt || (!isValid && !generatedPrompt)}
            loading={isGeneratingPrompt}
            variant="contained"
            color="primary"
            sx={{
              borderRadius: theme.spacing(1),
              minHeight: theme.spacing(38 / 8),
              color: "primary.contrastText",
            }}
          >
            <Typography fontWeight={"fontWeightSemiBold"} variant="s1">
              {generatedPrompt ? "Continue" : "Generate"}
            </Typography>
          </LoadingButton>
        </Stack>
      </Drawer>
      <ConfirmDialog
        content="Are you sure you want to close? Your work will be lost"
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => handleClose(true)}
          >
            Confirm
          </Button>
        }
        open={isConfirmDialogOpen}
        onClose={() => setIsConfirmDialogOpen(false)}
        title="Confirm Action"
        message="Are you sure you want to close?"
      />
    </>
  );
}

GeneratePromptDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  promptFor: PropTypes.number.isRequired,
  onApplyPrompt: PropTypes.func,
  allColumns: PropTypes.array,
};
