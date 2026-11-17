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

import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { EMBED_STATUS, EmbeddedSurface } from "src/sections/omega/embed";
import { useConsolePaletteBridge } from "src/sections/common/CommandPalette/consoleBridge";
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
// hello and gate, so a Console page that reloads in place goes back
// to opening until its new document confirms its own key.

// Who reads the routing detail when the Console doesn't answer: an
// organization owner or admin, or a workspace admin. It comes from the
// organization and workspace contexts the page's access check already reads
// (shell/omega-access.js), never a new request: the backend's levels
// (8 = admin, 15 = owner), or, when a level is missing, the role names the
// settings nav checks (ROLES.OWNER, ADMIN and WORKSPACE_ADMIN in
// src/utils/rolePermissionMapping.js, written out here because many tests
// replace that module). Anyone else, and anyone whose role hasn't loaded,
// gets the line that asks their admin.
const ADMIN_LEVEL = 8;
const isAdminLevel = (value) =>
  value !== null &&
  value !== undefined &&
  value !== "" &&
  Number(value) >= ADMIN_LEVEL;
const ADMIN_ROLES = new Set(["Owner", "Admin", "workspace_admin"]);

export default function ConsoleFrame({ src, mintAssertion, onViewerRefused }) {
  const expectedOrigin = useMemo(() => consoleOrigin(src), [src]);
  const frame = useMemo(() => consoleFrameAttributes(src), [src]);
  const containerRef = useRef(null);
  const [status, setStatus] = useState(EMBED_STATUS.connecting);
  const key = useConsoleKey(mintAssertion ? { mintAssertion } : undefined);
  const { setFrameShown } = useConsoleChrome();
  const telemetry = useMemo(() => createConsoleTelemetry(), []);
  const inbox = useConsoleInbox();
  const { orgLevel, currentOrganizationRole } = useOrganization();
  const { wsLevel, currentWorkspaceRole } = useWorkspace();
  const admin =
    isAdminLevel(orgLevel) ||
    isAdminLevel(wsLevel) ||
    ADMIN_ROLES.has(currentOrganizationRole) ||
    ADMIN_ROLES.has(currentWorkspaceRole);

  const connected = status === EMBED_STATUS.connected;
  const copy = useMemo(() => {
    // The no-answer panel names, for an admin, where this frame looked for
    // the Console.
    const words = consoleGateCopy(key.mintFailure, { src, admin });
    // Waiting for the Console to confirm its key is still part of opening it,
    // so the pending panel keeps the same words.
    return { ...words, checking: words.connecting };
  }, [key.mintFailure, src, admin]);

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
  // The bridge the Console uses to open the app palette (mounted once for the
  // whole route by its layout) and to hand it its commands.
  useConsolePaletteBridge({ containerRef, expectedOrigin, enabled: connected });

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
