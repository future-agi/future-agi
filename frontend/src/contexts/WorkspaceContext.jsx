import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import PropTypes from "prop-types";
import axios, { endpoints } from "src/utils/axios";
import { useAuthContext } from "src/auth/hooks";
import { useOrganization } from "src/contexts/OrganizationContext";
import { enqueueSnackbar } from "src/components/snackbar";
import logger from "src/utils/logger";
import {
  SS_KEY_ORG_ID,
  SS_KEY_WORKSPACE_DISPLAY_NAME,
  SS_KEY_WORKSPACE_ID,
  SS_KEY_WORKSPACE_NAME,
  SS_KEY_WORKSPACE_ORG_ID,
  SS_KEY_WORKSPACE_ROLE,
  SS_KEY_WS_LEVEL,
} from "src/utils/sessionKeys";

import { useWorkspaceUnavailableRecovery } from "./useWorkspaceUnavailableRecovery";

// --- sessionStorage helpers ---------------------------------------------------

const EMPTY_WORKSPACE = {
  id: null,
  name: null,
  displayName: null,
  role: null,
  wsLevel: null,
  orgId: null,
};

export function readSessionWorkspace() {
  try {
    return {
      id: sessionStorage.getItem(SS_KEY_WORKSPACE_ID) || null,
      name: sessionStorage.getItem(SS_KEY_WORKSPACE_NAME) || null,
      displayName:
        sessionStorage.getItem(SS_KEY_WORKSPACE_DISPLAY_NAME) || null,
      role: sessionStorage.getItem(SS_KEY_WORKSPACE_ROLE) || null,
      wsLevel: (() => {
        const raw = sessionStorage.getItem(SS_KEY_WS_LEVEL);
        if (raw == null) return null;
        const parsed = parseInt(raw, 10);
        return Number.isNaN(parsed) ? null : parsed;
      })(),
      orgId: sessionStorage.getItem(SS_KEY_WORKSPACE_ORG_ID) || null,
    };
  } catch {
    return { ...EMPTY_WORKSPACE };
  }
}

export function readSessionOrgId() {
  try {
    return sessionStorage.getItem(SS_KEY_ORG_ID) || null;
  } catch {
    return null;
  }
}

// Sessions written before workspaceOrgId existed have no orgId, so they fail
// the check and get reseeded.
export function readSessionWorkspaceForOrg(orgId) {
  const stored = readSessionWorkspace();
  if (!stored.id || !orgId || stored.orgId !== orgId) return null;
  return stored;
}

export function writeSessionWorkspace({
  id,
  name,
  displayName,
  role,
  wsLevel,
  orgId,
}) {
  try {
    if (id) sessionStorage.setItem(SS_KEY_WORKSPACE_ID, id);
    else sessionStorage.removeItem(SS_KEY_WORKSPACE_ID);

    if (name) sessionStorage.setItem(SS_KEY_WORKSPACE_NAME, name);
    else sessionStorage.removeItem(SS_KEY_WORKSPACE_NAME);

    if (displayName)
      sessionStorage.setItem(SS_KEY_WORKSPACE_DISPLAY_NAME, displayName);
    else sessionStorage.removeItem(SS_KEY_WORKSPACE_DISPLAY_NAME);

    if (role) sessionStorage.setItem(SS_KEY_WORKSPACE_ROLE, role);
    else sessionStorage.removeItem(SS_KEY_WORKSPACE_ROLE);

    if (wsLevel != null) sessionStorage.setItem(SS_KEY_WS_LEVEL, wsLevel);
    else sessionStorage.removeItem(SS_KEY_WS_LEVEL);

    if (orgId) sessionStorage.setItem(SS_KEY_WORKSPACE_ORG_ID, orgId);
    else sessionStorage.removeItem(SS_KEY_WORKSPACE_ORG_ID);
  } catch {
    // sessionStorage may be unavailable in some contexts (e.g. SSR)
  }
}

