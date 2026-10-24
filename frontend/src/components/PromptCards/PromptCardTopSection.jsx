import {
  Box,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import React, { useRef, useState } from "react";
import SVGColor from "src/components/svg-color";
import {
  GeneratePromptButton,
  GeneratePromptButtonIcon,
  SyncButton,
  SyncButtonCheckbox,
} from "./PromptCardStyleComponents";
import PropTypes from "prop-types";
import { PROMPT_ROLES_DISPLAY_NAMES, PromptRoles } from "src/utils/constants";
import { ShowComponent } from "src/components/show";
import { capitalize } from "lodash";
import CustomTooltip from "../tooltip";
import SvgColor from "src/components/svg-color";
import { getPromptRoleOptions, PROMPT_EDITOR_OPTIONS } from "./common";

const AttachmentOptions = [
  {
    label: "Add image",
    icon: "/assets/icons/components/ic_add_image.svg",
    value: "image",
  },
  {
    label: "Add pdf",
    icon: "/assets/icons/components/ic_add_file.svg",
    value: "pdf",
  },
  {
    label: "Add audio",
    icon: "/assets/icons/components/ic_add_audio.svg",
    value: "audio",
  },
  {
    label: "Record an audio",
    icon: "/assets/icons/components/ic_record_audio.svg",
    value: "recordAudio",
  },
];

const RoleSelection = ({
  role,
  onRoleChange,
  required = false,
  existingRoles = [],
  allowAllRoleChange,
  compact = false,
}) => {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const hasSystemRole = existingRoles.includes(PromptRoles.SYSTEM);
  const theme = useTheme();

  const handleClose = () => {
    setOpen(false);
  };

  if (role === PromptRoles.SYSTEM && !allowAllRoleChange) {
    return (
      <Typography variant="s1" fontWeight={500}>
        System (optional)
      </Typography>
    );
  }

  return (
    <>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          padding: theme.spacing(0.75, 1.5),
          cursor: onRoleChange ? "pointer" : "default", // show pointer if menu is clickable
          backgroundColor: open ? "divider" : "transparent", // optional: highlight when open
          ...(compact && { height: "32px", boxSizing: "border-box" }),
        }}
        onClick={() => onRoleChange && setOpen(true)} // open menu on box click
      >
        <Typography typography="s1" fontWeight={500} ref={ref}>
          {PROMPT_ROLES_DISPLAY_NAMES[role] ?? role}
          {required && " (required)"}
        </Typography>

        <ShowComponent condition={Boolean(onRoleChange)}>
          <SVGColor
            src="/assets/icons/custom/lucide--chevron-down.svg"
            sx={{
              width: "16px",
              height: "16px",
              color: "text.primary",
              rotate: open ? "180deg" : "0deg",
              transition: "all 0.2s ease-in-out",
            }}
          />
        </ShowComponent>
      </Box>
      <Menu
        id="basic-menu"
        anchorEl={ref.current}
        open={open}
        onClose={handleClose}
        sx={{
          "& .MuiPopover-paper": {
            borderRadius: "8px !important",
            padding: 1,
            minWidth: "200px",
            border: "1px solid",
            borderColor: "divider",
          },
        }}
      >
        {getPromptRoleOptions(hasSystemRole, allowAllRoleChange).map(
          (option) => (
            <MenuItem
              selected={role === option}
              key={option}
              onClick={() => {
                onRoleChange(option);
                handleClose();
              }}
              sx={{
                typography: "s1",
                "&.Mui-selected": {
                  backgroundColor: "background.neutral",
                  "&.Mui-focusVisible": {
                    backgroundColor: "background.neutral",
                  },
                },
              }}
            >
              {capitalize(option)}
            </MenuItem>
          ),
        )}
      </Menu>
    </>
  );
};

RoleSelection.propTypes = {
  role: PropTypes.oneOf(Object.values(PromptRoles)).isRequired,
  onRoleChange: PropTypes.func,
  required: PropTypes.bool,
  existingRoles: PropTypes.arrayOf(PropTypes.string),
  allowAllRoleChange: PropTypes.bool,
  compact: PropTypes.bool,
};

