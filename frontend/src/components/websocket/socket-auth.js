import {
  getAccessToken,
  getRefreshToken,
  getRememberMe,
} from "src/auth/context/jwt/utils";
import axiosInstance, { endpoints } from "src/utils/axios";
import logger from "src/utils/logger";

// The server accepts the handshake and then closes with this code when it
// rejects the token, so "open" alone does not mean the socket is usable.
export const AUTH_REJECTED_CLOSE_CODE = 4003;

// Stop after this many consecutive token rejections; a new token or a page
// reload starts over.
export const MAX_AUTH_FAILURES = 3;

// The REST client refreshes the access token in storage without updating the
// auth context, so the stored token is the current one.
export const getCurrentAccessToken = (fallback) => getAccessToken() || fallback;

/**
 * After the server rejects a socket token, make sure the next attempt can
 * carry a current one: reuse a token the REST client already refreshed,
 * otherwise let one authenticated request run the REST client's refresh.
 *
 * Resolves to false when there is nothing to refresh with (no remember-me or
 * no refresh token), so the caller should stop reconnecting; true otherwise.
 */
export const refreshRejectedSocketToken = async (rejectedToken, fallback) => {
  if (getCurrentAccessToken(fallback) !== rejectedToken) return true;
  if (!getRememberMe() || !getRefreshToken()) return false;
  try {
    await axiosInstance.get(endpoints.auth.me);
  } catch (e) {
    logger.debug("WebSocket token refresh request failed", e);
  }
  return true;
};
