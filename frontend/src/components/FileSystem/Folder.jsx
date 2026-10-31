import {
  Box,
  IconButton,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef, useState, useEffect } from "react";
import { Link, useParams } from "react-router-dom";
import SvgColor from "src/components/svg-color";
import { DeleteItem } from "src/sections/workbench-v2/components/DeleteItem";
import { RenameItem } from "src/sections/workbench-v2/components/RenameItem";
import {
  handleMenuItemEvent,
  PROMPT_ITEM_TYPES,
} from "../../sections/workbench-v2/common";
import { Events } from "../../utils/Mixpanel";

const openFolderIcon = "/assets/icons/ic_open_folder.svg";
const folderIcon = "/assets/icons/ic_folder.svg";

export default function Folder({
  label,
  isChildren,
  isActive,
  hasChildren,
  onToggle,
  createdBy,
  id,
  sx = {},
  type,
  onClick,
  isSample,
}) {
  const theme = useTheme();
  const { folder } = useParams();
  const isLinkActive = folder === String(id);
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const containerRef = useRef(null);
  const [selectAction, setSelectedActionName] = useState(null);
  const [typographyWidth, setTypographyWidth] = useState(150);

  // Calculate available width for typography based on container width
  useEffect(() => {
    const calculateTypographyWidth = () => {
      if (containerRef.current) {
        const containerWidth = containerRef.current.offsetWidth;
        // Calculate width excluding other elements:
        // - Chevron icon: ~30px (when hasChildren)
        // - Folder icon: ~32px
        // - Menu button: ~40px (when isChildren)
        // - Padding and gaps: ~16px

        let reservedWidth = 16 + 8 + 16; // folder icon + gaps + padding

        if (hasChildren) {
          reservedWidth += 24; // chevron icon
        }

        if (isChildren) {
          reservedWidth += 32 + 28; // left padding + menu button
        } else {
          reservedWidth += 0; // no menu button for parent folders
        }

        const availableWidth = Math.max(containerWidth - reservedWidth, 50); // minimum 50px
        setTypographyWidth(availableWidth);
      }
    };

    calculateTypographyWidth();

    // Use ResizeObserver for better performance than window resize
    const resizeObserver = new ResizeObserver(() => {
      calculateTypographyWidth();
    });

    if (containerRef.current) {
      resizeObserver.observe(containerRef.current);
    }

    return () => {
      resizeObserver.disconnect();
    };
  }, [hasChildren, isChildren]);

  const handleClick = (e) => {
    // Prevent navigation when clicking on interactive elements
    if (e.target.closest("button") || e.target.closest('[role="button"]')) {
      e.preventDefault();
      return;
    }

    onClick({ folderName: label, id });
  };

  const handleToggleClick = (e) => {
    e.preventDefault(); // Prevent navigation
    e.stopPropagation(); // Prevent event bubbling
    onToggle();
  };

  const handleMenuClick = (e) => {
    e.preventDefault(); // Prevent navigation
    e.stopPropagation(); // Prevent event bubbling
    setOpen(true);
  };

  return (
    <Link
      to={`/dashboard/workbench/${id}`}
      style={{
        textDecoration: "none",
        display: "block",
        width: "100%",
      }}
      onClick={handleClick}
    >
      <Stack
        ref={containerRef}
        direction={"row"}
        alignItems={"center"}
        gap={theme.spacing(1)}
        sx={{
          pl: isChildren ? "32px" : 1,
          pr: 0.5,
          minHeight: 28,
          borderRadius: "6px",
          bgcolor: isLinkActive ? "action.selected" : "unset",
          "&:hover": isLinkActive ? undefined : { bgcolor: "action.hover" },
          cursor: "pointer",
          ...sx,
        }}
        component={"div"}
      >
        {hasChildren && (
          <IconButton
            size="small"
            onClick={handleToggleClick}
            sx={{ zIndex: 1 }} // Ensure it's above the link
          >
            <SvgColor
              sx={{
                height: 14,
                width: 14,
                color: "text.primary",
                transform: isActive ? "rotate(0deg)" : "rotate(-90deg)",
                transition: "transform 0.2s ease",
                flexShrink: 0,
              }}
              src="/assets/icons/custom/lucide--chevron-down.svg"
            />
          </IconButton>
        )}

        {/* Monochrome 16px icon, like the sidebar: grey at rest, primary when
            the folder is open. */}
        <Box
          sx={{ position: "relative", width: 16, height: 16, flexShrink: 0 }}
        >
          <SvgColor
            src={folderIcon}
            sx={{
              position: "absolute",
              inset: 0,
              width: 16,
              height: 16,
              opacity: isLinkActive ? 0 : 1,
              transition: "opacity 0.2s ease-in-out",
              color: "text.secondary",
            }}
          />
          <SvgColor
            src={openFolderIcon}
            sx={{
              color: "text.primary",
              position: "absolute",
              inset: 0,
              width: 16,
              height: 16,
              opacity: isLinkActive ? 1 : 0,
              transition: "opacity 0.2s ease-in-out",
            }}
          />
        </Box>

        <Typography
          fontSize={13}
          lineHeight="20px"
          fontWeight={isLinkActive ? 500 : 400}
          color={isLinkActive ? "text.primary" : "text.secondary"}
          sx={{
            flex: 1,
            minWidth: 0,
            width: `${typographyWidth}px`,
            maxWidth: `${typographyWidth}px`,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            transition: "width 0.2s ease",
          }}
          title={label}
        >
          {label}
        </Typography>

        <IconButton
          size="small"
          disabled={isSample || !isChildren}
          sx={{
            opacity: isSample || !isChildren ? 0 : 1,
            flexShrink: 0,
            zIndex: 1, // Ensure it's above the link
          }}
          onClick={handleMenuClick}
          ref={ref}
        >
          <SvgColor
            sx={{
              color: "text.disabled",
              height: 16,
              width: 16,
              rotate: "90deg",
            }}
            src="/assets/icons/ic_ellipsis.svg"
          />
        </IconButton>

        <Menu
          anchorEl={ref?.current}
          open={open}
          onClose={() => setOpen(false)}
          PaperProps={{
            sx: {
              mt: 1,
              minWidth: 200,
              p: 0.5,
            },
          }}
          anchorOrigin={{ horizontal: "right", vertical: "top" }}
        >
          <MenuItem
            onClick={() => {
              handleMenuItemEvent(
                Events.promptRenameClicked,
                PROMPT_ITEM_TYPES.FOLDER,
              );
              setOpen(false);
              setSelectedActionName("Rename");
            }}
            sx={{ px: 1.25, py: 0.75 }}
          >
            <ListItemText
              primary="Rename"
              primaryTypographyProps={{
                typography: "s1",
                fontWeight: "500",
              }}
            />
          </MenuItem>
          <MenuItem
            onClick={() => {
              handleMenuItemEvent(
                Events.promptDeleteClicked,
                PROMPT_ITEM_TYPES.FOLDER,
              );
              setOpen(false), setSelectedActionName("Delete");
            }}
            // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
            sx={{ color: "status.danger.fg", px: 1.25, py: 0.75 }}
          >
            <ListItemText
              primary="Delete"
              primaryTypographyProps={{
                typography: "s1",
                fontWeight: "500",
              }}
            />
          </MenuItem>
        </Menu>
      </Stack>

      <RenameItem
        key={label}
        name={label}
        open={selectAction === "Rename"}
        onClose={() => setSelectedActionName(null)}
        id={id}
        type={PROMPT_ITEM_TYPES.FOLDER}
      />
      <DeleteItem
        name={label}
        createdBy={createdBy}
        open={selectAction === "Delete"}
        onClose={() => setSelectedActionName(null)}
        id={id}
        type={type}
      />
    </Link>
  );
}

Folder.propTypes = {
  label: PropTypes.string,
  isChildren: PropTypes.bool,
  isActive: PropTypes.bool,
  hasChildren: PropTypes.bool,
  onToggle: PropTypes.func,
  id: PropTypes.string.isRequired,
  sx: PropTypes.object,
  createdBy: PropTypes.string,
  type: PropTypes.string,
  onClick: PropTypes.func,
  isSample: PropTypes.bool,
};
