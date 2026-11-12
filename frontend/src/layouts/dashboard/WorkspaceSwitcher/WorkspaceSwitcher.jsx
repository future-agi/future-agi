import {
  Box,
  Popover,
  Typography,
  Button,
  ButtonBase,
  Avatar,
  Divider,
  Tooltip,
} from "@mui/material";
import React, { useEffect, useId, useRef, useState } from "react";
import SVGColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import SelectWorkspace from "./SelectWorkspace";
import SelectOrganization from "./SelectOrganization";
import { ORG_CREATE_SSO_REASON, canInviteMembers } from "./orgCreateReason";
import CreateOrganizationModal from "./CreateOrganizationModal";
import ThemeSelector from "./ThemeSelector";
import {
  useSettingsOpen,
  useCreateWorkspaceModal,
  useCreateOrganizationModal,
} from "../states";
import { useAuthContext } from "src/auth/hooks";
import { signOutToSignIn } from "src/auth/sign-out";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useOrganization } from "src/contexts/OrganizationContext";
import PropTypes from "prop-types";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useLocation, useNavigate } from "react-router";
import AllActionForm from "src/pages/dashboard/settings/UserManagementV2/AllActionForm";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { isPrototypeMode } from "src/utils/prototype";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { useOrganizationSettingsLanding } from "src/pages/dashboard/settings/organization-settings-landing";

// --- Section header with optional + button -----------------------------------

const SectionHeader = ({ label, onAdd, disabledReason }) => (
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
    {/* Sentence-case section label, like the sidebar's group labels. */}
    <Typography
      sx={{
        fontSize: 12,
        lineHeight: "16px",
        fontWeight: 500,
        textTransform: "none",
        letterSpacing: 0,
        color: "text.tertiary",
      }}
    >
      {label}
    </Typography>
    {onAdd && (
      // Never removed (owner rule): when creation is not allowed the + stays,
      // disabled, and says why.
      <Tooltip
        title={disabledReason || ""}
        disableHoverListener={!disabledReason}
      >
        <Box
          component={ButtonBase}
          aria-label={
            disabledReason
              ? `Create ${label.toLowerCase()}. ${disabledReason}`
              : `Create ${label.toLowerCase()}`
          }
          aria-disabled={disabledReason ? "true" : undefined}
          onClick={(e) => {
            e.stopPropagation();
            if (!disabledReason) onAdd();
          }}
          sx={{
            cursor: disabledReason ? "default" : "pointer",
            display: "flex",
            alignItems: "center",
            borderRadius: "4px",
            p: 0.25,
            opacity: disabledReason ? 0.5 : 1,
            "&:hover": {
              backgroundColor: disabledReason ? "transparent" : "action.hover",
            },
          }}
        >
          <Iconify
            icon="solar:add-linear"
            sx={{ width: 16, height: 16, color: "text.disabled" }}
          />
        </Box>
      </Tooltip>
    )}
  </Box>
);

