// One question to the Omega gateway per page load.
//
// An install that leaves VITE_OMEGA_CONSOLE unset shows the Omega tabs only
// where an Omega gateway answers on this origin (flags.js). The gateway
// answers GET <base>/healthz itself, before any session or upstream, with
// 200 application/json {"live": true} (services/omega-gateway/src/server.mjs,
// its /healthz probe). Anything else means no gateway, and no failed request
// where the stack is ours:
//   - the UI's own nginx answers exactly this path with 200 {"live": false}
//     (frontend/nginx.conf, deploy/standalone/nginx.conf and the chart's
//     templates/frontend/configmap.yaml); an edge that routes /omega/api to a
//     gateway answers first;
//   - an SPA host answers 200 with index.html (vercel.json, the Vite dev
//     server, whose fallback needs Accept */*, which is why no Accept header
//     is sent), so the status alone is never enough;
//   - a network error, or no answer within GATEWAY_PROBE_TIMEOUT_MS.
// main.jsx starts the question as the app boots and renders at once. The
// state is a small store: CHECKING until the answer, then UP or DOWN for the
// page's life (no polling, no retry). The router and the nav read it through
// useOmegaConsole.js and build again when it changes.

import { useSyncExternalStore } from "react";

const DEFAULT_OMEGA_API_BASE = "/omega/api";
// The rule of shared/omegaClient.js resolveOmegaApiBase. Not imported from
// there: that module builds its default client when it loads.
const BASE_RE = /^(?:\/[A-Za-z0-9_-]+)+$/;

export const GATEWAY_PROBE_TIMEOUT_MS = 2000;

export const GATEWAY_STATE = Object.freeze({
  // Never asked: a set switch, or a unit test that did not ask.
  IDLE: "idle",
  CHECKING: "checking",
  UP: "up",
  DOWN: "down",
});

let state = GATEWAY_STATE.IDLE;
let settling = null;
// Bumped by resetGatewayProbe(), so an answer that lands after a reset (a
// test that left its probe out) never writes into the next one's state.
let generation = 0;
const listeners = new Set();

function setState(next) {
  if (state === next) return;
  state = next;
  listeners.forEach((listener) => listener());
}

/** `<base>/healthz` on the data client's base (VITE_OMEGA_API_BASE, else /omega/api). */
export function gatewayProbeUrl(base = import.meta.env?.VITE_OMEGA_API_BASE) {
  const root =
    typeof base === "string" && BASE_RE.test(base)
      ? base
      : DEFAULT_OMEGA_API_BASE;
  return `${root}/healthz`;
}

async function isGatewayAnswer(response) {
  if (!response?.ok) return false;
  const type = response.headers?.get?.("content-type") || "";
  if (!type.toLowerCase().includes("json")) return false;
  const body = await response.json();
  return body?.live === true;
}

/**
 * Ask once. Resolves true only for a 2xx JSON answer whose `live` is true,
 * false for anything else (a timeout included). Never rejects.
 */
export function askGateway({
  fetchImpl = (...args) => globalThis.fetch(...args),
  timeoutMs = GATEWAY_PROBE_TIMEOUT_MS,
  url = gatewayProbeUrl(),
} = {}) {
  return new Promise((resolve) => {
    let timer = null;
    let done = false;
    const finish = (answer) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(answer === true);
    };
    const controller =
      typeof AbortController === "function" ? new AbortController() : null;
    // The timer settles the answer even when a fetch ignores the abort.
    timer = setTimeout(() => {
      controller?.abort();
      finish(false);
    }, timeoutMs);
    try {
      Promise.resolve(
        fetchImpl(url, {
          method: "GET",
          credentials: "omit",
          cache: "no-store",
          signal: controller?.signal,
        }),
      )
        .then(isGatewayAnswer)
        .then(finish, () => finish(false));
    } catch {
      finish(false);
    }
  });
}

/** This page's state: IDLE, CHECKING, UP or DOWN. */
export function gatewayProbeState() {
  return state;
}

/** Called after every change of state; returns the unsubscribe function. */
export function subscribeGatewayProbe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The state, re-rendering the caller when it changes. */
export function useGatewayProbeState() {
  return useSyncExternalStore(
    subscribeGatewayProbe,
    gatewayProbeState,
    gatewayProbeState,
  );
}

/**
 * Ask the gateway once for this page. The state is CHECKING as soon as this
 * returns. Later calls return the same promise, which resolves to
 * GATEWAY_STATE.UP or DOWN and never rejects.
 */
export function settleGatewayProbe(options) {
  if (!settling) {
    const asked = generation;
    setState(GATEWAY_STATE.CHECKING);
    settling = askGateway(options).then((up) => {
      if (asked !== generation) return state;
      setState(up ? GATEWAY_STATE.UP : GATEWAY_STATE.DOWN);
      return state;
    });
  }
  return settling;
}

/** Tests only: forget this page's answer (subscribers are not called). */
export function resetGatewayProbe() {
  generation += 1;
  state = GATEWAY_STATE.IDLE;
  settling = null;
}
