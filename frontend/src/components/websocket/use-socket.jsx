import PropTypes from "prop-types";
import React, {
  createContext,
  useEffect,
  useRef,
  useState,
  useCallback,
  useMemo,
} from "react";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { HOST_API } from "src/config-global";
import logger from "src/utils/logger";
import {
  AUTH_REJECTED_CLOSE_CODE,
  MAX_AUTH_FAILURES,
  getCurrentAccessToken,
  refreshRejectedSocketToken,
} from "./socket-auth";

export const WebSocketContext = createContext();

const RECONNECT_INTERVAL = 5000;
const MAX_RECONNECT_DELAY = 30000; // 30 seconds max delay
const MAX_RECONNECT_ATTEMPTS = 10; // Give up after this many consecutive failures
const HEARTBEAT_INTERVAL = 10000;

// Close codes the server sends when this credential or organization no longer
// authorizes the socket (sockets/consumer.py, sockets/membership.py,
// tfc/middleware/jwt_auth.py). The same credential would be refused again, so
// these never reconnect; a new token or organization opens a fresh socket.
// Any other code, including 1011 when the server could not complete
// authentication (database or cache unavailable), reconnects with backoff.
const AUTHORIZATION_CLOSE_CODES = new Set([4001, 4002]);
// The server accepts the handshake before closing with this code, so the
// open does not count as a successful connection for the retry backoff.
const AUTHENTICATION_UNAVAILABLE_CLOSE_CODE = 1011;
// Once the retry budget is spent during such an outage, keep retrying at this
// slow, fixed interval: the credential was never refused.
const AUTHENTICATION_UNAVAILABLE_RETRY_DELAY = 60000;