SectionHeader.propTypes = {
  label: PropTypes.string.isRequired,
  onAdd: PropTypes.func,
  disabledReason: PropTypes.string,
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
    component={ButtonBase}
    aria-expanded={hasSubmenu ? active : undefined}
    onClick={onClick}
    onKeyDown={(event) => {
      if (hasSubmenu && event.key === "ArrowRight") {
        event.preventDefault();
        onClick?.(event);
      }
    }}
    onMouseEnter={onMouseEnter}
    onMouseLeave={onMouseLeave}
    sx={{
      display: "flex",
      alignItems: "center",
      gap: 1,
      px: 1.5,
      py: 0.5,
      cursor: "pointer",
      borderRadius: "6px",
      mx: 0.5,
      width: "calc(100% - 8px)",
      textAlign: "left",
      "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main" },
      backgroundColor: active ? "action.selected" : "transparent",
      "&:hover": { backgroundColor: "action.hover" },
    }}
  >
    {icon}
    <Typography
      fontWeight={400}
      color="text.primary"
      typography="s2_1"
      sx={{ flex: 1 }}
    >
      {title}
    </Typography>
    {hasSubmenu && (
      <Iconify
        icon="solar:alt-arrow-right-linear"
        sx={{ width: 16, height: 16, color: "text.secondary" }}
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

// --- Listed row that opens nothing for this person ---------------------------

// Stays in the menu and in the tab order, named by its title, and says why it
// opens nothing; a click or a key press does nothing.
const UnavailableMenuRow = ({ icon, title, reason }) => {
  const titleId = useId();
  const reasonId = useId();
  return (
    <Box
      component={ButtonBase}
      disableRipple
      aria-disabled="true"
      aria-labelledby={titleId}
      aria-describedby={reasonId}
      sx={{
        display: "flex",
        alignItems: "flex-start",
        gap: 1,
        px: 1.5,
        py: 0.5,
        cursor: "default",
        borderRadius: "6px",
        mx: 0.5,
        width: "calc(100% - 8px)",
        textAlign: "left",
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
        },
      }}
    >
      {icon}
      <Box component="span" sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          id={titleId}
          component="span"
          display="block"
          fontWeight={400}
          color="text.disabled"
          typography="s2_1"
        >
          {title}
        </Typography>
        <Typography
          id={reasonId}
          component="span"
          display="block"
          variant="caption"
          color="text.secondary"
        >
          {reason}
        </Typography>
      </Box>
    </Box>
  );
};

UnavailableMenuRow.propTypes = {
  icon: PropTypes.node,
  title: PropTypes.string.isRequired,
  reason: PropTypes.string.isRequired,
};

// --- Avatar row (workspace / org current) ------------------------------------

const AvatarRow = React.forwardRef(
  ({ letter, label, onMouseEnter, onMouseLeave, onClick, active }, ref) => (
    <Box
      component={ButtonBase}
      ref={ref}
      aria-label={label}
      aria-expanded={active}
      onKeyDown={(event) => {
        if (event.key === "ArrowRight") {
          event.preventDefault();
          onClick?.(event);
        }
      }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      onClick={onClick}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        px: 1.5,
        py: 0.5,
        cursor: "pointer",
        borderRadius: "6px",
        mx: 0.5,
        width: "calc(100% - 8px)",
        textAlign: "left",
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
        },
        "&:hover": { backgroundColor: "action.hover" },
      }}
    >
      <Avatar
        variant="rounded"
        sx={{
          width: 20,
          height: 20,
          borderRadius: "4px",
          fontSize: "12px",
          fontWeight: 600,
        }}
      >
        {letter}
      </Avatar>
      <Typography
        fontWeight={400}
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
        icon="solar:alt-arrow-right-linear"
        sx={{ width: 16, height: 16, color: "text.secondary" }}
      />
    </Box>
  ),
);

