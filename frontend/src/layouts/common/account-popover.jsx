import React from "react";
import PropTypes from "prop-types";
import { m } from "framer-motion";

import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Avatar from "@mui/material/Avatar";
import Divider from "@mui/material/Divider";
import { alpha } from "@mui/material/styles";
import MenuItem from "@mui/material/MenuItem";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";

import { useRouter } from "src/routes/hooks";

import { useAuthContext } from "src/auth/hooks";

import { varHover } from "src/components/animate";
import CustomPopover, { usePopover } from "src/components/custom-popover";
import logger from "src/utils/logger";
import Iconify from "src/components/iconify";

// ----------------------------------------------------------------------

const OPTIONS = [
  {
    label: "Home",
    linkTo: "/",
  },
  {
    label: "Profile",
    linkTo: "/#1",
  },
  {
    label: "Settings",
    linkTo: "/#2",
  },
];

// ----------------------------------------------------------------------

export default function AccountPopover({ variant }) {
  const router = useRouter();

  const { logout, user } = useAuthContext();

  const popover = usePopover();

  const handleLogout = async () => {
    try {
      await logout();
      localStorage.removeItem("intial-render");
      popover.onClose();
      router.replace("/");
    } catch (error) {
      logger.error("Logout failed", error);
    }
  };

  const handleClickItem = (path) => {
    popover.onClose();
    router.push(path);
  };

  // Design system nav account row: avatar, name and email, opening the
  // same account menu.
  const navTrigger = (
    <Box
      component="button"
      type="button"
      onClick={popover.onOpen}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        width: 1,
        height: 36,
        px: 0.75,
        border: 0,
        borderRadius: "6px",
        bgcolor: popover.open ? "action.selected" : "transparent",
        color: "inherit",
        font: "inherit",
        textAlign: "left",
        cursor: "pointer",
        "&:hover": { bgcolor: "action.hover" },
        "&:focus-visible": {
          outline: (t) => `2px solid ${t.palette.border.focus}`,
          outlineOffset: -2,
        },
      }}
    >
      <Avatar
        src={user?.photoURL}
        alt={user?.name}
        sx={{ width: 24, height: 24, fontSize: 11, fontWeight: 600 }}
      >
        {user?.name?.charAt(0).toUpperCase()}
      </Avatar>
      <Box sx={{ minWidth: 0, flex: 1, display: "grid" }}>
        <Typography
          noWrap
          sx={{
            fontSize: 12,
            lineHeight: "16px",
            fontWeight: 500,
            color: "text.primary",
          }}
        >
          {user?.name}
        </Typography>
        <Typography
          noWrap
          sx={{ fontSize: 12, lineHeight: "16px", color: "text.tertiary" }}
        >
          {user?.email}
        </Typography>
      </Box>
      <Iconify
        icon="solar:alt-arrow-up-linear"
        width={14}
        sx={{ color: "text.disabled", flexShrink: 0 }}
      />
    </Box>
  );

  return (
    <>
      {variant === "nav" ? (
        navTrigger
      ) : (
        <IconButton
          component={m.button}
          whileTap="tap"
          whileHover="hover"
          variants={varHover(1.05)}
          onClick={popover.onOpen}
          sx={{
            width: 40,
            background: (theme) => alpha(theme.palette.text.disabled, 0.08),
            ...(popover.open && {
              background: (theme) =>
                `linear-gradient(135deg, ${theme.palette.primary.light} 0%, ${theme.palette.primary.main} 100%)`,
            }),
          }}
        >
          <Avatar
            src={user?.photoURL}
            alt={user?.name}
            sx={{
              width: 36,
              height: 36,
            }}
          >
            {user?.name.charAt(0).toUpperCase()}
          </Avatar>
        </IconButton>
      )}

      <CustomPopover
        open={popover.open}
        onClose={popover.onClose}
        arrow={variant === "nav" ? "bottom-left" : "top-right"}
        hiddenArrow={variant === "nav"}
        sx={{ width: variant === "nav" ? 208 : 200, p: 0 }}
      >
        <Box sx={{ p: 2, pb: 1.5 }}>
          <Typography variant="subtitle2" noWrap>
            {user?.name}
          </Typography>

          <Typography variant="body2" sx={{ color: "text.secondary" }} noWrap>
            {user?.email}
          </Typography>
        </Box>

        <Divider sx={{ borderStyle: "dashed" }} />

        <Stack sx={{ p: 1 }}>
          {OPTIONS.map((option) => (
            <MenuItem
              key={option.label}
              onClick={() => handleClickItem(option.linkTo)}
            >
              {option.label}
            </MenuItem>
          ))}
        </Stack>

        <Divider sx={{ borderStyle: "dashed" }} />

        <MenuItem onClick={handleLogout} sx={{ m: 1 }}>
          Logout
        </MenuItem>
      </CustomPopover>
    </>
  );
}

AccountPopover.propTypes = { variant: PropTypes.oneOf(["nav"]) };
