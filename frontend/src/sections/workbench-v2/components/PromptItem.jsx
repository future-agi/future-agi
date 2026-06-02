import {
  Avatar,
  AvatarGroup,
  Box,
  IconButton,
  ListItemText,
  Menu,
  MenuItem,
  Skeleton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import { format, isValid } from "date-fns";
import PropTypes from "prop-types";
import React, { useRef, useState } from "react";
import { Link } from "react-router-dom";
import SvgColor from "src/components/svg-color";
import {
  handleMenuItemEvent,
  PROMPT_ICON_MAPPER,
  PROMPT_ITEM_TYPES,
} from "../common";
import { RenameItem } from "./RenameItem";
import { DeleteItem } from "./DeleteItem";
import MoveItem from "./MoveItem";
import { ShowComponent } from "src/components/show";
import _ from "lodash";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const Wrapper = ({ type, id, onClick, children, theme }) => {
  if (type === PROMPT_ITEM_TYPES.TEMPLATE) {
    return (
      <Box
        onClick={onClick}
        sx={{
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          flex: 1,
          gap: theme.spacing(1.5),
          textDecoration: "none",
        }}
      >
        {children}
      </Box>
    );
  }

  return (
    <Link
      to={
        type === PROMPT_ITEM_TYPES.FOLDER
          ? `/dashboard/workbench/${id}`
          : `/dashboard/workbench/create/${id}`
      }
      style={{
        textDecoration: "none",
        display: "flex",
        alignItems: "center",
        flex: 1,
        gap: theme.spacing(1.5),
      }}
    >
      {children}
    </Link>
  );
};

Wrapper.propTypes = {
  type: PropTypes.oneOf([
    PROMPT_ITEM_TYPES.FILE,
    PROMPT_ITEM_TYPES.FOLDER,
    PROMPT_ITEM_TYPES.TEMPLATE,
  ]).isRequired,
  id: PropTypes.string,
  onClick: PropTypes.func,
  children: PropTypes.node.isRequired,
  theme: PropTypes.object.isRequired,
};

export default function PromptItem({
  type = PROMPT_ITEM_TYPES.FILE,
  name,
  createdBy,
  lastModified,
  lastModifiedBy,
  sx,
  id,
  isLoading,
  extraData,
  setSelectedPromptTemplate,
  isSearching,
}) {
  const { role } = useAuthContext();
  const canWrite = RolePermission.PROMPTS[PERMISSIONS.UPDATE][role];
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const [selectedActineName, setSelectedActionName] = useState(null);

  const loading = (
    <Box
      sx={{
        height: "100px",
        display: "flex",
        alignItems: "center",
        px: 2,
        ...sx,
      }}
    >
      <Stack direction="row" alignItems="center" spacing={2} width="100%">
        {/* File icon skeleton */}
        <Skeleton variant="rectangular" width={44} height={44} />
        <Stack spacing={1} flexGrow={1}>
          {/* Title skeleton */}
          <Skeleton variant="text" width="40%" height={24} />
          {/* Subtitle / metadata skeleton */}
          <Skeleton variant="text" width="60%" height={16} />
        </Stack>
        {/* Menu dots skeleton */}
        <Skeleton variant="circular" width={24} height={24} />
      </Stack>
    </Box>
  );

  if (isLoading) {
    return loading;
  }

  const handleTemplateClick = (id) => {
    if (!extraData) return;
    trackEvent(Events.promptTemplateIdClicked, {
      [PropertyName.id]: id,
      [PropertyName.category]: extraData?.category ?? "my-templates",
    });
    setSelectedPromptTemplate({
      id: id,
      name: name,
      promptConfig: extraData,
    });
  };

  return (
    <Stack
      sx={{
        padding: theme.spacing(2),
        textDecoration: "none",
        color: "inherit",
        gap: theme.spacing(1.5),
        ...sx,
      }}
    >
      <ShowComponent condition={isSearching}>
        <Stack direction={"row"} alignItems={"center"}>
          <Typography
            typography={"s2"}
            fontWeight={"fontWeightMedium"}
            color={!extraData?.prompt_folder ? "text.primary" : "text.disabled"}
          >
            All Prompts
          </Typography>
          <ShowComponent condition={extraData?.prompt_folderName}>
            <SvgColor
              sx={{ height: 16, width: 16, color: "text.primary" }}
              src="/assets/icons/custom/lucide--chevron-right.svg"
            />
            <Typography
              typography={"s2"}
              fontWeight={"fontWeightMedium"}
              color={"text.primary"}
            >
              {extraData?.prompt_folderName}
            </Typography>
          </ShowComponent>
        </Stack>
      </ShowComponent>
      <Stack direction="row" alignItems="center" gap={theme.spacing(1.5)}>
        <Wrapper
          type={type}
          id={id}
          onClick={() => handleTemplateClick(id)}
          theme={theme}
        >
          <Box
            sx={{
              boxShadow: (themeLocal) =>
                themeLocal.palette.mode === "dark"
                  ? "0 4px 12px rgba(0, 0, 0, 0.4)"
                  : "2px -2px 12px 0px rgba(0, 0, 0, 0.08)",
              height: 44,
              width: 44,
              bgcolor: "background.paper",
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              borderRadius: "4px",
              border: "1px solid",
              borderColor: (themeLocal) =>
                themeLocal.palette.mode === "dark"
                  ? "rgba(255, 255, 255, 0.12)"
                  : "divider",
            }}
          >
            <Box
              component="img"
              src={PROMPT_ICON_MAPPER[type] ?? PROMPT_ICON_MAPPER["PROMPT"]}
              sx={{
                height: 20,
                width: 20,
              }}
            />
          </Box>
          <Stack>
            <Typography
              variant="m3"
              color="text.primary"
              fontWeight="fontWeightMedium"
            >
              {name}
            </Typography>
            <Typography
              color="text.secondary"
              variant="s2"
              fontWeight="fontWeightRegular"
            >
              {createdBy && (
                <>
                  Created by {createdBy}
                  {(lastModifiedBy || lastModified) && " · "}
                </>
              )}
              {/* {lastModifiedBy && <>Last modified by {lastModifiedBy}</>} */}
              {lastModified && isValid(new Date(lastModified)) && (
                <>
                  {" "}
                  Last modified{" "}
                  {format(new Date(lastModified), "MMMM d, yyyy 'at' h:mmaaa")}
                </>
              )}
            </Typography>
          </Stack>
        </Wrapper>
        <AvatarGroup
          sx={{
            mt: "2px",
            "& .MuiAvatar-root": {
              width: 24,
              height: 24,
              fontSize: 10,
              border: "1px solid",
              borderColor: "primary.main",
              bgcolor: "background.paper",
              color: "primary.main",
            },
          }}
        >
          {extraData?.collaborators?.map((c, index) => (
            <CustomTooltip
              size="small"
              type="black"
              arrow
              key={index}
              title={c?.email}
              show
            >
              <Avatar
                key={index}
                alt={c?.name}
                sx={{
                  width: 24,
                  height: 24,
                  fontSize: 10,
                  border: "1px solid",
                  borderColor: "primary.main",
                  bgcolor: "background.paper",
                  color: "primary.main",
                }}
              >
                {_.toUpper(c?.name?.[0])}
              </Avatar>
            </CustomTooltip>
          ))}
        </AvatarGroup>
        {!extraData?.isSample && canWrite && (
          <IconButton
            ref={ref}
            size="small"
            sx={{ ml: "auto" }}
            onClick={(e) => {
              e.stopPropagation();
              setOpen(true);
            }}
          >
            <SvgColor
              src="/assets/icons/ic_ellipsis.svg"
              sx={{
                color: "text.disabled",
                height: 24,
                width: 24,
                rotate: "90deg",
              }}
            />
          </IconButton>
        )}

        {/* Menu */}
        <Menu
          anchorEl={ref?.current}
          open={open}
          onClose={() => setOpen(false)}
          PaperProps={{
            sx: {
              mt: 1,
              minWidth: 100,
              p: 0.5,
            },
          }}
          anchorOrigin={{ horizontal: "left", vertical: "top" }}
        >
          <MenuItem
            sx={{ px: 1.25, py: 0.75 }}
            onClick={() => {
              // TODO: open rename wrapper
              if (type) {
                handleMenuItemEvent(Events.promptRenameClicked, type);
              }
              setOpen(false);
              setSelectedActionName("Rename");
            }}
          >
            {/* <ListItemIcon sx={{ minWidth: "unset", mr: 0 }}>
            <SvgColor
              src="/assets/icons/ic_pen.svg"
              sx={{
                width: 16,
                height: 16,
                ml: 0.5,
              }}
            />
          </ListItemIcon> */}
            <ListItemText
              primary="Rename"
              primaryTypographyProps={{
                typography: "s1",
                fontWeight: "500",
              }}
            />
          </MenuItem>

          {type === PROMPT_ITEM_TYPES.FILE && (
            <MenuItem
              sx={{ px: 1.25, py: 0.75 }}
              onClick={() => {
                // TODO: open move wrapper
                if (type) {
                  handleMenuItemEvent(Events.promptMoveClicked, type);
                }
                setOpen(false);
                setSelectedActionName("Move");
              }}
            >
              {/* <ListItemIcon sx={{ minWidth: "unset", mr: 0 }}>
                <Iconify
                 icon="subway:folder-2"
                  sx={{
                    width: 16,
                    height: 16,
                    ml: 0.5,
                  }}
                />
              </ListItemIcon> */}
              <ListItemText
                primary="Move"
                primaryTypographyProps={{
                  typography: "s1",
                  fontWeight: "500",
                }}
              />
            </MenuItem>
          )}

          <MenuItem
            sx={{ color: "error.main", px: 1.25, py: 0.75 }}
            onClick={() => {
              // TODO: open delete wrapper
              if (type) {
                handleMenuItemEvent(Events.promptDeleteClicked, type);
              }
              setOpen(false);
              setSelectedActionName("Delete");
            }}
          >
            {/* <ListItemIcon sx={{ minWidth: "unset", mr: 0 }}>
            <SvgColor
              src="/assets/icons/ic_delete.svg"
              sx={{
                height: 20,
                width: 20,
              }}
            />
          </ListItemIcon> */}
            <ListItemText
              primary="Delete"
              primaryTypographyProps={{
                typography: "s1",
                fontWeight: "500",
              }}
            />
          </MenuItem>
        </Menu>

        <RenameItem
          key={id + "rename"}
          name={name}
          open={selectedActineName === "Rename"}
          onClose={() => setSelectedActionName(null)}
          id={id}
          type={type}
        />
        <DeleteItem
          key={id + "delete"}
          createdBy={createdBy}
          name={name}
          type={type}
          open={selectedActineName === "Delete"}
          onClose={() => setSelectedActionName(null)}
          id={id}
        />
        <MoveItem
          key={id + "move"}
          folderId={extraData?.prompt_folder}
          name={name}
          open={selectedActineName === "Move"}
          onClose={() => setSelectedActionName(null)}
          id={id}
        />
      </Stack>
    </Stack>
  );
}

PromptItem.propTypes = {
  type: PropTypes.oneOf(Object.values(PROMPT_ITEM_TYPES)),
  name: PropTypes.string,
  createdBy: PropTypes.string,
  lastModified: PropTypes.string,
  lastModifiedBy: PropTypes.string,
  sx: PropTypes.object,
  id: PropTypes.string,
  isLoading: PropTypes.bool,
  extraData: PropTypes.object,
  setSelectedPromptTemplate: PropTypes.func,
  isSearching: PropTypes.bool,
};
