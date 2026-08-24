import React from "react";
import PropTypes from "prop-types";
import { useMemo, useEffect, useReducer, useCallback } from "react";

import axios, { endpoints } from "src/utils/axios";
import axiosPackage from "axios";
import { HOST_API } from "src/config-global";

import { AuthContext } from "./auth-context";
import {
  clearTokens,
  setRefreshToken,
  setRememberMe,
  setSession,
} from "./utils";
import { identifyUser, resetUser } from "src/utils/Mixpanel";
import { identifyPostHogUser, resetPostHogUser } from "src/utils/PostHog";
import { useQueryClient } from "@tanstack/react-query";
import { setUser } from "@sentry/react";
import logger from "src/utils/logger";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import {
  SS_KEY_ORG_DISPLAY_NAME,
  SS_KEY_ORG_ID,
  SS_KEY_ORG_LEVEL,
  SS_KEY_ORG_NAME,
  SS_KEY_ORG_ROLE,
  SS_KEY_USER_ID,
} from "src/utils/sessionKeys";

// Helper to decode JWT and extract user ID (without verification)
function decodeTokenUserId(token) {
  if (!token) return null;
  try {
    const base64Url = token.split(".")[1];
    const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
    const jsonPayload = decodeURIComponent(
      window
        .atob(base64)
        .split("")
        .map((c) => `%${`00${c.charCodeAt(0).toString(16)}`.slice(-2)}`)
        .join(""),
    );
    const payload = JSON.parse(jsonPayload);
    return payload.user_id || payload.sub || null;
  } catch {
    return null;
  }
}

// Pin the backend-resolved org before `user` reaches the tree, so no consumer
// can fire an org-scoped request before the org is known.
export function pinResolvedOrganization(user) {
  const org = user?.organization;
  const orgId = org?.id || sessionStorage.getItem(SS_KEY_ORG_ID);
  if (!orgId) return null;

  const previousOrgId = sessionStorage.getItem(SS_KEY_ORG_ID);
  sessionStorage.setItem(SS_KEY_ORG_ID, orgId);
  if (org?.id) {
    const orgChanged = previousOrgId !== org.id;
    const put = (key, value) => {
      if (value != null && value !== "")
        sessionStorage.setItem(key, String(value));
      else if (orgChanged) sessionStorage.removeItem(key);
    };
    put(SS_KEY_ORG_NAME, org.name);
    put(SS_KEY_ORG_DISPLAY_NAME, org.display_name);
    put(SS_KEY_ORG_ROLE, user?.organization_role);
    put(SS_KEY_ORG_LEVEL, user?.org_level);
  }
  return orgId;
}

const initialState = {
  user: null,
  loading: true,
};

const reducer = (state, action) => {
  switch (action.type) {
    case "INITIAL":
      return { ...state, loading: false, user: action.payload.user };
    case "LOGIN":
    case "REGISTER":
      return { ...state, user: action.payload.user };
    case "LOGOUT":
      return { ...state, user: null };
    case "UPDATE":
      return { ...state, user: { ...state.user, ...action.payload.user } };
    default:
      return state;
  }
};

// ----------------------------------------------------------------------

const STORAGE_KEY = "accessToken";

