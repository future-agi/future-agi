import PropTypes from "prop-types";
import React, {
  createContext,
  useEffect,
  useRef,
  useState,
  useCallback,
  useMemo,
} from "react";
import { useNavigate } from "react-router";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { HOST_API } from "src/config-global";
import logger from "src/utils/logger";

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
const AUTHORIZATION_CLOSE_CODES = new Set([4001, 4002, 4003]);
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
  const reconnectAttemptsBeforeOpen = useRef(0);
  const retryBudgetCredentialRef = useRef(null);
  // Set when the server last closed with 1011; cleared by any server frame.
  const authenticationUnavailableRef = useRef(false);
  const navigate = useNavigate();

  const socketRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const heartbeatIntervalRef = useRef(null);
  const accessToken = useRef(null);
  const socketOrganizationId = useRef(null);
  const organizationId = user?.organization?.id;
  // An org-less session (removed from its organization) has no socket to open.
  const requiresOrgSetup = Boolean(user?.requires_org_setup);
  // Set to the { token, organizationId } the server refused; cleared when either changes.
  const authorizationRefusedRef = useRef(null);
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
    // The server refused this credential; only a new token or organization retries.
    if (authorizationRefusedRef.current) {
      return;
    }

    // Don't attempt to reconnect if already trying or if connection is already open
    if (
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

  const createWebSocketConnection = useCallback(() => {
    if (!accessToken.current) {
      logger.debug("No access token found, skipping WebSocket connection.");
      return;
    }

    // Close any existing connection before creating a new one
    closeConnection();

    const isSecure = HOST_API.includes("https");
    const wsHost = HOST_API.replace(/^https?:\/\//, "").replace(/\/+$/, "");
    const protocol = isSecure ? "wss" : "ws";
    const query = new URLSearchParams({ token: accessToken.current });
    if (organizationId) query.set("organization_id", organizationId);
    const wsUrl = `${protocol}://${wsHost}/ws/connect/?${query}`;

    const socket = new WebSocket(wsUrl);
    socketRef.current = socket;
    socketOrganizationId.current = organizationId;

    // Create and store event handlers
    const openHandler = () => {
      setIsConnected(true);
      setError(null);
      reconnectAttemptsBeforeOpen.current = reconnectAttempts.current;
      reconnectAttempts.current = 0; // Reset reconnect attempts on successful connection
      logger.debug("WebSocket connected");
      startHeartbeat();
    };

    const messageHandler = (event) => {
      // A frame from the server means the socket was authenticated, so a
      // later 1011 close starts a new backoff instead of resuming the old one.
      reconnectAttemptsBeforeOpen.current = 0;
      authenticationUnavailableRef.current = false;
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
      setIsConnected(false);
      socketRef.current?.close?.();
      socketRef.current = null;
      stopHeartbeat();

      if (AUTHORIZATION_CLOSE_CODES.has(event.code)) {
        authorizationRefusedRef.current = {
          token: accessToken.current,
          organizationId: socketOrganizationId.current,
        };
        if (reconnectTimeoutRef.current) {
          clearTimeout(reconnectTimeoutRef.current);
          reconnectTimeoutRef.current = null;
        }
        logger.addBreadcrumb({
          category: "websocket",
          level: "info",
          message: `WebSocket closed by the server (${event.code}); not reconnecting`,
        });
        return;
      }

      if (event.code === AUTHENTICATION_UNAVAILABLE_CLOSE_CODE) {
        // Keep backing off while the server cannot authenticate the socket.
        reconnectAttempts.current = reconnectAttemptsBeforeOpen.current;
        authenticationUnavailableRef.current = true;
      } else {
        authenticationUnavailableRef.current = false;
      }

      if (event.code === 1006) {
        logger.debug("WebSocket closed with 1006 - skipping reconnect");
        return;
      }

      logger.debug("WebSocket closed:", event);

      // if (event.code === 4003) {
      //     console.warn('WebSocket closed with 4003 - forcing logout');
      //     setSession(null);
      //     resetUser();
      //     navigate('/auth/jwt/login');
      // } else
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
    navigate,
    startHeartbeat,
    stopHeartbeat,
    attemptReconnect,
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
      accessToken.current = user?.accessToken;

      const refused = authorizationRefusedRef.current;
      if (
        refused &&
        refused.token === user?.accessToken &&
        refused.organizationId === organizationId
      ) {
        return;
      }
      authorizationRefusedRef.current = null;

      const budget = retryBudgetCredentialRef.current;
      if (
        !budget ||
        budget.token !== user?.accessToken ||
        budget.organizationId !== organizationId
      ) {
        // A new credential or organization gets the full retry budget.
        retryBudgetCredentialRef.current = {
          token: user?.accessToken,
          organizationId,
        };
        reconnectAttempts.current = 0;
        reconnectAttemptsBeforeOpen.current = 0;
        authenticationUnavailableRef.current = false;
        // A retry scheduled for the old credential would replace the socket
        // opened below.
        if (reconnectTimeoutRef.current) {
          clearTimeout(reconnectTimeoutRef.current);
          reconnectTimeoutRef.current = null;
        }
      }

      if (
        !socketRef.current ||
        socketRef.current.readyState !== WebSocket.OPEN ||
        socketOrganizationId.current !== organizationId
      ) {
        createWebSocketConnection();
      }
    } else {
      accessToken.current = null;
      closeConnection();
    }
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
