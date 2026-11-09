// React glue for one framed surface: runs the omega-embed/1 bridge, keeps the
// parent URL and the child route in step, passes the theme down, and tracks
// the gate the user sees (connecting, checking, connected, needs-key, refused,
// timeout; the steps are in gate-status.js).
//
// With `requireDoc` (the EE Console, RL 1275A) the child names its document in
// every hello and gate, and the frame's load event goes to the bridge
// (onFrameLoad). When the bridge reports a new document, the old document's
// status ends: the surface goes back to connecting, its bounds start over and,
// after a reload, so does the wait for a hello. Nothing here ever sees a doc.
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { createEmbedBridge } from "./bridge";
import { HELLO_TIMEOUT_MS, diagnoseMissingHello } from "./diagnosis";
import { EMBED_STATUS, GATE_EVENT, nextEmbedStatus } from "./gate-status";
import { childPathFromParent, parentPathFromChild } from "./route-sync";

export { EMBED_STATUS };

// How long the child may take to report its gate when its word decides:
// - after a hello we hold no key for. A repeat hello from the SAME document
//   can race with a key just handed over. A reloaded Studio document has lost
//   its memory-only key and reports "needs-key"; neither side restores it
//   from storage. Only a freshly opened/pasted link can hand over a key again;
// - with `requireGate` (Studio, the EE Console), after a key was handed over
//   and is being checked.
// This bound only matters for a child that never reports a gate; it sits well
// above Studio's 3 s wait for a key. When it passes, the surface asks for a key
// again (needs-key); it never turns connected on its own.
//
// A surface may set `noKeyTimeoutMs` to 0 (the Console: a hello it could not
// answer means its own mint failed, so it already knows why). That says
// nothing about a key it did hand over: the child needs a round trip before it
// can report on it, so such a key gets this whole bound instead.
//
// The bound for a key runs from the first key handed over in an attempt. The
// Console is handed a fresh key for every hello, so a repeat hello must not
// push it out, and once it has passed a further key changes nothing: the
// surface keeps asking for a key until the child reports its gate or the
// user reconnects (retry).
export const NO_KEY_GATE_TIMEOUT_MS = 8000;

const keyCheckTimeoutMs = (noKeyTimeoutMs) =>
  noKeyTimeoutMs > 0 ? noKeyTimeoutMs : NO_KEY_GATE_TIMEOUT_MS;