AvatarRow.displayName = "AvatarRow";
AvatarRow.propTypes = {
  active: PropTypes.bool,
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
  const [focusSubmenu, setFocusSubmenu] = useState(null);
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
  // "Organization settings" opens where it leads somewhere this person may
  // open: on cloud (Usage Summary, open to every role), and off cloud only
  // when an organization settings page admits their role. Until the
  // deployment mode is known, it stays offered as before. Everyone else still
  // sees the row, disabled, with the reason.
  const { isCloud, isSuccess: deploymentModeKnown } = useDeploymentMode();
  const organizationSettingsPage = useOrganizationSettingsLanding();
  const offerOrganizationSettings =
    isPrototypeMode() ||
    !deploymentModeKnown ||
    isCloud ||
    organizationSettingsPage !== null;

  const orgDisplayLabel =
    currentOrganizationDisplayName || currentOrganizationName || "Organization";

  const wsDisplayLabel =
    currentWorkspaceDisplayName ||
    user?.default_workspace_display_name ||
    "Workspace";

  const canCreateOrganization = user?.can_create_organization === true;
  const canInvite = canInviteMembers({
    currentWorkspaceRole,
    user,
    currentOrganizationRole,
  });

  const onLogoutClick = () => {
    trackEvent(Events.logoutConfirmed, { [PropertyName.click]: "click" });
    signOutToSignIn(logout, navigate);
  };

  const clearPopperTimeouts = () => {
    [wsPopperTimeoutRef, orgPopperTimeoutRef, themePopperTimeoutRef].forEach(
      (ref) => {
        clearTimeout(ref.current);
        ref.current = null;
      },
    );
  };

  // Explicit activation focuses choices; hover keeps the existing preview.
  const openSubmenu = (name, target, focus = false) => {
    clearPopperTimeouts();
    setFocusSubmenu(focus ? name : null);
    setWsPopperOpen(name === "workspace");
    setOrgPopperOpen(name === "organization");
    setThemePopperOpen(name === "theme");
    if (name === "workspace") setWsPopperAnchorEl(target);
    if (name === "organization") setOrgPopperAnchorEl(target);
    if (name === "theme") setThemePopperAnchorEl(target);
  };

  // Close all sub-poppers
  const closeAllPoppers = () => {
    clearPopperTimeouts();
    setFocusSubmenu(null);
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
        // A keyboard user reaches and opens the switcher like a button.
        role="button"
        tabIndex={0}
        aria-label="Switch workspace"
        aria-haspopup="dialog"
        aria-expanded={open}
        sx={{
          cursor: "pointer",
          borderRadius: "6px",
          width: "100%",
          outline: "none",
          "&:hover": { backgroundColor: "action.hover" },
          "&:focus-visible": {
            outline: (theme) => `2px solid ${theme.palette.primary.main}`,
            outlineOffset: "-2px",
          },
        }}
        onClick={() => setOpen(!open)}
        data-testid="workspace-switcher"
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen(!open);
          }
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            borderRadius: "6px",
            px: 0.75,
            height: 32,
            width: "100%",
            justifyContent: collapsed ? "center" : "space-between",
          }}
          ref={anchorEl}
        >
          <Avatar
            variant="rounded"
            sx={{
              width: 20,
              height: 20,
              borderRadius: "4px",
            }}
          >
            <Typography fontSize={11} fontWeight={600} color="text.primary">
              {wsDisplayLabel?.slice(0, 1)}
            </Typography>
          </Avatar>

          {!collapsed && (
            <Typography
              fontSize={12}
              lineHeight="20px"
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
            role: "dialog",
            "aria-label": "Workspace and account",
            sx: {
              overflow: "visible",
              borderRadius: "8px",
              border: "1px solid",
              borderColor: "divider",
              mt: 0.5,
              width: 240,
              py: 0.5,
            },
          },
        }}
      >
        {/* ============ WORKSPACE section ============ */}
        <SectionHeader
          label="Workspace"
          // L7b absent-sweep (switcher:org-workspace-actions): creating a
          // workspace or an organization and inviting members are not
          // mocked, so prototype builds leave them out.
          onAdd={
            !isPrototypeMode() &&
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
            active={wsPopperOpen}
            onClick={(e) => openSubmenu("workspace", e.currentTarget, true)}
            onMouseEnter={(e) => openSubmenu("workspace", e.currentTarget)}
            onMouseLeave={() => {
              wsPopperTimeoutRef.current = setTimeout(
                () => setWsPopperOpen(false),
                100,
              );
            }}
          />
        )}

        {/* Invite members */}
        {canInvite && !isPrototypeMode() && (
          <MenuRow
            icon={
              <Iconify
                icon="solar:add-linear"
                sx={{ width: 16, height: 16, color: "text.secondary" }}
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
              sx={{ width: 16, height: 16, color: "text.secondary" }}
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
          label="Organization"
          onAdd={
            isPrototypeMode()
              ? undefined
              : () => {
                  setOpen(false);
                  useCreateOrganizationModal.getState().setOpen(true);
                }
          }
          disabledReason={
            canCreateOrganization ? undefined : ORG_CREATE_SSO_REASON
          }
        />

        {/* Current organization with sub-menu */}
        <AvatarRow
          letter={orgDisplayLabel?.slice(0, 1)}
          label={orgDisplayLabel}
          // eslint-disable-next-line no-restricted-syntax -- ds: avatar (organisation)
          color="#b8a068"
          active={orgPopperOpen}
          onClick={(e) => openSubmenu("organization", e.currentTarget, true)}
          onMouseEnter={(e) => openSubmenu("organization", e.currentTarget)}
          onMouseLeave={() => {
            orgPopperTimeoutRef.current = setTimeout(
              () => setOrgPopperOpen(false),
              100,
            );
          }}
        />

        {/* Organization settings */}
        {offerOrganizationSettings ? (
          <MenuRow
            icon={
              <Iconify
                icon="solar:settings-linear"
                sx={{ width: 16, height: 16, color: "text.secondary" }}
              />
            }
            title="Organization settings"
            onClick={() => {
              localStorage.setItem(
                "redirect-url-from-settings",
                location.pathname,
              );
              // L7b absent-sweep: a prototype build has no usage summary (not
              // mocked); its Organization settings open the Workspaces page.
              // Elsewhere an organization Owner or Admin opens Org Settings:
              // the settings menu lists it for them and its route admits them.
              // Everyone else opens the usage summary route, which lands on
              // the organization page their role opens where it is not offered.
              const orgRole = user?.organization_role;
              let destination = "/dashboard/settings/usage-summary";
              if (isPrototypeMode()) {
                destination = "/dashboard/settings/workspace";
              } else if (orgRole === "Owner" || orgRole === "Admin") {
                destination = "/dashboard/settings/org-settings";
              }
              navigate(destination);
              setOpen(false);
              closeAllPoppers(); // Close sub-poppers to prevent hover triggering during navigation
            }}
          />
        ) : (
          <UnavailableMenuRow
            icon={
              <Iconify
                icon="solar:settings-linear"
                sx={{ width: 16, height: 16, color: "text.disabled" }}
              />
            }
            title="Organization settings"
            reason="No organization settings page is open to your role."
          />
        )}

        <Divider sx={{ my: 0.5 }} />

        {/* ============ ACCOUNT section ============ */}
        <SectionHeader label="Account" />

        {/* User info */}
        <Box
          component={ButtonBase}
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
            py: 0.5,
            mx: 0.5,
            width: "calc(100% - 8px)",
            textAlign: "left",
            "&:focus-visible": {
              outline: "2px solid",
              outlineColor: "primary.main",
            },
            cursor: "pointer",
            borderRadius: "6px",
            "&:hover": { backgroundColor: "action.hover" },
          }}
        >
          <Avatar
            sx={{
              width: 24,
              height: 24,
              fontSize: "12px",
              fontWeight: 600,
            }}
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
              sx={{ width: 16, height: 16, color: "text.secondary" }}
            />
          }
          title="Theme"
          hasSubmenu
          active={themePopperOpen}
          onClick={(e) => openSubmenu("theme", e.currentTarget, true)}
          onMouseEnter={(e) => openSubmenu("theme", e.currentTarget)}
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
              icon="solar:logout-linear"
              sx={{ width: 16, height: 16, color: "text.secondary" }}
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
        {/* Inside the popover so keyboard focus remains in its focus trap. */}
        <SelectWorkspace
          open={open && wsPopperOpen}
          anchorEl={wsPopperAnchorEl}
          setOpen={setWsPopperOpen}
          autoFocus={focusSubmenu === "workspace"}
          ref={wsPopperTimeoutRef}
        />
        <SelectOrganization
          open={open && orgPopperOpen}
          anchorEl={orgPopperAnchorEl}
          setOpen={setOrgPopperOpen}
          canCreateOrganization={canCreateOrganization}
          autoFocus={focusSubmenu === "organization"}
          ref={orgPopperTimeoutRef}
        />
        <ThemeSelector
          open={open && themePopperOpen}
          anchorEl={themePopperAnchorEl}
          setOpen={setThemePopperOpen}
          autoFocus={focusSubmenu === "theme"}
          ref={themePopperTimeoutRef}
        />
      </Popover>

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
      {!isPrototypeMode() && (
        <>
          <AllActionForm
            openActionForm={inviteUser ? { action: "invite-user" } : null}
            onClose={() => setInviteUser(false)}
            requiredKeys={{ email: true, role: true }}
            showInviteLinks
          />
          {canCreateOrganization && <CreateOrganizationModal />}
        </>
      )}
    </Box>
  );
};

WorkspaceSwitcher.propTypes = {
  collapsed: PropTypes.bool,
};

export default WorkspaceSwitcher;
