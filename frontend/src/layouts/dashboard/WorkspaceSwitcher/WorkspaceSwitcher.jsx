import {
  Box,
  Popover,
  Typography,
  Button,
  Avatar,
  Divider,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import React, { useEffect, useRef, useState } from "react";
import SVGColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import SelectWorkspace from "./SelectWorkspace";
import SelectOrganization from "./SelectOrganization";
import CreateOrganizationModal from "./CreateOrganizationModal";
import ThemeSelector from "./ThemeSelector";
import {
  useSettingsOpen,
  useCreateWorkspaceModal,
  useCreateOrganizationModal,
} from "../states";
import { useAuthContext } from "src/auth/hooks";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useOrganization } from "src/contexts/OrganizationContext";
import PropTypes from "prop-types";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useLocation, useNavigate } from "react-router";
import AllActionForm from "src/pages/dashboard/settings/UserManagementV2/AllActionForm";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

// --- Section header with optional + button -----------------------------------

const SectionHeader = ({ label, onAdd }) => (
  <Box
    sx={{
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      px: 1.5,
      pt: 1,
      pb: 0.5,
    }}
  >
    <Typography
      variant="overline"
      sx={{
        fontSize: "11px",
        fontWeight: 600,
        letterSpacing: "0.08em",
        color: "text.disabled",
      }}
    >
      {label}
    </Typography>
    {onAdd && (
      <Box
        onClick={(e) => {
          e.stopPropagation();
          onAdd();
        }}
        sx={{
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          borderRadius: 0.5,
          p: 0.25,
          "&:hover": { backgroundColor: "action.hover" },
        }}
      >
        <Iconify
          icon="eva:plus-fill"
          sx={{ width: 16, height: 16, color: "text.disabled" }}
        />
      </Box>
    )}
  </Box>
);

SectionHeader.propTypes = {
  label: PropTypes.string.isRequired,
  onAdd: PropTypes.func,
};

// --- Clickable menu row -------------------------------------------------------

const MenuRow = ({
  icon,
  title,
  onClick,
  onMouseEnter,
  onMouseLeave,
  hasSubmenu,
  active,
}) => (
  <Box
    onClick={onClick}
    onMouseEnter={onMouseEnter}
    onMouseLeave={onMouseLeave}
    sx={{
      display: "flex",
      alignItems: "center",
      gap: 1,
      px: 1.5,
      py: 0.75,
      cursor: "pointer",
      borderRadius: "6px",
      mx: 0.5,
      backgroundColor: active ? "action.selected" : "transparent",
      "&:hover": { backgroundColor: "action.hover" },
    }}
  >
    {icon}
    <Typography
      fontWeight={500}
      color="text.primary"
      typography="s2_1"
      sx={{ flex: 1 }}
    >
      {title}
    </Typography>
    {hasSubmenu && (
      <Iconify
        icon="eva:chevron-right-fill"
        sx={{ width: 18, height: 18, color: "text.secondary" }}
      />
    )}
  </Box>
);

MenuRow.propTypes = {
  icon: PropTypes.node,
  title: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  onMouseEnter: PropTypes.func,
  onMouseLeave: PropTypes.func,
  hasSubmenu: PropTypes.bool,
  active: PropTypes.bool,
};

// --- Avatar row (workspace / org current) ------------------------------------

const AvatarRow = React.forwardRef(
  ({ letter, label, onMouseEnter, onMouseLeave, onClick, color }, _ref) => (
    <Box
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      onClick={onClick}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        px: 1.5,
        py: 0.75,
        cursor: "pointer",
        borderRadius: "6px",
        mx: 0.5,
        "&:hover": { backgroundColor: "action.hover" },
      }}
    >
      <Avatar
        variant="rounded"
        sx={(theme) => ({
          width: 28,
          height: 28,
          fontSize: "13px",
          fontWeight: 600,
          backgroundColor: alpha(color || theme.palette.primary.main, 0.2),
          color: color || theme.palette.primary.main,
        })}
      >
        {letter}
      </Avatar>
      <Typography
        fontWeight={500}
        color="text.primary"
        typography="s2_1"
        sx={{
          flex: 1,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </Typography>
      <Iconify
        icon="eva:chevron-right-fill"
        sx={{ width: 18, height: 18, color: "text.secondary" }}
      />
    </Box>
  ),
);

