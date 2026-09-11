import {
  Divider,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import React from "react";
import { useNavigate } from "react-router";
import { useAuthContext } from "src/auth/hooks";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { trackEvent, Events } from "src/utils/Mixpanel";
import SvgColor from "src/components/svg-color";

const ProfilePopover = ({ anchorEl, open, onClose }) => {
  const { logout } = useAuthContext();
  const { user } = useAuthContext();

  const isOwner = user?.organization_role === "Owner";

  const navigate = useNavigate();

  const onLogoutClick = () => {
    logout();
    navigate("/", { replace: true });
  };

  // const ShieldIcon = ({ ...props }) => {
  //   return (
  //     <svg
  //       viewBox="0 0 24 24"
  //       xmlns="http://www.w3.org/2000/svg"
  //       {...props} // Spread other props for additional customizations
  //       <path d="M3 5.75A.75.75 0 0 1 3.75 5c2.663 0 5.258-.943 7.8-2.85a.75.75 0 0 1 .9 0C14.992 4.057 17.587 5 20.25 5a.75.75 0 0 1 .75.75V11c0 5.001-2.958 8.676-8.725 10.948a.75.75 0 0 1-.55 0C5.958 19.676 3 16 3 11V5.75Z" />
  //     </svg>
  //     >
  //   );
  // };

  return (
    <Popover
      anchorEl={anchorEl}
      open={open}
      onClose={onClose}
      anchorOrigin={{
        vertical: "bottom",
        horizontal: "left",
      }}
      transformOrigin={{
        vertical: "top",
        horizontal: "left",
      }}
      elevation={20}
      PaperProps={{
        sx: {
          padding: 0,
          width: 238,
          border: "1px solid var(--border-light)",
          boxShadow: "none",
        },
      }}
    >
      <List sx={{ padding: 0 }}>
        <Stack spacing={0.2}>
          <List sx={{ margin: 1, backgroundColor: "background.neutral" }}>
            <ListItem sx={{ height: 30, paddingLeft: 1.5, paddingBottom: 0 }}>
              <Typography
                variant="subtitle2"
                sx={{
                  fontSize: "14px",
                  fontWeight: 500,
                  color: "text.primary",
                  letterSpacing: "-0.2px",
                }}
              >
                {user.name}
              </Typography>
            </ListItem>
            <ListItem
              sx={{
                height: 20,
                paddingTop: 1.2,
                paddingLeft: 1.5,
                paddingBottom: 3,
              }}
            >
              <Typography
                variant="subtitle2"
                sx={{
                  fontSize: "13px",
                  fontWeight: 400,
                  color: "text.secondary",
                  letterSpacing: "-0.2px",
                }}
              >
                {user.email}
              </Typography>
            </ListItem>
          </List>
          {isOwner && (
            <ListItemButton
              sx={{ paddingTop: 1, paddingBottom: 1 }}
              onClick={() => {
                onClose();
                navigate("/dashboard/settings/user-management");
                trackEvent(Events.administrationButtonClicked);
              }}
            >
              <ListItemIcon sx={{ marginRight: 0.5 }}>
                <SvgColor
                  src="/assets/icons/settings/userManagement.svg"
                  sx={{
                    width: 20,
                    height: 20,
                  }}
                />
              </ListItemIcon>
              <ListItemText
                primary="Administration"
                primaryTypographyProps={{
                  fontSize: "13px",
                  fontWeight: 400,
                  paddingLeft: 0.4,
                  color: "text.primary",
                }}
              />
            </ListItemButton>
          )}
          <Divider />

          <ListItemButton
            sx={{ padding: 1, paddingLeft: 2 }}
            onClick={() => {
              onClose();
              navigate("/dashboard/settings/profile-settings");
              trackEvent(Events.ProfileButtonlicked);
            }}
          >
            <ListItemIcon
              sx={{ marginRight: 0.5, paddingTop: 0.5, paddingBottom: 0.5 }}
            >
              <SvgColor
                src="/assets/icons/settings/Profile.svg"
                sx={{
                  width: 20,
                  height: 20,
                }}
              />
            </ListItemIcon>
            <ListItemText
              primary="Profile"
              primaryTypographyProps={{
                fontSize: "13px",
                fontWeight: 400,
                paddingLeft: 0.4,
                color: "text.primary",
              }}
            />
          </ListItemButton>

          {isOwner && (
            <>
              {/* <ListItemButton
                sx={{ padding: 1 }}
                onClick={() => {
                  onClose();
                  navigate("/dashboard/settings/api_keys");
                  trackEvent(Events.apiKeysButtonClicked);
                }}
              >
                <ListItemIcon sx={{ marginRight: 0.5 }}>
                  <APIKeyIcon
                    width={18}
                    height={18}
                    fill="grey"
                    stroke="currentColor"
                    strokeWidth={0.8}
                  />
                </ListItemIcon>
                <ListItemText
                  primary="API Keys"
                  primaryTypographyProps={{
                    fontSize: "13px",
                    fontWeight: 500,
                    paddingLeft: 0.4,
                    color: "grey",
                  }}
                />
              </ListItemButton> */}

              <ListItemButton
                sx={{ marginRight: 0.5, paddingTop: 0.5, paddingBottom: 0.5 }}
                onClick={() => {
                  onClose();
                  navigate("/dashboard/settings/pricing");
                  trackEvent(Events.managePlansButtonClicked);
                }}
              >
                <ListItemIcon sx={{ marginRight: 0.5 }}>
                  <Iconify
                    icon="proicons:dollar"
                    sx={{
                      color: "text.primary",
                      width: 20,
                      height: 20,
                    }}
                  />
                </ListItemIcon>
                <ListItemText
                  primary="Manage Plans"
                  primaryTypographyProps={{
                    fontSize: "13px",
                    fontWeight: 400,
                    paddingLeft: 0.4,
                    color: "text.primary",
                  }}
                />
              </ListItemButton>
            </>
          )}

          <Divider sx={{ marginTop: 4 }} />
          <ListItemButton
            sx={{ paddingLeft: 0.5, marginTop: 0.5, marginBottom: 0.5 }}
            onClick={() => {
              onLogoutClick();
              trackEvent(Events.logoutButtonClicked);
            }}
          >
            <ListItemIcon sx={{ marginRight: 0.5, marginLeft: 2 }}>
              <Iconify
                icon="mynaui:logout"
                sx={{
                  width: 20,
                  height: 20,
                  color: "red.500",
                }}
              />
            </ListItemIcon>
            <ListItemText
              primary="Logout"
              primaryTypographyProps={{
                fontSize: "14px",
                fontWeight: 400,
                color: "red.500",
              }}
            />
          </ListItemButton>
        </Stack>
      </List>
    </Popover>
  );
};

ProfilePopover.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool,
  onClose: PropTypes.func,
};

export default ProfilePopover;
