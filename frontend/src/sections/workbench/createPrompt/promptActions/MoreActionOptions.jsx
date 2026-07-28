import React, { forwardRef, useEffect, useRef, useState } from "react";
import { Box, IconButton, Popper, Typography } from "@mui/material";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";

import { usePromptWorkbenchContext } from "../WorkbenchContext";

const OptionItem = ({ onClick, icon, text, disabled, disabledText }) => {
  return (
    <CustomTooltip title={disabledText} show={disabled} placement="right" arrow>
      <Box
        sx={{
          display: "flex",
          gap: "6px",
          padding: "4px",
          cursor: "pointer",
        }}
        onClick={disabled ? undefined : onClick}
      >
        <IconButton
          sx={{
            padding: 0,
            margin: 0,
            "&:hover": {
              backgroundColor: "background.paper",
            },
          }}
        >
          <SvgColor
            src={icon}
            sx={{
              height: "16px",
              width: "16px",
              color: disabled ? "text.disabled" : "text.disabled",
            }}
          />
        </IconButton>
        <Typography
          variant="s3"
          fontWeight={"fontWeightRegular"}
          color={disabled ? "text.disabled" : "text.primary"}
        >
          {text}
        </Typography>
      </Box>
    </CustomTooltip>
  );
};

OptionItem.propTypes = {
  onClick: PropTypes.func,
  icon: PropTypes.string,
  text: PropTypes.string,
  disabled: PropTypes.bool,
  disabledText: PropTypes.string,
};

const MoreActionOptionsChild = (
  { open, id, onClose, handleRename, saveDefault, setSaveCommitOpen, data },
  ref,
) => {
  const popperRef = useRef(null);
  const [disableClickOutside] = useState(false);

  useEffect(() => {
    function handleClickOutside(event) {
      if (popperRef?.current && !popperRef?.current?.contains(event.target)) {
        onClose(false);
      }
    }
    if (!disableClickOutside) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [popperRef, open, disableClickOutside, onClose]);

  const { selectedVersions } = usePromptWorkbenchContext();

  return (
    <Popper
      id={id}
      anchorEl={ref?.current}
      open={open}
      ref={popperRef}
      placement="bottom"
      modifiers={modifier}
      onClick={(e) => e.stopPropagation()}
      sx={{ zIndex: 10 }}
    >
      <Box
        sx={{
          width: "140px",
          height: "100%",
          bgcolor: "background.paper",
          border: "1px solid",
          borderColor: "divider",
          // p: "12px",
          top: "2px",
          overflowY: "auto",
          borderRadius: "4px",
          position: "relative",
        }}
      >
        <Box
          sx={{
            padding: "8px",
            backgroundColor: "background.paper",
            borderRadius: "8px",
          }}
        >
          <Box sx={{ display: "flex", flexDirection: "column" }}>
            <OptionItem
              onClick={handleRename}
              icon="/assets/prompt/editPencil.svg"
              text="Rename"
            />
            <OptionItem
              onClick={() => setSaveCommitOpen(true)}
              icon="/assets/prompt/commit.svg"
              text="Save and commit"
              disabled={data?.isDraft || selectedVersions.length > 1}
              disabledText={
                data?.isDraft && selectedVersions.length === 1
                  ? "Please run the prompt before saving and committing"
                  : ""
              }
            />
            <OptionItem
              onClick={saveDefault}
              icon="/assets/prompt/saveDefault.svg"
              text="Set as default"
              disabled={data?.isDraft || selectedVersions.length > 1}
              disabledText={
                data?.isDraft && selectedVersions.length === 1
                  ? "Please run the prompt before setting it as default"
                  : ""
              }
            />
          </Box>
        </Box>
      </Box>
    </Popper>
  );
};

const moreActionOptionsPropTypes = {
  open: PropTypes.bool,
  id: PropTypes.string,
  onClose: PropTypes.func,
  handleRename: PropTypes.func,
  saveDefault: PropTypes.func,
  setSaveCommitOpen: PropTypes.func,
  data: PropTypes.shape({
    isDraft: PropTypes.bool,
  }),
};

MoreActionOptionsChild.propTypes = moreActionOptionsPropTypes;

// @ts-ignore
MoreActionOptionsChild.propTypes = moreActionOptionsPropTypes;
export const MoreActionOptions = forwardRef(MoreActionOptionsChild);
MoreActionOptions.propTypes = moreActionOptionsPropTypes;

const modifier = [
  {
    name: "flip",
    enabled: true,
    options: {
      altBoundary: true,
      rootBoundary: "document",
    },
  },
  {
    name: "preventOverflow",
    enabled: true,
    options: {
      altAxis: true,
      altBoundary: true,
      tether: true,
      rootBoundary: "document",
    },
  },
];
