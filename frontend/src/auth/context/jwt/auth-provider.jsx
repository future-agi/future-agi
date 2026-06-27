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

// Session storage key for per-tab user tracking
const SESSION_USER_ID_KEY = "currentUserId";

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
        const response = await axios.get(endpoints.auth.me, {
          headers: {
            Authorization: `Bearer ${accessToken}`,
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
        sessionStorage.setItem(SESSION_USER_ID_KEY, user?.id);

        setSession(accessToken, sessionStorage.getItem("organizationId"));
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
      const currentUserId = sessionStorage.getItem(SESSION_USER_ID_KEY);

      // Token was cleared (logout in another tab)
      if (!newToken) {
        logger.info("Token cleared in another tab, logging out this tab");
        queryClient.clear();
        sessionStorage.removeItem(SESSION_USER_ID_KEY);
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
        sessionStorage.removeItem(SESSION_USER_ID_KEY);
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

    setSession(accessToken, sessionStorage.getItem("organizationId"));
    if (refreshToken) {
      setRefreshToken(refreshToken);
    }

    const user = userResponse.data;

    // Store user ID in sessionStorage for cross-tab user detection
    sessionStorage.setItem(SESSION_USER_ID_KEY, user.id);

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
      sessionStorage.removeItem(SESSION_USER_ID_KEY);
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