function clearSessionWorkspace() {
  try {
    sessionStorage.removeItem(SS_KEY_WORKSPACE_ID);
    sessionStorage.removeItem(SS_KEY_WORKSPACE_NAME);
    sessionStorage.removeItem(SS_KEY_WORKSPACE_DISPLAY_NAME);
    sessionStorage.removeItem(SS_KEY_WORKSPACE_ROLE);
    sessionStorage.removeItem(SS_KEY_WS_LEVEL);
    sessionStorage.removeItem(SS_KEY_WORKSPACE_ORG_ID);
  } catch {
    // noop
  }
}

// --- Axios header sync -------------------------------------------------------

function setWorkspaceHeader(workspaceId) {
  if (workspaceId) {
    axios.defaults.headers.common["X-Workspace-Id"] = workspaceId;
  } else {
    delete axios.defaults.headers.common["X-Workspace-Id"];
  }
}

// --- Context -----------------------------------------------------------------

const WorkspaceContext = createContext({
  currentWorkspaceId: null,
  currentWorkspaceName: null,
  currentWorkspaceDisplayName: null,
  currentWorkspaceRole: null,
  wsLevel: null,
  switchWorkspace: async () => {},
  clearWorkspace: () => {},
  updateWorkspaceName: () => {},
  isReady: false,
});

export function useWorkspace() {
  return useContext(WorkspaceContext);
}

// --- Provider ----------------------------------------------------------------

