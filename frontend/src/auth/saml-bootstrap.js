import axios from "src/utils/axios";
import {
  SS_KEY_ORG_DISPLAY_NAME,
  SS_KEY_ORG_ID,
  SS_KEY_ORG_LEVEL,
  SS_KEY_ORG_NAME,
  SS_KEY_ORG_ROLE,
  SS_KEY_USER_ID,
  SS_KEY_WORKSPACE_DISPLAY_NAME,
  SS_KEY_WORKSPACE_ID,
  SS_KEY_WORKSPACE_NAME,
  SS_KEY_WORKSPACE_ORG_ID,
  SS_KEY_WORKSPACE_ROLE,
  SS_KEY_WS_LEVEL,
} from "src/utils/sessionKeys";

const SAML_NEXT_KEY = "fai_saml_next";
export const SAML_AUTH_GENERATION_KEY = "fai_auth_generation";

export const SESSION_KEYS_TO_CLEAR = [
  SS_KEY_ORG_ID,
  SS_KEY_ORG_NAME,
  SS_KEY_ORG_DISPLAY_NAME,
  SS_KEY_ORG_ROLE,
  SS_KEY_ORG_LEVEL,
  SS_KEY_USER_ID,
  SS_KEY_WORKSPACE_ID,
  SS_KEY_WORKSPACE_NAME,
  SS_KEY_WORKSPACE_DISPLAY_NAME,
  SS_KEY_WORKSPACE_ROLE,
  SS_KEY_WS_LEVEL,
  SS_KEY_WORKSPACE_ORG_ID,
];

export function isSafeSamlNext(next) {
  return (
    typeof next === "string" &&
    next.startsWith("/") &&
    !next.startsWith("//") &&
    !next.includes("\\") &&
    !next.includes("://") &&
    next.length <= 512
  );
}

export function bootstrapSamlSession() {
  const query = new URLSearchParams(window.location.search);
  const token = query.get("sso_token");
  if (!token || query.get("auth") !== "saml") return false;

  const generation = crypto.randomUUID();
  sessionStorage.setItem(SAML_AUTH_GENERATION_KEY, generation);
  localStorage.setItem(SAML_AUTH_GENERATION_KEY, generation);

  localStorage.setItem("accessToken", token);
  localStorage.removeItem("refreshToken");
  localStorage.removeItem("rememberMe");
  localStorage.removeItem("sosMode");
  localStorage.removeItem("redirectUrl");

  SESSION_KEYS_TO_CLEAR.forEach((key) => sessionStorage.removeItem(key));
  sessionStorage.removeItem(SAML_NEXT_KEY);

  delete axios.defaults.headers.common["X-Organization-Id"];
  delete axios.defaults.headers.common["X-Workspace-Id"];
  axios.defaults.headers.common.Authorization = `Bearer ${token}`;

  const next = query.get("next");
  if (isSafeSamlNext(next)) {
    sessionStorage.setItem(SAML_NEXT_KEY, next);
  }

  history.replaceState(null, "", location.pathname);
  return true;
}
