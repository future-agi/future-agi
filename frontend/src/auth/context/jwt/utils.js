import { intervalToDuration } from "date-fns";

import axios, { endpoints } from "src/utils/axios";
import { getRecaptchaToken } from "src/utils/recaptchaService";

// ----------------------------------------------------------------------

function jwtDecode(token) {
  const base64Url = token.split(".")[1];
  const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
  const jsonPayload = decodeURIComponent(
    window
      .atob(base64)
      .split("")
      .map((c) => `%${`00${c.charCodeAt(0).toString(16)}`.slice(-2)}`)
      .join(""),
  );

  return JSON.parse(jsonPayload);
}

// ----------------------------------------------------------------------

export const isValidToken = (accessToken) => {
  if (!accessToken) {
    return false;
  }

  const decoded = jwtDecode(accessToken);

  const currentTime = Date.now() / 1000;

  return decoded.exp > currentTime;
};

// ----------------------------------------------------------------------

export const setSession = (accessToken, organizationId) => {
  if (accessToken) {
    localStorage.setItem("accessToken", accessToken);

    axios.defaults.headers.common.Authorization = `Bearer ${accessToken}`;
    // Organization ID: set from login response initially, then managed by
    // OrganizationProvider via sessionStorage (per-tab isolation).
    // On login, the provider will overwrite this with its sessionStorage value.
    if (organizationId)
      axios.defaults.headers.common["X-Organization-Id"] = organizationId;
    // NOTE: X-Workspace-Id is now managed by WorkspaceProvider via sessionStorage.
    // Do NOT set it here — this prevents cross-tab leakage.
  } else {
    localStorage.removeItem("accessToken");
    localStorage.removeItem("sosMode");

    delete axios.defaults.headers.common.Authorization;
    delete axios.defaults.headers.common["X-Workspace-Id"];
    delete axios.defaults.headers.common["X-Organization-Id"];
  }
};

export const setRememberMe = (value) => {
  localStorage.setItem("rememberMe", value);
};

export const getRememberMe = () => {
  return JSON.parse(localStorage.getItem("rememberMe"));
};

export const setRefreshToken = (value) => {
  localStorage.setItem("refreshToken", value);
};

export const getAccessToken = () => {
  return localStorage.getItem("accessToken");
};

export const getRefreshToken = () => {
  return localStorage.getItem("refreshToken");
};

export const clearTokens = () => {
  localStorage.removeItem("accessToken");
  localStorage.removeItem("refreshToken");
  localStorage.removeItem("rememberMe");
  localStorage.removeItem("sosMode");
};

export const refreshTokenRequest = async () => {
  const refreshToken = getRefreshToken();
  const recaptchaToken = await getRecaptchaToken("refresh");

  if (!refreshToken) throw new Error("No refresh token");

  return axios.post(
    endpoints.auth.refreshToken,
    {
      refresh: refreshToken,
      recaptcha_response: recaptchaToken,
    },
    {
      headers: {
        "Content-Type": "application/json",
        Authorization: "",
      },
    },
  );
};

let isRefreshing = false;
let failedQueue = [];

export const getIsRefreshing = () => isRefreshing;
export const setIsRefreshing = (value) => {
  isRefreshing = value;
};

export const addToQueue = (promise) => failedQueue.push(promise);

export const processQueue = (error, token = null) => {
  failedQueue.forEach((prom) => {
    if (error) prom.reject(error);
    else prom.resolve(token);
  });
  failedQueue = [];
};

export function getDateAge(date) {
  try {
    const start = new Date(date);
    if (isNaN(start.getTime())) {
      return null; // invalid date
    }

    const end = new Date();

    const { years, months, days, hours, minutes } = intervalToDuration({
      start,
      end,
    });

    if (years) {
      // If more than a year, show "X yr Y mon"
      return `${years} yr${years > 1 ? "s" : ""}${
        months ? ` ${months} mon${months > 1 ? "s" : ""}` : ""
      }`;
    }

    if (months) {
      return `${months} mon${months > 1 ? "s" : ""}${
        days ? ` ${days} day${days > 1 ? "s" : ""}` : ""
      }`;
    }

    if (days) {
      return `${days} day${days > 1 ? "s" : ""}${
        hours ? ` ${hours} hr${hours > 1 ? "s" : ""}` : ""
      }`;
    }

    if (hours) {
      return `${hours} hr${hours > 1 ? "s" : ""}${
        minutes ? ` ${minutes} min` : ""
      }`;
    }

    if (minutes) {
      return `${minutes} min`;
    }

    return "Just now";
  } catch (err) {
    return null;
  }
}
