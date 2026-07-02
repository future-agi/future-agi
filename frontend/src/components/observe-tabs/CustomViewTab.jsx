import React, { useRef, useState, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  IconButton,
  Typography,
  TextField,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

const CustomViewTab = ({
  view,
  shortcut,
  isActive,
  isDirty,
  isRenaming,
  onClick,
  onClose,
  onContextMenu,
  onRenameSubmit,
  onRenameCancel,
}) => {
  const theme = useTheme();
  const [renameValue, setRenameValue] = useState(view.name);
  const inputRef = useRef(null);

  useEffect(() => {
    if (isRenaming) {
      setRenameValue(view.name);
      // Focus after render
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [isRenaming, view.name]);

  const handleRenameKeyDown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (renameValue.trim()) {
        onRenameSubmit(view.id, renameValue.trim());
      }
    } else if (e.key === "Escape") {
      onRenameCancel();
    }
  };

  const handleRenameBlur = () => {
    if (renameValue.trim() && renameValue.trim() !== view.name) {
      onRenameSubmit(view.id, renameValue.trim());
    } else {
      onRenameCancel();
    }
  };

  const tabKey = `view-${view.id}`;

  return (
    <CustomTooltip
      show
      title={
        !isActive && shortcut ? `${view.name} · Press ${shortcut}` : view.name
      }
      placement="bottom"
      arrow
      size="small"
      type="black"
    >
      <ButtonBase
        onClick={() => onClick(tabKey)}
        onContextMenu={(e) => {
          e.preventDefault();
          onContextMenu(e.clientX, e.clientY, view.id);
        }}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          height: 26,
          px: "8px",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          bgcolor: isActive ? "action.hover" : "background.paper",
          color: "text.primary",
          transition: "background-color 100ms",
          "&:hover": {
            bgcolor: isActive ? "action.selected" : "background.neutral",
          },
          "&:hover .close-btn": { opacity: 1 },
        }}
      >
        <Iconify
          icon="mdi:eye-outline"
          width={14}
          sx={{ color: "text.primary", flexShrink: 0 }}
        />
        {isRenaming ? (
          <TextField
            inputRef={inputRef}
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={handleRenameKeyDown}
            onBlur={handleRenameBlur}
            onClick={(e) => e.stopPropagation()}
            variant="standard"
            size="small"
            sx={{
              width: 100,
              "& .MuiInput-input": { fontSize: 13, py: 0 },
            }}
            InputProps={{ disableUnderline: false }}
          />
        ) : (
          <Typography
            sx={{
              fontSize: 13,
              fontWeight: 500,
              fontFamily: "'IBM Plex Sans', sans-serif",
              color: "text.primary",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              maxWidth: 200,
              lineHeight: "20px",
            }}
          >
            {view.name}
          </Typography>
        )}
        {!isRenaming && (
          <Iconify
            className="close-btn"
            icon="mdi:close"
            width={12}
            onClick={(e) => {
              e.stopPropagation();
              onClose(view.id);
            }}
            sx={{
              opacity: 0,
              color: "text.disabled",
              cursor: "pointer",
              flexShrink: 0,
              transition: "opacity 100ms",
              "&:hover": { color: "text.primary" },
            }}
          />
        )}
      </ButtonBase>
    </CustomTooltip>
  );
};

CustomViewTab.propTypes = {
  view: PropTypes.shape({
    id: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
  }).isRequired,
  shortcut: PropTypes.string,
  isActive: PropTypes.bool.isRequired,
  isDirty: PropTypes.bool,
  isRenaming: PropTypes.bool,
  onClick: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
  onContextMenu: PropTypes.func.isRequired,
  onRenameSubmit: PropTypes.func.isRequired,
  onRenameCancel: PropTypes.func.isRequired,
};

export default React.memo(CustomViewTab);
