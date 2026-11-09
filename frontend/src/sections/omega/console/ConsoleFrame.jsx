import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import { Helmet } from "react-helmet-async";

import { EMBED_STATUS, EmbeddedSurface } from "src/sections/omega/embed";
import {
  badgeText,
  readConsoleInbox,
  reportConsoleInbox,
  useConsoleInbox,
} from "src/sections/omega/shell/console-inbox";
import { maybeNotifyInbox } from "src/sections/omega/shell/console-notify";

import { useConsoleChrome } from "./console-chrome-link";
import {
  CONSOLE_BASE_PATH,
  CONSOLE_NO_KEY_TIMEOUT_MS,
  CONSOLE_ROUTE_MODE,
  consoleFrameAttributes,
  consoleOrigin,
} from "./console-config";
import { CONSOLE_LABEL, consoleGateCopy } from "./console-copy";
import { createConsoleTelemetry } from "./console-telemetry";
import { MINT_STATE } from "./session-assertion";
import { useConsoleKey } from "./use-console-key";
import { useHostNavigateMessages } from "./use-host-navigate";

// The Omega Console in a full-width frame. The frame gets fresh Omega session
// assertions as its keys over omega-embed/1 (as many as each hello asks for,
// CP P7), follows the Future AGI theme, and keeps its route in step with this
// page's URL both ways. Its health reports go to Future AGI's error tracker
// (P8) and its Inbox count to the product switcher and this tab's title
// (P15). The Console draws its own sidebar; its "Future AGI" entry brings the
// user back here. The frame counts as connected only once the Console's gate
// confirms the key it was handed (embed.gate "ready"), and only for the
// document that is in the frame now: the Console names its document in every
// hello and gate (RL 1275A), so a Console page that reloads in place goes back
// to opening until its new document confirms its own key.

export default function ConsoleFrame({ src, mintAssertion, onViewerRefused }) {
  const expectedOrigin = useMemo(() => consoleOrigin(src), [src]);
  const frame = useMemo(() => consoleFrameAttributes(src), [src]);
  const containerRef = useRef(null);
  const [status, setStatus] = useState(EMBED_STATUS.connecting);
  const key = useConsoleKey(mintAssertion ? { mintAssertion } : undefined);
  const { setFrameShown } = useConsoleChrome();
  const telemetry = useMemo(() => createConsoleTelemetry(), []);
  const inbox = useConsoleInbox();

  const connected = status === EMBED_STATUS.connected;
  const copy = useMemo(() => {
    const words = consoleGateCopy(key.mintFailure);
    // Waiting for the Console to confirm its key is still part of opening it,
    // so the pending panel keeps the same words.
    return { ...words, checking: words.connecting };
  }, [key.mintFailure]);

  const { onStatusChange: keyStatusChange } = key;
  const onStatusChange = useCallback(
    (next) => {
      setStatus(next);
      keyStatusChange(next);
    },
    [keyStatusChange],
  );

  const onBadge = useCallback((count) => {
    const previous = readConsoleInbox();
    reportConsoleInbox(count);
    maybeNotifyInbox(previous, count);
  }, []);

  useHostNavigateMessages({ containerRef, expectedOrigin, enabled: connected });

  useEffect(() => {
    setFrameShown(connected);
  }, [connected, setFrameShown]);
  useEffect(() => () => setFrameShown(false), [setFrameShown]);

  // The exchange refused a viewer: the page swaps the frame for its panel.
  const viewerRefused = key.mintFailure === MINT_STATE.VIEWER;
  useEffect(() => {
    if (viewerRefused) onViewerRefused?.();
  }, [viewerRefused, onViewerRefused]);

  const title =
    inbox > 0 ? `(${badgeText(inbox)}) ${CONSOLE_LABEL}` : CONSOLE_LABEL;

  return (
    <Box
      ref={containerRef}
      // The shell's route tests find every tab entry by data-testid
      // "omega-tab-<tab>".
      data-testid="omega-tab-console"
      data-state={status}
      data-frame-origin={frame.crossOrigin ? "console" : "same"}
      sx={{
        flex: "1 1 auto",
        display: "flex",
        flexDirection: "column",
        width: 1,
        height: 1,
        minWidth: 0,
        minHeight: 0,
      }}
    >
      <Helmet>
        <title>{title}</title>
      </Helmet>
      <EmbeddedSurface
        src={src}
        expectedOrigin={expectedOrigin || undefined}
        getToken={key.getToken}
        getTokens={key.getTokens}
        basePath={CONSOLE_BASE_PATH}
        routeMode={CONSOLE_ROUTE_MODE}
        title={CONSOLE_LABEL}
        label={CONSOLE_LABEL}
        copy={copy}
        // The Console signs in through Future AGI only, so the gate offers no
        // "open it directly" link (an empty string turns it off).
        openHref=""
        noKeyTimeoutMs={CONSOLE_NO_KEY_TIMEOUT_MS}
        // Handing the key over proves nothing: the Console may turn it down
        // or never answer. It is connected only once its gate says "ready";
        // until then the panel stays pending, then shows why it is not.
        requireGate
        // The Console names its document (doc) in every hello and gate and
        // uses only a key that echoes it: one without a doc is not the
        // Console, and an in-place reload is a new document to confirm.
        requireDoc
        onStatusChange={onStatusChange}
        onTelemetry={telemetry.report}
        onBadge={onBadge}
        allow={frame.allow}
        sandbox={frame.sandbox}
      />
    </Box>
  );
}

ConsoleFrame.propTypes = {
  src: PropTypes.string.isRequired,
  mintAssertion: PropTypes.func,
  onViewerRefused: PropTypes.func,
};
