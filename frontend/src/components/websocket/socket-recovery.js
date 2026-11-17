import { useEffect, useRef, useState } from "react";
import axios from "src/utils/axios";
import logger from "src/utils/logger";

export const MAX_SOCKET_FAILURES = 10;
const RESUME_AFTER_MS = 60000;
// Shared across routes and remounts, but never persisted. No tickets are cached.
const ticketFailures = new Map();
const resumeListeners = new Set();
let hiddenAt = null;

const refused = (retryable = false) => Object.assign(
  new Error("Socket ticket refused"), { retryable },
);

export async function withSocketTicketRetry(token, request) {
  let state = ticketFailures.get(token);
  if (!state) {
    state = { failures: 0, blocked: false, warned: false };
    ticketFailures.set(token, state);
    // Retain only a few recent sessions, not a history of browser credentials.
    if (ticketFailures.size > 8) ticketFailures.delete(ticketFailures.keys().next().value);
  }
  if (state.blocked) throw refused();
  try {
    const result = await request();
    // Another concurrent request may have received an authentication refusal.
    if (!state.blocked) state.failures = 0;
    return result;
  } catch (error) {
    state.failures += 1;
    state.blocked ||= [401, 403].includes(error?.status) || state.failures >= MAX_SOCKET_FAILURES;
    if (!state.warned) {
      state.warned = true;
      logger.warn("Live updates are unavailable. Socket ticket requests are limited; they can resume after a session change or returning to this tab after a pause. You are still signed in.");
    }
    // Do not expose response bodies, credentials or upstream exception text.
    throw refused(!state.blocked);
  }
}

function handleVisibility() {
  if (document.visibilityState === "hidden") {
    hiddenAt ??= Date.now();
    return;
  }
  if (document.visibilityState !== "visible") return;
  const resume = hiddenAt !== null && Date.now() - hiddenAt >= RESUME_AFTER_MS;
  hiddenAt = null;
  if (!resume) return;
  ticketFailures.clear();
  resumeListeners.forEach((listener) => listener());
}

export function useSocketResume(onResume) {
  const callback = useRef(onResume);
  useEffect(() => { callback.current = onResume; }, [onResume]);
  useEffect(() => {
    const listener = () => callback.current();
    if (resumeListeners.size === 0) {
      hiddenAt = document.visibilityState === "hidden" ? Date.now() : null;
      document.addEventListener("visibilitychange", handleVisibility);
    }
    resumeListeners.add(listener);
    return () => {
      resumeListeners.delete(listener);
      if (resumeListeners.size === 0) document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, []);
}

export function getSocketSessionToken(contextToken) {
  if (!contextToken) return null;
  const header = axios.defaults.headers.common.Authorization;
  return typeof header === "string" && header.startsWith("Bearer ")
    ? header.slice(7) || contextToken
    : contextToken;
}

// Silent HTTP refresh publishes the new session in HTTP defaults before retrying
// the request, but does not update auth context. Observe that publication without
// refreshing, logging out, reading cross-tab storage or changing HTTP outcomes.
export function useSocketSessionToken(contextToken) {
  const [session, setSession] = useState(() => ({
    contextToken, token: getSocketSessionToken(contextToken),
  }));
  useEffect(() => {
    let active = true;
    const update = () => {
      if (!active) return;
      const token = getSocketSessionToken(contextToken);
      setSession((previous) => previous.contextToken === contextToken && previous.token === token
        ? previous : { contextToken, token });
    };
    update();
    const responses = axios.interceptors?.response;
    const interceptor = responses?.use((response) => {
      update();
      return response;
    }, (error) => {
      // Refresh may succeed even when the retried operation itself is refused.
      update();
      return Promise.reject(error);
    });
    return () => {
      active = false;
      responses?.eject(interceptor);
    };
  }, [contextToken]);
  return session.contextToken === contextToken ? session.token : contextToken;
}
