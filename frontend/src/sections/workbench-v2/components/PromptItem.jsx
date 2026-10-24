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
          minWidth: 0,
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
        height: "48px",
        display: "flex",
        alignItems: "center",
        px: 1.5,
        ...sx,
      }}
    >
      <Stack direction="row" alignItems="center" spacing={2} width="100%">
        {/* File icon skeleton */}
        <Skeleton
          variant="rectangular"
          width={28}
          height={28}
          sx={{ borderRadius: "6px" }}
        />
        <Stack spacing={0.5} flexGrow={1}>
          {/* Title skeleton */}
          <Skeleton variant="text" width="40%" height={16} />
          {/* Subtitle / metadata skeleton */}
          <Skeleton variant="text" width="60%" height={12} />
        </Stack>
        {/* Menu dots skeleton */}
        <Skeleton variant="circular" width={20} height={20} />
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
        padding: theme.spacing(1, 1.5),
        textDecoration: "none",
        color: "inherit",
        gap: theme.spacing(0.5),
        ...sx,
      }}
    >
      <ShowComponent condition={isSearching}>
        <Stack direction={"row"} alignItems={"center"}>
          <Typography
            typography={"s2"}
            fontWeight={"fontWeightRegular"}
            color={
              !extraData?.prompt_folder ? "text.secondary" : "text.disabled"
            }
          >
            All Prompts
          </Typography>
          <ShowComponent condition={extraData?.prompt_folderName}>
            <SvgColor
              sx={{ height: 16, width: 16, color: "text.tertiary" }}
              src="/assets/icons/custom/lucide--chevron-right.svg"
            />
            <Typography
              typography={"s2"}
              fontWeight={"fontWeightRegular"}
              color={"text.secondary"}
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
              height: 28,
              width: 28,
              flexShrink: 0,
              bgcolor: "background.paper",
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              borderRadius: "6px",
              border: "1px solid",
              borderColor: "border.subtle",
            }}
          >
            <Box
              component="img"
              src={PROMPT_ICON_MAPPER[type] ?? PROMPT_ICON_MAPPER["PROMPT"]}
              sx={{
                height: 16,
                width: 16,
              }}
            />
          </Box>
          <Stack sx={{ minWidth: 0 }}>
            <Typography
              fontSize={13}
              lineHeight="20px"
              color="text.primary"
              fontWeight="fontWeightMedium"
              noWrap
            >
              {name}
            </Typography>
            <Typography
              color="text.tertiary"
              variant="s2"
              fontWeight="fontWeightRegular"
              noWrap
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
            "& .MuiAvatar-root": {
              width: 24,
              height: 24,
              fontSize: 11,
              fontWeight: 600,
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
                  fontSize: 11,
                  fontWeight: 600,
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
                height: 16,
                width: 16,
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
              minWidth: 200,
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
                 icon="solar:folder-linear"
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
            // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
            sx={{ color: "status.danger.fg", px: 1.25, py: 0.75 }}
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