function PromptMenu({
  disabled,
  onDelete,
  onCopyPrompt,
  expandable,
  onExpandPrompt,
}) {
  const [openMenu, setOpenMenu] = useState(false);
  const attachmentAnchorEl = useRef(null);
  return (
    <>
      <IconButton
        onClick={() => setOpenMenu(true)}
        ref={attachmentAnchorEl}
        disabled={disabled}
        size="small"
      >
        <SvgColor
          sx={{
            height: 16,
            width: 16,
            color: "text.primary",
            rotate: "90deg",
          }}
          src={"/assets/icons/navbar/ic_ellipsis.svg"}
        />
      </IconButton>
      <Menu
        anchorEl={attachmentAnchorEl.current}
        open={openMenu}
        onClose={() => setOpenMenu(false)}
        sx={{
          "& .MuiPopover-paper": {
            borderRadius: "4px !important",
            padding: 1,
          },
        }}
        transformOrigin={{ horizontal: "right", vertical: "top" }}
        anchorOrigin={{ horizontal: "right", vertical: "bottom" }}
      >
        {PROMPT_EDITOR_OPTIONS.map((option) => {
          if (option?.name === "maximize" && !expandable) return null;
          if (!onDelete && option?.name === "delete") return null;
          return (
            <MenuItem
              key={option.name}
              onClick={() => {
                setOpenMenu(false);
                if (option?.name === "delete") {
                  onDelete?.();
                } else if (option?.name === "copy") {
                  onCopyPrompt?.();
                } else if (option?.name === "maximize") {
                  onExpandPrompt?.();
                }
              }}
              sx={{ columnGap: 1, padding: 0.5, pb: 0 }}
              hidden={option.name === "maximize" ? !expandable : false}
            >
              <SVGColor
                src={option.icon}
                sx={{
                  width: "16px",
                  height: "16px",
                  color: option?.color ?? "text.primary",
                }}
              />
              <Typography
                color={option?.color ?? "text.primary"}
                typography="s1"
              >
                {option?.label}
              </Typography>
            </MenuItem>
          );
        })}
      </Menu>
    </>
  );
}

PromptMenu.propTypes = {
  disabled: PropTypes.bool,
  onDelete: PropTypes.func,
  onCopyPrompt: PropTypes.func,
  expandable: PropTypes.bool,
  onExpandPrompt: PropTypes.func,
};

