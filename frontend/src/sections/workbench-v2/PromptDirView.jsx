import { Box, Divider, useTheme } from "@mui/material";
import React, { useEffect, useState, useCallback, useRef } from "react";
import FileSystem from "../../components/FileSystem/FileSystem";
import { Outlet } from "react-router";
import { useSearchParams } from "react-router-dom";
import AddFolder from "./components/AddFolder";
import CreateNewPrompt from "./components/CreateNewPrompt";
import { resetPromptState, usePromptStore } from "./store/usePromptStore";
import { ChoosePromptTemplateDrawer } from "src/sections/workbench/ChoosePromptTemplateDrawer.jsx";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "../../utils/axios";
import { FOLDERS } from "./common";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import {
  getPromptOnboardingRouteParams,
  PROMPT_ONBOARDING_MODES,
} from "../workbench/createPrompt/promptActions/promptOnboardingRoute";

export default function PromptDirView() {
  const theme = useTheme();
  const [openNewFOlderModal, setOpenNewFOlderModal] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(300);
  const [isResizing, setIsResizing] = useState(false);
  const containerRef = useRef(null);
  const onboardingActionHandledRef = useRef(false);
  const [searchParams] = useSearchParams();

  const {
    newPromptModal,
    setNewPromptModal,
    selectTemplateDrawerOpen,
    setSelectTemplateDrawerOpen,
  } = usePromptStore();

  const { data, isLoading } = useQuery({
    queryKey: ["prompt-folders"],
    queryFn: () => axios.get(endpoints.develop.runPrompt.promptFolder),
    select: (d) => d.data?.result,
  });

  const startResizing = () => {
    setIsResizing(true);
  };

  const stopResizing = useCallback(() => {
    setIsResizing(false);
  }, []);

  const resize = useCallback(
    (e) => {
      if (isResizing && containerRef.current) {
        const containerRect = containerRef.current.getBoundingClientRect();
        const newWidth = e.clientX - containerRect.left;

        // Set min and max width constraints
        const minWidth = 200;
        const maxWidth = containerRect.width * 0.4; // Max 60% of container width

        if (newWidth >= minWidth && newWidth <= maxWidth) {
          setSidebarWidth(newWidth);
        }
      }
    },
    [isResizing],
  );

  useEffect(() => {
    if (isResizing) {
      document.addEventListener("mousemove", resize);
      document.addEventListener("mouseup", stopResizing);
      // Add styles to prevent text selection while resizing
      document.body.style.userSelect = "none";
      document.body.style.cursor = "col-resize";
    } else {
      document.removeEventListener("mousemove", resize);
      document.removeEventListener("mouseup", stopResizing);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    }

    return () => {
      document.removeEventListener("mousemove", resize);
      document.removeEventListener("mouseup", stopResizing);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
  }, [isResizing, resize, stopResizing]);

  useEffect(() => {
    return () => {
      resetPromptState();
    };
  }, []);

  useEffect(() => {
    if (onboardingActionHandledRef.current) return;
    const { action, isOnboarding, mode } =
      getPromptOnboardingRouteParams(searchParams);
    if (
      isOnboarding &&
      (action === PROMPT_ONBOARDING_MODES.CREATE_PROMPT ||
        mode === PROMPT_ONBOARDING_MODES.CREATE_PROMPT)
    ) {
      onboardingActionHandledRef.current = true;
      setNewPromptModal(true);
      trackEvent(Events.promptCreateClicked, {
        source: "onboarding",
      });
    }
  }, [searchParams, setNewPromptModal]);

  const handleAddNewFolder = () => {
    setOpenNewFOlderModal(true);
    trackEvent(Events.promptAddNewFolderClicked, {
      [PropertyName.click]: true,
    });
  };

  const handleFolderClick = ({ _folderName, id }) => {
    if (id === "my-templates") {
      trackEvent(Events.promptMyTemplatesClicked, {
        source: "folder-list",
      });
    }
  };

  return (
    <>
      <Box
        ref={containerRef}
        sx={{
          display: "flex",
          flexDirection: "row",
          height: "100vh",
          overflowY: "clip",
        }}
      >
        <Box
          sx={{
            width: `${sidebarWidth}px`,
            height: "100vh",
            padding: theme.spacing(2),
            overflowY: "auto",
            minWidth: "200px",
            maxWidth: "60%",
            backgroundColor: "background.paper",
          }}
        >
          <FileSystem
            nodes={data}
            isLoading={isLoading}
            onAddNew={handleAddNewFolder}
            mainFolders={FOLDERS}
            onFolderClick={handleFolderClick}
          />
        </Box>

        <Divider
          orientation="vertical"
          sx={{
            borderColor: "divider",
            cursor: "col-resize",
            width: "4px",
            // backgroundColor: isResizing ? theme.palette.primary.main : "transparent",
            transition: "backgroundColor 0.2s ease",
            position: "relative",
            "&::after": {
              content: '""',
              position: "absolute",
              top: 0,
              left: "-2px",
              right: "-2px",
              bottom: 0,
              cursor: "col-resize",
            },
          }}
          onMouseDown={startResizing}
        />

        <Box
          sx={{
            flex: 1,
            height: "100vh",
            overflow: "hidden",
          }}
        >
          <Outlet />
        </Box>
      </Box>

      <AddFolder
        open={openNewFOlderModal}
        onClose={() => setOpenNewFOlderModal(false)}
      />
      <CreateNewPrompt
        open={newPromptModal}
        onClose={() => setNewPromptModal(false)}
      />
      <ChoosePromptTemplateDrawer
        open={selectTemplateDrawerOpen}
        onClose={() => setSelectTemplateDrawerOpen(false)}
      />
    </>
  );
}
