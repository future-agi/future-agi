import {
  Avatar,
  Box,
  Chip,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import stringAvatar from "src/utils/stringAvatar";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import CellMarkdown from "src/sections/common/CellMarkdown";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { useMutation } from "@tanstack/react-query";
import { LoadingButton } from "@mui/lab";

const allMenuItems = [
  {
    id: 1,
    title: "Disable Key",
    icon: "mdi:cancel",
    iconColor: "red.500",
    titleColor: "red.500",
    iconType: "iconify",
    actionType: "disable",
  },
  {
    id: 2,
    title: "Re-enable key",
    icon: "/assets/icons/re_enable.svg",
    iconColor: "text.disabled",
    titleColor: "text.primary",
    iconType: "svg",
    actionType: "enable",
  },
  {
    id: 3,
    title: "Delete Key",
    icon: "/assets/icons/ic_delete.svg",
    iconColor: "red.500",
    titleColor: "red.500",
    iconType: "svg",
    actionType: "delete",
  },
];

const getKeyName = (data) => data?.key_name || data?.keyName || "this key";

const ActionMenu = (props) => {
  const data = props.data;
  const keyName = getKeyName(data);

  const [anchorEl, setAnchorEl] = useState(null);
  const [menuItems, setMenuItems] = useState([...allMenuItems]);
  const open = Boolean(anchorEl);
  const [openConfirmation, setOpenConfirmation] = useState(null);

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

  const handleMenuItemClick = (action) => {
    setOpenConfirmation(action);
  };
  const handledialogClose = () => {
    setOpenConfirmation(null);
    handleClose();
  };

  const { mutate: handleMutate, isPending: loading } = useMutation({
    /**
     *
     * @param {Object} variable
     * @returns
     */
    mutationFn: (variable) => {
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
      props?.api?.refreshServerSide?.({ purge: true }); // hard refresh the table
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
        <Box
          sx={{
            display: "flex",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            height: "26px",
            width: "71px",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            "&:hover": {
              backgroundColor: "background.default",
            },
          }}
          onClick={handleClick}
        >
          <SvgColor
            sx={{
              color: "text.disabled",
              width: "16px",
              height: "16px",
              ml: 1,
            }}
            src="/assets/icons/action_buttons/ic_configure.svg"
          />
          <IconButton size="small" sx={{ ml: 0.2 }}>
            <Iconify
              icon={`fluent:chevron-${open ? "up" : "down"}-16-filled`}
              width="18px"
              height="18px"
              color="text.disabled"
            />
          </IconButton>
        </Box>
      </Box>

      <Menu
        anchorEl={anchorEl}
        open={open}
        onClose={handleClose}
        MenuListProps={{
          "aria-labelledby": "actions-button",
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
            width: 224,
            border: "1px solid",
            borderColor: (theme) => theme.palette.divider,
            borderRadius: "8px",
            padding: 1.5,
            mt: 0.2,
          },
        }}
      >
        {menuItems.map((item, index) => (
          <MenuItem
            key={index}
            onClick={() => handleMenuItemClick(item)}
            sx={{
              py: 0.5,
              "&:hover": {
                bgcolor: "action.hover",
                "& .MuiTypography-root": {
                  fontWeight: 500,
                },
              },
            }}
          >
            <ListItemIcon sx={{ minWidth: 30, mr: 0 }}>
              {item.iconType === "svg" && (
                <SvgColor
                  src={item.icon}
                  sx={{ width: 20, height: 20, color: item.iconColor }}
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
        ))}
      </Menu>
      <ConfirmDialog
        fullWidth
        content={
          <Box>
            <Typography typography={"s1"} fontWeight={"fontWeightRegular"}>
              <CellMarkdown
                text={`Are you sure you want to delete **${keyName}**? `}
              />
            </Typography>
            <Typography typography={"s1"} fontWeight={"fontWeightRegular"}>
              Both the API key and secret Key will be permanently deleted and
              cannot be undone.
            </Typography>
          </Box>
        }
        action={
          <LoadingButton
            loading={loading}
            size="small"
            variant="contained"
            color="error"
            onClick={() =>
              handleMutate({
                action: "delete",
                successMessage: `${keyName} has been deleted`,
              })
            }
            sx={{ color: "common.white" }}
            startIcon={
              <SvgColor
                src={"/assets/icons/ic_delete.svg"}
                sx={{
                  color: (theme) => theme.palette.background.paper,
                  mb: 0.25,
                }}
              />
            }
          >
            Delete Key
          </LoadingButton>
        }
        open={openConfirmation?.actionType === "delete"}
        onClose={handledialogClose}
        title="Delete Key?"
      />
      <ConfirmDialog
        fullWidth
        content={
          <Box>
            <Typography typography={"s1"} fontWeight={"fontWeightRegular"}>
              <CellMarkdown
                text={`Are you sure you want to disable **${keyName}**?`}
              />
            </Typography>
            <Typography typography={"s1"} fontWeight={"fontWeightRegular"}>
              Both the API key and Secret Key will be disabled
            </Typography>
          </Box>
        }
        action={
          <LoadingButton
            loading={loading}
            size="small"
            variant="contained"
            color="error"
            onClick={() =>
              handleMutate({
                action: "disable",
                successMessage: `${keyName} has been disabled`,
              })
            }
          >
            Disable Key
          </LoadingButton>
        }
        open={openConfirmation?.actionType === "disable"}
        onClose={handledialogClose}
        title="Disable Key?"
      />
      <ConfirmDialog
        fullWidth
        content={
          <Box>
            <Typography typography={"s1"} fontWeight={"fontWeightRegular"}>
              <CellMarkdown
                text={`Are you sure you want to re-enable **${keyName}**?`}
              />
            </Typography>
            <Typography typography={"s1"} fontWeight={"fontWeightRegular"}>
              Both the API key and Secret Key will be re-enabled again
            </Typography>
          </Box>
        }
        action={
          <LoadingButton
            loading={loading}
            size="small"
            variant="contained"
            color="primary"
            onClick={() =>
              handleMutate({
                action: "enable",
                successMessage: `${keyName} has been enabled`,
              })
            }
          >
            Re-enable
          </LoadingButton>
        }
        open={openConfirmation?.actionType === "enable"}
        onClose={handledialogClose}
        title="Re-Enable API Key"
      />
    </>
  );
};

ActionMenu.propTypes = {
  value: PropTypes.string,
  data: PropTypes.object,
  api: PropTypes.object,
};

const CellRenderer = (props) => {
  const { value } = props;
  const columnKey = props?.column?.colDef?.field || "";

  if (columnKey === "createdBy" || columnKey === "created_by") {
    if (!props?.value?.length) {
      return "-";
    }
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          height: "100%",
          gap: (theme) => theme.spacing(0.5),
        }}
      >
        <Avatar
          variant="rounded"
          {...stringAvatar(props?.value)}
          sx={{
            width: (theme) => theme.spacing(3),
            height: (theme) => theme.spacing(3),
            color: "pink.500",
            backgroundColor: "background.neutral",
          }}
        />
        <Typography typography="s2" fontWeight={"fontWeightRegular"}>
          {props?.value}
        </Typography>
      </Box>
    );
  }

  if (columnKey === "id") {
    return <ActionMenu {...props} />;
  }

  if (columnKey === "keyName" || columnKey === "key_name") {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          height: "100%",
          minWidth: 0,
          width: "100%",
        }}
      >
        <Typography
          component="span"
          noWrap
          typography="s2"
          sx={{ minWidth: 0, flex: "0 1 auto" }}
        >
          {value}
        </Typography>
        {!props?.data?.enabled && (
          <Chip
            label="Disabled"
            sx={{
              padding: "4px 8px",
              borderRadius: "4px",
              ml: 1,
              flexShrink: 0,
              color: "text.primary",
              height: "26px",
              backgroundColor: "background.neutral",
              "&:hover": {
                backgroundColor: "action.hover",
              },
            }}
          />
        )}
      </Box>
    );
  }

  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      {value}
    </Box>
  );
};

export default CellRenderer;

CellRenderer.propTypes = {
  value: PropTypes.string,
  column: PropTypes.object,
  data: PropTypes.object,
};
