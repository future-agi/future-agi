import { useEffect, useRef, useCallback } from "react";
import { useAuthContext } from "src/auth/hooks";
import { requestWebSocketUrl } from "src/components/websocket/use-socket";
import { MAX_SOCKET_FAILURES, useSocketResume, useSocketSessionToken } from "src/components/websocket/socket-recovery";
import logger from "src/utils/logger";

/**
 * Opens a dedicated WebSocket to `ws/simulation-updates/` for the given
 * testId and invokes `onUpdate` whenever a `simulation_update` message
 * arrives.  Auto-reconnects on close with exponential back-off.
 *
 * Usage:
 *   useSimulationSocket(testId, () => refreshGrid());
 */
export const useSimulationSocket = (testId, onUpdate) => {
  const { user } = useAuthContext();
  const token = useSocketSessionToken(user?.accessToken);
  const socketRef = useRef(null);
  const onUpdateRef = useRef(onUpdate);
  onUpdateRef.current = onUpdate;
  const retriesRef = useRef(0);
  const timerRef = useRef(null);
  // Track whether the hook is still mounted
  const mountedRef = useRef(true);
  const generationRef = useRef(0);
  const pendingRef = useRef(false);

  const connect = useCallback(async () => {
    if (!token || !testId || !mountedRef.current || pendingRef.current || retriesRef.current >= MAX_SOCKET_FAILURES) return;
    timerRef.current = null;

    // Don't open a second socket if one is already connecting/open
    if (
      socketRef.current &&
      (socketRef.current.readyState === WebSocket.CONNECTING ||
        socketRef.current.readyState === WebSocket.OPEN)
    ) {
      return;
    }

    const generation = generationRef.current;
    pendingRef.current = true;
    let ws;
    try {
      const url = await requestWebSocketUrl(token, "/ws/simulation-updates/", { test_id: String(testId) });
      if (!mountedRef.current || generation !== generationRef.current) return;
      ws = new WebSocket(url);
    } catch (error) {
      if (mountedRef.current && generation === generationRef.current) {
        if (error?.retryable === false) {
          retriesRef.current = MAX_SOCKET_FAILURES;
          return;
        }
        const delay = Math.min(1000 * 2 ** retriesRef.current++, 30000);
        timerRef.current = setTimeout(connect, delay);
      }
      return;
    } finally {
      if (generation === generationRef.current) pendingRef.current = false;
    }
    socketRef.current = ws;

    ws.onopen = () => {
      retriesRef.current = 0; // reset back-off on successful connect
    };

    ws.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data);
        if (parsed?.type === "simulation_update") {
          onUpdateRef.current?.(parsed.data);
        }
      } catch {
        // ignore non-JSON
      }
    };

    ws.onerror = () => {
      logger.warn("Simulation WebSocket error");
    };

    ws.onclose = (event) => {
      // Only act if this is still the current socket — prevents stale
      // sockets (e.g. from React StrictMode double-mount) from wiping
      // the ref or triggering spurious reconnects.
      if (socketRef.current !== ws) return;
      socketRef.current = null;

      // Auth-related errors → do NOT retry
      if ([4001, 4003, 4401, 4403, 1008].includes(event.code)) {
        logger.warn("WebSocket auth/policy error:", event.code);
        return;
      }

      if (mountedRef.current) {
        const delay = Math.min(1000 * 2 ** retriesRef.current, 30000);
        retriesRef.current += 1;
        timerRef.current = setTimeout(connect, delay);
      }
    };
  }, [token, testId]);

  useSocketResume(() => {
    if (socketRef.current?.readyState === WebSocket.OPEN || pendingRef.current) return;
    clearTimeout(timerRef.current);
    retriesRef.current = 0;
    connect();
  });

  useEffect(() => {
    mountedRef.current = true;
    retriesRef.current = 0;
    connect();
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      pendingRef.current = false;
      clearTimeout(timerRef.current);
      if (socketRef.current) {
        socketRef.current.close();
        socketRef.current = null;
      }
    };
  }, [connect]);
};
