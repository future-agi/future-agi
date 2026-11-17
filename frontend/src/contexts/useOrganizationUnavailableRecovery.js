import { useEffect } from "react";
import axiosPackage from "axios";
import axios, { endpoints } from "src/utils/axios";
import { HOST_API } from "src/config-global";
import { enqueueSnackbar } from "src/components/snackbar";
import {
  SS_KEY_ORG_DISPLAY_NAME,
  SS_KEY_ORG_ID,
  SS_KEY_ORG_LEVEL,
  SS_KEY_ORG_NAME,
  SS_KEY_ORG_ROLE,
  SS_KEY_WORKSPACE_DISPLAY_NAME,
  SS_KEY_WORKSPACE_ID,
  SS_KEY_WORKSPACE_NAME,
  SS_KEY_WORKSPACE_ORG_ID,
  SS_KEY_WORKSPACE_ROLE,
  SS_KEY_WS_LEVEL,
} from "src/utils/sessionKeys";

const organizationKeys = [
  SS_KEY_ORG_ID, SS_KEY_ORG_NAME, SS_KEY_ORG_DISPLAY_NAME,
  SS_KEY_ORG_ROLE, SS_KEY_ORG_LEVEL,
];
const workspaceKeys = [
  SS_KEY_WORKSPACE_ID, SS_KEY_WORKSPACE_NAME, SS_KEY_WORKSPACE_DISPLAY_NAME,
  SS_KEY_WORKSPACE_ORG_ID, SS_KEY_WORKSPACE_ROLE, SS_KEY_WS_LEVEL,
];
const requestOrganization = (config) =>
  config?.headers?.get?.("X-Organization-Id") ??
  config?.headers?.["X-Organization-Id"];
const currentOrganization = () =>
  axios.defaults.headers.common["X-Organization-Id"] ||
  sessionStorage.getItem(SS_KEY_ORG_ID) || null;

export function isOrganizationUnavailable(error) {
  let data = error?.response?.data;
  // An adapter sees the wire body before Axios transforms JSON responses.
  if (typeof data === "string") {
    try { data = JSON.parse(data); } catch { return false; }
  }
  return error?.response?.status === 403 &&
    data?.code === "organization_unavailable";
}

function clearTenantSelection() {
  [...organizationKeys, ...workspaceKeys].forEach((key) => sessionStorage.removeItem(key));
  delete axios.defaults.headers.common["X-Organization-Id"];
  delete axios.defaults.headers.common["X-Workspace-Id"];
}

function selectOrganization(org) {
  if (!org) return;
  const values = [org.id, org.name, org.display_name || org.name, org.role, org.level];
  organizationKeys.forEach((key, index) => {
    if (values[index] != null) sessionStorage.setItem(key, String(values[index]));
  });
  axios.defaults.headers.common["X-Organization-Id"] = org.id;
}

// Bootstrap already uses the raw sign-in client. It must see the typed 403
// before the app's response interceptor treats user-info failures as logout.
function read(accessToken, url, organizationId = null) {
  return axiosPackage.get(`${HOST_API}${url}`, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "X-Organization-Id": organizationId,
      "X-Workspace-Id": null,
    },
  });
}