const PromptTopSection = ({
  onGeneratePrompt,
  onImprovePrompt,
  onAttachment,
  onDelete,
  role,
  onRoleChange,
  isSync,
  onSyncChange,
  required,
  disabled,
  existingRoles = [],
  allowAllRoleChange = false,
  onCopyPrompt,
  dragHandleProps = {},
  sortable = false,
  expandable,
  onExpandPrompt,
  hideMenu = false,
  compact = false,
}) => {
  const [openMenu, setOpenMenu] = useState(false);
  const attachmentAnchorEl = useRef(null);

  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
      }}
    >
      <Stack direction={"row"} alignItems={"center"} spacing={1}>
        <ShowComponent condition={sortable}>
          <IconButton
            sx={{
              cursor: "grab",
              "&:active": { cursor: "grabbing" },
            }}
            size="small"
            {...dragHandleProps}
          >
            <SvgColor
              sx={{
                rotate: "90deg",
                height: 16,
                width: 16,
              }}
              src="/assets/icons/ic_dragger_rect.svg"
            />
          </IconButton>
        </ShowComponent>
        <RoleSelection
          role={role}
          onRoleChange={onRoleChange}
          required={required}
          existingRoles={existingRoles}
          allowAllRoleChange={allowAllRoleChange}
          compact={compact}
        />
      </Stack>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
        <ShowComponent condition={Boolean(onGeneratePrompt)}>
          <CustomTooltip show title="Generate Prompt" arrow size="small">
            <GeneratePromptButton
              onClick={onGeneratePrompt}
              startIcon={<GeneratePromptButtonIcon />}
              size="small"
              disabled={disabled}
            />
          </CustomTooltip>
        </ShowComponent>
        <ShowComponent condition={Boolean(onImprovePrompt)}>
          <CustomTooltip show title="Improve Prompt" arrow size="small">
            <IconButton
              size="small"
              onClick={onImprovePrompt}
              disabled={disabled}
            >
              <SVGColor
                src="/assets/icons/components/ic_improve_prompt.svg"
                sx={{ width: "20px", height: "20px", color: "text.primary" }}
              />
            </IconButton>
          </CustomTooltip>
        </ShowComponent>
        <ShowComponent condition={Boolean(onAttachment)}>
          <CustomTooltip show title="Attach files" arrow size="small">
            <IconButton
              size="small"
              onClick={() => setOpenMenu(true)}
              ref={attachmentAnchorEl}
              disabled={disabled}
              color="text.primary"
            >
              <SVGColor
                src="/assets/icons/components/ic_attachment.svg"
                sx={{ width: "20px", height: "20px", color: "text.primary" }}
              />
            </IconButton>
          </CustomTooltip>
        </ShowComponent>
        <Menu
          anchorEl={attachmentAnchorEl.current}
          open={openMenu}
          onClose={() => setOpenMenu(false)}
          sx={{
            "& .MuiPopover-paper": {
              borderRadius: "4px !important",
            },
          }}
        >
          {AttachmentOptions.map((option) => (
            <MenuItem
              key={option.value}
              onClick={() => {
                onAttachment(option.value);
                setOpenMenu(false);
              }}
              sx={{ gap: 1 }}
            >
              <SVGColor
                src={option.icon}
                sx={{ width: "16px", height: "16px" }}
              />
              <Typography variant="s1">{option.label}</Typography>
            </MenuItem>
          ))}
        </Menu>
        {/* <ShowComponent condition={Boolean(onDelete)}>
          <CustomTooltip show title="Delete" arrow size="small">
            <IconButton
              size="small"
              onClick={onDelete}
              disabled={disabled}
              color="text.primary"
            >
              <SVGColor
                src="/assets/icons/ic_delete.svg"
                sx={{ width: "20px", height: "20px" }}
              />
            </IconButton>
          </CustomTooltip>
        </ShowComponent> */}
        <ShowComponent condition={Boolean(onSyncChange)}>
          <SyncButton onClick={() => onSyncChange(!isSync)}>
            <SyncButtonCheckbox checked={isSync} />
            Sync
          </SyncButton>
        </ShowComponent>
        <ShowComponent condition={!hideMenu}>
          <PromptMenu
            disabled={disabled}
            onDelete={onDelete}
            onCopyPrompt={onCopyPrompt}
            expandable={expandable}
            onExpandPrompt={onExpandPrompt}
          />
        </ShowComponent>
      </Box>
    </Box>
  );
};

PromptTopSection.propTypes = {
  onGeneratePrompt: PropTypes.func,
  onImprovePrompt: PropTypes.func,
  onAttachment: PropTypes.func,
  onDelete: PropTypes.func,
  role: PropTypes.oneOf(Object.values(PromptRoles)).isRequired,
  onRoleChange: PropTypes.func,
  isSync: PropTypes.bool,
  onSyncChange: PropTypes.func,
  required: PropTypes.bool,
  disabled: PropTypes.bool,
  existingRoles: PropTypes.arrayOf(PropTypes.string),
  allowAllRoleChange: PropTypes.bool,
  onCopyPrompt: PropTypes.func,
  dragHandleProps: PropTypes.object,
  sortable: PropTypes.bool,
  expandable: PropTypes.bool,
  onExpandPrompt: PropTypes.func,
  hideMenu: PropTypes.bool,
  compact: PropTypes.bool,
};

export default PromptTopSection;
