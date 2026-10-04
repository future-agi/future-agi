import React, { useRef, useState, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  IconButton,
  Typography,
  TextField,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

const CustomViewTab = ({
  view,
  projectName = "this project",
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
        view.visibility === "project"
          ? `Shared with ${projectName} · owned by ${view.is_owner ? "you" : "a teammate"}`
          : `Personal · owned by you${!isActive && shortcut ? ` · Press ${shortcut}` : ""}`
      }
      placement="bottom"
      arrow
      size="small"
      type="black"
    >
      <ButtonBase
        component="div"
        role="tab"
        aria-selected={isActive}
        aria-label={`${view.name} · ${view.visibility === "project" ? "Shared" : "Personal"} · owned by ${view.is_owner ? "you" : "a teammate"}`}
        data-view-id={view.id}
        tabIndex={isActive ? 0 : -1}
        onKeyDown={(e) => {
          if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
            e.preventDefault();
            const rect = e.currentTarget.getBoundingClientRect();
            onContextMenu(rect.left, rect.bottom, view.id);
          }
        }}
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
          "&:hover .close-btn, &:focus-within .close-btn": { opacity: 1 },
          "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main" },
        }}
      >
        <Iconify
          icon={view.visibility === "project" ? "mdi:account-group-outline" : "mdi:eye-outline"}
          width={14}
          sx={{ color: "text.primary", flexShrink: 0 }}
        />
        {isRenaming && view.can_edit ? (
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
        {isDirty && isActive && <Box component="span" aria-label="Unsaved changes" sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "primary.main" }} />}
        {!isRenaming && view.can_delete && (
          <IconButton
            className="close-btn"
            aria-label={`Delete ${view.name}`}
            size="small"
            onKeyDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); onClose(view.id); }}
            sx={{ opacity: 0, p: 0, color: "text.disabled", "&:focus-visible": { opacity: 1, outline: "2px solid", outlineColor: "primary.main" } }}
          ><Iconify icon="mdi:close" width={12} /></IconButton>
        )}
      </ButtonBase>
    </CustomTooltip>
  );
};

CustomViewTab.propTypes = {
  projectName: PropTypes.string,
  view: PropTypes.shape({
    visibility: PropTypes.string,
    is_owner: PropTypes.bool,
    can_edit: PropTypes.bool,
    can_delete: PropTypes.bool,
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
