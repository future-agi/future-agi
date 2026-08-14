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
import { enqueueSnackbar } from "src/components/snackbar";
import logger from "src/utils/logger";

// --- sessionStorage helpers ---------------------------------------------------

const SS_KEY_WORKSPACE_ID = "workspaceId";
const SS_KEY_WORKSPACE_NAME = "workspaceName";
const SS_KEY_WORKSPACE_DISPLAY_NAME = "workspaceDisplayName";
const SS_KEY_WORKSPACE_ROLE = "workspaceRole";
const SS_KEY_WS_LEVEL = "wsLevel";

function readSessionWorkspace() {
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
    };
  } catch {
    return {
      id: null,
      name: null,
      displayName: null,
      role: null,
      wsLevel: null,
    };
  }
}

function writeSessionWorkspace({ id, name, displayName, role, wsLevel }) {
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

  const [workspace, setWorkspace] = useState(() => {
    // On mount, try sessionStorage first (survives refresh, per-tab)
    const stored = readSessionWorkspace();
    if (stored.id) {
      setWorkspaceHeader(stored.id);
      return stored;
    }
    return {
      id: null,
      name: null,
      displayName: null,
      role: null,
      wsLevel: null,
    };
  });

  const [isReady, setIsReady] = useState(() => {
    // Ready immediately if sessionStorage had a workspace
    return !!readSessionWorkspace().id;
  });

  // When user data arrives (login / refresh), seed workspace if not already set
  useEffect(() => {
    if (!authenticated || !user) return;

    // If sessionStorage already has a workspace, trust it (per-tab persistence)
    const stored = readSessionWorkspace();
    if (stored.id) {
      // Sync axios header (might have been lost after token refresh)
      setWorkspaceHeader(stored.id);
      const updated = { ...stored };
      // Only update role/name/wsLevel from user-info when the workspace IDs match.
      // If they differ (e.g., another tab switched), the user-info response reflects
      // the OTHER workspace's data — we must not overwrite this tab's stored values.
      const userDefaultWsId = user.default_workspace_id;
      const userDefaultWsRole = user.default_workspace_role;
      const userWsLevel = user.ws_level;
      const userDefaultWsName = user.default_workspace_name;
      const userDefaultWsDisplayName = user.default_workspace_display_name;
      if (stored.id === userDefaultWsId) {
        updated.role = userDefaultWsRole || stored.role;
        updated.wsLevel = userWsLevel != null ? userWsLevel : stored.wsLevel;
        updated.name = userDefaultWsName || stored.name;
        updated.displayName = userDefaultWsDisplayName || stored.displayName;
      }
      setWorkspace(updated);
      writeSessionWorkspace(updated);
      setIsReady(true);
      return;
    }

    // No sessionStorage → seed from user-info response (new tab / fresh open)
    const seedDefaultWsId = user.default_workspace_id;
    if (seedDefaultWsId) {
      const seedWsLevel = user.ws_level;
      const initial = {
        id: seedDefaultWsId,
        name: user.default_workspace_name ?? null,
        displayName: user.default_workspace_display_name ?? null,
        role: user.default_workspace_role ?? null,
        wsLevel: seedWsLevel != null ? seedWsLevel : null,
      };
      setWorkspace(initial);
      writeSessionWorkspace(initial);
      setWorkspaceHeader(initial.id);
      setIsReady(true);
    }
  }, [authenticated, user]);

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
    [workspace.id],
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
    setWorkspace({
      id: null,
      name: null,
      displayName: null,
      role: null,
      wsLevel: null,
    });
    setIsReady(false);
  }, []);

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