export function WorkspaceProvider({ children }) {
  const { user, authenticated, loading } = useAuthContext();
  const { currentOrganizationId } = useOrganization();

  const [workspace, setWorkspace] = useState(() => {
    // On mount, try sessionStorage first (survives refresh, per-tab)
    const stored = readSessionWorkspaceForOrg(readSessionOrgId());
    if (stored) {
      setWorkspaceHeader(stored.id);
      return stored;
    }
    return { ...EMPTY_WORKSPACE };
  });

  // Stored selectors are hints until an access-checked user-info arrives.
  const [isReady, setIsReady] = useState(false);

  const applyUserWorkspace = useCallback((userInfo, fallbackOrgId = null) => {
    const next = userInfo?.default_workspace_id
      ? {
          id: userInfo.default_workspace_id,
          name: userInfo.default_workspace_name ?? null,
          displayName: userInfo.default_workspace_display_name ?? null,
          role: userInfo.default_workspace_role ?? null,
          wsLevel: userInfo.ws_level ?? null,
          orgId: userInfo.organization?.id || fallbackOrgId,
        }
      : { ...EMPTY_WORKSPACE };
    setWorkspace(next);
    writeSessionWorkspace(next);
    setWorkspaceHeader(next.id);
    setIsReady(true);
  }, []);

  // When user data arrives (login / refresh), seed workspace if not already set
  useEffect(() => {
    if (!authenticated || !user) return;

    // The resolved org first: effects run child-first and this provider sits
    // inside OrganizationProvider, so currentOrganizationId is still the
    // previous org on the render where `user` arrives. Preferring it adopts the
    // old org's workspace row, and with it the old org's role and wsLevel.
    const activeOrgId = user.organization?.id || currentOrganizationId || null;

    const stored = readSessionWorkspaceForOrg(activeOrgId);
    if (stored && stored.id !== user.default_workspace_id) {
      // Initial user-info may reflect another tab's default. Ask again with
      // this tab's explicit selector: a valid independent selection survives,
      // while a rejected selector resolves to an accessible landing (or none).
      setWorkspaceHeader(stored.id);
      setIsReady(false);
      let active = true;
      const canApply = () =>
        active &&
        readSessionOrgId() === activeOrgId &&
        readSessionWorkspaceForOrg(activeOrgId)?.id === stored.id &&
        axios.defaults.headers.common["X-Workspace-Id"] === stored.id;
      axios
        .get(endpoints.auth.me, {
          headers: {
            "X-Workspace-Id": stored.id,
            "X-Organization-Id": activeOrgId,
          },
        })
        .then(({ data }) => {
          if (canApply()) {
            applyUserWorkspace(
              data?.organization?.id === activeOrgId ? data : null,
              activeOrgId,
            );
          }
        })
        .catch(() => {
          // Never label an unverified selector ready or replay a mutation.
          // The workspace list remains available for manual recovery.
          if (canApply()) {
            applyUserWorkspace(null);
            enqueueSnackbar(
              "Could not verify the selected workspace. Select a workspace to continue.",
              {
                variant: "error",
                preventDuplicate: true,
              },
            );
          }
        });
      return () => {
        active = false;
      };
    }

    // Nothing usable stored → seed from user-info (new tab / org changed)
    applyUserWorkspace(user, activeOrgId);
  }, [authenticated, user, currentOrganizationId, applyUserWorkspace]);

  // Switch workspace — called from UI
  const switchWorkspace = useCallback(
    async (newWorkspaceId, oldWorkspaceId) => {
      try {
        const response = await axios.post(endpoints.workspaces.switch, {
          old_workspace_id: oldWorkspaceId || workspace.id,
          new_workspace_id: newWorkspaceId,
        });

        const wsData = response?.data?.workspace || {};
        const newWs = {
          id: wsData.id || newWorkspaceId,
          name: wsData.name || null,
          displayName: wsData.display_name || wsData.name || null,
          role: response?.data?.user_role || null,
          wsLevel: workspace.wsLevel, // preserve until user-info re-fetched
          // A null here removes workspaceOrgId, and the reader then rejects
          // the row after the reload below, silently dropping the user back on
          // their default workspace. The tab's pinned org is the last resort.
          orgId:
            currentOrganizationId ||
            workspace.orgId ||
            readSessionOrgId() ||
            null,
        };

        // 1. Update sessionStorage
        writeSessionWorkspace(newWs);

        // 2. Update axios header
        setWorkspaceHeader(newWs.id);

        // 3. Hard refresh — clears all React state, query cache, component trees
        window.location.assign("/dashboard/develop");
      } catch (error) {
        logger.error("Workspace switch failed:", error);
        enqueueSnackbar(
          error?.result || error?.message || "Failed to switch workspace",
          { variant: "error" },
        );
        throw error;
      }
    },
    [workspace.id, workspace.wsLevel, workspace.orgId, currentOrganizationId],
  );

  // Update workspace display name in-place (e.g. after rename in settings)
  const updateWorkspaceName = useCallback((newDisplayName) => {
    setWorkspace((prev) => {
      const updated = { ...prev, displayName: newDisplayName };
      writeSessionWorkspace(updated);
      return updated;
    });
  }, []);

  // Clear workspace (logout, deleted workspace, etc.)
  const clearWorkspace = useCallback(() => {
    clearSessionWorkspace();
    setWorkspaceHeader(null);
    setWorkspace({ ...EMPTY_WORKSPACE });
    setIsReady(false);
  }, []);

  useWorkspaceUnavailableRecovery({
    authenticated,
    userId: user?.id,
    organizationId: user?.organization?.id || currentOrganizationId,
    clearWorkspace,
    applyUserWorkspace,
  });

  // Clear on logout (but NOT during initial auth loading — sessionStorage
  // must survive page refreshes for workspace switching to work correctly)
  useEffect(() => {
    if (!authenticated && !loading) {
      clearWorkspace();
    }
  }, [authenticated, loading, clearWorkspace]);

  const value = useMemo(
    () => ({
      currentWorkspaceId: workspace.id,
      currentWorkspaceName: workspace.name,
      currentWorkspaceDisplayName: workspace.displayName,
      currentWorkspaceRole: workspace.role,
      wsLevel: workspace.wsLevel,
      switchWorkspace,
      clearWorkspace,
      updateWorkspaceName,
      isReady,
    }),
    [workspace, switchWorkspace, clearWorkspace, updateWorkspaceName, isReady],
  );

  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}

WorkspaceProvider.propTypes = {
  children: PropTypes.node,
};
