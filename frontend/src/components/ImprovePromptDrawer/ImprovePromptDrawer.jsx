import { Box, Button, Collapse, Drawer } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation } from "@tanstack/react-query";
import ImprovePromptFormLeft from "./ImprovePromptFormLeft";
import ImprovedPrompt from "./ImprovedPrompt";
import { ConfirmDialog } from "../custom-dialog";
import { enqueueSnackbar } from "notistack";
import { extractTextFromPrompt } from "./common";
import logger from "src/utils/logger";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useParams } from "react-router";
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
export default function ImprovePromptDrawer({
  open,
  onClose,
  variables,
  existingPrompt,
  onApplyPrompt,
  promptFor,
}) {
  const { id } = useParams();
  const extractedPromptText = extractTextFromPrompt(existingPrompt);
  const [showImrpovedPrompt, setShowImrpovedPrompt] = useState(false);
  const [openConfirmDialog, setOpenConfirmDialog] = useState(false);
  const [currentPrompt, setCurrentPrompt] = useState(0);
  const [streamedText, setStreamedText] = useState("");
  const [loadingStage, setLoadingStage] = useState("");
  const [isImprovingPrompt, setIsImprovingPrompt] = useState(false);
  const [followUpPrompts, setFollowUpPrompts] = useState([]);
  const followUpMessagesContainerRef = useRef(null);
  const [promptHistory, setPromptHistory] = useState([]);
  const scrollTimeoutRef = useRef(null);
  const improvedPromptCloseClicked = useRef(false);
  const [activeSocketRef, closeActiveSocket] = useActiveSocket();
  const promptStreamUrl = usePromptStreamUrl();

  const {
    control,
    handleSubmit,
    reset,
    formState: { isValid },
  } = useForm({
    resolver: zodResolver(schema),
    defaultValues: {
      prompt: "",
    },
  });

  const { mutate: improvePrompt } = useMutation({
    mutationFn: (data) => {
      return new Promise((resolve, reject) => {
        // We can receive two different "completed" messages:
        // one from llm streaming activity completion and one canonical final completion.
        // settled ensures we resolve/reject only once.
        let settled = false;
        const isSettled = () => settled;
        const markSettled = () => {
          settled = true;
        };
        const resetImprovementState = () => {
          setIsImprovingPrompt(false);
          setLoadingStage("");
        };
        const completeImprovement = (message, promptText) => {
          if (settled || !promptText) return;
          settled = true;
          setStreamedText("");
          setIsImprovingPrompt(false);
          const copy = [...promptHistory, promptText];
          setPromptHistory(copy);
          setCurrentPrompt(copy.length - 1);
          resolve(message);
        };

        // @ts-ignore
        const socket = runPromptOverSocket({
          url: promptStreamUrl,
          payload: {
            existing_prompt: data?.existingPrompt,
            improvement_requirements: data?.prompt,
            type: "improve_prompt",
          },
          onMessage: (data) => {
            const wsData = data;
            // Surface top-level BE error frames (permission, workspace, etc.)
            // before the type filter so the spinner doesn't hang on 4003/4004.
            const handled = handleWsErrorFrame({
              wsData,
              isSettled,
              markSettled,
              cleanup: resetImprovementState,
              reject,
              defaultMessage: "Failed to improve prompt",
            });
            if (handled) return;
            if (wsData?.type !== "improve_prompt") return;
            const generatePromptData = wsData;
            const current_activity = generatePromptData?.current_activity;
            if (current_activity) {
              setLoadingStage(current_activity);
            }
            const status = generatePromptData?.status;
            if (status === "error") {
              enqueueSnackbar(
                generatePromptData?.error || "Failed to improve prompt",
                {
                  variant: "error",
                },
              );
              setIsImprovingPrompt(false);
              setLoadingStage("");
              if (promptHistory.length > 0) {
                setCurrentPrompt(promptHistory.length - 1);
              }
              return;
            }
            if (
              status === "running" &&
              current_activity === "generate_refined_prompt"
            ) {
              setStreamedText((prev) => prev + generatePromptData?.chunk);
            } else if (status === "completed") {
              // Complete only on canonical final prompt, or final-stage chunk completion
              const promptText =
                generatePromptData?.prompt ||
                (current_activity === "generate_refined_prompt"
                  ? generatePromptData?.chunk
                  : "");
              completeImprovement(data, promptText);
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
              cleanup: () => setIsImprovingPrompt(false),
              reject,
            });
            if (handled) return;
            if (settled) return;
            settled = true;
            setIsImprovingPrompt(false);
            reject(
              new Error("WebSocket closed before prompt improvement completed"),
            );
          },
        });
        activeSocketRef.current = socket;
      });
    },
    onSuccess: () => {
      trackEvent(Events.promptImprovePromptClicked, {
        [PropertyName.promptId]: id,
      });
    },
    onError: (error) => {
      logger.error("Error generating prompt:", error);
    },
  });

  const hadnleImprovePrompt = (data) => {
    setLoadingStage("generate_planning");
    setIsImprovingPrompt(true);
    const promptData = { ...data };
    if (promptHistory.length === 0) {
      promptData["existingPrompt"] = extractedPromptText;
    } else {
      promptData["existingPrompt"] = promptHistory[currentPrompt];
    }
    handleAppendPrompt(data?.prompt);
    reset();
    improvePrompt(promptData);
    setShowImrpovedPrompt(true);

    //  clear previous timeout
    if (scrollTimeoutRef.current) {
      clearTimeout(scrollTimeoutRef.current);
      scrollTimeoutRef.current = null;
    }

    // Ensure scrolling happens after the DOM updates
    scrollTimeoutRef.current = setTimeout(() => {
      if (followUpMessagesContainerRef.current) {
        followUpMessagesContainerRef.current.scrollTo({
          top: followUpMessagesContainerRef.current.scrollHeight,
          behavior: "smooth",
        });
      }
    }, 0);
  };

  const handleAppendPrompt = (prompt) => {
    setFollowUpPrompts((prev) => [...prev, prompt]);
  };

  const handleClose = () => {
    closeActiveSocket();
    onClose();
    setShowImrpovedPrompt(false);
    setOpenConfirmDialog(false);
    setFollowUpPrompts([]);
    setLoadingStage("");
    setPromptHistory([]);
    setIsImprovingPrompt(false);
    reset();
  };

  const handleConfirmClose = () => {
    if (improvedPromptCloseClicked.current) {
      improvedPromptCloseClicked.current = false;
      setShowImrpovedPrompt(false);
      setOpenConfirmDialog(false);
    } else {
      handleClose();
    }
  };

  const handleSoftClose = () => {
    if (isValid || isImprovingPrompt || promptHistory?.length > 0) {
      setOpenConfirmDialog(true);
    } else {
      handleClose();
    }
  };

  const promptController = {
    hasNext: currentPrompt + 1 < promptHistory.length,
    hasPrevious: currentPrompt - 1 >= 0,
    onNext: () => {
      setCurrentPrompt((prev) =>
        prev + 1 < promptHistory.length ? prev + 1 : prev,
      );
    },
    onPrevious: () => {
      setCurrentPrompt((prev) => (prev - 1 >= 0 ? prev - 1 : prev));
    },
    copyCurrent: () => {
      navigator.clipboard.writeText(promptHistory[currentPrompt]);
      enqueueSnackbar("Prompt copied", { variant: "success" });
    },
    apply: () => {
      if (typeof promptFor !== "number" && !promptHistory[currentPrompt])
        return;
      onApplyPrompt(promptFor, promptHistory[currentPrompt]);
      handleConfirmClose();
    },
    isImprovingPrompt,
  };

  // show leave site warning when prompt is being generated
  useBeforeUnload(isImprovingPrompt || promptHistory?.length > 0);

  // cleanup scroll timeout on unmount (socket cleanup handled by useActiveSocket)
  useEffect(() => {
    return () => {
      if (scrollTimeoutRef.current) {
        clearTimeout(scrollTimeoutRef.current);
        scrollTimeoutRef.current = null;
      }
    };
  }, []);

  return (
    <>
      <Drawer
        anchor="right"
        open={open}
        onClose={handleSoftClose}
        PaperProps={{
          sx: {
            height: "100vh",
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
          sx={{ display: "flex", height: "100%", justifyContent: "flex-end" }}
        >
          <ImprovePromptFormLeft
            handleHideShowImprovePrompt={() => {
              setShowImrpovedPrompt(false);
            }}
            hadnleShowImprovedPrompt={() => setShowImrpovedPrompt(true)}
            handleClose={handleSoftClose}
            followUpMessagesContainerRef={followUpMessagesContainerRef}
            followUpPrompts={followUpPrompts}
            onSubmit={handleSubmit(hadnleImprovePrompt)}
            control={control}
            isImprovingPrompt={isImprovingPrompt}
            isValid={isValid}
          />
          <Collapse in={showImrpovedPrompt} orientation="horizontal">
            <ImprovedPrompt
              variables={variables}
              handleClose={() => {
                improvedPromptCloseClicked.current = true;
                handleSoftClose();
              }}
              improvedPrompt={promptHistory[currentPrompt]}
              promptController={promptController}
              loadingStage={loadingStage}
              streamedText={streamedText}
            />
          </Collapse>
        </Box>
      </Drawer>
      <ConfirmDialog
        content="Are you sure you want to close? Your work will be lost"
        action={
          <Button
            variant="contained"
            color="error"
            size="small"
            onClick={handleConfirmClose}
          >
            Confirm
          </Button>
        }
        open={openConfirmDialog}
        onClose={() => {
          improvedPromptCloseClicked.current = false;
          setOpenConfirmDialog(false);
        }}
        title="Confirm Action"
        message="Are you sure you want to proceed?"
      />
    </>
  );
}

ImprovePromptDrawer.propTypes = {
  onClose: PropTypes.func.isRequired,
  open: PropTypes.bool.isRequired,
  variables: PropTypes.array,
  existingPrompt: PropTypes.string,
  onApplyPrompt: PropTypes.func,
  promptFor: PropTypes.number,
};