export function useEmbedSession({
  frameRef,
  frameSrc,
  expectedOrigin,
  getToken,
  getTokens,
  basePath,
  themeMode,
  productAvailability,
  probePath,
  onOpen,
  onStatusChange,
  onTelemetry,
  onBadge,
  helloTimeoutMs = HELLO_TIMEOUT_MS,
  noKeyTimeoutMs = NO_KEY_GATE_TIMEOUT_MS,
  requireGate = false,
  requireDoc = false,
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const [status, setStatus] = useState(EMBED_STATUS.connecting);
  const [diagnosis, setDiagnosis] = useState(null);
  const [attempt, setAttempt] = useState(0);

  const bridgeRef = useRef(null);
  const helloSeen = useRef(false);
  const noKeyTimer = useRef(null);
  // The running bound waits on a key handed over (not on a hello we held no
  // key for).
  const checkingKey = useRef(false);
  // This attempt's bound for a key passed with no word from the child.
  const keyCheckLapsed = useRef(false);
  // Cancels the running wait for a hello.
  const stopHelloWait = useRef(() => {});
  // The frame element fired a load event (so a bridge started later knows).
  const frameLoaded = useRef(false);
  const lastChildPath = useRef(
    basePath
      ? childPathFromParent(location.pathname, location.search, basePath)
      : "/",
  );
  const latest = useRef({});
  latest.current = {
    getToken,
    getTokens,
    onTelemetry,
    onBadge,
    onOpen,
    onStatusChange,
    location,
    navigate,
    basePath,
    noKeyTimeoutMs,
    requireGate,
    frameSrc,
    probePath,
    helloTimeoutMs,
  };

  // Ends the wait: the child reported its gate, the user reconnects, or the
  // bridge goes away.
  const clearNoKeyTimer = useCallback(() => {
    clearTimeout(noKeyTimer.current);
    noKeyTimer.current = null;
    checkingKey.current = false;
    keyCheckLapsed.current = false;
  }, []);

  const step = useCallback((event) => {
    const { requireGate: gated } = latest.current;
    setStatus((current) =>
      nextEmbedStatus(current, event, { requireGate: gated }),
    );
  }, []);

  // The bound on the child's word (NO_KEY_GATE_TIMEOUT_MS). A key handed over
  // (`forKey`) gets the whole bound once: it replaces a bound still waiting on
  // a hello we held no key for, never a bound already running for a key.
  const awaitVerdict = useCallback(
    ({ forKey = false } = {}) => {
      if (noKeyTimer.current && (!forKey || checkingKey.current)) return;
      clearTimeout(noKeyTimer.current);
      checkingKey.current = forKey;
      const { noKeyTimeoutMs: bound } = latest.current;
      noKeyTimer.current = setTimeout(
        () => {
          noKeyTimer.current = null;
          if (checkingKey.current) keyCheckLapsed.current = true;
          checkingKey.current = false;
          step({ kind: GATE_EVENT.noVerdict });
        },
        forKey ? keyCheckTimeoutMs(bound) : bound,
      );
    },
    [step],
  );

  const cancelHelloWait = useCallback(() => {
    stopHelloWait.current();
    stopHelloWait.current = () => {};
  }, []);

  // Waits helloTimeoutMs for a hello. None: find out why, then show the cause.
  const waitForHello = useCallback(() => {
    cancelHelloWait();
    helloSeen.current = false;
    let cancelled = false;
    const {
      frameSrc: src,
      probePath: probe,
      helloTimeoutMs: timeoutMs,
    } = latest.current;
    const timer = setTimeout(async () => {
      if (helloSeen.current || cancelled) return;
      setStatus(EMBED_STATUS.diagnosing);
      const result = await diagnoseMissingHello({ src, probePath: probe });
      if (cancelled || helloSeen.current) return;
      setDiagnosis(result);
      setStatus(EMBED_STATUS.timeout);
    }, timeoutMs);
    stopHelloWait.current = () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [cancelHelloWait]);

  // The bridge lives as long as the frame's origin does.
  useEffect(() => {
    const bridge = createEmbedBridge({
      expectedOrigin,
      requireDoc,
      getFrameWindow: () => frameRef.current?.contentWindow ?? null,
      getToken: (context) =>
        (context
          ? latest.current.getToken?.(context)
          : latest.current.getToken?.()) ?? null,
      // Forward the document's cancellation context all the way to a stateful
      // minter. Discarding only its returned keys is too late for its cache.
      getTokens: (want, context) => {
        if (latest.current.getTokens) {
          return context
            ? latest.current.getTokens(want, context)
            : latest.current.getTokens(want);
        }
        const token = context
          ? latest.current.getToken?.(context)
          : latest.current.getToken?.();
        return Promise.resolve(token ?? null).then((value) =>
          value ? [value] : [],
        );
      },
      onEvent: (event) => {
        const { location: loc, navigate: nav, basePath: base } = latest.current;
        switch (event.kind) {
          case "hello":
            helloSeen.current = true;
            break;
          case GATE_EVENT.document:
            // A new document (requireDoc): what the old one was handed or
            // proved ends, and so do the bounds that waited on it.
            clearNoKeyTimer();
            setDiagnosis(null);
            step(event);
            // Load waits for a live proof/new hello; an early replacement
            // hello also starts a fresh bound rather than inheriting A's wait.
            waitForHello();
            break;
          case GATE_EVENT.keySent:
            if (latest.current.requireGate) {
              // Its bound already passed unanswered: another key is the same
              // unconfirmed attempt and must not bring "checking" back.
              if (keyCheckLapsed.current) break;
              step(event);
              // Only the child's gate can say the key works.
              awaitVerdict({ forKey: true });
              break;
            }
            step(event);
            break;
          case GATE_EVENT.noKey:
            step(event);
            setDiagnosis(null);
            awaitVerdict();
            break;
          case GATE_EVENT.gate:
            clearNoKeyTimer();
            step(event);
            break;
          case "route": {
            if (!base) break;
            lastChildPath.current = event.path;
            const target = parentPathFromChild(base, event.path);
            if (target !== `${loc.pathname}${loc.search}`) {
              // The child's first report after a hello only corrects the URL;
              // a push there would make Back bounce straight forward again.
              nav(target, { replace: event.first });
            }
            break;
          }
          case "open":
            if (latest.current.onOpen) latest.current.onOpen(event.url);
            else
              nav(`${event.url.pathname}${event.url.search}${event.url.hash}`);
            break;
          case "telemetry":
            latest.current.onTelemetry?.(event.message);
            break;
          case "badge":
            latest.current.onBadge?.(event.inbox);
            break;
          default:
            break;
        }
      },
    });
    bridgeRef.current = bridge;
    // The frame already loaded a document that this bridge did not see.
    if (frameLoaded.current) bridge.documentLoaded();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
      clearNoKeyTimer();
    };
  }, [
    expectedOrigin,
    requireDoc,
    frameRef,
    clearNoKeyTimer,
    step,
    awaitVerdict,
    waitForHello,
  ]);

  // No hello within the timeout: find out why, then show the cause.
  useEffect(() => {
    waitForHello();
    return cancelHelloWait;
  }, [
    attempt,
    frameSrc,
    probePath,
    helloTimeoutMs,
    waitForHello,
    cancelHelloWait,
  ]);

  // Every load suspends readiness; the bridge probes a pre-load candidate.
  // Never infer document identity from hello/load arrival order.
  const onFrameLoad = useCallback(() => {
    frameLoaded.current = true;
    bridgeRef.current?.documentLoaded();
  }, []);

  // Parent URL -> child route (Back/Forward, links elsewhere in the app).
  useEffect(() => {
    if (!basePath) return;
    const childPath = childPathFromParent(
      location.pathname,
      location.search,
      basePath,
    );
    if (childPath === lastChildPath.current) return;
    lastChildPath.current = childPath;
    bridgeRef.current?.navigate(childPath);
  }, [basePath, location.pathname, location.search]);

  useEffect(() => {
    bridgeRef.current?.setTheme(themeMode);
  }, [themeMode, expectedOrigin]);

  useEffect(() => {
    if (productAvailability !== undefined) {
      bridgeRef.current?.setProductAvailability(productAvailability);
    }
  }, [productAvailability, expectedOrigin, requireDoc, frameRef]);

  useEffect(() => {
    latest.current.onStatusChange?.(status, diagnosis);
  }, [status, diagnosis]);

  // Reloads the frame and starts a fresh handshake and timer.
  const retry = useCallback(() => {
    clearNoKeyTimer();
    // A new frame element: its document has not loaded yet.
    frameLoaded.current = false;
    const bridge = bridgeRef.current;
    if (bridge) {
      bridge.reset();
      // The reloaded frame starts at its first route; send the current one
      // as soon as it says hello.
      if (latest.current.basePath) bridge.navigate(lastChildPath.current);
    }
    setDiagnosis(null);
    setStatus(EMBED_STATUS.connecting);
    setAttempt((n) => n + 1);
  }, [clearNoKeyTimer]);

  return { status, diagnosis, attempt, retry, onFrameLoad };
}