AvatarRow.displayName = "AvatarRow";
AvatarRow.propTypes = {
  letter: PropTypes.string,
  label: PropTypes.string,
  onMouseEnter: PropTypes.func,
  onMouseLeave: PropTypes.func,
  onClick: PropTypes.func,
  color: PropTypes.string,
};

// --- Main component ----------------------------------------------------------

const WorkspaceSwitcher = ({ collapsed }) => {
  const [open, setOpen] = useState(false);
  const [wsPopperOpen, setWsPopperOpen] = useState(false);
  const [wsPopperAnchorEl, setWsPopperAnchorEl] = useState(null);
  const [orgPopperOpen, setOrgPopperOpen] = useState(false);
  const [orgPopperAnchorEl, setOrgPopperAnchorEl] = useState(null);
  const [themePopperOpen, setThemePopperOpen] = useState(false);
  const [themePopperAnchorEl, setThemePopperAnchorEl] = useState(null);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [inviteUser, setInviteUser] = useState(false);
  const anchorEl = useRef(null);
  const wsPopperTimeoutRef = useRef(null);
  const orgPopperTimeoutRef = useRef(null);
  const themePopperTimeoutRef = useRef(null);

  const navigate = useNavigate();
  const location = useLocation();
  const { logout, user } = useAuthContext();
  const {
    currentWorkspaceId,
    currentWorkspaceDisplayName,
    currentWorkspaceRole,
  } = useWorkspace();
  const {
    currentOrganizationDisplayName,
    currentOrganizationName,
    currentOrganizationRole,
  } = useOrganization();

  const orgDisplayLabel =
    currentOrganizationDisplayName || currentOrganizationName || "Organization";

  const wsDisplayLabel =
    currentWorkspaceDisplayName ||
    user?.default_workspace_display_name ||
    "Workspace";

  const allowedToInvite = ["Owner", "Admin", "workspace_admin"];
  const canInvite =
    allowedToInvite.includes(
      currentWorkspaceRole || user?.default_workspace_role,
    ) || allowedToInvite.includes(currentOrganizationRole);

  const onLogoutClick = () => {
    logout();
    trackEvent(Events.logoutConfirmed, { [PropertyName.click]: "click" });
    navigate("/", { replace: true });
  };

  // Close all sub-poppers
  const closeAllPoppers = () => {
    setWsPopperOpen(false);
    setOrgPopperOpen(false);
    setThemePopperOpen(false);
  };

  // Cleanup timeouts on unmount
  useEffect(() => {
    return () => {
      if (wsPopperTimeoutRef.current) clearTimeout(wsPopperTimeoutRef.current);
      if (orgPopperTimeoutRef.current)
        clearTimeout(orgPopperTimeoutRef.current);
      if (themePopperTimeoutRef.current)
        clearTimeout(themePopperTimeoutRef.current);
    };
  }, []);

  return (
    <Box sx={{ px: 0.5, width: collapsed ? "48px" : "auto" }}>
      {/* ---- Trigger button ---- */}
      <Box
        sx={{
          cursor: "pointer",
          borderRadius: "6px",
          width: "100%",
          "&:hover": { backgroundColor: "action.hover" },
        }}
        onClick={() => setOpen(!open)}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            borderRadius: 0.5,
            px: 0.75,
            py: 0.5,
            width: "100%",
            justifyContent: collapsed ? "center" : "space-between",
          }}
          ref={anchorEl}
        >
          <Avatar
            variant="rounded"
            sx={(theme) => ({
              width: theme.spacing(3),
              height: theme.spacing(3),
              backgroundColor: alpha(theme.palette.primary.main, 0.2),
            })}
          >
            <Typography
              typography="s1"
              fontWeight="fontWeightMedium"
              color="text.primary"
            >
              {wsDisplayLabel?.slice(0, 1)}
            </Typography>
          </Avatar>

          {!collapsed && (
            <Typography
              typography="s1"
              color="text.primary"
              sx={{
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                flex: 1,
              }}
              fontWeight={500}
            >
              {wsDisplayLabel}
            </Typography>
          )}
          {!collapsed && (
            <SVGColor
              src="/assets/icons/custom/lucide--chevron-down.svg"
              sx={{ width: "20px", height: "20px" }}
            />
          )}
        </Box>
      </Box>

      {/* ---- Popover dropdown ---- */}
      <Popover
        open={open}
        onClose={() => {
          setOpen(false);
          closeAllPoppers();
        }}
        anchorEl={anchorEl.current}
        anchorOrigin={{ vertical: 26, horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: {
              borderRadius: "8px",
              border: "1px solid",
              borderColor: "divider",
              boxShadow: "0 4px 12px 0 rgba(0,0,0,0.08)",
              mt: 0.5,
              width: 260,
              py: 0.5,
            },
          },
        }}
      >
        {/* ============ WORKSPACE section ============ */}
        <SectionHeader
          label="WORKSPACE"
          onAdd={
            user?.ws_enabled &&
            (user?.organization_role === "Owner" ||
              user?.organization_role === "Admin")
              ? () => {
                  setOpen(false);
                  useCreateWorkspaceModal.getState().setOpen(true);
                  trackEvent(Events.workspaceCreateClicked, {
                    [PropertyName.click]: "click",
                  });
                }
              : undefined
          }
        />

        {/* Current workspace with sub-menu */}
        {user?.ws_enabled && (
          <AvatarRow
            letter={wsDisplayLabel?.slice(0, 1)}
            label={wsDisplayLabel}
            onMouseEnter={(e) => {
              if (wsPopperTimeoutRef.current) {
                clearTimeout(wsPopperTimeoutRef.current);
                wsPopperTimeoutRef.current = null;
              }
              setWsPopperAnchorEl(e.currentTarget);
              setWsPopperOpen(true);
              setOrgPopperOpen(false);
              setThemePopperOpen(false);
            }}
            onMouseLeave={() => {
              wsPopperTimeoutRef.current = setTimeout(
                () => setWsPopperOpen(false),
                100,
              );
            }}
          />
        )}

        {/* Invite members */}
        {canInvite && (
          <MenuRow
            icon={
              <Iconify
                icon="eva:plus-outline"
                sx={{ width: 20, height: 20, color: "text.secondary" }}
              />
            }
            title="Invite members"
            onClick={() => {
              trackEvent(Events.workspaceInviteMembersClicked, {
                [PropertyName.click]: "click",
              });
              setInviteUser(true);
              setOpen(false);
            }}
          />
        )}

        {/* Settings */}
        <MenuRow
          icon={
            <Iconify
              icon="solar:settings-linear"
              sx={{ width: 20, height: 20, color: "text.secondary" }}
            />
          }
          title="Workspace settings"
          onClick={() => {
            useSettingsOpen.setState({ settingOpen: true });
            trackEvent(Events.workspaceSettingsClicked, {
              [PropertyName.click]: "click",
            });
            localStorage.setItem(
              "redirect-url-from-settings",
              location.pathname,
            );
            navigate(
              currentWorkspaceId
                ? `/dashboard/settings/workspace/${currentWorkspaceId}/general`
                : "/dashboard/settings/profile-settings",
            );
            setOpen(false);
            closeAllPoppers(); // Close sub-poppers to prevent hover triggering during navigation
          }}
        />

        <Divider sx={{ my: 0.5 }} />

        {/* ============ ORGANIZATION section ============ */}
        <SectionHeader
          label="ORGANIZATION"
          onAdd={() => {
            setOpen(false);
            useCreateOrganizationModal.getState().setOpen(true);
          }}
        />

        {/* Current organization with sub-menu */}
        <AvatarRow
          letter={orgDisplayLabel?.slice(0, 1)}
          label={orgDisplayLabel}
          color="#b8a068"
          onMouseEnter={(e) => {
            if (orgPopperTimeoutRef.current) {
              clearTimeout(orgPopperTimeoutRef.current);
              orgPopperTimeoutRef.current = null;
            }
            setOrgPopperAnchorEl(e.currentTarget);
            setOrgPopperOpen(true);
            setWsPopperOpen(false);
            setThemePopperOpen(false);
          }}
          onMouseLeave={() => {
            orgPopperTimeoutRef.current = setTimeout(
              () => setOrgPopperOpen(false),
              100,
            );
          }}
        />

        {/* Organization settings */}
        <MenuRow
          icon={
            <Iconify
              icon="solar:settings-linear"
              sx={{ width: 20, height: 20, color: "text.secondary" }}
            />
          }
          title="Organization settings"
          onClick={() => {
            localStorage.setItem(
              "redirect-url-from-settings",
              location.pathname,
            );
            navigate("/dashboard/settings/usage-summary");
            setOpen(false);
            closeAllPoppers(); // Close sub-poppers to prevent hover triggering during navigation
          }}
        />

        <Divider sx={{ my: 0.5 }} />

        {/* ============ ACCOUNT section ============ */}
        <SectionHeader label="ACCOUNT" />

        {/* User info */}
        <Box
          onMouseEnter={() => {
            closeAllPoppers();
          }}
          onClick={() => {
            setOpen(false);
            closeAllPoppers();
            navigate("/dashboard/settings/profile-settings");
          }}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            px: 1.5,
            py: 0.75,
            mx: 0.5,
            cursor: "pointer",
            borderRadius: "6px",
            "&:hover": { backgroundColor: "action.hover" },
          }}
        >
          <Avatar
            sx={(theme) => ({
              width: 28,
              height: 28,
              fontSize: "13px",
              fontWeight: 600,
              backgroundColor: alpha(theme.palette.success.main, 0.2),
              color: theme.palette.success.main,
            })}
          >
            {(user?.name || user?.email || "U")?.slice(0, 1).toUpperCase()}
          </Avatar>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              fontWeight={500}
              color="text.primary"
              typography="s2_1"
              sx={{
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {user?.name || "User"}
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                display: "block",
              }}
            >
              {user?.email}
            </Typography>
          </Box>
        </Box>

        {/* Theme */}
        <MenuRow
          icon={
            <Iconify
              icon="solar:monitor-linear"
              sx={{ width: 20, height: 20, color: "text.secondary" }}
            />
          }
          title="Theme"
          hasSubmenu
          onMouseEnter={(e) => {
            if (themePopperTimeoutRef.current) {
              clearTimeout(themePopperTimeoutRef.current);
              themePopperTimeoutRef.current = null;
            }
            setThemePopperAnchorEl(e.currentTarget);
            setThemePopperOpen(true);
            setWsPopperOpen(false);
            setOrgPopperOpen(false);
          }}
          onMouseLeave={() => {
            themePopperTimeoutRef.current = setTimeout(
              () => setThemePopperOpen(false),
              100,
            );
          }}
        />

        {/* Log out */}
        <MenuRow
          icon={
            <Iconify
              icon="mynaui:logout"
              sx={{ width: 20, height: 20, color: "text.secondary" }}
            />
          }
          title="Log out"
          onClick={() => {
            trackEvent(Events.logoutClicked, {
              [PropertyName.click]: "click",
            });
            setDeleteModalOpen(true);
            setOpen(false);
          }}
        />
      </Popover>

      {/* ---- Sub-poppers ---- */}
      <SelectWorkspace
        open={wsPopperOpen}
        anchorEl={wsPopperAnchorEl}
        setOpen={setWsPopperOpen}
        ref={wsPopperTimeoutRef}
      />
      <SelectOrganization
        open={orgPopperOpen}
        anchorEl={orgPopperAnchorEl}
        setOpen={setOrgPopperOpen}
        ref={orgPopperTimeoutRef}
      />
      <ThemeSelector
        open={themePopperOpen}
        anchorEl={themePopperAnchorEl}
        setOpen={setThemePopperOpen}
        ref={themePopperTimeoutRef}
      />

      {/* ---- Modals ---- */}
      <ConfirmDialog
        open={deleteModalOpen}
        onClose={() => setDeleteModalOpen(false)}
        title="Are you sure you want to logout?"
        content={
          <Typography color="text.secondary">
            By logging out your current progress will be paused.
          </Typography>
        }
        action={
          <Button
            variant="contained"
            color="error"
            size="small"
            onClick={onLogoutClick}
          >
            Logout
          </Button>
        }
      />
      <AllActionForm
        openActionForm={inviteUser ? { action: "invite-user" } : null}
        onClose={() => setInviteUser(false)}
        requiredKeys={{ email: true, role: true }}
      />
      <CreateOrganizationModal />
    </Box>
  );
};

WorkspaceSwitcher.propTypes = {
  collapsed: PropTypes.bool,
};

export default WorkspaceSwitcher;