export const WebSocketProvider = ({ children }) => {
  const { user, authenticated } = useSafeAuthContext();
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState(null);
  const reconnectAttempts = useRef(0);
  const authenticationUnavailableRef = useRef(false);

  const socketRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const heartbeatIntervalRef = useRef(null);
  const accessToken = useRef(null);
  const organizationId = user?.organization?.id;
  const requiresOrgSetup = Boolean(user?.requires_org_setup);
  const refusedCredentialRef = useRef(null);
  const authFailuresRef = useRef(0);
  const socketIdRef = useRef(0);
  const refreshingRef = useRef(null);
  const createWebSocketConnectionRef = useRef(null);
  const messageListenersRef = useRef(new Set());

  // Store event handlers to properly remove them later
  const eventHandlersRef = useRef({
    open: null,
    message: null,
    error: null,
    close: null,
  });

  const stopHeartbeat = useCallback(() => {
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current);
      heartbeatIntervalRef.current = null;
    }
  }, []);

  const closeConnection = useCallback(() => {
    stopHeartbeat();

    if (socketRef.current) {
      const socket = socketRef.current;

      // Remove all event listeners
      if (eventHandlersRef.current.open) {
        socket.removeEventListener("open", eventHandlersRef.current.open);
      }
      if (eventHandlersRef.current.message) {
        socket.removeEventListener("message", eventHandlersRef.current.message);
      }
      if (eventHandlersRef.current.error) {
        socket.removeEventListener("error", eventHandlersRef.current.error);
      }
      if (eventHandlersRef.current.close) {
        socket.removeEventListener("close", eventHandlersRef.current.close);
      }

      socket.close();
      socketRef.current = null;
      setIsConnected(false);
    }
  }, [stopHeartbeat]);

  const startHeartbeat = useCallback(() => {
    if (authenticated) {
      stopHeartbeat();
      heartbeatIntervalRef.current = setInterval(() => {
        if (socketRef.current?.readyState === WebSocket.OPEN) {
          try {
            socketRef.current.send(JSON.stringify({ type: "ping" }));
          } catch (e) {
            logger.warn("Heartbeat send failed:", e);
            closeConnection();
          }
        } else if (
          socketRef.current?.readyState !== WebSocket.CONNECTING &&
          !reconnectTimeoutRef.current
        ) {
          logger.warn("Heartbeat failed, websocket not open. Reconnecting...");
          attemptReconnect();
        }
      }, HEARTBEAT_INTERVAL);
    }
  }, [authenticated, closeConnection]);

  const attemptReconnect = useCallback(() => {
    if (
      !accessToken.current ||
      authFailuresRef.current >= MAX_AUTH_FAILURES ||
      refusedCredentialRef.current?.token ===
        getCurrentAccessToken(accessToken.current)
    ) {
      return;
    }

    // Don't attempt to reconnect if already trying or if connection is already open
    if (
      refreshingRef.current ||
      reconnectTimeoutRef.current ||
      socketRef.current?.readyState === WebSocket.OPEN ||
      socketRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return;
    }

    const exhausted = reconnectAttempts.current >= MAX_RECONNECT_ATTEMPTS;

    // Reconnection permanently exhausted — stop retrying. The single actionable
    // exception is emitted from errorHandler once this cap is reached; here we
    // just stop the loop so transient drops don't retry forever. While the
    // server cannot authenticate the socket (1011) it keeps retrying slowly.
    if (exhausted && !authenticationUnavailableRef.current) {
      logger.addBreadcrumb({
        category: "websocket",
        level: "warning",
        message: `WebSocket reconnection exhausted after ${reconnectAttempts.current} attempts`,
      });
      return;
    }

    // Use exponential backoff with a maximum delay
    const backoffTime = exhausted
      ? AUTHENTICATION_UNAVAILABLE_RETRY_DELAY
      : Math.min(
          RECONNECT_INTERVAL * Math.pow(2, reconnectAttempts.current),
          MAX_RECONNECT_DELAY,
        );

    logger.debug(
      `Attempting WebSocket reconnect in ${backoffTime}ms (attempt ${reconnectAttempts.current + 1})...`,
    );

    reconnectTimeoutRef.current = setTimeout(() => {
      reconnectAttempts.current = Math.min(
        reconnectAttempts.current + 1,
        MAX_RECONNECT_ATTEMPTS,
      );
      // Use ref to avoid circular dependency
      if (createWebSocketConnectionRef.current) {
        createWebSocketConnectionRef.current();
      }
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }, backoffTime);
  }, []);

  // After the server rejects a token, make sure the next attempt carries a
  // current one before reconnecting.
  const refreshTokenThenReconnect = useCallback(
    async (rejectedToken, socketId) => {
      if (refreshingRef.current === socketId) return;

      refreshingRef.current = socketId;
      let canRetry;
      try {
        canRetry = await refreshRejectedSocketToken(
          rejectedToken,
          accessToken.current,
        );
      } finally {
        if (refreshingRef.current === socketId) refreshingRef.current = null;
      }

      // A newer socket was opened while refreshing (for example by a retry
      // scheduled earlier); its own close decides whether to reconnect.
      if (socketIdRef.current !== socketId) return;

      if (!canRetry) {
        // Nothing to refresh with; a new sign-in brings a new token.
        authFailuresRef.current = MAX_AUTH_FAILURES;
        return;
      }

      attemptReconnect();
    },
    [attemptReconnect],
  );

  const createWebSocketConnection = useCallback(() => {
    if (!accessToken.current) return;
    const token = getCurrentAccessToken(accessToken.current);
    const refused = refusedCredentialRef.current;
    if (
      refused &&
      refused.token === token &&
      refused.organizationId === organizationId
    )
      return;
    refusedCredentialRef.current = null;
    if (!token) {
      logger.debug("No access token found, skipping WebSocket connection.");
      return;
    }

    // Close any existing connection before creating a new one
    closeConnection();

    const isSecure = HOST_API.includes("https");
    const wsHost = HOST_API.replace(/^https?:\/\//, "").replace(/\/+$/, "");
    const protocol = isSecure ? "wss" : "ws";
    const query = new URLSearchParams({ token });
    if (organizationId) query.set("organization_id", organizationId);
    const wsUrl = `${protocol}://${wsHost}/ws/connect/?${query}`;

    const socket = new WebSocket(wsUrl);
    socketIdRef.current += 1;
    const socketId = socketIdRef.current;
    socketRef.current = socket;

    // Create and store event handlers
    const openHandler = () => {
      setIsConnected(true);
      setError(null);
      // The handshake is accepted before the server validates the token.
      logger.debug("WebSocket connected");
      startHeartbeat();
    };

    const messageHandler = (event) => {
      // A server frame confirms authentication and restores both retry budgets.
      authenticationUnavailableRef.current = false;
      reconnectAttempts.current = 0;
      authFailuresRef.current = 0;
      let message = event.data;
      try {
        message = JSON.parse(event.data);
      } catch {
        // Some websocket producers may send plain text. Forward it unchanged.
      }

      messageListenersRef.current.forEach((listener) => {
        try {
          listener(message);
        } catch (e) {
          logger.error("WebSocket message listener failed", e);
        }
      });
    };

    const errorHandler = () => {
      // WebSocket error events don't provide detailed error information.
      // Extract useful context from the WebSocket state for debugging.
      const ws = socketRef.current;
      const readyStateMap = {
        [WebSocket.CONNECTING]: "CONNECTING",
        [WebSocket.OPEN]: "OPEN",
        [WebSocket.CLOSING]: "CLOSING",
        [WebSocket.CLOSED]: "CLOSED",
      };

      const errorContext = {
        readyState: ws ? readyStateMap[ws.readyState] || ws.readyState : "null",
        url: ws?.url ? ws.url.replace(/token=[^&]+/, "token=***") : "unknown",
        protocol: ws?.protocol || "none",
        reconnectAttempts: reconnectAttempts.current,
      };

      // Create a proper Error object with meaningful context
      const wsError = new Error(
        `WebSocket connection failed (state: ${errorContext.readyState}, attempts: ${errorContext.reconnectAttempts})`,
      );
      wsError.name = "WebSocketError";

      // Transient WS drops (network blips, navigation, token expiry, proxy
      // idle-timeout) are routine and must NOT create Sentry issues. Record a
      // breadcrumb for context and only capture an exception once reconnection
      // is permanently exhausted (the final attempt).
      if (reconnectAttempts.current >= MAX_RECONNECT_ATTEMPTS) {
        logger.error("WebSocket error:", wsError, errorContext);
      } else {
        logger.addBreadcrumb({
          category: "websocket",
          level: "warning",
          message: wsError.message,
          data: errorContext,
        });
      }
      setError("WebSocket connection error");
      setIsConnected(false);
      // A failure other than 1011 ends the slow retries for unavailable
      // authentication; the normal retry limit applies again.
      authenticationUnavailableRef.current = false;

      if (
        socketRef.current?.readyState !== WebSocket.OPEN &&
        socketRef.current?.readyState !== WebSocket.CONNECTING
      ) {
        attemptReconnect();
      }
    };

    const closeHandler = (event) => {
      if (socketRef.current !== socket) return;
      setIsConnected(false);
      socketRef.current = null;
      stopHeartbeat();

      if (
        AUTHORIZATION_CLOSE_CODES.has(event.code) ||
        event.code === AUTH_REJECTED_CLOSE_CODE
      ) {
        refusedCredentialRef.current = { token, organizationId };
        if (reconnectTimeoutRef.current) {
          clearTimeout(reconnectTimeoutRef.current);
          reconnectTimeoutRef.current = null;
        }
      }

      if (AUTHORIZATION_CLOSE_CODES.has(event.code)) {
        logger.addBreadcrumb({
          category: "websocket",
          level: "info",
          message: `WebSocket closed by the server (${event.code}); not reconnecting`,
        });
        return;
      }

      if (event.code === AUTHENTICATION_UNAVAILABLE_CLOSE_CODE) {
        // Keep backing off while the server cannot authenticate the socket.
        authenticationUnavailableRef.current = true;
      } else {
        authenticationUnavailableRef.current = false;
      }

      if (event.code === 1006) {
        logger.debug("WebSocket closed with 1006 - skipping reconnect");
        return;
      }

      logger.debug("WebSocket closed:", event);

      if (event.code === AUTH_REJECTED_CLOSE_CODE) {
        authFailuresRef.current += 1;
        if (authFailuresRef.current >= MAX_AUTH_FAILURES) {
          logger.addBreadcrumb({
            category: "websocket",
            level: "warning",
            message: `WebSocket token rejected ${authFailuresRef.current} times; not reconnecting`,
          });
          return;
        }
        if (authenticated) {
          reconnectAttempts.current = authFailuresRef.current - 1;
          refreshTokenThenReconnect(token, socketId);
        }
        return;
      }

      if (authenticated) {
        attemptReconnect();
      }
    };

    // Store handlers for later removal
    eventHandlersRef.current = {
      open: openHandler,
      message: messageHandler,
      error: errorHandler,
      close: closeHandler,
    };

    // Add event listeners
    socket.addEventListener("open", openHandler);
    socket.addEventListener("message", messageHandler);
    socket.addEventListener("error", errorHandler);
    socket.addEventListener("close", closeHandler);
  }, [
    authenticated,
    organizationId,
    closeConnection,
    startHeartbeat,
    stopHeartbeat,
    attemptReconnect,
    refreshTokenThenReconnect,
  ]);

  const sendMessage = useCallback(
    (message) => {
      if (socketRef.current?.readyState === WebSocket.OPEN) {
        socketRef.current.send(JSON.stringify(message));
        return true;
      } else {
        logger.warn("Cannot send, WebSocket not open");
        if (
          socketRef.current?.readyState !== WebSocket.CONNECTING &&
          !reconnectTimeoutRef.current
        ) {
          attemptReconnect();
        }
        return false;
      }
    },
    [attemptReconnect],
  );

  const addMessageListener = useCallback((listener) => {
    messageListenersRef.current.add(listener);
    return () => {
      messageListenersRef.current.delete(listener);
    };
  }, []);

  // Consolidated authentication state handling
  useEffect(() => {
    if (authenticated && user?.accessToken && !requiresOrgSetup) {
      accessToken.current = user.accessToken;
      createWebSocketConnection();
    }

    // A retry or refresh belongs only to this credential and organization.
    return () => {
      accessToken.current = null;
      socketIdRef.current += 1;
      refreshingRef.current = null;
      authFailuresRef.current = 0;
      reconnectAttempts.current = 0;
      authenticationUnavailableRef.current = false;
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
      closeConnection();
    };
  }, [
    authenticated,
    user?.accessToken,
    organizationId,
    requiresOrgSetup,
    createWebSocketConnection,
    closeConnection,
  ]);

  // Network status handling
  useEffect(() => {
    const handleOnline = () => {
      logger.debug("Network reconnected");
      if (
        authenticated &&
        (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN)
      ) {
        attemptReconnect();
      }
    };

    const handleOffline = () => {
      logger.debug("Network disconnected");
      closeConnection();
    };

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      closeConnection();

      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
    };
  }, [attemptReconnect, closeConnection, authenticated]);

  useEffect(() => {
    createWebSocketConnectionRef.current = createWebSocketConnection;
  }, [createWebSocketConnection]);

  const contextValue = useMemo(
    () => ({
      socket: socketRef.current,
      sendMessage,
      addMessageListener,
      isConnected,
      error,
      closeConnection,
    }),
    [sendMessage, addMessageListener, isConnected, error, closeConnection],
  );

  return (
    <WebSocketContext.Provider value={contextValue}>
      {children}
    </WebSocketContext.Provider>
  );
};

WebSocketProvider.propTypes = {
  children: PropTypes.node.isRequired,
};

export default WebSocketContext;
