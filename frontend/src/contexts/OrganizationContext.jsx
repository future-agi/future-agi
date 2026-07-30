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

const SS_KEY_ORG_ID = "organizationId";
const SS_KEY_ORG_NAME = "organizationName";
const SS_KEY_ORG_DISPLAY_NAME = "organizationDisplayName";
const SS_KEY_ORG_ROLE = "organizationRole";
const SS_KEY_ORG_LEVEL = "orgLevel";

function readSessionOrganization() {
  try {
    return {
      id: sessionStorage.getItem(SS_KEY_ORG_ID) || null,
      name: sessionStorage.getItem(SS_KEY_ORG_NAME) || null,
      displayName: sessionStorage.getItem(SS_KEY_ORG_DISPLAY_NAME) || null,
      role: sessionStorage.getItem(SS_KEY_ORG_ROLE) || null,
      orgLevel: (() => {
        const raw = sessionStorage.getItem(SS_KEY_ORG_LEVEL);
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
      orgLevel: null,
    };
  }
}

function writeSessionOrganization({ id, name, displayName, role, orgLevel }) {
  try {
    if (id) sessionStorage.setItem(SS_KEY_ORG_ID, id);
    else sessionStorage.removeItem(SS_KEY_ORG_ID);

    if (name) sessionStorage.setItem(SS_KEY_ORG_NAME, name);
    else sessionStorage.removeItem(SS_KEY_ORG_NAME);

    if (displayName)
      sessionStorage.setItem(SS_KEY_ORG_DISPLAY_NAME, displayName);
    else sessionStorage.removeItem(SS_KEY_ORG_DISPLAY_NAME);

    if (role) sessionStorage.setItem(SS_KEY_ORG_ROLE, role);
    else sessionStorage.removeItem(SS_KEY_ORG_ROLE);

    if (orgLevel != null) sessionStorage.setItem(SS_KEY_ORG_LEVEL, orgLevel);
    else sessionStorage.removeItem(SS_KEY_ORG_LEVEL);
  } catch {
    // sessionStorage may be unavailable in some contexts (e.g. SSR)
  }
}

function clearSessionOrganization() {
  try {
    sessionStorage.removeItem(SS_KEY_ORG_ID);
    sessionStorage.removeItem(SS_KEY_ORG_NAME);
    sessionStorage.removeItem(SS_KEY_ORG_DISPLAY_NAME);
    sessionStorage.removeItem(SS_KEY_ORG_ROLE);
    sessionStorage.removeItem(SS_KEY_ORG_LEVEL);
  } catch {
    // noop
  }
}

// --- Axios header sync -------------------------------------------------------

function setOrganizationHeader(organizationId) {
  if (organizationId) {
    axios.defaults.headers.common["X-Organization-Id"] = organizationId;
  } else {
    delete axios.defaults.headers.common["X-Organization-Id"];
  }
}

// --- Context -----------------------------------------------------------------

const OrganizationContext = createContext({
  currentOrganizationId: null,
  currentOrganizationName: null,
  currentOrganizationDisplayName: null,
  currentOrganizationRole: null,
  orgLevel: null,
  switchOrganization: async () => {},
  clearOrganization: () => {},
  isReady: false,
});

export function useOrganization() {
  return useContext(OrganizationContext);
}

// --- Provider ----------------------------------------------------------------

export function OrganizationProvider({ children }) {
  const { user, authenticated, loading } = useAuthContext();

  const [organization, setOrganization] = useState(() => {
    // On mount, try sessionStorage first (survives refresh, per-tab)
    const stored = readSessionOrganization();
    if (stored.id) {
      setOrganizationHeader(stored.id);
      return stored;
    }
    return {
      id: null,
      name: null,
      displayName: null,
      role: null,
      orgLevel: null,
    };
  });

  const [isReady, setIsReady] = useState(() => {
    // Ready immediately if sessionStorage had an organization
    return !!readSessionOrganization().id;
  });

  // When user data arrives (login / refresh), seed organization if not already set
  useEffect(() => {
    if (!authenticated || !user) return;

    // If sessionStorage already has an org, trust it (per-tab persistence)
    const stored = readSessionOrganization();
    if (stored.id) {
      // Sync axios header (might have been lost after token refresh)
      setOrganizationHeader(stored.id);
      const updated = { ...stored };
      // Always sync role/level from latest user-info response
      updated.role = user.organization_role || stored.role;
      updated.orgLevel =
        user.org_level != null ? user.org_level : stored.orgLevel;
      setOrganization(updated);
      writeSessionOrganization(updated);
      setIsReady(true);
      return;
    }

    // No sessionStorage → fetch org list from membership and seed first org
    const seedFromMembership = async () => {
      try {
        const response = await axios.get(endpoints.organizations.list);
        const orgs =
          response?.data?.result?.organizations || response?.data || [];
        if (orgs.length > 0) {
          const first = orgs[0];
          const initial = {
            id: first.id,
            name: first.name || null,
            displayName: first.display_name || null,
            role: user.organization_role || null,
            orgLevel: user.org_level != null ? user.org_level : null,
          };
          setOrganization(initial);
          writeSessionOrganization(initial);
          setOrganizationHeader(initial.id);
        }
        setIsReady(true);
      } catch (error) {
        logger.error("Failed to seed organization from membership:", error);
        setIsReady(true);
      }
    };
    seedFromMembership();
  }, [authenticated, user]);

  // Switch organization — called from UI
  const switchOrganization = useCallback(async (newOrganizationId) => {
    try {
      const response = await axios.post(endpoints.organizations.switch, {
        organization_id: newOrganizationId,
      });

      const result = response?.data?.result || response?.data || {};
      const orgData = result.organization || {};
      const wsData = result.workspace || {};

      const newOrg = {
        id: orgData.id || newOrganizationId,
        name: orgData.name || null,
        displayName: orgData.display_name || orgData.name || null,
        role: result.org_role || null,
        orgLevel: result.org_level != null ? result.org_level : null,
      };

      // 1. Update organization sessionStorage
      writeSessionOrganization(newOrg);

      // 2. Update organization axios header
      setOrganizationHeader(newOrg.id);

      // 3. If a workspace was returned, update workspace sessionStorage too
      if (wsData.id) {
        sessionStorage.setItem("workspaceId", wsData.id);
        sessionStorage.setItem("workspaceName", wsData.name || "");
        sessionStorage.setItem(
          "workspaceDisplayName",
          wsData.display_name || wsData.name || "",
        );
        axios.defaults.headers.common["X-Workspace-Id"] = wsData.id;
      }

      // 4. Hard refresh — clears all React state, query cache, component trees
      window.location.assign("/dashboard/develop");
    } catch (error) {
      logger.error("Organization switch failed:", error);
      enqueueSnackbar(
        error?.response?.data?.result ||
          error?.message ||
          "Failed to switch organization",
        { variant: "error" },
      );
      throw error;
    }
  }, []);

  // Clear organization (logout, etc.)
  const clearOrganization = useCallback(() => {
    clearSessionOrganization();
    setOrganizationHeader(null);
    setOrganization({
      id: null,
      name: null,
      displayName: null,
      role: null,
      orgLevel: null,
    });
    setIsReady(false);
  }, []);

  // Clear on logout (but NOT during initial auth loading — sessionStorage
  // must survive page refreshes for org switching to work correctly)
  useEffect(() => {
    if (!authenticated && !loading) {
      clearOrganization();
    }
  }, [authenticated, loading, clearOrganization]);

  const value = useMemo(
    () => ({
      currentOrganizationId: organization.id,
      currentOrganizationName: organization.name,
      currentOrganizationDisplayName: organization.displayName,
      currentOrganizationRole: organization.role,
      orgLevel: organization.orgLevel,
      switchOrganization,
      clearOrganization,
      isReady,
    }),
    [organization, switchOrganization, clearOrganization, isReady],
  );

  return (
    <OrganizationContext.Provider value={value}>
      {children}
    </OrganizationContext.Provider>
  );
}

OrganizationProvider.propTypes = {
  children: PropTypes.node,
};
