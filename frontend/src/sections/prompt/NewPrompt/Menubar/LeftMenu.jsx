import React, { useState } from "react";
import {
  Box,
  Chip,
  Drawer,
  IconButton,
  Tooltip,
  TextField,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useParams } from "react-router";
import PropTypes from "prop-types";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";

import Versions from "../TopMenuOptions/Versions";
import PromtLeftSidebar from "../../PromtSidebar/PromtLeftSidebar";
import logger from "src/utils/logger";

const ICON_BUTTONS = [
  { icon: "solar:add-linear", action: "add", tooltip: "New Prompt" },
  { icon: "solar:list-linear", action: "list", tooltip: "Your Prompts" },
];

const LeftMenu = ({
  versionList,
  currentIndex,
  versionIndex,
  setVersionIndex,
  setCurrentIndex,
  handleDelete,
  searchQuery,
  setSearchQuery,
  handleCreateDraft,
  currentTitle,
  setCurrentTitle,
  setVersionList,
}) => {
  const { id } = useParams();
  const [open, setOpen] = useState(false);
  // const [showVersion,setShowVersion] = useState()
  const [drawerAnchor, setDrawerAnchor] = useState("left");
  const [showVersion, setShowVersion] = useState(false);
  const [showLeftSideBar, setShowLeftSideBar] = useState(false);

  const handleAction = (action) => {
    switch (action) {
      case "add":
        handleCreateDraft();
        break;
      case "list":
        setOpen(true);
        setShowLeftSideBar(true);
        setShowVersion(false);
        setDrawerAnchor("left");
        break;
      default:
        logger.warn("Unknown action:", action);
    }
  };

  const { mutate: changeName } = useMutation({
    /**
     *
     * @param {Object} variables
     * @param {Object} variables.name
     */
    mutationFn: ({ name }) => {
      return axios.post(endpoints.develop.runPrompt.getNameChange(id), {
        name: name,
      });
    },
    onSuccess: () => {
      // enqueueSnackbar("Name Changed successfully", { variant: "success" });
    },
    onError: (error) => {
      // enqueueSnackbar("Failed to Change Name", { variant: "error" });
      logger.error("Something went wrong", error);
    },
  });

  const handleNameChange = (event, value) => {
    if (event.key === "Enter") {
      event.preventDefault();
      const data = { name: value };
      changeName(data);
      trackEvent(Events.promptRenamed, {
        [PropertyName.originalName]: currentTitle,
        [PropertyName.newName]: data,
        [PropertyName.propId]: id,
      });
      document.activeElement?.blur();
    }
  };

  const handleTitleChange = (event) => {
    setCurrentTitle(event.target.value);
  };

  const handleDeletePrompt = (id) => {
    handleDelete(id);
  };

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: "12px",
        paddingTop: 0.5,
      }}
    >
      {ICON_BUTTONS.map(({ icon, action, tooltip }) => (
        <Tooltip key={icon} title={tooltip} placement="bottom" arrow>
          <IconButton size="small" onClick={() => handleAction(action)}>
            <Iconify
              sx={{ cursor: "pointer" }}
              height={24}
              width={24}
              icon={icon}
              color="text.disabled"
            />
          </IconButton>
        </Tooltip>
      ))}

      <TextField
        size="small"
        value={currentTitle}
        onChange={handleTitleChange}
        onBlur={(e) => {
          e.preventDefault();
          const data = { name: currentTitle };
          changeName(data);
        }}
        onKeyDown={(event) => handleNameChange(event, currentTitle)}
        InputProps={{ disableUnderline: true }}
        sx={{
          // width: "120px",
          flexGrow: 1,
          fontSize: "16px",
          "& .MuiInputBase-input": {
            padding: 0,
          },
        }}
      />

      <Box sx={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <Chip
          color="primary"
          variant="soft"
          label={
            versionList?.[versionIndex]?.templateVersion.toUpperCase() ?? "V1"
          }
          onClick={() => {
            // setDrawerComponent(<Versions onClose={() => setOpen(false)} />);
            setShowVersion(true);
            setShowLeftSideBar(false);
            setDrawerAnchor("right");
            setOpen(true);
          }}
        />
        {versionList?.[versionIndex]?.isDraft ? (
          <Chip color="warning" variant="soft" label="Draft" />
        ) : null}
      </Box>

      <Drawer
        anchor={drawerAnchor}
        open={open}
        onClose={() => setOpen(false)}
        PaperProps={{
          sx: {
            height: "100vh",
            width: "480px",
            position: "fixed",
            zIndex: 9999,
            borderRadius: "8px",
            backgroundColor: "background.paper",
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
            padding: "12px 0 36px 0",
            display: "flex",
            flexDirection: "column",
            height: "100%",
            flex: "1",
          }}
        >
          {/* {drawerComponent} */}
          {showVersion ? (
            <Versions
              versionList={versionList}
              onClose={() => {
                setOpen(false);
                setShowVersion(false);
              }}
              versionIndex={versionIndex}
              setVersionIndex={setVersionIndex}
            />
          ) : null}
          {showLeftSideBar ? (
            <PromtLeftSidebar
              currentIndex={currentIndex}
              onDelete={handleDeletePrompt}
              setCurrentIndex={setCurrentIndex}
              closeSidebar={() => {
                setOpen(false);
                setShowLeftSideBar(false);
              }}
              searchQuery={searchQuery}
              setSearchQuery={setSearchQuery}
              fromLeftMenu={true}
              setVersionList={setVersionList}
              setVersionIndex={setVersionIndex}
            />
          ) : null}
        </Box>
      </Drawer>
    </Box>
  );
};

export default LeftMenu;

LeftMenu.propTypes = {
  versionList: PropTypes.array.isRequired,
  currentIndex: PropTypes.number.isRequired,
  setCurrentIndex: PropTypes.func.isRequired,
  handleDelete: PropTypes.func,
  searchQuery: PropTypes.string,
  setSearchQuery: PropTypes.func,
  handleCreateDraft: PropTypes.func,
  currentTitle: PropTypes.string,
  setCurrentTitle: PropTypes.func,
  versionIndex: PropTypes.number,
  setVersionIndex: PropTypes.func,
  setVersionList: PropTypes.func,
};