// One coordinator belongs to one AuthProvider, not to a tenant subtree that
// remounts when the organization changes. Block the stale id and a successor
// whose bootstrap is pending or failed, without blocking later membership loss.
export function createOrganizationRecovery() {
  const attempted = new Set();
  let pending = null;
  let pendingId = null;
  let generation = 0;
  let pendingNotice = null;

  const reset = () => {
    generation += 1;
    attempted.clear();
    pending = null;
    pendingId = null;
    pendingNotice = null;
  };

  // The tenant gate unmounts the snackbar host during recovery. Keep the
  // notice with this AuthProvider-owned coordinator until that tree commits.
  const notifyRecoveredOrganization = () => {
    if (!pendingNotice) return;
    const message = pendingNotice;
    pendingNotice = null;
    enqueueSnackbar(message, { variant: "info", preventDuplicate: true });
  };

  const recover = (error, accessToken, onStart) => {
    if (!isOrganizationUnavailable(error)) return Promise.resolve(null);
    const staleId = requestOrganization(error.config);
    if (pending && pendingId === staleId) return pending;
    if (!staleId || staleId !== currentOrganization() || attempted.has(staleId)) {
      return Promise.resolve(null);
    }
    attempted.add(staleId);
    pendingId = staleId;
    const run = generation;
    const tokenBefore = localStorage.getItem("accessToken");
    let expectedId = null;
    const isCurrent = () => run === generation &&
      localStorage.getItem("accessToken") === tokenBefore &&
      currentOrganization() === expectedId;
    onStart?.();
    clearTenantSelection();

    const recovery = (async () => {
      const response = await read(accessToken, endpoints.organizations.list);
      if (!isCurrent()) return null;
      const candidate = response?.data?.result?.organizations || response?.data;
      if (!Array.isArray(candidate)) throw new Error("Could not load accessible organizations.");
      const organizations = candidate.filter((org) => org?.id && org.id !== staleId);
      const selected = organizations.find((org) => org.is_selected) || organizations[0] || null;
      expectedId = selected?.id || null;
      if (selected) attempted.add(selected.id);
      selectOrganization(selected);
      // Exactly one fresh bootstrap. Do not call loadUser here (it recovers).
      const bootstrap = await read(accessToken, endpoints.auth.me, expectedId);
      if (!isCurrent()) return null;
      if (selected) {
        // This successor's loop guard ends only after a successful, current
        // bootstrap. A later independent removal must be able to recover again.
        attempted.delete(selected.id);
        pendingNotice = `You no longer have access to that organization. Switched to ${selected.display_name || selected.name}.`;
      }
      return bootstrap;
    })();
    pending = recovery.finally(() => {
      if (run === generation) {
        pending = null;
        pendingId = null;
      }
    });
    return pending;
  };

  const loadUser = async (accessToken, onStart, signingIn = false) => {
    const organizationId = sessionStorage.getItem(SS_KEY_ORG_ID);
    if (signingIn) {
      try {
        return await read(accessToken, endpoints.auth.me, organizationId);
      } catch (error) {
        const recovered = await recover(error, accessToken, onStart);
        if (recovered) return recovered;
        throw error;
      }
    }

    // Preserve the app client's existing 401/refresh behavior at page load.
    // Only the typed organization refusal is intercepted before its otherwise
    // unconditional user-info 403 logout. Failed recovery is rethrown below
    // with its original response, without passing it through that logout path.
    let recoveryError;
    const config = {
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "X-Organization-Id": organizationId,
        "X-Workspace-Id": null,
      },
      organizationRecoveryWrapped: true,
    };
    if (axios.defaults.adapter) {
      const adapter = axiosPackage.getAdapter(axios.defaults.adapter);
      config.adapter = async (request) => {
        try {
          return await adapter(request);
        } catch (error) {
          if (!isOrganizationUnavailable(error)) throw error;
          try {
            const authorization = request.headers?.get?.("Authorization") ?? request.headers?.Authorization;
            const token = authorization?.startsWith("Bearer ") ? authorization.slice(7) : accessToken;
            const recovered = await recover(error, token, onStart);
            if (recovered) return { ...recovered, config: request };
            recoveryError = error;
          } catch (reason) {
            recoveryError = reason;
          }
          throw new Error("Organization recovery failed.");
        }
      };
    }
    try {
      return await axios.get(endpoints.auth.me, config);
    } catch (error) {
      throw recoveryError || error;
    }
  };
  return { loadUser, recover, reset, notifyRecoveredOrganization };
}

export function useOrganizationUnavailableRecovery({ authenticated, recoverOrganization }) {
  useEffect(() => {
    if (!authenticated || !recoverOrganization) return undefined;
    let active = true;
    // Use the public adapter API rather than modifying the shared interceptor
    // or its private handler array. This retains the failed request's scope
    // before the app's normalizer deliberately strips credentials/config.
    const interceptor = axios.interceptors.request.use((config) => {
      if (config.organizationRecoveryWrapped) return config;
      const adapter = axiosPackage.getAdapter(config.adapter || axios.defaults.adapter);
      config.organizationRecoveryWrapped = true;
      config.adapter = async (request) => {
        try {
          return await adapter(request);
        } catch (error) {
          if (active && isOrganizationUnavailable(error)) {
            let recovered;
            try {
              recovered = await recoverOrganization(error);
            } catch {
              // The original operation still fails; neither a failed read nor
              // a changed membership authorizes replaying a user's mutation.
            }
            if (recovered && request.method === "get" && request.url === endpoints.auth.me) {
              return { ...recovered, config: request };
            }
          }
          throw error;
        }
      };
      return config;
    });
    return () => {
      active = false;
      axios.interceptors.request.eject(interceptor);
    };
  }, [authenticated, recoverOrganization]);
}
