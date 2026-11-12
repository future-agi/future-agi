import { Box, Button, Divider, useMediaQuery, useTheme } from "@mui/material";
import React, { useEffect, useState, useCallback, useRef } from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import FileSystem from "../../components/FileSystem/FileSystem";
import { Outlet } from "react-router";
import AddFolder from "./components/AddFolder";
import CreateNewPrompt from "./components/CreateNewPrompt";
import { resetPromptState, usePromptStore } from "./store/usePromptStore";
import { ChoosePromptTemplateDrawer } from "src/sections/workbench/ChoosePromptTemplateDrawer.jsx";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "../../utils/axios";
import { FOLDERS } from "./common";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

const FOLDER_RAIL_ID = "prompt-folders";

export default function PromptDirView() {
  const theme = useTheme();
  const [openNewFOlderModal, setOpenNewFOlderModal] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(224);
  const [isResizing, setIsResizing] = useState(false);
  const containerRef = useRef(null);
  // On a phone (below sm) a 224 px rail left the prompt list
  // about 160 px. There the rail folds behind a Folders button above the
  // list, and opens full width under it.
  const isPhone = useMediaQuery(theme.breakpoints.down("sm"));
  const [foldersOpen, setFoldersOpen] = useState(false);

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

  const handleAddNewFolder = () => {
    setOpenNewFOlderModal(true);
    trackEvent(Events.promptAddNewFolderClicked, {
      [PropertyName.click]: true,
    });
  };

  const handleFolderClick = ({ _folderName, id }) => {
    // On a phone, picking a folder folds the rail so its prompts show.
    setFoldersOpen(false);
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
          flexDirection: isPhone ? "column" : "row",
          height: "100vh",
          overflowY: "clip",
        }}
      >
        {isPhone && (
          <Box
            sx={{
              paddingX: theme.spacing(2),
              paddingTop: theme.spacing(1.5),
              backgroundColor: "background.paper",
            }}
          >
            <Button
              size="small"
              variant="outlined"
              aria-expanded={foldersOpen}
              aria-controls={FOLDER_RAIL_ID}
              onClick={() => setFoldersOpen((open) => !open)}
              startIcon={
                <SvgColor src="/assets/icons/ic_folder.svg" width={16} />
              }
              endIcon={
                <Iconify
                  icon={
                    foldersOpen
                      ? "solar:alt-arrow-up-linear"
                      : "solar:alt-arrow-down-linear"
                  }
                  width={16}
                />
              }
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              Folders
            </Button>
          </Box>
        )}
        <Box
          id={FOLDER_RAIL_ID}
          hidden={isPhone && !foldersOpen}
          sx={
            isPhone
              ? {
                  width: "100%",
                  maxHeight: "45vh",
                  flexShrink: 0,
                  padding: theme.spacing(1.5),
                  overflowY: "auto",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  backgroundColor: "background.paper",
                }
              : {
                  width: `${sidebarWidth}px`,
                  height: "100vh",
                  padding: theme.spacing(1.5),
                  overflowY: "auto",
                  minWidth: "200px",
                  maxWidth: "60%",
                  backgroundColor: "background.paper",
                }
          }
        >
          <FileSystem
            nodes={data}
            isLoading={isLoading}
            onAddNew={handleAddNewFolder}
            mainFolders={FOLDERS}
            onFolderClick={handleFolderClick}
          />
        </Box>

        {!isPhone && (
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
        )}

        <Box
          sx={{
            flex: 1,
            minWidth: 0,
            minHeight: 0,
            height: isPhone ? "auto" : "100vh",
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
