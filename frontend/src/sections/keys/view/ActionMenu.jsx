import {
  Box,
  Button,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useId, useState } from "react";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import CellMarkdown from "src/sections/common/CellMarkdown";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { useMutation } from "@tanstack/react-query";
import { LoadingButton } from "@mui/lab";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

const allMenuItems = [
  {
    id: 1,
    title: "Disable key",
    verb: "disable",
    icon: "solar:forbidden-circle-linear",
    iconColor: "status.danger.fg",
    titleColor: "status.danger.fg",
    iconType: "iconify",
    actionType: "disable",
  },
  {
    id: 2,
    title: "Re-enable key",
    verb: "re-enable",
    icon: "/assets/icons/re_enable.svg",
    iconColor: "text.disabled",
    titleColor: "text.primary",
    iconType: "svg",
    actionType: "enable",
  },
  {
    id: 3,
    title: "Delete key",
    verb: "delete",
    icon: "/assets/icons/ic_delete.svg",
    iconColor: "status.danger.fg",
    titleColor: "status.danger.fg",
    iconType: "svg",
    actionType: "delete",
  },
];

const ActionMenu = ({ data, onRefresh }) => {
  const _theme = useTheme();
  const [anchorEl, setAnchorEl] = useState(null);
  const [menuItems, setMenuItems] = useState([...allMenuItems]);
  const open = Boolean(anchorEl);
  const [openConfirmation, setOpenConfirmation] = useState(null);
  const actionsId = useId();
  const buttonId = `${actionsId}-button`;
  const menuId = `${actionsId}-menu`;
  const actionsName = data?.key_name
    ? `Actions for ${data.key_name}`
    : "Key actions";
  const actionsHint = data?.enabled
    ? "Disable or delete this key"
    : "Re-enable or delete this key";

  const handleClick = (event) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
    setMenuItems(
      allMenuItems.filter((item) =>
        data.enabled ? item.id !== 2 : item.id !== 1,
      ),
    );
  };

  const handleClose = () => {
    setAnchorEl(null);
  };

  // The menu closes as its action's confirmation opens, so it does not stay
  // open behind the dialog.
  const handleMenuItemClick = (action) => {
    if (data?.type === "system") return;
    handleClose();
    setOpenConfirmation(action);
  };
  const handledialogClose = () => {
    setOpenConfirmation(null);
    handleClose();
  };

  const { mutate: handleMutate, isPending: loading } = useMutation({
    mutationFn: (variable) => {
      if (data?.type === "system") {
        throw new Error("System keys cannot be changed.");
      }
      const payload = { key_id: data.id };
      if (variable?.action == "disable") {
        return axios.post(endpoints.keys.disablekey, payload);
      } else if (variable?.action == "delete") {
        return axios.delete(endpoints.keys.deleteKey, {
          data: payload,
        });
      } else {
        return axios.post(endpoints.keys.enableKey, payload);
      }
    },
    onSuccess: (_data, variable) => {
      onRefresh?.();
      handledialogClose();
      enqueueSnackbar(variable?.successMessage, {
        variant: "success",
      });
    },
  });

  return (
    <>
      <Box
        sx={{
          display: "flex",
          gap: 1,
          alignItems: "center",
          height: "100%",
        }}
      >
        <CustomTooltip show title={actionsHint} placement="top" arrow>
          <Button
            id={buttonId}
            size="small"
            variant="outlined"
            color="inherit"
            aria-label={actionsName}
            aria-haspopup="menu"
            aria-controls={open ? menuId : undefined}
            aria-expanded={open ? "true" : undefined}
            onClick={handleClick}
            startIcon={
              <SvgColor
                width={16}
                sx={{ color: "text.disabled" }}
                src="/assets/icons/action_buttons/ic_configure.svg"
              />
            }
            endIcon={
              <Iconify
                icon={`fluent:chevron-${open ? "up" : "down"}-16-filled`}
                width="18px"
                height="18px"
                color="text.disabled"
              />
            }
            sx={{ borderColor: "divider" }}
          />
        </CustomTooltip>
      </Box>

      <Menu
        id={menuId}
        anchorEl={anchorEl}
        open={open}
        onClose={handleClose}
        MenuListProps={{
          "aria-labelledby": buttonId,
        }}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "right",
        }}
        transformOrigin={{
          vertical: "top",
          horizontal: "right",
        }}
        PaperProps={{
          sx: {
            width: 240,
            border: "1px solid",
            borderColor: (theme) => theme.palette.divider,
            borderRadius: "8px",
            padding: 1.5,
            mt: 0.2,
          },
        }}
      >
        {menuItems.map((item) => (
          <CustomTooltip
            show={data?.type === "system"}
            key={item.title}
            title={`Can't ${item.verb} a system key`}
            placement="left"
            arrow
          >
            <Box>
              <MenuItem
                onClick={() => handleMenuItemClick(item)}
                disabled={data?.type === "system"}
                sx={{
                  py: 0.5,
                  "&:hover": {
                    bgcolor: "action.hover",
                    "& .MuiTypography-root": {},
                  },
                }}
              >
                <ListItemIcon sx={{ minWidth: 30, mr: 0 }}>
                  {item.iconType === "svg" && (
                    <SvgColor
                      src={item.icon}
                      sx={{ width: 16, height: 16, color: item.iconColor }}
                    />
                  )}
                  {item.iconType === "iconify" && (
                    <Iconify
                      icon={item.icon}
                      width="20px"
                      height="20px"
                      color={item.iconColor}
                    />
                  )}
                </ListItemIcon>
                <ListItemText
                  primary={item.title}
                  primaryTypographyProps={{
                    variant: "body2",
                    color: item.titleColor,
                  }}
                />
              </MenuItem>
            </Box>
          </CustomTooltip>
        ))}
      </Menu>
      <ConfirmDialog
        fullWidth
        content={
          <Box>
            <Typography typography="s1" fontWeight="fontWeightRegular">
              <CellMarkdown
                text={`Are you sure you want to delete **${data.key_name}**? `}
              />
            </Typography>
            <Typography typography="s1" fontWeight="fontWeightRegular">
              The API key and secret key will be deleted. This cannot be undone.
            </Typography>
          </Box>
        }
        action={
          <LoadingButton
            loading={loading}
            disabled={data?.type === "system"}
            size="small"
            variant="contained"
            color="error"
            onClick={() =>
              handleMutate({
                action: "delete",
                successMessage: `${data.key_name} has been deleted`,
              })
            }
            sx={{ color: "common.white" }}
            startIcon={
              <SvgColor
                src="/assets/icons/ic_delete.svg"
                sx={{
                  color: (theme) => theme.palette.background.paper,
                  mb: 0.25,
                }}
              />
            }
          >
            Delete key
          </LoadingButton>
        }
        open={openConfirmation?.actionType === "delete"}
        onClose={handledialogClose}
        title="Delete key?"
      />
      <ConfirmDialog
        fullWidth
        content={
          <Box>
            <Typography typography="s1" fontWeight="fontWeightRegular">
              <CellMarkdown
                text={`Are you sure you want to disable **${data.key_name}**?`}
              />
            </Typography>
            <Typography typography="s1" fontWeight="fontWeightRegular">
              The API key and secret key will stop working until you re-enable
              the key.
            </Typography>
          </Box>
        }
        action={
          <LoadingButton
            loading={loading}
            disabled={data?.type === "system"}
            size="small"
            variant="contained"
            color="error"
            onClick={() =>
              handleMutate({
                action: "disable",
                successMessage: `${data.key_name} has been disabled`,
              })
            }
          >
            Disable key
          </LoadingButton>
        }
        open={openConfirmation?.actionType === "disable"}
        onClose={handledialogClose}
        title="Disable key?"
      />
      <ConfirmDialog
        fullWidth
        content={
          <Box>
            <Typography typography="s1" fontWeight="fontWeightRegular">
              <CellMarkdown
                text={`Are you sure you want to re-enable **${data.key_name}**?`}
              />
            </Typography>
            <Typography typography="s1" fontWeight="fontWeightRegular">
              The API key and secret key will work again.
            </Typography>
          </Box>
        }
        action={
          <LoadingButton
            loading={loading}
            disabled={data?.type === "system"}
            size="small"
            variant="contained"
            color="primary"
            onClick={() =>
              handleMutate({
                action: "enable",
                successMessage: `${data.key_name} has been re-enabled`,
              })
            }
          >
            Re-enable key
          </LoadingButton>
        }
        open={openConfirmation?.actionType === "enable"}
        onClose={handledialogClose}
        title="Re-enable key?"
      />
    </>
  );
};

ActionMenu.propTypes = {
  data: PropTypes.object.isRequired,
  onRefresh: PropTypes.func,
};

export default ActionMenu;