export function AuthProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const queryClient = useQueryClient();

  const initialize = useCallback(async () => {
    try {
      const accessToken = localStorage.getItem(STORAGE_KEY);

      if (accessToken) {
        // Without this the backend answers for whichever org was last switched
        // to in any tab.
        const storedOrgId = sessionStorage.getItem(SS_KEY_ORG_ID);
        const response = await axios.get(endpoints.auth.me, {
          headers: {
            Authorization: `Bearer ${accessToken}`,
            ...(storedOrgId ? { "X-Organization-Id": storedOrgId } : {}),
          },
        });

        const user = response.data;

        // Org-less user (removed from org) — still authenticated but flagged
        if (user?.requires_org_setup) {
          setSession(accessToken, null);
          dispatch({
            type: "INITIAL",
            payload: {
              user: { ...user, requires_org_setup: true, accessToken },
            },
          });
          return;
        }

        // Store user ID in sessionStorage for cross-tab user detection
        sessionStorage.setItem(SS_KEY_USER_ID, user?.id);

        setSession(accessToken, pinResolvedOrganization(user));
        if (user?.remember_me) {
          setRememberMe(user.remember_me);
        }
        identifyUser(user);
        identifyPostHogUser(user);
        setUser({
          id: user?.id,
          email: user?.email,
        });

        dispatch({
          type: "INITIAL",
          payload: {
            user: {
              ...user,
              accessToken,
            },
          },
        });
      } else {
        dispatch({
          type: "INITIAL",
          payload: {
            user: null,
          },
        });
      }
    } catch (error) {
      // Only clear session for authentication errors (401, 403) or specific user_not_found error
      // Don't clear session for network errors (no response) or server errors (5xx)
      if (
        error?.code === "user_not_found" ||
        error?.statusCode === 401 ||
        (error?.statusCode === 403 &&
          error?.config?.url?.includes("/accounts/user-info/"))
      ) {
        setSession(null);
      }

      dispatch({
        type: "INITIAL",
        payload: {
          user: null,
        },
      });
    }
  }, []);

  useEffect(() => {
    initialize();
  }, [initialize]);

  // Cross-tab user change detection
  // When another tab logs in as a different user, force logout this tab
  useEffect(() => {
    const handleStorageChange = (event) => {
      // Only handle accessToken changes from other tabs
      if (event.key !== STORAGE_KEY) return;

      const newToken = event.newValue;
      const currentUserId = sessionStorage.getItem(SS_KEY_USER_ID);

      // Token was cleared (logout in another tab)
      if (!newToken) {
        logger.info("Token cleared in another tab, logging out this tab");
        queryClient.clear();
        sessionStorage.removeItem(SS_KEY_USER_ID);
        dispatch({ type: "LOGOUT" });
        window.location.href = "/auth/jwt/login";
        return;
      }

      // Token changed - check if it's a different user
      const newUserId = decodeTokenUserId(newToken);

      if (currentUserId && newUserId && currentUserId !== newUserId) {
        // Different user logged in from another tab
        logger.warn(
          "Different user detected in another tab. Forcing logout to prevent data leakage.",
        );
        queryClient.clear();
        sessionStorage.removeItem(SS_KEY_USER_ID);
        sessionStorage.setItem(
          "auth_error",
          "Another user logged in from a different tab. Please log in again.",
        );
        dispatch({ type: "LOGOUT" });
        window.location.href = "/auth/jwt/login";
      }
    };

    window.addEventListener("storage", handleStorageChange);

    return () => {
      window.removeEventListener("storage", handleStorageChange);
    };
  }, [queryClient]);

  // LOGIN
  const login = useCallback(async (response) => {
    if (response.status !== 200) return;
    const { access: accessToken, refresh: refreshToken } = response.data;
    const userResponse = await axiosPackage.get(
      `${HOST_API}${endpoints.auth.me}`,
      {
        headers: {
          Authorization: `Bearer ${accessToken}`,
        },
      },
    );

    const user = userResponse.data;

    setSession(accessToken, pinResolvedOrganization(user));
    if (refreshToken) {
      setRefreshToken(refreshToken);
    }

    // Store user ID in sessionStorage for cross-tab user detection
    sessionStorage.setItem(SS_KEY_USER_ID, user.id);

    identifyUser(user);
    identifyPostHogUser(user);
    setUser({
      id: user.id,
      email: user.email,
    });
    if (user?.remember_me) {
      setRememberMe(user.remember_me);
    }

    dispatch({
      type: "LOGIN",
      payload: {
        user: {
          ...user,
          accessToken,
        },
      },
    });
  }, []);

  const register = useCallback(async (payload) => {
    try {
      const data = { ...payload };

      const response = await axios.post(endpoints.auth.register, data);

      return response.data; // Return response so calling function can use it
    } catch (error) {
      if (
        (error?.statusCode >= 400 && error?.statusCode < 500) ||
        error?.name === "NotAllowedError"
      ) {
        logger.info("Registration Error (expected)", error);
      } else {
        logger.error("Registration Error:", error);
      }
      throw error; // Ensure errors are caught by caller
    }
  }, []);

  const awsRegister = useCallback(async (payload) => {
    try {
      const data = { ...payload };

      const response = await axios.post(endpoints.auth.awsSignUp, data);

      return response.data; // Return response so calling function can use it
    } catch (error) {
      if (
        (error?.statusCode >= 400 && error?.statusCode < 500) ||
        error?.name === "NotAllowedError"
      ) {
        logger.info("Registration Error (expected)", error);
      } else {
        logger.error("Registration Error:", error);
      }
      throw error; // Ensure errors are caught by caller
    }
  }, []);

  // LOGOUT
  const logout = useCallback(async () => {
    try {
      const accessToken = localStorage.getItem(STORAGE_KEY);
      setSession(null);
      clearTokens();
      resetUser();
      resetPostHogUser();
      sessionStorage.removeItem("2fa_challenge");
      sessionStorage.removeItem(SS_KEY_USER_ID);
      localStorage.removeItem("initial-render"); // Clear flag so next login triggers redirect logic
      useFalconStore.getState().resetAll();
      dispatch({
        type: "LOGOUT",
      });
      await axios.post(
        endpoints.auth.logout,
        {},
        {
          headers: {
            Authorization: `Bearer ${accessToken}`,
          },
        },
      );
      queryClient.clear();
    } catch (error) {
      logger.error("Logout Error:", error);
      throw error; // Ensure errors are caught by caller
    }
  }, [queryClient]);

  const updateUserData = useCallback((userData) => {
    try {
      dispatch({
        type: "UPDATE",
        payload: {
          user: { ...userData },
        },
      });
    } catch (error) {
      logger.error("Update user data Error:", error);
      throw error; // Ensure errors are caught by caller
    }
  }, []);

  // ----------------------------------------------------------------------

  const { user, loading } = state;

  const checkAuthenticated = user ? "authenticated" : "unauthenticated";

  const status = loading ? "loading" : checkAuthenticated;

  const memoizedValue = useMemo(
    () => ({
      user: user,
      method: "jwt",
      loading: status === "loading",
      authenticated: status === "authenticated",
      unauthenticated: status === "unauthenticated",
      role: user?.default_workspace_role || user?.organization_role,
      orgLevel: user?.org_level ?? null,
      wsLevel: user?.ws_level ?? null,
      effectiveLevel: user?.effective_level ?? null,
      //
      login,
      register,
      logout,
      initialize,
      updateUserData,
      awsRegister,
    }),
    [
      user,
      status,
      login,
      register,
      logout,
      initialize,
      updateUserData,
      awsRegister,
    ],
  );

  return (
    <AuthContext.Provider value={memoizedValue}>
      {children}
    </AuthContext.Provider>
  );
}

AuthProvider.propTypes = {
  children: PropTypes.node,
};
